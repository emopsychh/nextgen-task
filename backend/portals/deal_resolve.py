"""Resolve accompaniment CRM deals by portal link field on the deal."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.conf import settings

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours import (
    hours_fields_configured,
    read_deal_hours,
    remaining_update_fields,
)
from portals.deal_hours_credit import (
    apply_hours_credit_to_new_deal,
    capture_hours_credit_if_won,
    read_deal_stage_fields,
)

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", re.I)


def accompaniment_category_id() -> str:
    return (settings.BITRIX_ACCOMPANIMENT_CATEGORY_ID or "").strip()


def portal_link_field() -> str:
    return (settings.BITRIX_DEAL_PORTAL_LINK_FIELD or "").strip()


def company_project_id_field() -> str:
    return (settings.BITRIX_COMPANY_PROJECT_ID_FIELD or "").strip()


def deal_not_found_for_portal_message(client_portal) -> str:
    """User-facing copy when no accompaniment deal matches this portal."""
    host = normalize_portal_host(getattr(client_portal, "domain", "") or "")
    label = (getattr(client_portal, "name", "") or "").strip() or host or "клиента"
    if host and label != host:
        where = f"«{label}» ({host})"
    elif host:
        where = f"«{host}»"
    else:
        where = "этого клиента"
    return (
        f"Для {where} не найдена открытая сделка сопровождения. "
        "В CRM укажите в сделке ссылку на этот портал Bitrix24 "
        "и нажмите «Выбрать сделку»."
    )


def normalize_portal_host(value: str) -> str:
    """Extract comparable host from a portal domain or Bitrix URL."""
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlparse(text).hostname or "").lower().strip(".")
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def portal_link_matches(field_value: str, client_host: str) -> bool:
    """True only when the deal UF host equals this client portal host.

    No substring / parent-domain fuzzy match — those caused unrelated portals
    to inherit another company's accompaniment hours.
    """
    if not client_host:
        return False
    field_host = normalize_portal_host(field_value)
    if field_host:
        return field_host == client_host
    # Plain text: accept only an exact host-shaped value (no "contains").
    raw = (field_value or "").strip().lower().strip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    return bool(raw) and raw == client_host and bool(_HOST_RE.match(raw))


def deactivate_bindings_for_deal(
    *, agency_portal, deal_id: str, except_client_portal=None
) -> int:
    """Ensure a CRM deal is active for at most one client under this agency."""
    from portals.models import PortalDealBinding

    qs = PortalDealBinding.objects.filter(
        agency_portal=agency_portal,
        deal_id=str(deal_id),
        is_active=True,
    )
    if except_client_portal is not None:
        qs = qs.exclude(client_portal_id=except_client_portal.id)
    return qs.update(is_active=False)


def deactivate_client_bindings(*, agency_portal, client_portal) -> int:
    from portals.models import PortalDealBinding

    return PortalDealBinding.objects.filter(
        agency_portal=agency_portal,
        client_portal=client_portal,
        is_active=True,
    ).update(is_active=False)


def deal_link_matches_client(deal: dict, client_portal) -> bool:
    link_field = portal_link_field()
    if not link_field:
        return False
    client_host = normalize_portal_host(getattr(client_portal, "domain", "") or "")
    if not client_host or not _HOST_RE.match(client_host):
        return False
    return portal_link_matches(str(deal.get(link_field) or ""), client_host)


def _deal_list_select() -> list[str]:
    select = [
        "ID",
        "TITLE",
        "CATEGORY_ID",
        "COMPANY_ID",
        "STAGE_ID",
        "DATE_MODIFY",
        "CLOSED",
    ]
    link_field = portal_link_field()
    if link_field:
        select.append(link_field)
    paid = (settings.BITRIX_DEAL_PAID_HOURS_FIELD or "").strip()
    rem = (settings.BITRIX_DEAL_REMAINING_HOURS_FIELD or "").strip()
    if paid:
        select.append(paid)
    if rem:
        select.append(rem)
    return select


def _unwrap_deal_list(result) -> list[dict]:
    deals = result if isinstance(result, list) else []
    if isinstance(result, dict):
        deals = result.get("deals") or result.get("items") or result.get("result") or []
    return [d for d in deals if isinstance(d, dict)]


def list_open_deals_for_portal(client: BitrixClient, client_portal) -> list[dict]:
    """
    Open accompaniment deals whose portal-link UF points at this client portal.

    Never returns deals linked to other portals — picker stays scoped to the
    client we are binding.
    """
    link_field = portal_link_field()
    client_host = normalize_portal_host(getattr(client_portal, "domain", "") or "")
    if not link_field or not client_host or not _HOST_RE.match(client_host):
        return []

    base_filter: dict = {"CLOSED": "N"}
    category = accompaniment_category_id()
    if category:
        base_filter["CATEGORY_ID"] = category

    select = _deal_list_select()
    order = {"DATE_MODIFY": "DESC"}

    # Prefer Bitrix LIKE filter on the portal-link UF field.
    candidates: list[dict] = []
    try:
        result = client.call(
            "crm.deal.list",
            {
                "filter": {**base_filter, f"%{link_field}": client_host},
                "order": order,
                "select": select,
                "start": 0,
            },
        )
        candidates = _unwrap_deal_list(result)
    except BitrixAPIError:
        candidates = []

    # Fallback: list open deals in the funnel and match in Python
    if not candidates:
        try:
            result = client.call(
                "crm.deal.list",
                {
                    "filter": base_filter,
                    "order": order,
                    "select": select,
                    "start": 0,
                },
            )
            candidates = _unwrap_deal_list(result)
        except BitrixAPIError:
            return []

    matched = [
        d
        for d in candidates
        if portal_link_matches(str(d.get(link_field) or ""), client_host)
    ]
    # Dedupe by ID, keep CRM order (newest first).
    seen: set[str] = set()
    unique: list[dict] = []
    for d in matched:
        did = str(d.get("ID") or d.get("id") or "")
        if not did or did in seen:
            continue
        seen.add(did)
        unique.append(d)
    return unique


def find_open_deal_for_portal(client: BitrixClient, client_portal) -> dict | None:
    """Newest open accompaniment deal for this client portal (or None)."""
    matched = list_open_deals_for_portal(client, client_portal)
    return matched[0] if matched else None


def serialize_deal_candidate(deal: dict, *, bound_client_portal_id: int | None = None) -> dict:
    """Compact payload for the agency deal picker."""
    from portals.deal_hours import read_deal_hours

    deal_id = str(deal.get("ID") or deal.get("id") or "")
    hours = read_deal_hours(deal) if hours_fields_configured() else None
    return {
        "deal_id": deal_id,
        "title": str(deal.get("TITLE") or deal.get("title") or f"Сделка #{deal_id}"),
        "stage_id": str(deal.get("STAGE_ID") or deal.get("stageId") or ""),
        "company_id": str(deal.get("COMPANY_ID") or deal.get("companyId") or ""),
        "paid_hours": float(hours.paid) if hours and hours.paid is not None else None,
        "remaining_hours": (
            float(hours.remaining) if hours and hours.remaining is not None else None
        ),
        "bound_to_other_client": bound_client_portal_id is not None,
        "bound_client_portal_id": bound_client_portal_id,
    }


def sync_deal_hours_meta(client: BitrixClient, deal_id: str, deal: dict | None = None) -> dict:
    """Title/category/stage/hours; seed remaining from paid when remaining is empty."""
    meta = {
        "deal_title": "",
        "category_id": "",
        "stage_id": "",
        "stage_semantic": "",
        "paid_hours": None,
        "remaining_hours": None,
    }
    if deal is None:
        deal = client.get_deal(deal_id)
    meta["deal_title"] = str(deal.get("TITLE") or deal.get("title") or "")
    stage_id, category_id, semantic = read_deal_stage_fields(client, deal)
    meta["category_id"] = category_id or str(
        deal.get("CATEGORY_ID") or deal.get("categoryId") or ""
    )
    meta["stage_id"] = stage_id
    meta["stage_semantic"] = semantic

    if hours_fields_configured():
        hours = read_deal_hours(deal)
        paid = hours.paid
        remaining = hours.remaining
        if remaining is None and paid is not None:
            client.update_deal(deal_id, remaining_update_fields(paid))
            remaining = paid
        meta["paid_hours"] = paid
        meta["remaining_hours"] = remaining
    return meta


def _apply_meta_to_binding(binding, meta: dict) -> list[str]:
    update_fields: list[str] = []
    if meta.get("deal_title"):
        binding.deal_title = meta["deal_title"]
        update_fields.append("deal_title")
    if meta.get("category_id") is not None:
        binding.category_id = meta["category_id"] or binding.category_id
        update_fields.append("category_id")
    if "stage_id" in meta:
        binding.stage_id = meta.get("stage_id") or ""
        update_fields.append("stage_id")
    if "stage_semantic" in meta:
        binding.stage_semantic = meta.get("stage_semantic") or ""
        update_fields.append("stage_semantic")
    if meta.get("paid_hours") is not None:
        binding.paid_hours = meta["paid_hours"]
        update_fields.append("paid_hours")
    if meta.get("remaining_hours") is not None:
        binding.remaining_hours = meta["remaining_hours"]
        update_fields.append("remaining_hours")
    return update_fields


def refresh_binding_from_deal(
    *,
    agency_portal,
    client_portal,
    binding,
    client: BitrixClient | None = None,
    require_portal_link_match: bool = True,
):
    """
    Refresh hours/stage for an existing binding and capture credit when won.
    Does not switch to another deal.

    If require_portal_link_match and the CRM portal-link UF no longer points at
    this client, the binding is deactivated and None is returned — so hours from
    another company's deal cannot stick to the wrong portal.
    """
    from portals.models import PortalLink

    if client is None:
        client = BitrixClient(agency_portal)

    if require_portal_link_match and portal_link_field():
        try:
            deal = client.get_deal(binding.deal_id)
        except BitrixAPIError:
            deal = None
        if not deal or not deal_link_matches_client(deal, client_portal):
            binding.is_active = False
            binding.save(update_fields=["is_active", "updated_at"])
            return None

    meta = sync_deal_hours_meta(client, binding.deal_id)
    update_fields = _apply_meta_to_binding(binding, meta)
    if update_fields:
        update_fields.append("updated_at")
        binding.save(update_fields=list(set(update_fields)))

    link = PortalLink.objects.filter(
        agency_portal=agency_portal,
        client_portal=client_portal,
    ).first()
    if link:
        capture_hours_credit_if_won(
            link=link,
            binding=binding,
            client=client,
            remaining_hours=meta.get("remaining_hours"),
            stage_semantic=meta.get("stage_semantic") or "",
        )
        # Re-read credit-related remaining if capture left CRM unchanged
        binding.refresh_from_db()
    return binding


def refresh_deal_hours_for_portal(client_portal) -> bool:
    """Refresh cached deal hours for a client portal (best-effort, agency CRM)."""
    from portals.models import PortalDealBinding, PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    if not link or not link.agency_portal or not link.agency_portal.access_token:
        return False
    binding = (
        PortalDealBinding.objects.filter(
            client_portal=client_portal,
            agency_portal=link.agency_portal,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if not binding or not binding.deal_id:
        return False
    try:
        resolve_or_refresh_binding(
            agency_portal=link.agency_portal,
            client_portal=client_portal,
        )
    except BitrixAPIError:
        # Transient CRM errors: refresh only if the deal still belongs to this portal.
        refresh_binding_from_deal(
            agency_portal=link.agency_portal,
            client_portal=client_portal,
            binding=binding,
            require_portal_link_match=True,
        )
    return True


def resolve_or_refresh_binding(*, agency_portal, client_portal, company_id: str | None = None):
    """
    Ensure an active PortalDealBinding exists for the client.

    Finds the open accompaniment deal by UF portal-link field → client portal
    domain (exact host). A deal may be active for only one client under the
    agency. If this portal has no matching deal/link, any previous binding is
    cleared so another company's hours cannot stick here.
    `company_id` is ignored (kept for call-site compatibility).
    """
    del company_id  # no longer used
    from portals.models import PortalDealBinding, PortalLink

    link = (
        PortalLink.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
        )
        .first()
    )
    if not link:
        return None

    if not portal_link_field():
        raise BitrixAPIError("Не задано BITRIX_DEAL_PORTAL_LINK_FIELD")

    if not agency_portal.access_token:
        raise BitrixAPIError("Agency portal has no Bitrix token")

    client = BitrixClient(agency_portal)
    previous = (
        PortalDealBinding.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )

    deal = find_open_deal_for_portal(client, client_portal)
    if not deal:
        # No open deal for THIS portal. Keep previous only if its CRM link still
        # points here (e.g. won deal). Otherwise clear — never inherit strangers' hours.
        if previous and previous.deal_id:
            kept = refresh_binding_from_deal(
                agency_portal=agency_portal,
                client_portal=client_portal,
                binding=previous,
                client=client,
                require_portal_link_match=True,
            )
            if kept is not None:
                return kept
        raise BitrixAPIError(deal_not_found_for_portal_message(client_portal))

    deal_id = str(deal.get("ID") or deal.get("id") or "")
    if not deal_id:
        raise BitrixAPIError("Bitrix вернул сделку без ID")

    # Before switching away from a won deal, capture any leftover hours
    if previous and previous.deal_id and str(previous.deal_id) != deal_id:
        try:
            refresh_binding_from_deal(
                agency_portal=agency_portal,
                client_portal=client_portal,
                binding=previous,
                client=client,
                require_portal_link_match=False,
            )
            link.refresh_from_db()
        except BitrixAPIError:
            pass

    meta = sync_deal_hours_meta(client, deal_id, deal)

    # New open deal (or first bind): roll pending credit into remaining
    # Roll pending credit (idempotent) and repair a double-apply if needed
    applied = apply_hours_credit_to_new_deal(
        link=link,
        client=client,
        new_deal_id=deal_id,
        current_remaining=meta.get("remaining_hours"),
    )
    if applied is not None:
        meta["remaining_hours"] = applied
        link.refresh_from_db()

    # Cache company + Bitrix workgroup id from company UF
    cache_company_and_group_on_link(client, link, deal)

    # This deal belongs to this client only — drop other portals' active claim.
    deactivate_bindings_for_deal(
        agency_portal=agency_portal,
        deal_id=deal_id,
        except_client_portal=client_portal,
    )
    PortalDealBinding.objects.filter(
        agency_portal=agency_portal,
        client_portal=client_portal,
        is_active=True,
    ).exclude(deal_id=deal_id).update(is_active=False)

    binding, _ = PortalDealBinding.objects.update_or_create(
        agency_portal=agency_portal,
        client_portal=client_portal,
        deal_id=deal_id,
        defaults={
            "deal_title": meta["deal_title"],
            "category_id": meta["category_id"],
            "stage_id": meta.get("stage_id") or "",
            "stage_semantic": meta.get("stage_semantic") or "",
            "paid_hours": meta["paid_hours"],
            "remaining_hours": meta["remaining_hours"],
            "is_active": True,
        },
    )
    if not binding.is_active:
        binding.is_active = True
        binding.deal_title = meta["deal_title"] or binding.deal_title
        binding.category_id = meta["category_id"] or binding.category_id
        binding.stage_id = meta.get("stage_id") or binding.stage_id
        binding.stage_semantic = meta.get("stage_semantic") or binding.stage_semantic
        binding.paid_hours = meta["paid_hours"]
        binding.remaining_hours = meta["remaining_hours"]
        binding.save(
            update_fields=[
                "is_active",
                "deal_title",
                "category_id",
                "stage_id",
                "stage_semantic",
                "paid_hours",
                "remaining_hours",
                "updated_at",
            ]
        )
    else:
        # Ensure stage fields are persisted even when update_or_create hit defaults path
        fields = _apply_meta_to_binding(binding, meta)
        if fields:
            fields.append("updated_at")
            binding.save(update_fields=list(set(fields)))

    return binding


def cache_company_and_group_on_link(client: BitrixClient, link, deal: dict) -> tuple[str, str]:
    """
    From deal.COMPANY_ID → company UF project id → PortalLink cache.
    Returns (company_id, group_id).
    """
    company_id = str(deal.get("COMPANY_ID") or deal.get("companyId") or "").strip()
    group_id = ""
    field = company_project_id_field()
    if company_id and field:
        try:
            company = client.get_company(company_id)
            raw = company.get(field)
            if raw is not None and raw != "":
                # UF may be list for some field types
                if isinstance(raw, (list, tuple)) and raw:
                    raw = raw[0]
                group_id = str(raw).strip()
        except BitrixAPIError:
            group_id = ""

    update_fields = []
    if company_id and link.bitrix_company_id != company_id:
        link.bitrix_company_id = company_id
        update_fields.append("bitrix_company_id")
    if group_id and link.bitrix_group_id != group_id:
        link.bitrix_group_id = group_id
        update_fields.append("bitrix_group_id")
    if update_fields:
        link.save(update_fields=update_fields)
    return company_id, group_id or link.bitrix_group_id


def resolve_bitrix_group_id(*, agency_portal, client_portal, force_refresh: bool = False) -> str:
    """
    Return Bitrix workgroup id for this client (cached on PortalLink).
    Raises BitrixAPIError when the company has no project id configured.
    """
    from portals.models import PortalLink

    link = (
        PortalLink.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
        )
        .first()
    )
    if not link:
        raise BitrixAPIError("Клиент не привязан к агентству")

    if link.bitrix_group_id and not force_refresh:
        return link.bitrix_group_id

    if not agency_portal.access_token:
        raise BitrixAPIError("Agency portal has no Bitrix token")
    if not portal_link_field():
        raise BitrixAPIError("Не задано BITRIX_DEAL_PORTAL_LINK_FIELD")
    if not company_project_id_field():
        raise BitrixAPIError("Не задано BITRIX_COMPANY_PROJECT_ID_FIELD")

    client = BitrixClient(agency_portal)
    deal = find_open_deal_for_portal(client, client_portal)
    if not deal:
        raise BitrixAPIError(deal_not_found_for_portal_message(client_portal))

    _, group_id = cache_company_and_group_on_link(client, link, deal)
    link.refresh_from_db()
    group_id = group_id or link.bitrix_group_id
    if not group_id:
        raise BitrixAPIError(
            "У компании в CRM ещё нет проекта сопровождения. "
            "Дождитесь нужной стадии воронки — робот создаст проект автоматически."
        )
    return group_id


def get_active_binding(*, agency_portal, client_portal):
    from portals.models import PortalDealBinding

    return (
        PortalDealBinding.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
