"""Bidirectional task status sync between Nextgen and Bitrix Tasks."""

from __future__ import annotations

import logging

from django.conf import settings

from portals.bitrix import (
    BITRIX_STATUS_COMPLETED,
    BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
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
    wanted = {"ONTASKUPDATE", "ONTASKCOMMENTADD", "ONTASKADD", "ONTASKDELETE"}
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
            h = str(row.get("handler") or row.get("HANDLER") or "").rstrip("/")
            if ev not in wanted:
                continue
            if h == handler.rstrip("/"):
                bound.add(ev)
                continue
            # Stale handler (old PUBLIC_APP_URL) — drop and rebind below
            if h:
                try:
                    client.call(
                        "event.unbind",
                        {
                            "event": row.get("event") or row.get("EVENT") or ev,
                            "handler": row.get("handler") or row.get("HANDLER") or h,
                        },
                    )
                except BitrixAPIError as exc:
                    logger.info(
                        "event.unbind stale %s for %s: %s", ev, portal.domain, exc
                    )
        ok = True
        for event_name, key in (
            ("OnTaskUpdate", "ONTASKUPDATE"),
            ("OnTaskCommentAdd", "ONTASKCOMMENTADD"),
            ("OnTaskAdd", "ONTASKADD"),
            ("OnTaskDelete", "ONTASKDELETE"),
        ):
            if key in bound:
                continue
            try:
                client.call("event.bind", {"event": event_name, "handler": handler})
                bound.add(key)
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
    """
    Map Bitrix task → local status.

    New Bitrix UI often keeps a misleading STATUS label after «остановить работу»
    while the action buttons already say «Начать». Prefer action.start/pause when
    present — that matches what the user can click.
    """
    if not isinstance(task_data, dict):
        return None

    code = bitrix_status_code(task_data)
    if code in (
        BITRIX_STATUS_COMPLETED,
        BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
    ):
        return "done"

    action = task_data.get("action") or task_data.get("ACTION") or {}
    if isinstance(action, dict) and action:
        pause = action.get("pause")
        start = action.get("start")
        # Normalize Bitrix bool-ish values
        def _flag(v) -> bool | None:
            if v is True or str(v).lower() in ("true", "1", "y"):
                return True
            if v is False or str(v).lower() in ("false", "0", "n"):
                return False
            return None

        pause_b = _flag(pause)
        start_b = _flag(start)
        # Can pause → work is active
        if pause_b is True:
            return "in_progress"
        # Can start, cannot pause → waiting / paused work (app «Пауза»)
        if start_b is True and pause_b is False:
            return "todo"

    if code is None:
        return None
    return BITRIX_TO_LOCAL.get(code)


def bitrix_time_spent_seconds(task_data: dict) -> int | None:
    """Closed time from tasks.task.get (excludes live timer tick)."""
    if not isinstance(task_data, dict):
        return None
    for key in (
        "timeSpentInLogs",
        "TIME_SPENT_IN_LOGS",
        "timeSpentFromLogs",
        "TIME_SPENT_FROM_LOGS",
    ):
        if key in task_data and task_data[key] not in (None, ""):
            try:
                return max(0, int(task_data[key]))
            except (TypeError, ValueError):
                continue
    return None


def bitrix_timer_started_at(timer_payload) -> "datetime | None":
    from datetime import datetime, timezone as dt_timezone

    if not isinstance(timer_payload, dict):
        return None
    raw = (
        timer_payload.get("TIMER_STARTED_AT")
        or timer_payload.get("timerStartedAt")
        or timer_payload.get("STARTED_AT")
        or timer_payload.get("startedAt")
    )
    if raw in (None, "", 0, "0"):
        return None
    try:
        ts = int(raw)
        if ts > 10_000_000_000:  # ms
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    return None


def bitrix_timer_is_running(
    task_data: dict | None, timer_payload, *, bitrix_task_id: str | None = None
) -> bool | None:
    """
    True/False when known; None when Bitrix gave no signal.
    Uses task.timer.get only — task.action.pause reflects STATUS, not the stopwatch.
    """
    if timer_payload is None:
        return None
    if timer_payload in ([], {}):
        return False
    if isinstance(timer_payload, list):
        return len(timer_payload) > 0
    if isinstance(timer_payload, dict):
        tid = str(
            timer_payload.get("TASK_ID")
            or timer_payload.get("taskId")
            or timer_payload.get("TASKID")
            or ""
        )
        if bitrix_task_id and tid and tid != str(bitrix_task_id):
            # Auth user has a live timer on another task → this one is stopped
            return False
        if bitrix_timer_started_at(timer_payload) is not None:
            return True
        if tid:
            return True
        # Non-empty dict without ids — treat as running only if it looks like a timer
        if any(
            k in timer_payload
            for k in ("USER_ID", "userId", "TIMER_STARTED_AT", "SECONDS")
        ):
            return True
        return False
    return None


def fetch_bitrix_timer_state(portal, bitrix_task_id: str, task_data: dict | None = None):
    """Return (running: bool|None, timer_payload, spent_seconds: int|None)."""
    client = BitrixClient(portal)
    timer_payload = None
    try:
        timer_payload = client.get_task_timer(bitrix_task_id)
    except BitrixAPIError:
        timer_payload = None
    running = bitrix_timer_is_running(
        task_data, timer_payload, bitrix_task_id=str(bitrix_task_id)
    )
    spent = bitrix_time_spent_seconds(task_data or {})
    if spent is None:
        spent = client.get_task_elapsed_seconds(bitrix_task_id)
    return running, timer_payload, spent


def _reconcile_tracked_seconds(task, bitrix_total: int) -> bool:
    """Align closed local entries to Bitrix elapsed total (no echo back)."""
    from datetime import timedelta

    from board.timeutils import task_tracked_seconds

    target = max(0, int(bitrix_total))
    local = int(task_tracked_seconds(task, include_running=False))
    diff = target - local
    if abs(diff) <= 1:
        return False
    last = (
        task.time_entries.filter(ended_at__isnull=False)
        .order_by("-ended_at", "-id")
        .first()
    )
    if not last or not last.ended_at:
        return False
    new_dur = max(0, int(last.duration_seconds or 0) + diff)
    last.duration_seconds = new_dur
    last.started_at = last.ended_at - timedelta(seconds=new_dur)
    last.save(update_fields=["duration_seconds", "started_at", "updated_at"])
    logger.info(
        "reconcile time task=%s local=%s bitrix=%s adjusted_entry=%s → %ss",
        task.id,
        local,
        target,
        last.id,
        new_dur,
    )
    return True


def apply_inbound_timer_state(
    task,
    *,
    running: bool | None,
    timer_payload=None,
    bitrix_total: int | None = None,
    bitrix_status: str | None = None,
) -> bool:
    """
    App time is entered manually. Do not mirror Bitrix stopwatch / elapsed
    into local TimeEntry rows. Only close leftover open entries when Bitrix
    clearly shows the task is paused or done.
    """
    from board.timeutils import stop_time_entry

    _ = timer_payload, bitrix_total
    if running is None and not bitrix_status:
        return False

    should_stop = running is False or bitrix_status in ("todo", "done")
    if not should_stop:
        return False

    changed = False
    for entry in task.time_entries.filter(ended_at__isnull=True):
        stop_time_entry(entry, sync_bitrix=False)
        changed = True
    return changed


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
    managers edit deadlines. Client Bitrix tasks are no longer created.
    """
    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        return agency, str(task.agency_bitrix_task_id)
    return None, None


def resolve_all_bitrix_task_sources(task) -> list[tuple]:
    """Agency Bitrix subtask only (client portal tasks are not synced)."""
    sources: list[tuple] = []
    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        sources.append((agency, str(task.agency_bitrix_task_id)))
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


def find_local_project_for_bitrix(*, portal, bitrix_task_id: str):
    """Match local project by agency parent Bitrix task id."""
    from board.models import Project
    from portals.models import PortalLink

    bitrix_task_id = str(bitrix_task_id)
    qs = Project.objects.select_related("portal")

    direct = qs.filter(bitrix_task_id=bitrix_task_id, portal=portal).first()
    if direct:
        return direct

    client_ids = list(
        PortalLink.objects.filter(agency_portal=portal).values_list(
            "client_portal_id", flat=True
        )
    )
    if client_ids:
        linked = qs.filter(
            bitrix_task_id=bitrix_task_id, portal_id__in=client_ids
        ).first()
        if linked:
            return linked

    return qs.filter(bitrix_task_id=bitrix_task_id).first()


def handle_bitrix_task_delete(*, portal, bitrix_task_id: str) -> dict:
    """
    OnTaskDelete from agency Bitrix: remove the local task or project.
    One app DB serves both agency and client UIs — no client Bitrix copy.
    """
    bitrix_task_id = str(bitrix_task_id)

    task = find_local_task_for_bitrix(portal=portal, bitrix_task_id=bitrix_task_id)
    if task:
        client_portal_id = task.project.portal_id
        project_id = task.project_id
        task_id = task.id
        task.delete()
        logger.info(
            "OnTaskDelete removed local task id=%s bitrix=%s portal=%s",
            task_id,
            bitrix_task_id,
            portal.id,
        )
        return {
            "ok": True,
            "deleted": "task",
            "task_id": task_id,
            "project_id": project_id,
            "client_portal_id": client_portal_id,
        }

    project = find_local_project_for_bitrix(
        portal=portal, bitrix_task_id=bitrix_task_id
    )
    if project:
        client_portal_id = project.portal_id
        project_id = project.id
        # Cascade removes local subtasks; Bitrix may emit separate OnTaskDelete
        # for children — those become no-ops after this.
        project.delete()
        logger.info(
            "OnTaskDelete removed local project id=%s bitrix=%s portal=%s",
            project_id,
            bitrix_task_id,
            portal.id,
        )
        return {
            "ok": True,
            "deleted": "project",
            "project_id": project_id,
            "client_portal_id": client_portal_id,
        }

    logger.info(
        "OnTaskDelete unknown bitrix id=%s portal=%s", bitrix_task_id, portal.id
    )
    return {"ok": True, "ignored": "unknown_task"}


def format_bitrix_deadline(due) -> str:
    """
    Write DEADLINE as wall-clock local time without a forced UTC day-shift.
    Date-only legacy → end of day 23:59:59.
    """
    from datetime import date, datetime

    from django.utils import timezone

    if not due:
        return ""
    if isinstance(due, date) and not isinstance(due, datetime):
        return f"{due.isoformat()}T23:59:59"
    dt = due
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def deadlines_equal(a, b) -> bool:
    """Compare due values at minute precision (ignore seconds/tz noise)."""
    from datetime import date, datetime

    from django.utils import timezone

    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    def norm(v):
        if isinstance(v, date) and not isinstance(v, datetime):
            return (v.year, v.month, v.day, 23, 59)
        dt = v
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        return (dt.year, dt.month, dt.day, dt.hour, dt.minute)

    return norm(a) == norm(b)


def _start_local_timer_from_inbound(task, *, started_at=None) -> None:
    """Deprecated no-op: app time is entered manually, not from Bitrix stopwatch."""
    _ = task, started_at
    return


def apply_inbound_status(
    task,
    new_status: str,
    *,
    stop_timers: bool = True,
    force: bool = False,
    allow_resume_from_pause: bool = False,
) -> bool:
    """
    Apply status that originated in Bitrix. Does not push back to Bitrix.

    Start and completion can arrive from Bitrix. Pause is local to each system
    and is therefore ignored. Done is terminal: inbound cannot reopen it.

    App pause (local todo after tracked work) must not be undone by either a
    periodic snapshot or a delayed explicit-start event from Bitrix.
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
    if new_status == Task.Status.TODO:
        return False
    if task.status == Task.Status.DONE and new_status != Task.Status.DONE:
        return False
    # Protect local app pause from Bitrix still being «in progress».
    # «todo» also means never-started — only block resume when the app already
    # tracked time (user had started then paused). First Bitrix start on a
    # virgin task must still apply.
    if (
        task.status == Task.Status.TODO
        and new_status == Task.Status.IN_PROGRESS
        and task.is_locally_paused
    ):
        return False
    if task.status == new_status:
        # Heal drift without a status change: close leftover open entries on done.
        if stop_timers and new_status == Task.Status.DONE:
            stopped = False
            for running in task.time_entries.filter(ended_at__isnull=True):
                stop_time_entry(running, sync_bitrix=False)
                stopped = True
            return stopped
        return False
    # Avoid clobbering an in-flight local→Bitrix push.
    # force=True (webhooks / pull): still skip for a short window so we don't
    # regress from a stale Bitrix echo before the outbound lands.
    if task.sync_status == Task.SyncStatus.PENDING:
        if not force:
            return False
        age = (timezone.now() - task.updated_at).total_seconds()
        if age < 12:
            return False

    old = task.status
    task.status = new_status
    if new_status in (Task.Status.IN_PROGRESS, Task.Status.DONE):
        task.is_locally_paused = False
    # Keep sync_status as synced — change came from Bitrix
    task.sync_status = Task.SyncStatus.SYNCED
    task.sync_error = ""
    task.save(
        update_fields=[
            "status",
            "is_locally_paused",
            "sync_status",
            "sync_error",
            "updated_at",
        ]
    )

    if stop_timers and new_status == Task.Status.DONE:
        for running in task.time_entries.filter(ended_at__isnull=True):
            stop_time_entry(running, sync_bitrix=False)

    logger.info(
        "inbound status task=%s %s→%s (force=%s resume_pause=%s)",
        task.id,
        old,
        new_status,
        force,
        allow_resume_from_pause,
    )
    return True


def parse_bitrix_deadline(task_data: dict):
    """Extract aware datetime from Bitrix DEADLINE (preserves time of day)."""
    from datetime import date, datetime, timezone as dt_timezone

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
    if isinstance(raw, datetime):
        dt = raw
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, dt_timezone.utc)
        return dt
    if isinstance(raw, date):
        from datetime import time as dtime

        dt = datetime.combine(raw, dtime(23, 59, 59))
        return timezone.make_aware(dt, dt_timezone.utc)

    text = str(raw).strip()
    if not text or text.lower() in ("false", "none", "null"):
        return None

    normalized = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    dt = parse_datetime(normalized)
    if dt is None:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(text[:19] if len(text) >= 19 else text, fmt)
                break
            except ValueError:
                continue
    if dt is None and len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            from datetime import time as dtime

            d = date.fromisoformat(text[:10])
            dt = datetime.combine(d, dtime(23, 59, 59))
        except ValueError:
            return None
    if dt is None:
        return None
    if timezone.is_naive(dt):
        # Naive ISO from us / Bitrix portal-local → treat as UTC wall (matches our writes)
        return timezone.make_aware(dt, dt_timezone.utc)
    return dt


