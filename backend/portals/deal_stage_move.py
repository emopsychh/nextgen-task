"""Move accompaniment CRM deal stages from work-report lifecycle events.

Product rules:
  • Report sent to client  → stage «Согласование отчёта»
  • Client accepts         → stage «Подписание акта»
  • Client contacts manager (former dispute) → leave stage as-is
  • Won/lost deals (SEMANTICS S/F) are never moved
  • Bitrix failures are logged only — never block the report action
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db import transaction

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours_credit import read_deal_stage_fields
from portals.deal_resolve import accompaniment_category_id

logger = logging.getLogger(__name__)

STAGE_REPORT_REVIEW = "report_review"
STAGE_ACT_SIGNING = "act_signing"

_STAGE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    STAGE_REPORT_REVIEW: (
        "Согласование отчёта",
        "Согласование отчета",
    ),
    STAGE_ACT_SIGNING: ("Подписание акта",),
}


def _norm_name(value: str) -> str:
    text = (value or "").strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def configured_stage_id(stage_key: str) -> str:
    if stage_key == STAGE_REPORT_REVIEW:
        return (getattr(settings, "BITRIX_DEAL_STAGE_REPORT_REVIEW", "") or "").strip()
    if stage_key == STAGE_ACT_SIGNING:
        return (getattr(settings, "BITRIX_DEAL_STAGE_ACT_SIGNING", "") or "").strip()
    return ""


def list_category_stages(client: BitrixClient, category_id: str) -> list[dict]:
    from portals.deal_hours_credit import deal_stage_entity_id

    entity = deal_stage_entity_id(category_id)
    try:
        result = client.call("crm.status.list", {"filter": {"ENTITY_ID": entity}})
    except BitrixAPIError as exc:
        logger.info("crm.status.list failed entity=%s: %s", entity, exc)
        return []

    rows = result if isinstance(result, list) else []
    if isinstance(result, dict):
        rows = result.get("statuses") or result.get("result") or result.get("items") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def resolve_stage_id(client: BitrixClient, category_id: str, stage_key: str) -> str:
    """Prefer env STAGE_ID; otherwise match by Russian stage name in the funnel."""
    configured = configured_stage_id(stage_key)
    if configured:
        return configured

    aliases = _STAGE_NAME_ALIASES.get(stage_key) or ()
    wanted = {_norm_name(a) for a in aliases}
    if not wanted:
        return ""

    for row in list_category_stages(client, category_id):
        name = str(row.get("NAME") or row.get("name") or "").strip()
        if _norm_name(name) not in wanted:
            continue
        sid = str(row.get("STATUS_ID") or row.get("statusId") or "").strip()
        if sid:
            return sid
    return ""


def _active_binding_for_portal(portal_id: int):
    from portals.models import PortalDealBinding

    return (
        PortalDealBinding.objects.filter(client_portal_id=portal_id, is_active=True)
        .select_related("agency_portal", "client_portal")
        .order_by("-updated_at")
        .first()
    )


def move_client_deal_stage(portal_id: int, stage_key: str) -> dict:
    """
    Move the client's accompaniment deal to the target stage.
    Returns a small result dict for logs/tests; never raises to callers of schedule_*.
    """
    binding = _active_binding_for_portal(portal_id)
    if not binding:
        return {"ok": False, "reason": "no_binding"}

    agency = binding.agency_portal
    if not agency or not agency.access_token:
        return {"ok": False, "reason": "no_agency_token"}

    deal_id = str(binding.deal_id or "").strip()
    if not deal_id:
        return {"ok": False, "reason": "no_deal"}

    client = BitrixClient(agency)
    category_id = (binding.category_id or "").strip() or accompaniment_category_id()

    try:
        deal = client.get_deal(deal_id)
    except BitrixAPIError as exc:
        logger.info(
            "deal stage: get_deal failed portal=%s deal=%s: %s",
            portal_id,
            deal_id,
            exc,
        )
        return {"ok": False, "reason": "get_deal_failed", "error": str(exc)}

    current_stage, deal_category, semantic = read_deal_stage_fields(client, deal)
    if deal_category:
        category_id = deal_category
    if semantic in ("S", "F"):
        logger.info(
            "deal stage: skip closed deal portal=%s deal=%s semantic=%s",
            portal_id,
            deal_id,
            semantic,
        )
        return {"ok": False, "reason": "deal_closed", "semantic": semantic}

    target = resolve_stage_id(client, category_id, stage_key)
    if not target:
        logger.info(
            "deal stage: target unresolved portal=%s key=%s category=%s",
            portal_id,
            stage_key,
            category_id,
        )
        return {"ok": False, "reason": "stage_unresolved", "stage_key": stage_key}

    if current_stage == target:
        return {
            "ok": True,
            "skipped": "already_on_stage",
            "deal_id": deal_id,
            "stage_id": target,
        }

    try:
        client.update_deal(deal_id, {"STAGE_ID": target})
    except BitrixAPIError as exc:
        logger.info(
            "deal stage: update failed portal=%s deal=%s → %s: %s",
            portal_id,
            deal_id,
            target,
            exc,
        )
        return {"ok": False, "reason": "update_failed", "error": str(exc)}

    binding.stage_id = target
    if category_id and binding.category_id != category_id:
        binding.category_id = category_id
        binding.save(update_fields=["stage_id", "category_id", "updated_at"])
    else:
        binding.save(update_fields=["stage_id", "updated_at"])

    logger.info(
        "deal stage: moved portal=%s deal=%s %s → %s (%s)",
        portal_id,
        deal_id,
        current_stage or "?",
        target,
        stage_key,
    )
    return {
        "ok": True,
        "deal_id": deal_id,
        "from_stage": current_stage,
        "stage_id": target,
        "stage_key": stage_key,
    }


def schedule_deal_stage_move(portal_id: int | None, stage_key: str) -> None:
    """Fire after the DB transaction commits; swallow all errors."""
    if not portal_id:
        return

    def _run() -> None:
        try:
            move_client_deal_stage(int(portal_id), stage_key)
        except Exception:
            logger.exception(
                "deal stage move crashed portal=%s key=%s", portal_id, stage_key
            )

    try:
        transaction.on_commit(_run)
    except Exception:
        # Outside atomic block — run immediately.
        _run()
