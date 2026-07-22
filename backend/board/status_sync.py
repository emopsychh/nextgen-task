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
    """Subscribe portal app to task status + comment events (idempotent best-effort)."""
    if not portal or not portal.access_token:
        return False
    handler = event_handler_url()
    client = BitrixClient(portal)
    wanted = {"ONTASKUPDATE", "ONTASKCOMMENTADD", "ONTASKADD"}
    try:
        existing = client.call("event.get") or []
        if isinstance(existing, dict):
            existing = existing.get("result") or existing.get("events") or []
        if not isinstance(existing, list):
            existing = []
        bound: set[str] = set()
        for row in existing:
            if not isinstance(row, dict):
                continue
            ev = str(row.get("event") or row.get("EVENT") or "").upper().replace("_", "")
            h = str(row.get("handler") or row.get("HANDLER") or "")
            if h.rstrip("/") == handler.rstrip("/") and ev in wanted:
                bound.add(ev)
        ok = True
        for event_name, key in (
            ("OnTaskUpdate", "ONTASKUPDATE"),
            ("OnTaskCommentAdd", "ONTASKCOMMENTADD"),
            ("OnTaskAdd", "ONTASKADD"),
        ):
            if key in bound:
                continue
            try:
                client.call("event.bind", {"event": event_name, "handler": handler})
            except BitrixAPIError as exc:
                logger.info("event.bind %s for %s: %s", event_name, portal.domain, exc)
                ok = False
        return ok or bool(bound)
    except BitrixAPIError as exc:
        logger.info("event.bind for %s: %s", portal.domain, exc)
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
    """
    Prefer agency Bitrix task (company workgroup / subtasks) — that is where
    managers edit deadlines. Fall back to the client portal copy.
    """
    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        return agency, str(task.agency_bitrix_task_id)
    if task.bitrix_task_id and client_portal.access_token:
        return client_portal, str(task.bitrix_task_id)
    return None, None


def resolve_all_bitrix_task_sources(task) -> list[tuple]:
    """Agency first, then client — for pulls that should reconcile both copies."""
    sources: list[tuple] = []
    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        sources.append((agency, str(task.agency_bitrix_task_id)))
    if task.bitrix_task_id and client_portal.access_token:
        sources.append((client_portal, str(task.bitrix_task_id)))
    return sources


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

    client_ids = list(
        PortalLink.objects.filter(agency_portal=portal).values_list(
            "client_portal_id", flat=True
        )
    )
    agency_hit = qs.filter(
        agency_bitrix_task_id=bitrix_task_id,
        project__portal_id__in=client_ids,
    ).first()
    if agency_hit:
        return agency_hit

    # Last resort: unique match by either id (covers mis-linked portals)
    return (
        qs.filter(agency_bitrix_task_id=bitrix_task_id).first()
        or qs.filter(bitrix_task_id=bitrix_task_id).first()
    )


def format_bitrix_deadline(due) -> str:
    """
    End of calendar day in portal-local time (no UTC offset).
    Using +00:00 made UTC+7 portals show 06:59 next day and flip-flop dates.
    """
    if not due:
        return ""
    return f"{due.isoformat()}T23:59:59"


def apply_inbound_status(
    task, new_status: str, *, stop_timers: bool = True, force: bool = False
) -> bool:
    """
    Apply status that originated in Bitrix. Does not push back to Bitrix.
    Returns True if the local row changed.
    """
    from django.utils import timezone

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
    # Avoid clobbering an in-flight local→Bitrix push.
    # force=True (webhooks): still skip for a short window so we don't regress
    # in_progress → todo from a stale Bitrix echo before start() lands.
    if task.sync_status == Task.SyncStatus.PENDING:
        if not force:
            return False
        age = (timezone.now() - task.updated_at).total_seconds()
        if age < 25:
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
            # Do not echo pauseTimer back to Bitrix — status already came from there.
            stop_time_entry(running, sync_bitrix=False)

    return True


def parse_bitrix_deadline(task_data: dict):
    """Extract calendar due date from Bitrix DEADLINE without UTC day-shift bugs."""
    from datetime import date, datetime, time, timezone as dt_timezone

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    raw = (
        task_data.get("deadline")
        or task_data.get("DEADLINE")
        or task_data.get("deadlineDate")
        or task_data.get("DEADLINE_D")
        or ""
    )
    if raw in (None, "", False, "false", "0"):
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw

    def _from_dt(dt: datetime) -> date:
        if timezone.is_naive(dt):
            # Our writes: YYYY-MM-DDT23:59:59 (portal-local, no offset)
            return dt.date()
        utc = dt.astimezone(dt_timezone.utc)
        # Legacy writes used T23:59:59+00:00; Bitrix often returns that as next-day 06:59 +07
        if utc.hour == 23 and utc.minute >= 59:
            return utc.date()
        if utc.hour == 0 and utc.minute == 0:
            return utc.date()
        return timezone.localtime(dt).date()

    if isinstance(raw, datetime):
        return _from_dt(raw)

    text = str(raw).strip()
    if not text or text.lower() in ("false", "none", "null"):
        return None

    normalized = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    dt = parse_datetime(normalized)
    if dt is None:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(text[:19] if len(text) >= 19 else text, fmt)
                break
            except ValueError:
                continue
    if dt is not None:
        return _from_dt(dt)

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def apply_inbound_deadline(task, new_due, *, allow_while_pending: bool = True) -> bool:
    """Apply deadline from Bitrix. Returns True if changed."""
    from board.models import Task

    # Deadline from Bitrix should win even if a local push is pending —
    # otherwise agency edits never appear while sync_status stuck on pending.
    if task.sync_status == Task.SyncStatus.PENDING and not allow_while_pending:
        return False
    if task.due_date == new_due:
        return False
    task.due_date = new_due
    if task.sync_status != Task.SyncStatus.PENDING:
        task.sync_status = Task.SyncStatus.SYNCED
        task.sync_error = ""
        task.save(update_fields=["due_date", "sync_status", "sync_error", "updated_at"])
    else:
        task.save(update_fields=["due_date", "updated_at"])
    return True


