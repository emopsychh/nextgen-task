"""Work report lifecycle helpers (agency → client agree / contact manager)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from portals.models import BitrixUser, PortalDealBinding

from .models import (
    Attachment,
    Project,
    Task,
    TimeEntry,
    WorkReport,
    WorkReportDisputeItem,
    WorkReportEvent,
    WorkReportLine,
)
from .realtime import publish_portal_event

ACTIVE = WorkReport.ACTIVE_STATUSES

# List filters for the reports hub UI.
BUCKET_STATUSES = {
    "current": (
        WorkReport.Status.DRAFT,
        WorkReport.Status.DISPUTED,
    ),
    "review": (WorkReport.Status.PENDING_CLIENT,),
    # Agreed / archived happy-path (legacy `paid` included).
    "accepted": (
        WorkReport.Status.ACCEPTED,
        WorkReport.Status.PAID,
    ),
}

# Old hub tab id — keep accepting ?bucket=paid
BUCKET_ALIASES = {"paid": "accepted"}


def normalize_report_bucket(bucket: str | None) -> str:
    raw = (bucket or "").strip()
    return BUCKET_ALIASES.get(raw, raw)


def _portal_tz(portal) -> ZoneInfo:
    try:
        return ZoneInfo(portal.timezone or "Europe/Moscow")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


def _parse_iso_date(value: str | None, field: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({field: "Ожидается дата в формате YYYY-MM-DD."}) from None


def _task_activity_row(task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "project_id": task.project_id,
        "project_name": task.project.name,
        "outcome": task.outcome or "",
        "completed_at": task.completed_at,
        "tracked_seconds": int(getattr(task, "_tracked_seconds", 0) or 0),
    }


def _completed_tasks_in_range(portal, range_start: datetime, range_end: datetime):
    return (
        Task.objects.filter(
            project__portal_id=portal.id,
            completed_at__gte=range_start,
            completed_at__lt=range_end,
        )
        .select_related("project")
        .annotate(_tracked_seconds=Sum("time_entries__duration_seconds"))
        .order_by("-completed_at", "id")
    )


def _as_report_date(value, field: str, default: date) -> date:
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_iso_date(str(value), field) or default


def period_activity_payload(portal, *, date_from=None, date_to=None) -> dict:
    """Completed work for an arbitrary inclusive date range in the client's timezone."""
    tz = _portal_tz(portal)
    today = timezone.now().astimezone(tz).date()
    end = _as_report_date(date_to, "to", today)
    start = _as_report_date(date_from, "from", end.replace(day=1))
    if start > end:
        raise ValidationError({"from": "Дата начала не может быть позже даты окончания."})
    if (end - start).days > 366:
        raise ValidationError({"to": "Период не может быть длиннее 366 дней."})

    range_start = datetime.combine(start, time.min, tzinfo=tz)
    range_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    rows = [_task_activity_row(task) for task in _completed_tasks_in_range(portal, range_start, range_end)]

    projects: dict[int, dict] = {}
    for row in rows:
        bucket = projects.setdefault(
            row["project_id"],
            {
                "id": row["project_id"],
                "name": row["project_name"],
                "tasks_count": 0,
                "tracked_seconds": 0,
            },
        )
        bucket["tasks_count"] += 1
        bucket["tracked_seconds"] += row["tracked_seconds"]

    project_list = sorted(projects.values(), key=lambda item: (-item["tracked_seconds"], item["name"].lower()))
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "tasks_count": len(rows),
        "projects_count": len(project_list),
        "total_tracked_seconds": sum(row["tracked_seconds"] for row in rows),
        "projects": project_list,
        "tasks": rows,
    }


