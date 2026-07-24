"""Work report lifecycle helpers (agency → client agree / contact manager)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from portals.models import BitrixUser, PortalDealBinding

from .models import Project, Task, TimeEntry, WorkReport, WorkReportDisputeItem, WorkReportEvent
from .realtime import publish_portal_event

ACTIVE = WorkReport.ACTIVE_STATUSES

# List filters for the reports hub UI.
BUCKET_STATUSES = {
    "current": (
        WorkReport.Status.DRAFT,
        WorkReport.Status.DISPUTED,
        WorkReport.Status.ACCEPTED,
    ),
    "review": (WorkReport.Status.PENDING_CLIENT,),
    "paid": (WorkReport.Status.PAID,),
}


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
    }


def report_portal_id(report: WorkReport) -> int | None:
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


def _disputed_task_ids(report: WorkReport) -> set[int] | None:
    if report.status != WorkReport.Status.DISPUTED:
        return None
    cache = getattr(report, "_prefetched_objects_cache", None) or {}
    if "dispute_items" in cache:
        return {item.task_id for item in report.dispute_items.all()}
    return set(report.dispute_items.values_list("task_id", flat=True))


def _seconds_by_task(project_ids: list[int]) -> dict[int, int]:
    if not project_ids:
        return {}
    return {
        row["task_id"]: int(row["total"] or 0)
        for row in TimeEntry.objects.filter(task__project_id__in=project_ids)
        .values("task_id")
        .annotate(total=Sum("duration_seconds"))
    }


def _build_projects_detail(
    project_ids: list[int],
    seconds_by_task: dict[int, int],
    disputed_task_ids: set[int] | None,
) -> list[dict]:
    if not project_ids:
        return []
    if disputed_task_ids is not None and not disputed_task_ids:
        return []

    projects = list(Project.objects.filter(id__in=project_ids).order_by("name", "id"))
    tasks_qs = Task.objects.filter(project_id__in=project_ids).order_by("created_at", "id")
    if disputed_task_ids is not None:
        tasks_qs = tasks_qs.filter(id__in=disputed_task_ids)

    tasks_by_project: dict[int, list[Task]] = {p.id: [] for p in projects}
    for task in tasks_qs:
        bucket = tasks_by_project.get(task.project_id)
        if bucket is not None:
            bucket.append(task)

    blocks = []
    for project in projects:
        tasks = []
        total = 0
        for task in tasks_by_project.get(project.id, []):
            secs = seconds_by_task.get(task.id, 0)
            total += secs
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "tracked_seconds": secs,
                    "outcome": task.outcome or "",
                    "disputed": disputed_task_ids is not None,
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
    project_ids = report_project_ids(report)
    disputed = _disputed_task_ids(report)
    seconds_by_task = _seconds_by_task(project_ids)
    projects_detail = _build_projects_detail(project_ids, seconds_by_task, disputed)

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

    return {
        "project_ids": project_ids,
        "project_names": project_names,
        "projects_detail": projects_detail,
        "total_tracked_seconds": sum(seconds_by_task.values()),
    }


def report_projects_payload(report: WorkReport) -> list[dict]:
    """Projects → tasks with live hours + task.outcome (no per-report text)."""
    return report_detail_metrics(report)["projects_detail"]

def refresh_report(report: WorkReport) -> WorkReport:
    return (
        WorkReport.objects.select_related("portal", "project", "project__portal", "created_by")
        .prefetch_related(
            "projects",
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


@transaction.atomic
def create_report(
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
    report.status = WorkReport.Status.PENDING_CLIENT
    report.sent_at = timezone.now()
    report.save(update_fields=["status", "sent_at", "updated_at"])
    append_event(report, WorkReportEvent.Kind.SENT, actor)
    publish_report_event(report, "report_sent")
    from portals.deal_stage_move import STAGE_REPORT_REVIEW, schedule_deal_stage_move

    schedule_deal_stage_move(report_portal_id(report), STAGE_REPORT_REVIEW)
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

    schedule_deal_stage_move(report_portal_id(report), STAGE_ACT_SIGNING)
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

    project_ids = report_project_ids(report)
    project_task_ids = set(
        Task.objects.filter(project_id__in=project_ids, id__in=task_ids).values_list(
            "id", flat=True
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
