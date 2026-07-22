"""Bidirectional task status sync between Nextgen and Bitrix Tasks."""

from __future__ import annotations

import logging

from django.conf import settings

from portals.bitrix import (
    BITRIX_TO_LOCAL,
    BitrixAPIError,
    BitrixClient,
    bitrix_status_code,
)

logger = logging.getLogger(__name__)


def event_handler_url() -> str:
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/api/bitrix/events/"


def ensure_task_event_bindings(portal) -> bool:
    """Subscribe portal app to OnTaskUpdate (idempotent best-effort)."""
    if not portal or not portal.access_token:
        return False
    handler = event_handler_url()
    client = BitrixClient(portal)
    try:
        existing = client.call("event.get") or []
        if isinstance(existing, dict):
            existing = existing.get("result") or existing.get("events") or []
        if not isinstance(existing, list):
            existing = []
        for row in existing:
            if not isinstance(row, dict):
                continue
            ev = str(row.get("event") or row.get("EVENT") or "").upper()
            h = str(row.get("handler") or row.get("HANDLER") or "")
            if ev in ("ONTASKUPDATE", "ON_TASK_UPDATE") and h.rstrip("/") == handler.rstrip("/"):
                return True
        client.call("event.bind", {"event": "OnTaskUpdate", "handler": handler})
        return True
    except BitrixAPIError as exc:
        # Already bound / no rights — not fatal
        logger.info("event.bind OnTaskUpdate for %s: %s", portal.domain, exc)
        return False
    except Exception as exc:
        logger.warning("event.bind failed for %s: %s", portal.domain, exc)
        return False


def local_status_from_bitrix_task(task_data: dict) -> str | None:
    code = bitrix_status_code(task_data)
    if code is None:
        return None
    return BITRIX_TO_LOCAL.get(code)


def _agency_portal_for_client(client_portal):
    from portals.models import PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    return link.agency_portal if link else None


def resolve_bitrix_task_source(task) -> tuple | tuple[None, None]:
    """Prefer client Bitrix task; fall back to agency copy."""
    client_portal = task.project.portal
    if task.bitrix_task_id and client_portal.access_token:
        return client_portal, str(task.bitrix_task_id)
    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        return agency, str(task.agency_bitrix_task_id)
    return None, None


def find_local_task_for_bitrix(*, portal, bitrix_task_id: str):
    """Match local task by client or agency Bitrix id for this portal."""
    from board.models import Task

    bitrix_task_id = str(bitrix_task_id)
    qs = Task.objects.select_related("project", "project__portal")

    # Client portal owns project.portal
    client_hit = qs.filter(
        bitrix_task_id=bitrix_task_id,
        project__portal=portal,
    ).first()
    if client_hit:
        return client_hit

    # Agency copy: agency_bitrix_task_id on a client project linked to this agency
    from portals.models import PortalLink

    client_ids = PortalLink.objects.filter(agency_portal=portal).values_list(
        "client_portal_id", flat=True
    )
    return qs.filter(
        agency_bitrix_task_id=bitrix_task_id,
        project__portal_id__in=client_ids,
    ).first()


def apply_inbound_status(task, new_status: str, *, stop_timers: bool = True) -> bool:
    """
    Apply status that originated in Bitrix. Does not push back to Bitrix.
    Returns True if the local row changed.
    """
    from board.models import Task
    from board.timeutils import stop_time_entry

    if new_status not in (
        Task.Status.TODO,
        Task.Status.IN_PROGRESS,
        Task.Status.DONE,
    ):
        return False
    if task.status == new_status:
        return False

    old = task.status
    task.status = new_status
    # Keep sync_status as synced — change came from Bitrix
    task.sync_status = Task.SyncStatus.SYNCED
    task.sync_error = ""
    task.save(update_fields=["status", "sync_status", "sync_error", "updated_at"])

    if stop_timers and old == Task.Status.IN_PROGRESS and new_status in (
        Task.Status.TODO,
        Task.Status.DONE,
    ):
        for running in task.time_entries.filter(ended_at__isnull=True):
            stop_time_entry(running)

    return True


def pull_task_status_from_bitrix(task) -> bool:
    """Fetch Bitrix status and update local task if different."""
    portal, bitrix_id = resolve_bitrix_task_source(task)
    if not portal or not bitrix_id:
        return False
    try:
        data = BitrixClient(portal).get_task(bitrix_id)
    except BitrixAPIError as exc:
        logger.info("pull status task=%s: %s", task.id, exc)
        return False
    local = local_status_from_bitrix_task(data)
    if not local:
        return False
    return apply_inbound_status(task, local)


def handle_bitrix_task_update(*, portal, bitrix_task_id: str) -> dict:
    """Process OnTaskUpdate: refresh local status from Bitrix."""
    task = find_local_task_for_bitrix(portal=portal, bitrix_task_id=str(bitrix_task_id))
    if not task:
        return {"ok": False, "reason": "unknown_task"}
    try:
        data = BitrixClient(portal).get_task(bitrix_task_id)
    except BitrixAPIError as exc:
        return {"ok": False, "reason": str(exc)}
    local = local_status_from_bitrix_task(data)
    if not local:
        return {"ok": False, "reason": "bad_status"}
    changed = apply_inbound_status(task, local)
    return {"ok": True, "task_id": task.id, "status": local, "changed": changed}