def bitrix_task_is_important(task_data: dict) -> bool | None:
    """Bitrix PRIORITY high (>=2) → important. None when Bitrix gave no signal."""
    if not isinstance(task_data, dict):
        return None
    for key in ("priority", "PRIORITY"):
        if key in task_data and task_data[key] not in (None, ""):
            try:
                return int(task_data[key]) >= 2
            except (TypeError, ValueError):
                continue
    return None


def apply_inbound_importance(task, is_important, *, allow_while_pending: bool = True) -> bool:
    """Apply «important» flag from Bitrix. Returns True if changed."""
    from board.models import Task

    if is_important is None:
        return False
    if task.sync_status == Task.SyncStatus.PENDING and not allow_while_pending:
        return False
    if bool(task.is_important) == bool(is_important):
        return False
    task.is_important = bool(is_important)
    if task.sync_status != Task.SyncStatus.PENDING:
        task.sync_status = Task.SyncStatus.SYNCED
        task.sync_error = ""
        task.save(
            update_fields=["is_important", "sync_status", "sync_error", "updated_at"]
        )
    else:
        task.save(update_fields=["is_important", "updated_at"])
    return True


def apply_inbound_title(task, raw_title: str, *, allow_while_pending: bool = True) -> bool:
    """
    Apply title from Bitrix (after stripping portal prefixes).
    When allow_while_pending=False, skip while local outbound sync is in flight —
    otherwise a mid-flight pull restores the old Bitrix title and the UI reverts.
    """
    from board.models import Task
    from board.titles import strip_portal_title_prefix

    raw = str(raw_title or "").strip()
    if not raw:
        return False
    if task.sync_status == Task.SyncStatus.PENDING and not allow_while_pending:
        return False
    new_title = strip_portal_title_prefix(raw, task.project.portal)
    if not new_title or new_title == task.title:
        return False
    task.title = new_title
    task.save(update_fields=["title", "updated_at"])
    return True


