"""Work report lifecycle helpers (agency → client agree/dispute)."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from portals.models import BitrixUser, PortalDealBinding

from .models import (
    Task,
    TimeEntry,
    WorkReport,
    WorkReportDisputeItem,
    WorkReportEvent,
    WorkReportLine,
)
from .realtime import publish_portal_event

ACTIVE = WorkReport.ACTIVE_STATUSES
REPORT_LINE_ATTACH_SALT = "board.report_line_attachment.download.v1"


def sign_report_line_attachment_id(att_id: int) -> str:
    from django.core import signing

    return signing.dumps(int(att_id), salt=REPORT_LINE_ATTACH_SALT)


def project_has_active_report(project_id: int, *, exclude_id: int | None = None) -> bool:
    qs = WorkReport.objects.filter(project_id=project_id, status__in=ACTIVE)
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


def ensure_report_lines(report: WorkReport) -> None:
    """Create missing line rows for every project task (idempotent)."""
    task_ids = list(
        Task.objects.filter(project_id=report.project_id).values_list("id", flat=True)
    )
    if not task_ids:
        return
    existing = set(
        WorkReportLine.objects.filter(report=report).values_list("task_id", flat=True)
    )
    missing = [tid for tid in task_ids if tid not in existing]
    if missing:
        WorkReportLine.objects.bulk_create(
            [WorkReportLine(report=report, task_id=tid) for tid in missing]
        )


def report_task_rows(report: WorkReport) -> list[dict]:
    """Live tasks merged with report lines + attachments."""
    ensure_report_lines(report)
    seconds_by_task = {
        row["task_id"]: int(row["total"] or 0)
        for row in TimeEntry.objects.filter(task__project_id=report.project_id)
        .values("task_id")
        .annotate(total=Sum("duration_seconds"))
    }
    lines = {
        line.task_id: line
        for line in WorkReportLine.objects.filter(report=report).prefetch_related(
            "attachments"
        )
    }
    rows = []
    for task in Task.objects.filter(project_id=report.project_id).order_by(
        "created_at", "id"
    ):
        line = lines.get(task.id)
        attachments = []
        if line:
            for att in line.attachments.all():
                attachments.append(
                    {
                        "id": att.id,
                        "url": (
                            f"/api/reports/line-attachments/{att.id}/download/"
                            f"?t={sign_report_line_attachment_id(att.id)}"
                            if att.file
                            else None
                        ),
                        "original_name": att.original_name or "",
                        "created_at": att.created_at.isoformat(),
                    }
                )
        rows.append(
            {
                "id": task.id,
                "line_id": line.id if line else None,
                "title": task.title,
                "status": task.status,
                "tracked_seconds": seconds_by_task.get(task.id, 0),
                "work_done": line.work_done if line else "",
                "attachments": attachments,
            }
        )
    return rows


def live_task_rows(project_id: int) -> list[dict]:
    """Current tasks + tracked seconds for a project (no snapshot)."""
    seconds_by_task = {
        row["task_id"]: int(row["total"] or 0)
        for row in TimeEntry.objects.filter(task__project_id=project_id)
        .values("task_id")
        .annotate(total=Sum("duration_seconds"))
    }
    rows = []
    for task in Task.objects.filter(project_id=project_id).order_by("created_at", "id"):
        rows.append(
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "tracked_seconds": seconds_by_task.get(task.id, 0),
            }
        )
    return rows


def live_total_seconds(project_id: int) -> int:
    total = (
        TimeEntry.objects.filter(task__project_id=project_id).aggregate(
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


def refresh_report(report: WorkReport) -> WorkReport:
    return (
        WorkReport.objects.select_related("project", "project__portal", "created_by")
        .prefetch_related(
            "events__actor",
            "dispute_items__task",
            "lines__attachments",
            "lines__task",
        )
        .get(pk=report.pk)
    )


def publish_report_event(report: WorkReport, kind: str) -> None:
    publish_portal_event(
        report.project.portal_id,
        {
            "kind": kind,
            "report_id": report.id,
            "project_id": report.project_id,
            "status": report.status,
        },
    )


def require_draft_editable(report: WorkReport) -> None:
    if report.status != WorkReport.Status.DRAFT:
        raise ValidationError(
            {"detail": "Редактировать описание и файлы можно только в черновике."}
        )


@transaction.atomic
def upsert_line_work_done(
    report: WorkReport, task_id: int, work_done: str
) -> WorkReportLine:
    require_draft_editable(report)
    if not Task.objects.filter(project_id=report.project_id, pk=task_id).exists():
        raise ValidationError({"task_id": "Задача не принадлежит этому проекту."})
    ensure_report_lines(report)
    line, _ = WorkReportLine.objects.get_or_create(report=report, task_id=task_id)
    line.work_done = (work_done or "").strip()
    line.save(update_fields=["work_done", "updated_at"])
    publish_report_event(report, "report_line_updated")
    return line


@transaction.atomic
def create_report(project, actor: BitrixUser | None) -> WorkReport:
    if project_has_active_report(project.id):
        raise ValidationError(
            {"detail": "У проекта уже есть активный отчёт. Закройте или завершите его."}
        )
    report = WorkReport.objects.create(
        project=project,
        status=WorkReport.Status.DRAFT,
        created_by=actor,
    )
    ensure_report_lines(report)
    append_event(report, WorkReportEvent.Kind.CREATED, actor)
    publish_report_event(report, "report_created")
    return refresh_report(report)

@transaction.atomic
def send_to_client(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status not in (WorkReport.Status.DRAFT,):
        raise ValidationError(
            {"detail": "Отправить клиенту можно только черновик."}
        )
    report.status = WorkReport.Status.PENDING_CLIENT
    report.sent_at = timezone.now()
    report.save(update_fields=["status", "sent_at", "updated_at"])
    append_event(report, WorkReportEvent.Kind.SENT, actor)
    publish_report_event(report, "report_sent")
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
        raise ValidationError({"detail": "Оспорить можно только отчёт на согласовании."})
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError({"client_comment": "Укажите комментарий к спору."})
    if not task_ids:
        raise ValidationError({"task_ids": "Выберите хотя бы одну задачу."})

    project_task_ids = set(
        Task.objects.filter(project_id=report.project_id, id__in=task_ids).values_list(
            "id", flat=True
        )
    )
    missing = [tid for tid in task_ids if tid not in project_task_ids]
    if missing:
        raise ValidationError({"task_ids": "Задачи не принадлежат этому проекту."})

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
    return refresh_report(report)


@transaction.atomic
def reopen_to_draft(report: WorkReport, actor: BitrixUser | None) -> WorkReport:
    if report.status != WorkReport.Status.DISPUTED:
        raise ValidationError({"detail": "Вернуть в черновик можно только оспоренный отчёт."})
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