def weekly_reports_payload(portal, *, weeks: int = 12) -> list[dict]:
    """Virtual Monday–Sunday reports of tasks completed in the client's timezone."""
    weeks = max(1, min(int(weeks or 12), 52))
    tz = _portal_tz(portal)
    today = timezone.now().astimezone(tz).date()
    current_start = today - timedelta(days=today.weekday())
    first_start = current_start - timedelta(weeks=weeks - 1)
    range_start = datetime.combine(first_start, time.min, tzinfo=tz)
    range_end = datetime.combine(current_start + timedelta(days=7), time.min, tzinfo=tz)

    by_week: dict = {}
    for task in _completed_tasks_in_range(portal, range_start, range_end):
        local_date = task.completed_at.astimezone(tz).date()
        week_start = local_date - timedelta(days=local_date.weekday())
        by_week.setdefault(week_start, []).append(_task_activity_row(task))

    result = []
    for offset in range(weeks):
        week_start = current_start - timedelta(weeks=offset)
        week_end = week_start + timedelta(days=6)
        tasks = by_week.get(week_start, [])
        result.append(
            {
                "key": week_start.isoformat(),
                "date_from": week_start.isoformat(),
                "date_to": week_end.isoformat(),
                "is_current": offset == 0,
                "tasks_count": len(tasks),
                "total_tracked_seconds": sum(row["tracked_seconds"] for row in tasks),
                "tasks": tasks,
            }
        )
    return result


def portal_has_active_report(portal_id: int, *, exclude_id: int | None = None) -> bool:
    qs = WorkReport.objects.filter(portal_id=portal_id, status__in=ACTIVE)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.exists()


def append_event(
    report: WorkReport,
    kind: str,
    actor: BitrixUser | None,
    payload: dict | None = None,
) -> WorkReportEvent:
    return WorkReportEvent.objects.create(
        report=report,
        actor=actor,
        kind=kind,
        payload=payload or {},
    )


def live_total_seconds_for_projects(project_ids: list[int]) -> int:
    if not project_ids:
        return 0
    total = (
        TimeEntry.objects.filter(task__project_id__in=project_ids).aggregate(
            total=Sum("duration_seconds")
        )["total"]
        or 0
    )
    return int(total)


def _deal_overage_payload(binding: PortalDealBinding) -> dict:
    from portals.deal_hours_overage import overage_hours_amount, portal_link_for_binding

    link = portal_link_for_binding(binding)
    deal_id = str(binding.deal_id or "").strip()
    pending = overage_hours_amount(getattr(link, "hours_overage", None)) if link else Decimal("0")
    source_id = str(getattr(link, "hours_overage_source_deal_id", "") or "") if link else ""
    if source_id != deal_id:
        pending = Decimal("0")
    carried = overage_hours_amount(getattr(binding, "hours_overage_applied", None))
    return {
        "hours_overage": float(pending) if pending > 0 else 0.0,
        "hours_overage_source_title": (
            (link.hours_overage_source_title if link and pending > 0 else "") or ""
        ),
        "carried_overage_hours": float(carried) if carried > 0 else 0.0,
    }