def apply_inbound_deadline(task, new_due, *, allow_while_pending: bool = True) -> bool:
    """Apply deadline from Bitrix. Returns True if changed."""
    from board.models import Task

    if task.sync_status == Task.SyncStatus.PENDING and not allow_while_pending:
        return False
    if deadlines_equal(task.due_date, new_due):
        return False
    task.due_date = new_due
    if task.sync_status != Task.SyncStatus.PENDING:
        task.sync_status = Task.SyncStatus.SYNCED
        task.sync_error = ""
        task.save(update_fields=["due_date", "sync_status", "sync_error", "updated_at"])
    else:
        task.save(update_fields=["due_date", "updated_at"])
    return True


def _parse_bitrix_ts(raw) -> float:
    from django.utils.dateparse import parse_datetime

    if raw in (None, "", False):
        return 0.0
    text = str(raw).strip().replace(" ", "T", 1)
    dt = parse_datetime(text)
    return dt.timestamp() if dt is not None else 0.0


def _bitrix_status_changed_date_only(task_data: dict) -> float:
    """
    Only STATUS_CHANGED_DATE.

    Do not fall back to CHANGED_DATE — timer ticks / title edits bump that while
    STATUS stays 3 and wrongly beat a real pause on the other portal.
    """
    if not isinstance(task_data, dict):
        return 0.0
    for key in ("statusChangedDate", "STATUS_CHANGED_DATE"):
        ts = _parse_bitrix_ts(task_data.get(key))
        if ts:
            return ts
    return 0.0


