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

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", re.I)


def accompaniment_category_id() -> str:
    return (settings.BITRIX_ACCOMPANIMENT_CATEGORY_ID or "").strip()


def portal_link_field() -> str:
    return (settings.BITRIX_DEAL_PORTAL_LINK_FIELD or "").strip()


def company_project_id_field() -> str:
    return (settings.BITRIX_COMPANY_PROJECT_ID_FIELD or "").strip()


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
    if not client_host:
        return False
    field_host = normalize_portal_host(field_value)
    if not field_host:
        # Plain text without URL shape — compare as host-ish fragment
        raw = (field_value or "").strip().lower()
        return client_host in raw or raw in client_host
    return field_host == client_host or field_host.endswith("." + client_host) or client_host.endswith(
        "." + field_host
    )


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


def find_open_deal_for_portal(client: BitrixClient, client_portal) -> dict | None:
    """
    Find the newest open accompaniment deal whose portal-link field
    points at this client Bitrix portal.
    """
    link_field = portal_link_field()
    client_host = normalize_portal_host(getattr(client_portal, "domain", "") or "")
    if not link_field or not client_host or not _HOST_RE.match(client_host):
        return None

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

    matched = [
        d
        for d in candidates
        if portal_link_matches(str(d.get(link_field) or ""), client_host)
    ]
    if not matched:
        return None
    return matched[0]


def sync_deal_hours_meta(client: BitrixClient, deal_id: str, deal: dict | None = None) -> dict:
    """Title/category/hours; seed remaining from paid when remaining is empty."""
    meta = {
        "deal_title": "",
        "category_id": "",
        "paid_hours": None,
        "remaining_hours": None,
    }
    if deal is None:
        deal = client.get_deal(deal_id)
    meta["deal_title"] = str(deal.get("TITLE") or deal.get("title") or "")
    meta["category_id"] = str(deal.get("CATEGORY_ID") or deal.get("categoryId") or "")

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


def resolve_or_refresh_binding(*, agency_portal, client_portal, company_id: str | None = None):
    """
    Ensure an active PortalDealBinding exists for the client.
    Finds the open accompaniment deal by UF portal-link field → client portal domain.
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
    deal = find_open_deal_for_portal(client, client_portal)
    if not deal:
        host = normalize_portal_host(client_portal.domain or "")
        raise BitrixAPIError(
            f"Не найдена открытая сделка с ссылкой на портал «{host}»"
            + (f" (воронка {accompaniment_category_id()})" if accompaniment_category_id() else "")
            + f" в поле {portal_link_field()}"
        )

    deal_id = str(deal.get("ID") or deal.get("id") or "")
    if not deal_id:
        raise BitrixAPIError("Bitrix вернул сделку без ID")

    meta = sync_deal_hours_meta(client, deal_id, deal)

    # Cache company + Bitrix workgroup id from company UF
    cache_company_and_group_on_link(client, link, deal)

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
            "paid_hours": meta["paid_hours"],
            "remaining_hours": meta["remaining_hours"],
            "is_active": True,
        },
    )
    if not binding.is_active:
        binding.is_active = True
        binding.deal_title = meta["deal_title"] or binding.deal_title
        binding.category_id = meta["category_id"] or binding.category_id
        binding.paid_hours = meta["paid_hours"]
        binding.remaining_hours = meta["remaining_hours"]
        binding.save(
            update_fields=[
                "is_active",
                "deal_title",
                "category_id",
                "paid_hours",
                "remaining_hours",
                "updated_at",
            ]
        )
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
        host = normalize_portal_host(client_portal.domain or "")
        raise BitrixAPIError(
            f"Не найдена открытая сделка с ссылкой на портал «{host}»"
        )

    _, group_id = cache_company_and_group_on_link(client, link, deal)
    link.refresh_from_db()
    group_id = group_id or link.bitrix_group_id
    if not group_id:
        raise BitrixAPIError(
            "У компании в CRM нет ID проекта "
            f"(поле {company_project_id_field()}). "
            "Дождитесь стадии 2 воронки — робот создаст проект."
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