def deal_hours_for_portal(portal_id: int) -> dict | None:
    binding = (
        PortalDealBinding.objects.filter(client_portal_id=portal_id, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    if not binding:
        return None
    return {
        "deal_id": binding.deal_id,
        "deal_title": binding.deal_title or "",
        "paid_hours": (
            float(binding.paid_hours) if binding.paid_hours is not None else None
        ),
        "remaining_hours": (
            float(binding.remaining_hours)
            if binding.remaining_hours is not None
            else None
        ),
        **_deal_overage_payload(binding),
    }


def deal_hours_for_report(report: WorkReport) -> dict | None:
    binding = getattr(report, "deal_binding", None)
    if not binding:
        return deal_hours_for_portal(report_portal_id(report)) if report_portal_id(report) else None
    return {
        "deal_id": binding.deal_id,
        "deal_title": binding.deal_title or "",
        "paid_hours": float(binding.paid_hours) if binding.paid_hours is not None else None,
        "remaining_hours": (
            float(binding.remaining_hours) if binding.remaining_hours is not None else None
        ),
        **_deal_overage_payload(binding),
    }


@transaction.atomic
def ensure_report_for_binding(binding: PortalDealBinding) -> WorkReport:
    report, created = WorkReport.objects.get_or_create(
        deal_binding=binding,
        defaults={
            "portal_id": binding.client_portal_id,
            "status": WorkReport.Status.DRAFT,
        },
    )
    if not report.portal_id:
        report.portal_id = binding.client_portal_id
        report.save(update_fields=["portal", "updated_at"])
    if created:
        append_event(report, WorkReportEvent.Kind.CREATED, None)
    from portals.deal_hours_overage import apply_hours_overage_to_binding

    apply_hours_overage_to_binding(binding)
    return report


def ensure_reports_for_portals(portal_ids: list[int]) -> None:
    bindings = PortalDealBinding.objects.filter(client_portal_id__in=portal_ids)
    existing = set(
        WorkReport.objects.filter(deal_binding_id__in=bindings).values_list(
            "deal_binding_id", flat=True
        )
    )
    for binding in bindings.exclude(id__in=existing).iterator():
        ensure_report_for_binding(binding)
    from portals.deal_hours_overage import apply_pending_hours_overage
    from portals.models import PortalLink

    for link in PortalLink.objects.filter(client_portal_id__in=portal_ids):
        apply_pending_hours_overage(link)


def report_portal_id(report: WorkReport) -> int | None:
    if report.deal_binding_id:
        return report.deal_binding.client_portal_id
    if report.portal_id:
        return report.portal_id
    if report.project_id:
        return report.project.portal_id
    return None


def report_project_ids(report: WorkReport) -> list[int]:
    # Prefer prefetched M2M to avoid an extra query on detail/actions.
    cache = getattr(report, "_prefetched_objects_cache", None) or {}
    if "projects" in cache:
        ids = [p.id for p in report.projects.all()]
        if ids:
            return ids
    ids = list(report.projects.values_list("id", flat=True))
    if ids:
        return ids
    if report.project_id:
        return [report.project_id]
    return []


def selected_task_ids(report: WorkReport) -> list[int]:
    return list(
        report.lines.filter(is_reserved=True)
        .order_by("task_id")
        .values_list("task_id", flat=True)
    )


def _disputed_task_ids(report: WorkReport) -> set[int] | None:
    if report.status != WorkReport.Status.DISPUTED:
        return None
    cache = getattr(report, "_prefetched_objects_cache", None) or {}
    if "dispute_items" in cache:
        return {item.task_id for item in report.dispute_items.all()}
    return set(report.dispute_items.values_list("task_id", flat=True))


def _seconds_by_task(report: WorkReport, task_ids: list[int]) -> dict[int, int]:
    if not task_ids:
        return {}
    entries = TimeEntry.objects.filter(task_id__in=task_ids)
    if report.deal_binding_id:
        # New rows carry an exact billing snapshot. Legacy billed rows have no
        # snapshot, so only use the report binding's lifetime as a bounded fallback.
        entries = entries.filter(
            Q(billed_deal_binding_id=report.deal_binding_id)
            | Q(
                billed_deal_binding__isnull=True,
                billed_to_deal_at__isnull=False,
                billed_to_deal_at__gte=report.deal_binding.created_at,
            )
        )
    return {
        row["task_id"]: int(row["total"] or 0)
        for row in entries
        .values("task_id")
        .annotate(total=Sum("duration_seconds"))
    }


def _result_file_payload(attachment: Attachment) -> dict:
    from board.naming import display_attachment_name
    from board.serializers import sign_attachment_id

    name = display_attachment_name(attachment) or (attachment.original_name or "").strip() or "Файл"
    url = ""
    if attachment.file:
        url = f"/api/attachments/{attachment.id}/download/?t={sign_attachment_id(attachment.id)}"
    return {"id": attachment.id, "name": name, "url": url}


def _task_row_extras(report: WorkReport, task_ids: list[int]) -> dict[int, dict]:
    extras: dict[int, dict] = {tid: {"files": []} for tid in task_ids}
    if not task_ids:
        return extras

    files = Attachment.objects.filter(task_id__in=task_ids, comment__isnull=True).order_by("id")
    for attachment in files:
        extra = extras.get(attachment.task_id)
        if extra is None:
            continue
        extra["files"].append(_result_file_payload(attachment))
    return extras


def _build_projects_detail(
    report: WorkReport,
    project_ids: list[int],
    selected_task_ids_: list[int],
    seconds_by_task: dict[int, int],
    disputed_task_ids: set[int] | None,
) -> list[dict]:
    if not project_ids:
        return []
    if disputed_task_ids is not None and not disputed_task_ids:
        return []

    projects = list(Project.objects.filter(id__in=project_ids).order_by("name", "id"))
    tasks_qs = Task.objects.filter(
        project_id__in=project_ids, id__in=selected_task_ids_
    ).order_by("created_at", "id")
    if disputed_task_ids is not None:
        tasks_qs = tasks_qs.filter(id__in=disputed_task_ids)

    tasks_by_project: dict[int, list[Task]] = {p.id: [] for p in projects}
    all_tasks: list[Task] = []
    for task in tasks_qs:
        bucket = tasks_by_project.get(task.project_id)
        if bucket is not None:
            bucket.append(task)
            all_tasks.append(task)

    extras = _task_row_extras(report, [task.id for task in all_tasks])

    blocks = []
    for project in projects:
        tasks = []
        total = 0
        for task in tasks_by_project.get(project.id, []):
            secs = seconds_by_task.get(task.id, 0)
            total += secs
            extra = extras.get(task.id) or {}
            files = extra.get("files") or []
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "tracked_seconds": secs,
                    "outcome": task.outcome or "",
                    "disputed": disputed_task_ids is not None,
                    "awaiting_client": task.awaiting_client_at is not None,
                    "files": files,
                    "file_name": files[0]["name"] if files else "",
                }
            )
        if not tasks and disputed_task_ids is not None:
            continue
        blocks.append(
            {
                "id": project.id,
                "name": project.name,
                "total_tracked_seconds": total,
                "tasks": tasks,
            }
        )
    return blocks