def _bitrix_status_changed_ts(task_data: dict) -> float:
    """Best-effort timestamp for ranking (status change first, then activity)."""
    if not isinstance(task_data, dict):
        return 0.0
    status_ts = _bitrix_status_changed_date_only(task_data)
    if status_ts:
        return status_ts
    for key in (
        "changedDate",
        "CHANGED_DATE",
        "changed",
        "CHANGED",
        "activityDate",
        "ACTIVITY_DATE",
    ):
        ts = _parse_bitrix_ts(task_data.get(key))
        if ts:
            return ts
    return 0.0


def _status_rank(status: str | None) -> int:
    # Higher = more "terminal"/paused preference when timestamps tie
    if status == "done":
        return 3
    if status == "todo":
        return 2
    if status == "in_progress":
        return 1
    return 0


def resolve_inbound_status_from_sources(task) -> tuple[str | None, dict | None, object | None, str | None]:
    """
    Load agency + client Bitrix copies and pick the inbound status.

    Prefer the copy with the newest status-change timestamp. On a tie, prefer
    todo/done over in_progress — pause must not lose to a stale agency «3».
    Returns (status, task_data, portal, bitrix_id).
    """
    from board.comment_sync import (
        latest_bitrix_work_activity,
        resolve_status_with_timer_activity,
    )

    sources = resolve_all_bitrix_task_sources(task)
    candidates: list[tuple[float, int, str, dict, object, str]] = []
    for portal, bitrix_id in sources:
        try:
            data = BitrixClient(portal).get_task(bitrix_id) or {}
        except BitrixAPIError as exc:
            logger.info(
                "pull status task=%s portal=%s id=%s: %s",
                task.id,
                portal.id,
                bitrix_id,
                exc,
            )
            continue
        if not data:
            continue
        mapped = local_status_from_bitrix_task(data)
        try:
            activity = latest_bitrix_work_activity(portal, bitrix_id, data)
        except Exception:
            logger.exception(
                "activity scan failed task=%s portal=%s", task.id, portal.id
            )
            activity = None
        resolved = resolve_status_with_timer_activity(mapped, activity)
        if not resolved:
            continue
        ts = _bitrix_status_changed_ts(data)
        candidates.append(
            (ts, _status_rank(resolved), resolved, data, portal, str(bitrix_id))
        )
        logger.info(
            "pull candidate task=%s %s#%s mapped=%s activity=%s resolved=%s ts=%s status_ts=%s",
            task.id,
            portal.role,
            bitrix_id,
            mapped,
            activity,
            resolved,
            ts,
            _bitrix_status_changed_date_only(data),
        )

    if not candidates:
        return None, None, None, None

    # Split-brain: one portal paused (todo), the other still in_progress.
    # Prod: client STATUS=2, agency STATUS=3 with a newer CHANGED_DATE (or even
    # statusChangedDate from timer/sync noise). Pause must win — otherwise the
    # app never stops. Re-start is handled by OnTaskUpdate + mirror to the other
    # copy so both agree before the next pull.
    todos = [c for c in candidates if c[2] == "todo"]
    progs = [c for c in candidates if c[2] == "in_progress"]
    if todos and progs:
        best_todo = max(
            todos,
            key=lambda row: (_bitrix_status_changed_date_only(row[3]), row[0], row[1]),
        )
        best_prog = max(
            progs,
            key=lambda row: (_bitrix_status_changed_date_only(row[3]), row[0], row[1]),
        )
        todo_ts = _bitrix_status_changed_date_only(best_todo[3])
        prog_ts = _bitrix_status_changed_date_only(best_prog[3])
        # Whichever copy changed its STATUS most recently wins. This lets a fresh
        # "start" on one portal beat a stale "todo" on the other (otherwise the
        # app would push a pause right back and ping-pong the task). Only when the
        # two status changes are effectively simultaneous do we keep the old
        # "pause wins" default, so a real pause is not undone by a stale echo.
        if prog_ts > todo_ts:
            chosen = best_prog
            reason = "start newer"
        else:
            chosen = best_todo
            reason = "pause wins"
        logger.info(
            "pull split-brain task=%s todo_status_ts=%s prog_status_ts=%s → %s (%s)",
            task.id,
            todo_ts,
            prog_ts,
            reason,
            chosen[2],
        )
        _ts, _rank, status, data, portal, bitrix_id = chosen
        return status, data, portal, bitrix_id

    # Newest change first; then pause/done beats in_progress
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _ts, _rank, status, data, portal, bitrix_id = candidates[0]
    return status, data, portal, bitrix_id


