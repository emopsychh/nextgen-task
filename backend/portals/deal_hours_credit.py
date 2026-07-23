"""Carry leftover accompaniment hours after a won deal into the next deal."""

from __future__ import annotations

import logging
from decimal import Decimal

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours import (
    hours_fields_configured,
    parse_hours,
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

    # Already captured from this deal
    if (
        str(link.hours_credit_source_deal_id or "") == deal_id
        and _credit_amount(link) > 0
    ):
        return False

    existing = _credit_amount(link)
    # If credit is from another deal that was never rolled — keep stacking
    new_credit = existing + rem
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
        "hours credit captured client=%s deal=%s +%s → total=%s",
        link.client_portal_id,
        deal_id,
        rem,
        new_credit,
    )
    return True


def apply_hours_credit_to_new_deal(
    *,
    link,
    client: BitrixClient,
    new_deal_id: str,
    current_remaining,
) -> Decimal | None:
    """
    Add pending credit to the new deal's remaining field and clear the credit.
    Returns the new remaining value written to Bitrix, or None if nothing applied.
    """
    if not hours_fields_configured():
        return None

    credit = _credit_amount(link)
    if credit <= 0:
        return None

    new_deal_id = str(new_deal_id or "").strip()
    if not new_deal_id:
        return None

    # Don't apply credit back onto the same deal it came from
    source_id = str(link.hours_credit_source_deal_id or "").strip()
    if source_id and source_id == new_deal_id:
        return None

    base = parse_hours(current_remaining)
    if base is None:
        base = Decimal("0.00")
    new_remaining = (base + credit).quantize(Decimal("0.01"))

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

    # Best-effort: zero remaining on the source (won) deal so CRM matches
    if source_id:
        try:
            client.update_deal(source_id, remaining_update_fields(Decimal("0.00")))
        except BitrixAPIError as exc:
            logger.info("zero source deal remaining failed deal=%s: %s", source_id, exc)
        try:
            client.add_deal_timeline_comment(
                new_deal_id,
                f"Перенесено {credit} ч остатка со сделки #{source_id} "
                f"«{link.hours_credit_source_title or source_id}».",
            )
        except BitrixAPIError:
            pass

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
    logger.info(
        "hours credit applied client=%s → deal=%s remaining=%s",
        link.client_portal_id,
        new_deal_id,
        new_remaining,
    )
    return new_remaining