def report_detail_metrics(report: WorkReport) -> dict:
    """One-shot metrics for WorkReportSerializer (single TimeEntry scan)."""
    task_ids = selected_task_ids(report)
    project_ids = list(
        Task.objects.filter(id__in=task_ids)
        .order_by()
        .values_list("project_id", flat=True)
        .distinct()
    )
    disputed = _disputed_task_ids(report)
    seconds_by_task = _seconds_by_task(report, task_ids)
    projects_detail = _build_projects_detail(
        report, project_ids, task_ids, seconds_by_task, disputed
    )

    cache = getattr(report, "_prefetched_objects_cache", None) or {}
    if "projects" in cache:
        project_names = sorted(p.name for p in report.projects.all())
    elif project_ids:
        project_names = list(
            Project.objects.filter(id__in=project_ids)
            .order_by("name")
            .values_list("name", flat=True)
        )
    else:
        project_names = []

    from portals.deal_hours_overage import overage_hours_amount, overage_seconds

    task_seconds = sum(seconds_by_task.values())
    binding = getattr(report, "deal_binding", None)
    carried_hours = overage_hours_amount(
        getattr(binding, "hours_overage_applied", None) if binding else None
    )
    carried = overage_seconds(carried_hours)
    return {
        "project_ids": project_ids,
        "project_names": project_names,
        "projects_detail": projects_detail,
        "task_tracked_seconds": task_seconds,
        "carried_overage_seconds": carried,
        "total_tracked_seconds": task_seconds + carried,
        "selected_task_ids": task_ids,
        "tasks_count": len(task_ids),
    }


def report_projects_payload(report: WorkReport) -> list[dict]:
    """Projects → tasks with live hours + task.outcome (no per-report text)."""
    return report_detail_metrics(report)["projects_detail"]