def mirror_work_status_to_other_bitrix_copies(
    task, status: str, *, source_portal=None
) -> None:
    """
    After inbound pause/start from one Bitrix copy, push the same work state
    to the other copy so agency/client stop disagreeing.
    """
    if status not in ("todo", "in_progress", "done"):
        return
    from board.tasks import apply_bitrix_status

    for portal, bitrix_id in resolve_all_bitrix_task_sources(task):
        if source_portal is not None and getattr(portal, "id", None) == getattr(
            source_portal, "id", None
        ):
            continue
        try:
            apply_bitrix_status(BitrixClient(portal), str(bitrix_id), status)
            logger.info(
                "mirrored status=%s task=%s → %s#%s",
                status,
                task.id,
                portal.role,
                bitrix_id,
            )
        except BitrixAPIError as exc:
            logger.info(
                "mirror status failed task=%s %s#%s: %s",
                task.id,
                portal.role,
                bitrix_id,
                exc,
            )


def _inbound_work_status(task) -> str | None:
    """Scan Bitrix copies for synchronized start/completion states."""
    from board.comment_sync import (
        latest_bitrix_work_activity,
        resolve_status_with_timer_activity,
    )

    seen_done = False
    seen_progress = False
    for portal, bitrix_id in resolve_all_bitrix_task_sources(task):
        try:
            data = BitrixClient(portal).get_task(bitrix_id) or {}
        except BitrixAPIError:
            continue
        if not data:
            continue
        mapped = local_status_from_bitrix_task(data)
        try:
            activity = latest_bitrix_work_activity(portal, bitrix_id, data)
        except Exception:
            activity = None
        resolved = resolve_status_with_timer_activity(mapped, activity)
        if resolved == "done":
            seen_done = True
        elif resolved == "in_progress":
            seen_progress = True
    if seen_done:
        return "done"
    if seen_progress:
        return "in_progress"
    return None