def pull_task_status_from_bitrix(task) -> bool:
    """
    Fetch Bitrix status + deadline (agency copy preferred) and update local task.
    """
    sources = resolve_all_bitrix_task_sources(task)
    if not sources:
        return False

    data = None
    for portal, bitrix_id in sources:
        try:
            data = BitrixClient(portal).get_task(bitrix_id)
        except BitrixAPIError as exc:
            logger.info("pull status task=%s portal=%s: %s", task.id, portal.id, exc)
            continue
        if data:
            break

    if not data:
        return False

    changed = False
    local = local_status_from_bitrix_task(data)
    if local:
        changed = apply_inbound_status(task, local) or changed
    due = parse_bitrix_deadline(data)
    task.refresh_from_db()
    changed = apply_inbound_deadline(task, due, allow_while_pending=True) or changed
    return changed


def handle_bitrix_task_update(*, portal, bitrix_task_id: str, event_data: dict | None = None) -> dict:
    """Process OnTaskUpdate: refresh local status/deadline, or ingest as project/subtask."""
    from portals.models import Portal

    task = find_local_task_for_bitrix(portal=portal, bitrix_task_id=str(bitrix_task_id))
    if task:
        data: dict = {}
        try:
            data = BitrixClient(portal).get_task(bitrix_task_id) or {}
        except BitrixAPIError as exc:
            logger.info("OnTaskUpdate get_task failed id=%s: %s", bitrix_task_id, exc)

        # Event payload often has DEADLINE immediately — use as fallback/primary
        after = {}
        if isinstance(event_data, dict):
            raw_after = event_data.get("FIELDS_AFTER") or event_data.get("fields_after") or {}
            if isinstance(raw_after, dict):
                after = raw_after

        merged = {**after, **data} if data else after
        if not merged:
            return {"ok": False, "reason": "empty_task_payload"}

        status_changed = False
        due_changed = False
        local = local_status_from_bitrix_task(merged)
        if local:
            # force=True: Bitrix start/pause/complete must update the app even during PENDING sync
            status_changed = apply_inbound_status(task, local, force=True)

        # Prefer get_task deadline; fall back to FIELDS_AFTER
        due = parse_bitrix_deadline(data) if data else None
        if due is None:
            due = parse_bitrix_deadline(after)
        # If get_task returned empty deadline but event has one, event wins
        event_due = parse_bitrix_deadline(after)
        if data and parse_bitrix_deadline(data) is None and event_due is not None:
            due = event_due

        task.refresh_from_db()
        due_changed = apply_inbound_deadline(task, due, allow_while_pending=True)
        if due_changed:
            try:
                _mirror_deadline_to_other_portals(
                    task, due, source_portal=portal
                )
            except Exception:
                logger.exception("mirror deadline failed for task %s", task.id)
        return {
            "ok": True,
            "task_id": task.id,
            "status": local,
            "due_date": due.isoformat() if due else None,
            "changed": status_changed or due_changed,
        }

    # Unknown task id — may be a new parent task (app Project) or subtask on agency
    if portal.role == Portal.Role.AGENCY:
        from board.project_sync import ingest_agency_bitrix_task

        result = ingest_agency_bitrix_task(
            agency_portal=portal, bitrix_task_id=str(bitrix_task_id)
        )
        if result.get("ok") and result.get("kind") == "task" and result.get("task_id"):
            from board.models import Task

            task = Task.objects.filter(pk=result["task_id"]).first()
            if task:
                try:
                    data = BitrixClient(portal).get_task(bitrix_task_id) or {}
                    after = {}
                    if isinstance(event_data, dict):
                        raw_after = event_data.get("FIELDS_AFTER") or {}
                        if isinstance(raw_after, dict):
                            after = raw_after
                    due = parse_bitrix_deadline(data) or parse_bitrix_deadline(after)
                    apply_inbound_deadline(task, due, allow_while_pending=True)
                    result["due_date"] = due.isoformat() if due else None
                except BitrixAPIError:
                    pass
        else:
            logger.info(
                "OnTaskUpdate unknown task id=%s portal=%s ingest=%s",
                bitrix_task_id,
                portal.id,
                result,
            )
        return result
    return {"ok": False, "reason": "unknown_task"}


def _mirror_deadline_to_other_portals(task, due, *, source_portal=None) -> None:
    """
    Push due_date only agency → client (never reverse) to avoid ping-pong.
    """
    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    if not agency or not task.bitrix_task_id or not client_portal.access_token:
        return
    # Only mirror when the change came from the agency copy
    if source_portal is not None and source_portal.id != agency.id:
        return
    if not task.agency_bitrix_task_id:
        return

    fields = {"DEADLINE": format_bitrix_deadline(due)}
    try:
        client = BitrixClient(client_portal)
        current = parse_bitrix_deadline(client.get_task(task.bitrix_task_id) or {})
        if current == due:
            return
        client.update_task(task.bitrix_task_id, fields)
    except BitrixAPIError as exc:
        logger.info("mirror deadline %s→client: %s", task.id, exc)