def refresh_report(report: WorkReport) -> WorkReport:
    return (
        WorkReport.objects.select_related(
            "portal", "project", "project__portal", "created_by",
            "deal_binding", "deal_binding__client_portal",
        )
        .prefetch_related(
            "projects",
            "lines",
            "events__actor",
            "dispute_items__task",
        )
        .get(pk=report.pk)
    )


def publish_report_event(report: WorkReport, kind: str) -> None:
    portal_id = report_portal_id(report)
    if not portal_id:
        return
    publish_portal_event(
        portal_id,
        {
            "kind": kind,
            "report_id": report.id,
            "project_id": report.project_id,
            "status": report.status,
        },
    )


def _report_is_editable(report: WorkReport) -> bool:
    return report.status == WorkReport.Status.DRAFT and report.sent_at is None


def report_binding_for_task(task: Task) -> PortalDealBinding | None:
    billed_id = (
        TimeEntry.objects.filter(task=task, billed_deal_binding_id__isnull=False)
        .order_by("-billed_to_deal_at", "-id")
        .values_list("billed_deal_binding_id", flat=True)
        .first()
    )
    if billed_id:
        return PortalDealBinding.objects.filter(pk=billed_id).first()
    portal_id = task.project.portal_id
    return (
        PortalDealBinding.objects.filter(client_portal_id=portal_id, is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )


def _sync_report_projects(report: WorkReport, task_ids: list[int] | None = None) -> None:
    if task_ids is None:
        task_ids = selected_task_ids(report)
    project_ids = list(
        Task.objects.filter(id__in=task_ids)
        .order_by()
        .values_list("project_id", flat=True)
        .distinct()
    )
    report.projects.set(project_ids)
    report.project_id = project_ids[0] if project_ids else None
    report.save(update_fields=["project", "updated_at"])


def _reserve_task_on_report(report: WorkReport, task: Task) -> bool:
    if (
        WorkReportLine.objects.filter(task=task, is_reserved=True)
        .exclude(report=report)
        .exists()
    ):
        return False
    line, created = WorkReportLine.objects.get_or_create(
        report=report,
        task=task,
        defaults={"is_reserved": True},
    )
    if created:
        return True
    if line.is_reserved:
        return False
    line.is_reserved = True
    line.save(update_fields=["is_reserved", "updated_at"])
    return True


def attach_completed_task_to_report(task: Task) -> WorkReport | None:
    """Pin a just-completed task onto the deal report it belongs to."""
    if task.status != Task.Status.DONE:
        return None
    binding = report_binding_for_task(task)
    if not binding:
        return None
    report = ensure_report_for_binding(binding)
    if not _report_is_editable(report):
        return None
    try:
        with transaction.atomic():
            if not _reserve_task_on_report(report, task):
                return None
            _sync_report_projects(report)
    except IntegrityError:
        return None
    publish_report_event(report, "report_updated")
    return refresh_report(report)


def sync_draft_report_completed_tasks(report: WorkReport) -> WorkReport:
    """Backfill a draft with done tasks billed to this deal or just completed on it."""
    if not _report_is_editable(report):
        return report
    binding = getattr(report, "deal_binding", None)
    if not binding:
        return report
    portal_id = binding.client_portal_id
    reserved_elsewhere = set(
        WorkReportLine.objects.filter(is_reserved=True)
        .exclude(report=report)
        .values_list("task_id", flat=True)
    )
    wanted = set(
        TimeEntry.objects.filter(
            billed_deal_binding=binding,
            task__status=Task.Status.DONE,
            task__project__portal_id=portal_id,
        ).values_list("task_id", flat=True)
    )
    billed_elsewhere = set(
        TimeEntry.objects.filter(
            billed_deal_binding__isnull=False,
            task__project__portal_id=portal_id,
        )
        .exclude(billed_deal_binding=binding)
        .values_list("task_id", flat=True)
    )
    if binding.is_active:
        recent = Task.objects.filter(
            project__portal_id=portal_id,
            status=Task.Status.DONE,
        ).filter(Q(completed_at__gte=binding.created_at) | Q(completed_at__isnull=True))
        wanted |= set(recent.values_list("id", flat=True)) - billed_elsewhere
    wanted -= reserved_elsewhere
    if not wanted:
        return report

    added = False
    try:
        with transaction.atomic():
            for task in Task.objects.filter(id__in=wanted, status=Task.Status.DONE):
                if _reserve_task_on_report(report, task):
                    added = True
            if added:
                _sync_report_projects(report)
    except IntegrityError:
        return refresh_report(report)
    if added:
        publish_report_event(report, "report_updated")
    return refresh_report(report)


@transaction.atomic
def create_report(
    portal,
    project_ids: list[int],
    actor: BitrixUser | None,
) -> WorkReport:
    raise ValidationError({"detail": "Отчёты создаются автоматически для CRM-сделок."})


@transaction.atomic
def set_report_tasks(report: WorkReport, task_ids: list[int]) -> WorkReport:
    if report.status != WorkReport.Status.DRAFT or report.sent_at is not None:
        raise ValidationError(
            {"detail": "Состав задач можно менять только до первой отправки черновика."}
        )
    wanted = set(task_ids)
    tasks = list(
        Task.objects.filter(
            id__in=wanted,
            project__portal_id=report_portal_id(report),
        )
    )
    valid = {task.id for task in tasks}
    if valid != wanted:
        raise ValidationError({"task_ids": "Некоторые задачи не принадлежат клиенту отчёта."})
    if any(task.status != Task.Status.DONE for task in tasks):
        raise ValidationError({"task_ids": "В отчёт можно добавить только завершённые задачи."})

    conflicts = list(
        WorkReportLine.objects.filter(task_id__in=wanted, is_reserved=True)
        .exclude(report=report)
        .values("task_id", "report_id")
    )
    if conflicts:
        raise ValidationError(
            {
                "task_ids": "Некоторые задачи уже выбраны в другом отчёте.",
                "conflicts": conflicts,
            }
        )

    existing = {line.task_id: line for line in report.lines.select_for_update()}
    for task_id, line in existing.items():
        reserved = task_id in wanted
        if line.is_reserved != reserved:
            line.is_reserved = reserved
            line.save(update_fields=["is_reserved", "updated_at"])
    for task_id in wanted - set(existing):
        WorkReportLine.objects.create(report=report, task_id=task_id, is_reserved=True)

    _sync_report_projects(report, list(wanted))
    return refresh_report(report)


def available_tasks_payload(report: WorkReport) -> dict:
    selected = set(selected_task_ids(report))
    reservations = {
        row["task_id"]: row["report_id"]
        for row in WorkReportLine.objects.filter(
            task__project__portal_id=report_portal_id(report),
            is_reserved=True,
        ).values("task_id", "report_id")
    }
    seconds = {
        row["task_id"]: int(row["total"] or 0)
        for row in TimeEntry.objects.filter(
            task__project__portal_id=report_portal_id(report)
        )
        .values("task_id")
        .annotate(total=Sum("duration_seconds"))
    }
    projects = []
    for project in Project.objects.filter(portal_id=report_portal_id(report)).order_by("name", "id"):
        tasks = []
        for task in project.tasks.all().order_by("created_at", "id"):
            if task.status != Task.Status.DONE and task.id not in selected:
                continue
            owner = reservations.get(task.id)
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "tracked_seconds": seconds.get(task.id, 0),
                    "selected": task.id in selected,
                    "unavailable": owner is not None and owner != report.id,
                    "unavailable_report_id": owner if owner != report.id else None,
                }
            )
        if tasks:
            projects.append({"id": project.id, "name": project.name, "tasks": tasks})
    return {"projects": projects}


