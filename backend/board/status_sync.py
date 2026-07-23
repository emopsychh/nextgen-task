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


def _start_local_timer_from_inbound(task) -> None:
    """Mirror Bitrix start into a local running entry without echoing to Bitrix."""
    from django.utils import timezone

    from board.models import TimeEntry
    from portals.models import BitrixUser, PortalLink

    if task.time_entries.filter(ended_at__isnull=True).exists():
        return
    author = None
    last = task.time_entries.order_by("-started_at").first()
    if last and last.author_id:
        author = last.author
    if author is None and task.created_by_id:
        from portals.models import Portal

        # Prefer agency users for timers
        if task.created_by.portal.role == Portal.Role.AGENCY:
            author = task.created_by
    if author is None:
        agency_ids = list(
            PortalLink.objects.filter(client_portal=task.project.portal).values_list(
                "agency_portal_id", flat=True
            )
        )
        if agency_ids:
            author = (
                BitrixUser.objects.filter(portal_id__in=agency_ids)
                .order_by("id")
                .first()
            )
    if author is None:
        return
    TimeEntry.objects.create(task=task, author=author, started_at=timezone.now())


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
    # force=True (webhooks / pull): still skip for a short window so we don't
    # regress in_progress → todo from a stale Bitrix echo before start() lands.
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
    elif stop_timers and new_status == Task.Status.IN_PROGRESS:
        _start_local_timer_from_inbound(task)

    logger.info(
        "inbound status task=%s %s→%s (force=%s)",
        task.id,
        old,
        new_status,
        force,
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


def pull_task_status_from_bitrix(task) -> bool:
    """
    Fetch Bitrix status + deadline + title/description (agency copy preferred).
    """
    from board.titles import strip_portal_title_prefix

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
        # Same as webhooks: Bitrix is source of truth on pull; short PENDING
        # window still protects against stale echo right after local→Bitrix push.
        changed = apply_inbound_status(task, local, force=True) or changed
    due = parse_bitrix_deadline(data)
    task.refresh_from_db()
    changed = apply_inbound_deadline(task, due, allow_while_pending=True) or changed

    raw_title = str(data.get("title") or data.get("TITLE") or "").strip()
    if raw_title:
        new_title = strip_portal_title_prefix(raw_title, task.project.portal)
        if new_title and new_title != task.title:
            task.title = new_title
            task.save(update_fields=["title", "updated_at"])
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
        meta_changed = False
        local = local_status_from_bitrix_task(merged)
        if local:
            # force=True: Bitrix start/pause/complete must update the app even during PENDING sync
            status_changed = apply_inbound_status(task, local, force=True)

        # Title / description from Bitrix (strip legacy portal prefixes)
        from board.titles import strip_portal_title_prefix

        raw_title = str(
            merged.get("title") or merged.get("TITLE") or task.title or ""
        ).strip()
        if raw_title:
            new_title = strip_portal_title_prefix(raw_title, task.project.portal)
            if new_title and new_title != task.title:
                task.title = new_title
                meta_changed = True
        raw_desc = merged.get("description")
        if raw_desc is None:
            raw_desc = merged.get("DESCRIPTION")
        if raw_desc is not None:
            new_desc = str(raw_desc).strip()
            if new_desc != (task.description or ""):
                task.description = new_desc
                meta_changed = True
        if meta_changed:
            task.save(update_fields=["title", "description", "updated_at"])

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
            "changed": status_changed or due_changed or meta_changed,
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