def pull_task_status_from_bitrix(task) -> bool:
    """
    Fetch Bitrix status + deadline + title/description from agency+client copies.

    Single resolve path (no second Bitrix scan). Start and completion are
    mirrored; pause remains independent.
    """
    status, data, portal, bitrix_id = resolve_inbound_status_from_sources(task)
    work = status if status in ("in_progress", "done") else None
    if not data or not portal or not bitrix_id:
        # Still apply work status if we could read it from a partial scan
        if work and task.status != work:
            prev = task.status
            applied = apply_inbound_status(task, work, force=True)
            if applied and work == "done" and prev != "done":
                try:
                    from board.completion import finalize_task_completion

                    finalize_task_completion(task)
                except Exception:
                    logger.exception("finalize_task_completion failed task=%s", task.id)
            return applied
        return False

    local = work

    changed = False
    if local and task.status != local:
        prev = task.status
        applied = apply_inbound_status(task, local, force=True)
        changed = applied or changed
        if applied and local == "done" and prev != "done":
            try:
                from board.completion import finalize_task_completion

                finalize_task_completion(task)
            except Exception:
                logger.exception("finalize_task_completion failed task=%s", task.id)

    due = parse_bitrix_deadline(data)
    task.refresh_from_db()
    changed = apply_inbound_deadline(task, due, allow_while_pending=True) or changed

    important = bitrix_task_is_important(data)
    # Do NOT clobber a pending local toggle: Bitrix lags right after we push,
    # so a pull mid-flight would reset the flag before the outbound sync lands.
    changed = apply_inbound_importance(task, important, allow_while_pending=False) or changed

    raw_title = str(data.get("title") or data.get("TITLE") or "").strip()
    title_changed = apply_inbound_title(task, raw_title, allow_while_pending=False)
    if title_changed:
        changed = True
        # Push cleaned title back so Bitrix drops legacy [portal] prefix
        try:
            from board.tasks import sync_task_to_bitrix
            from django.conf import settings

            if settings.CELERY_TASK_ALWAYS_EAGER:
                sync_task_to_bitrix(task.id)
            else:
                sync_task_to_bitrix.delay(task.id)
        except Exception:
            logger.exception("enqueue title cleanup sync failed task=%s", task.id)
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

        # Event payload often has DEADLINE/STATUS immediately — use as primary for status
        after = {}
        before = {}
        if isinstance(event_data, dict):
            raw_after = event_data.get("FIELDS_AFTER") or event_data.get("fields_after") or {}
            if isinstance(raw_after, dict):
                after = raw_after
            raw_before = (
                event_data.get("FIELDS_BEFORE") or event_data.get("fields_before") or {}
            )
            if isinstance(raw_before, dict):
                before = raw_before

        merged = {**after, **data} if data else after
        if not merged:
            return {"ok": False, "reason": "empty_task_payload"}

        status_changed = False
        due_changed = False
        meta_changed = False
        # Accept start and completion; Bitrix pause is independent.
        after_status = local_status_from_bitrix_task(after) if after else None
        before_status = local_status_from_bitrix_task(before) if before else None
        data_status = local_status_from_bitrix_task(data) if data else None
        local = after_status if after_status is not None else data_status
        # A direct get reflects the current Bitrix state. Event delivery may be
        # delayed (notably the old completion event arriving after renew).
        if (
            data_status is not None
            and after_status is not None
            and data_status != after_status
        ):
            local = data_status
        # Prefer an explicit FIELDS_BEFORE→AFTER transition when present.
        if (
            after_status is not None
            and before_status is not None
            and after_status != before_status
            and (data_status is None or data_status == after_status)
        ):
            local = after_status
        logger.info(
            "OnTaskUpdate id=%s mapped=%s before=%s after=%s",
            bitrix_task_id,
            local,
            before_status,
            after_status,
        )
        if local in ("in_progress", "done") and task.status != local:
            prev = task.status
            # Keep the transition flag for virgin tasks. apply_inbound_status
            # still protects an app-paused task that already has tracked time.
            explicit_start = (
                local == "in_progress"
                and after_status == "in_progress"
                and before_status is not None
                and before_status != "in_progress"
            )
            status_changed = apply_inbound_status(
                task,
                local,
                force=True,
                allow_resume_from_pause=explicit_start or local == "done",
            )
            if status_changed and local == "done" and prev != "done":
                try:
                    from board.completion import finalize_task_completion

                    finalize_task_completion(task)
                except Exception:
                    logger.exception(
                        "finalize_task_completion failed id=%s", bitrix_task_id
                    )

        # Title / description from Bitrix (strip legacy portal prefixes).
        # Skip title while PENDING — same race as importance (local rename vs stale pull).
        from board.models import Task

        raw_title = str(
            merged.get("title") or merged.get("TITLE") or task.title or ""
        ).strip()
        title_applied = apply_inbound_title(task, raw_title, allow_while_pending=False)
        raw_desc = merged.get("description")
        if raw_desc is None:
            raw_desc = merged.get("DESCRIPTION")
        desc_changed = False
        if raw_desc is not None and task.sync_status != Task.SyncStatus.PENDING:
            new_desc = str(raw_desc).strip()
            if new_desc != (task.description or ""):
                task.description = new_desc
                desc_changed = True
        if desc_changed:
            task.save(update_fields=["description", "updated_at"])
        meta_changed = title_applied or desc_changed

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

        important = bitrix_task_is_important(data or merged)
        if important is None and after:
            important = bitrix_task_is_important(after)
        # Skip while a local push is pending so we don't undo it with a stale echo.
        importance_changed = apply_inbound_importance(
            task, important, allow_while_pending=False
        )

        return {
            "ok": True,
            "task_id": task.id,
            "status": local,
            "due_date": due.isoformat() if due else None,
            "changed": (
                status_changed
                or due_changed
                or meta_changed
                or importance_changed
            ),
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
        if deadlines_equal(current, due):
            return
        client.update_task(task.bitrix_task_id, fields)
    except BitrixAPIError as exc:
        logger.info("mirror deadline %s→client: %s", task.id, exc)