def _legacy_create_report(
    portal,
    project_ids: list[int],
    actor: BitrixUser | None,
) -> WorkReport:
    if portal_has_active_report(portal.id):
        raise ValidationError(
            {"detail": "У клиента уже есть активный отчёт. Закройте или завершите его."}
        )
    if not project_ids:
        raise ValidationError({"project_ids": "Выберите хотя бы один проект."})

    projects = list(
        Project.objects.filter(portal=portal, id__in=project_ids).order_by("id")
    )
    found = {p.id for p in projects}
    missing = [pid for pid in project_ids if pid not in found]
    if missing:
        raise ValidationError({"project_ids": "Проекты не принадлежат этому клиенту."})

    report = WorkReport.objects.create(
        portal=portal,
        project=projects[0],
        status=WorkReport.Status.DRAFT,
        created_by=actor,
    )
    report.projects.set(projects)
    append_event(
        report,
        WorkReportEvent.Kind.CREATED,
        actor,
        payload={"project_ids": [p.id for p in projects]},
    )
    publish_report_event(report, "report_created")
    return refresh_report(report)


@transaction.atomic
def send_to_client(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status not in (WorkReport.Status.DRAFT,):
        raise ValidationError(
            {"detail": "Отправить клиенту можно только отчёт на рассмотрении руководителя."}
        )
    reserved_lines = report.lines.filter(is_reserved=True)
    if not reserved_lines.exists():
        raise ValidationError({"detail": "Закройте хотя бы одну задачу — она появится в отчёте."})
    if reserved_lines.exclude(task__status=Task.Status.DONE).exists():
        raise ValidationError({"detail": "В отчёт можно отправить только завершённые задачи."})
    binding = getattr(report, "deal_binding", None)
    if not binding or binding.paid_hours is None:
        raise ValidationError({"detail": "Нельзя отправить отчёт: в сделке не указан размер пакета."})
    closed_hours = (
        Decimal(report_detail_metrics(report)["total_tracked_seconds"] or 0) / Decimal(3600)
    ).quantize(Decimal("0.01"))
    leftover = (binding.paid_hours - closed_hours).quantize(Decimal("0.01"))
    if leftover > Decimal("0.02"):
        raise ValidationError(
            {
                "detail": (
                    "В отчёте ещё не весь пакет. "
                    f"Закройте задачи на {leftover} ч."
                )
            }
        )
    overage = (closed_hours - binding.paid_hours).quantize(Decimal("0.01"))
    if overage > Decimal("0.00"):
        from portals.deal_hours_overage import capture_hours_overage, portal_link_for_binding

        link = portal_link_for_binding(binding)
        capture_hours_overage(link=link, binding=binding, overage=overage)
        from portals.deal_hours_overage import apply_pending_hours_overage

        apply_pending_hours_overage(link)
    report.status = WorkReport.Status.PENDING_CLIENT
    report.sent_at = timezone.now()
    report.save(update_fields=["status", "sent_at", "updated_at"])
    append_event(report, WorkReportEvent.Kind.SENT, actor)
    publish_report_event(report, "report_sent")
    from portals.deal_stage_move import STAGE_REPORT_REVIEW, schedule_deal_stage_move

    schedule_deal_stage_move(
        report_portal_id(report), STAGE_REPORT_REVIEW, binding_id=report.deal_binding_id
    )
    return refresh_report(report)


@transaction.atomic
def accept_report(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status != WorkReport.Status.PENDING_CLIENT:
        raise ValidationError({"detail": "Согласовать можно только отчёт на согласовании."})
    report.status = WorkReport.Status.ACCEPTED
    report.accepted_at = timezone.now()
    report.client_comment = ""
    report.save(update_fields=["status", "accepted_at", "client_comment", "updated_at"])
    WorkReportDisputeItem.objects.filter(report=report).delete()
    append_event(report, WorkReportEvent.Kind.ACCEPTED, actor)
    publish_report_event(report, "report_accepted")
    from portals.deal_stage_move import STAGE_ACT_SIGNING, schedule_deal_stage_move

    schedule_deal_stage_move(
        report_portal_id(report), STAGE_ACT_SIGNING, binding_id=report.deal_binding_id
    )
    from portals.deal_hours_overage import apply_pending_hours_overage, portal_link_for_binding

    apply_pending_hours_overage(portal_link_for_binding(getattr(report, "deal_binding", None)))
    return refresh_report(report)


@transaction.atomic
def dispute_report(
    report: WorkReport,
    actor: BitrixUser | None,
    *,
    comment: str,
    task_ids: list[int],
    notes_by_task: dict[int, str] | None = None,
) -> WorkReport:
    if report.status != WorkReport.Status.PENDING_CLIENT:
        raise ValidationError(
            {"detail": "Связаться с менеджером можно только по отчёту на согласовании."}
        )
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError({"client_comment": "Напишите сообщение менеджеру."})
    if not task_ids:
        raise ValidationError({"task_ids": "Выберите хотя бы одну задачу."})

    project_task_ids = set(
        report.lines.filter(is_reserved=True, task_id__in=task_ids).values_list(
            "task_id", flat=True
        )
    )
    missing = [tid for tid in task_ids if tid not in project_task_ids]
    if missing:
        raise ValidationError({"task_ids": "Задачи не принадлежат проектам отчёта."})

    notes_by_task = notes_by_task or {}
    WorkReportDisputeItem.objects.filter(report=report).delete()
    WorkReportDisputeItem.objects.bulk_create(
        [
            WorkReportDisputeItem(
                report=report,
                task_id=tid,
                note=(notes_by_task.get(tid) or "").strip(),
            )
            for tid in task_ids
        ]
    )
    report.status = WorkReport.Status.DISPUTED
    report.client_comment = comment
    report.save(update_fields=["status", "client_comment", "updated_at"])
    append_event(
        report,
        WorkReportEvent.Kind.DISPUTED,
        actor,
        payload={"task_ids": task_ids, "comment": comment},
    )
    publish_report_event(report, "report_disputed")
    # Deal stays on «Согласование отчёта» — no stage move.
    return refresh_report(report)


@transaction.atomic
def reopen_to_draft(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status != WorkReport.Status.DISPUTED:
        raise ValidationError(
            {
                "detail": "Вернуть на рассмотрение руководителя можно только отчёт "
                "после обращения к менеджеру."
            }
        )
    report.status = WorkReport.Status.DRAFT
    report.save(update_fields=["status", "updated_at"])
    append_event(report, WorkReportEvent.Kind.REOPENED, actor)
    publish_report_event(report, "report_reopened")
    return refresh_report(report)


@transaction.atomic
def dismiss_dispute(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    """
    Agency closes a «Связь с менеджером» thread without reopening the report.
    Frees the active-report slot; history stays in «Все».
    """
    if report.status != WorkReport.Status.DISPUTED:
        raise ValidationError(
            {
                "detail": "Снять с контроля можно только отчёт "
                "в статусе «Связь с менеджером»."
            }
        )
    report.status = WorkReport.Status.DISMISSED
    report.save(update_fields=["status", "updated_at"])
    report.lines.filter(is_reserved=True).update(is_reserved=False)
    report.projects.clear()
    append_event(report, WorkReportEvent.Kind.DISMISSED, actor)
    publish_report_event(report, "report_dismissed")
    return refresh_report(report)


@transaction.atomic
def mark_paid(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status != WorkReport.Status.ACCEPTED:
        raise ValidationError({"detail": "Отметить оплаченным можно только согласованный отчёт."})
    report.status = WorkReport.Status.PAID
    report.paid_at = timezone.now()
    report.save(update_fields=["status", "paid_at", "updated_at"])
    append_event(report, WorkReportEvent.Kind.PAID, actor)
    publish_report_event(report, "report_paid")
    return refresh_report(report)


def require_agency(user) -> None:
    if not getattr(user, "is_agency", False):
        raise PermissionDenied("Действие доступно только агентству")


def require_client(user) -> None:
    if getattr(user, "is_agency", False):
        raise PermissionDenied("Действие доступно только клиенту")
