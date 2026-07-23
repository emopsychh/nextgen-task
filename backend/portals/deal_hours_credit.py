"""Carry leftover accompaniment hours after a won deal into the next deal."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours import (
    hours_fields_configured,
    parse_hours,
    read_deal_hours,
    remaining_update_fields,
)

logger = logging.getLogger(__name__)

# (ENTITY_ID, STATUS_ID) → "S" | "F" | ""
_STAGE_SEMANTIC_CACHE: dict[tuple[str, str], str] = {}


def deal_stage_entity_id(category_id: str | None) -> str:
    cat = str(category_id or "").strip()
    if not cat or cat == "0":
        return "DEAL_STAGE"
    return f"DEAL_STAGE_{cat}"


def stage_semantic_for(
    client: BitrixClient, *, category_id: str, stage_id: str
) -> str:
    """Return Bitrix stage SEMANTICS: S (success), F (failure), or empty."""
    stage_id = str(stage_id or "").strip()
    if not stage_id:
        return ""
    entity = deal_stage_entity_id(category_id)
    cache_key = (entity, stage_id)
    if cache_key in _STAGE_SEMANTIC_CACHE:
        return _STAGE_SEMANTIC_CACHE[cache_key]

    try:
        result = client.call("crm.status.list", {"filter": {"ENTITY_ID": entity}})
    except BitrixAPIError as exc:
        logger.info("crm.status.list failed entity=%s: %s", entity, exc)
        return ""

    rows = result if isinstance(result, list) else []
    if isinstance(result, dict):
        rows = result.get("statuses") or result.get("result") or result.get("items") or []
    if not isinstance(rows, list):
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("STATUS_ID") or row.get("statusId") or "").strip()
        if not sid:
            continue
        raw = row.get("SEMANTICS")
        if raw in (None, ""):
            extra = row.get("EXTRA") or row.get("extra") or {}
            if isinstance(extra, dict):
                raw = extra.get("SEMANTICS") or extra.get("semantics")
        sem = str(raw or "").strip().upper()[:1]
        if sem not in ("S", "F"):
            sem = ""
        _STAGE_SEMANTIC_CACHE[(entity, sid)] = sem

    return _STAGE_SEMANTIC_CACHE.get(cache_key, "")


def read_deal_stage_fields(client: BitrixClient, deal: dict) -> tuple[str, str, str]:
    """Return (stage_id, category_id, stage_semantic)."""
    stage_id = str(
        deal.get("STAGE_ID") or deal.get("stageId") or deal.get("STAGE") or ""
    ).strip()
    category_id = str(
        deal.get("CATEGORY_ID") or deal.get("categoryId") or ""
    ).strip()
    semantic = stage_semantic_for(client, category_id=category_id, stage_id=stage_id)
    return stage_id, category_id, semantic


def _credit_amount(link) -> Decimal:
    raw = getattr(link, "hours_credit", None)
    parsed = parse_hours(raw)
    if parsed is None:
        return Decimal("0.00")
    return max(Decimal("0.00"), parsed)


def _last_amount(link) -> Decimal:
    parsed = parse_hours(getattr(link, "hours_credit_last_amount", None))
    if parsed is None:
        return Decimal("0.00")
    return max(Decimal("0.00"), parsed)


def capture_hours_credit_if_won(
    *,
    link,
    binding,
    client: BitrixClient,
    remaining_hours,
    stage_semantic: str,
) -> bool:
    """
    If deal is on a SUCCESS stage and still has remaining hours, park them
    on the portal link as credit (once per source deal). Does not change paid.
    """
    del client  # reserved for future Bitrix writes on capture
    if not hours_fields_configured():
        return False
    if str(stage_semantic or "").upper() != "S":
        return False

    rem = parse_hours(remaining_hours)
    if rem is None or rem <= 0:
        return False

    deal_id = str(binding.deal_id or "").strip()
    if not deal_id:
        return False

    from portals.models import PortalLink

    with transaction.atomic():
        link = PortalLink.objects.select_for_update().get(pk=link.pk)

        # Already rolled this source into a later deal — never re-capture
        if str(link.hours_credit_last_source_deal_id or "") == deal_id:
            return False

        # Pending credit already parked from this deal
        if (
            str(link.hours_credit_source_deal_id or "") == deal_id
            and _credit_amount(link) > 0
        ):
            return False

        existing = _credit_amount(link)
        # Replace empty/pending only; don't silently stack the same package twice
        if existing > 0 and str(link.hours_credit_source_deal_id or "") not in ("", deal_id):
            new_credit = existing + rem
        else:
            new_credit = rem

        link.hours_credit = new_credit
        link.hours_credit_source_deal_id = deal_id
        link.hours_credit_source_title = (
            binding.deal_title or f"Сделка #{deal_id}"
        )[:500]
        link.save(
            update_fields=[
                "hours_credit",
                "hours_credit_source_deal_id",
                "hours_credit_source_title",
            ]
        )
        logger.info(
            "hours credit captured client=%s deal=%s → %s",
            link.client_portal_id,
            deal_id,
            new_credit,
        )
        return True


def repair_double_applied_remaining(
    *,
    link,
    client: BitrixClient,
    deal_id: str,
    paid,
    remaining,
) -> Decimal | None:
    """
    If credit was applied twice (remaining ≈ paid + 2×credit), fix to paid+credit.
    """
    deal_id = str(deal_id or "").strip()
    if not deal_id:
        return None

    paid_h = parse_hours(paid)
    rem_h = parse_hours(remaining)
    if paid_h is None or rem_h is None or paid_h < 0 or rem_h <= paid_h:
        return None

    last = _last_amount(link)

    # Infer credit from a won sibling binding still caching pre-zero remaining
    if last <= 0:
        from portals.models import PortalDealBinding

        won = (
            PortalDealBinding.objects.filter(
                agency_portal_id=link.agency_portal_id,
                client_portal_id=link.client_portal_id,
                stage_semantic__iexact="S",
            )
            .exclude(deal_id=deal_id)
            .order_by("-updated_at")
            .first()
        )
        if won:
            guess = parse_hours(won.remaining_hours)
            if guess and guess > 0:
                last = guess

    if last <= 0:
        # Bootstrap when remaining looks like paid + 2×(~paid) transfers
        if rem_h + Decimal("0.05") >= (paid_h * Decimal("3")):
            last = ((rem_h - paid_h) / 2).quantize(Decimal("0.01"))
        else:
            return None

    expected = (paid_h + last).quantize(Decimal("0.01"))
    doubled = (paid_h + last + last).quantize(Decimal("0.01"))
    if rem_h + Decimal("0.15") < doubled:
        return None
    if rem_h <= expected + Decimal("0.05"):
        return None

    try:
        client.update_deal(deal_id, remaining_update_fields(expected))
    except BitrixAPIError as exc:
        logger.info("repair double credit failed deal=%s: %s", deal_id, exc)
        return None

    link.hours_credit_applied_to_deal_id = deal_id
    link.hours_credit_last_amount = last
    link.hours_credit = Decimal("0.00")
    link.hours_credit_source_deal_id = ""
    link.hours_credit_source_title = ""
    link.save(
        update_fields=[
            "hours_credit",
            "hours_credit_source_deal_id",
            "hours_credit_source_title",
            "hours_credit_applied_to_deal_id",
            "hours_credit_last_amount",
        ]
    )

    logger.warning(
        "repaired double-applied hours deal=%s %s → %s (paid=%s credit=%s)",
        deal_id,
        rem_h,
        expected,
        paid_h,
        last,
    )
    return expected


def apply_hours_credit_to_new_deal(
    *,
    link,
    client: BitrixClient,
    new_deal_id: str,
    current_remaining=None,
) -> Decimal | None:
    """
    Add pending credit to the new deal's remaining field and clear the credit.
    Idempotent: will not add twice to the same deal. May repair a double-apply.
    """
    if not hours_fields_configured():
        return None

    new_deal_id = str(new_deal_id or "").strip()
    if not new_deal_id:
        return None

    from portals.models import PortalLink

    with transaction.atomic():
        link = PortalLink.objects.select_for_update().get(pk=link.pk)

        # Fresh Bitrix snapshot (ignore possibly stale current_remaining)
        try:
            deal = client.get_deal(new_deal_id)
        except BitrixAPIError as exc:
            logger.info("apply credit get_deal failed %s: %s", new_deal_id, exc)
            return None
        hours = read_deal_hours(deal)
        paid = hours.paid
        rem = hours.remaining
        if rem is None:
            rem = paid if paid is not None else Decimal("0.00")

        # Always attempt repair of a known double-apply pattern
        fixed = repair_double_applied_remaining(
            link=link,
            client=client,
            deal_id=new_deal_id,
            paid=paid,
            remaining=rem,
        )
        if fixed is not None:
            link.refresh_from_db()
            return fixed

        # Already applied to this deal
        if str(link.hours_credit_applied_to_deal_id or "") == new_deal_id:
            if _credit_amount(link) > 0:
                link.hours_credit = Decimal("0.00")
                link.hours_credit_source_deal_id = ""
                link.hours_credit_source_title = ""
                link.save(
                    update_fields=[
                        "hours_credit",
                        "hours_credit_source_deal_id",
                        "hours_credit_source_title",
                    ]
                )
            return rem

        credit = _credit_amount(link)
        if credit <= 0:
            return None

        source_id = str(link.hours_credit_source_deal_id or "").strip()
        if source_id and source_id == new_deal_id:
            return None

        # If CRM remaining already looks like paid+credit, just mark applied
        if paid is not None:
            expected = (paid + credit).quantize(Decimal("0.01"))
            if rem + Decimal("0.05") >= expected:
                link.hours_credit_applied_to_deal_id = new_deal_id
                link.hours_credit_last_amount = credit
                if source_id:
                    link.hours_credit_last_source_deal_id = source_id
                link.hours_credit = Decimal("0.00")
                link.hours_credit_source_deal_id = ""
                link.hours_credit_source_title = ""
                link.save(
                    update_fields=[
                        "hours_credit",
                        "hours_credit_source_deal_id",
                        "hours_credit_source_title",
                        "hours_credit_applied_to_deal_id",
                        "hours_credit_last_amount",
                        "hours_credit_last_source_deal_id",
                    ]
                )
                return rem

        new_remaining = (rem + credit).quantize(Decimal("0.01"))
        try:
            client.update_deal(new_deal_id, remaining_update_fields(new_remaining))
        except BitrixAPIError as exc:
            logger.info(
                "apply hours credit failed deal=%s credit=%s: %s",
                new_deal_id,
                credit,
                exc,
            )
            return None

        if source_id:
            try:
                client.update_deal(source_id, remaining_update_fields(Decimal("0.00")))
            except BitrixAPIError as exc:
                logger.info(
                    "zero source deal remaining failed deal=%s: %s", source_id, exc
                )
            try:
                client.add_deal_timeline_comment(
                    new_deal_id,
                    f"Перенесено {credit} ч остатка со сделки #{source_id} "
                    f"«{link.hours_credit_source_title or source_id}».",
                )
            except BitrixAPIError:
                pass

        link.hours_credit_applied_to_deal_id = new_deal_id
        link.hours_credit_last_amount = credit
        link.hours_credit_last_source_deal_id = source_id
        link.hours_credit = Decimal("0.00")
        link.hours_credit_source_deal_id = ""
        link.hours_credit_source_title = ""
        link.save(
            update_fields=[
                "hours_credit",
                "hours_credit_source_deal_id",
                "hours_credit_source_title",
                "hours_credit_applied_to_deal_id",
                "hours_credit_last_amount",
                "hours_credit_last_source_deal_id",
            ]
        )
        logger.info(
            "hours credit applied client=%s → deal=%s remaining=%s (was %s + %s)",
            link.client_portal_id,
            new_deal_id,
            new_remaining,
            rem,
            credit,
        )
        return new_remaining
