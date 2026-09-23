import logging
import mimetypes
import sys
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from portals.models import Portal, PortalLink
from portals.permissions import IsPortalAuthenticated, can_access_client_portal

from .events import append_task_change_events
from .models import (
    Attachment,
    BacklogItem,
    Comment,
    Project,
    ProjectMeeting,
    SupportTicket,
    SupportTicketMessage,
    Task,
    TimeEntry,
    WorkReport,
)
from .naming import display_attachment_name
from .serializers import (
    ATTACHMENT_SIGN_SALT,
    AttachmentSerializer,
    BacklogItemSerializer,
    CommentSerializer,
    ProjectSerializer,
    ProjectMeetingSerializer,
    SupportTicketCreateSerializer,
    SupportTicketListSerializer,
    SupportTicketMessageCreateSerializer,
    SupportTicketMessageSerializer,
    SupportTicketSerializer,
    TaskListSerializer,
    TaskSerializer,
    WorkReportDisputeInputSerializer,
    WorkReportListSerializer,
    WorkReportSerializer,
    serialize_thread_items,
)
from .tasks import (
    pull_task_from_bitrix,
    sync_comment_to_bitrix,
    sync_project_to_bitrix,
    sync_task_to_bitrix,
)
from .timeutils import stop_time_entry
from .realtime import publish_portal_event, publish_task_event

logger = logging.getLogger(__name__)


def _defer_bitrix_job(task_fn, *args) -> None:
    """Run Bitrix sync off the request thread.

    Eager Celery has no worker, so calling the task inline holds the HTTP
    response for the whole Bitrix round trip. Tests still run inline.
    """
    if not settings.CELERY_TASK_ALWAYS_EAGER:
        task_fn.delay(*args)
        return
    if "test" in sys.argv:
        task_fn(*args)
        return

    import threading

    from django.db import close_old_connections

    def _worker() -> None:
        try:
            close_old_connections()
            task_fn(*args)
        except Exception:
            logger.exception(
                "background bitrix job failed %s",
                getattr(task_fn, "name", task_fn),
            )
        finally:
            close_old_connections()

    threading.Thread(target=_worker, daemon=True).start()


def enqueue_bitrix_sync(task_id: int) -> None:
    if not settings.BITRIX_AGENCY_TASK_SYNC:
        return
    _defer_bitrix_job(sync_task_to_bitrix, task_id)


def enqueue_project_sync(project_id: int) -> None:
    if not settings.BITRIX_AGENCY_TASK_SYNC:
        return
    _defer_bitrix_job(sync_project_to_bitrix, project_id)


def enqueue_comment_sync(comment_id: int) -> None:
    if not settings.BITRIX_AGENCY_TASK_SYNC:
        return
    _defer_bitrix_job(sync_comment_to_bitrix, comment_id)


def enqueue_task_pull(
    task_id: int,
    *,
    include_status: bool = True,
    include_comments: bool = True,
    include_files: bool = False,
) -> None:
    if not settings.BITRIX_AGENCY_TASK_SYNC:
        return
    # In eager/dev mode there is no worker; never turn an interactive GET into
    # a 30-second Bitrix call. Webhooks and the next normal sync still catch up.
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return
    pull_task_from_bitrix.apply_async(
        args=[task_id],
        kwargs={
            "include_status": include_status,
            "include_comments": include_comments,
            "include_files": include_files,
        },
        expires=90,
    )


def accessible_portal_ids(user):
    if user.is_agency:
        linked = list(
            PortalLink.objects.filter(agency_portal=user.portal).values_list(
                "client_portal_id", flat=True
            )
        )
        return linked + [user.portal.id]
    return [user.portal.id]


class ActivityFeedView(APIView):
    """Aggregated recent actions for a client portal."""

    permission_classes = [IsPortalAuthenticated]

    def get(self, request):
        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        try:
            portal = Portal.objects.get(pk=portal_id)
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")

        events: list[dict] = []

        for project in Project.objects.filter(portal=portal).order_by("-created_at")[:20]:
            events.append(
                {
                    "id": f"project-{project.id}",
                    "type": "project_created",
                    "title": "Проект создан",
                    "subtitle": "Добавлен новый модуль работ",
                    "project_name": project.name,
                    "task_title": None,
                    "at": project.created_at.isoformat(),
                    "project_id": project.id,
                    "task_id": None,
                }
            )

        for task in (
            Task.objects.filter(project__portal=portal)
            .select_related("project")
            .order_by("-created_at")[:30]
        ):
            events.append(
                {
                    "id": f"task-created-{task.id}",
                    "type": "task_created",
                    "title": "Добавлена задача",
                    "subtitle": None,
                    "project_name": task.project.name,
                    "task_title": task.title,
                    "at": task.created_at.isoformat(),
                    "project_id": task.project_id,
                    "task_id": task.id,
                }
            )
            if task.updated_at and task.updated_at > task.created_at:
                status_labels = {
                    "todo": "Ждёт выполнения",
                    "in_progress": "Выполняется",
                    "done": "Завершена",
                }
                status = status_labels.get(task.status, task.status)
                events.append(
                    {
                        "id": f"task-updated-{task.id}-{int(task.updated_at.timestamp())}",
                        "type": "task_updated",
                        "title": f"Статус изменён на «{status}»",
                        "subtitle": None,
                        "project_name": task.project.name,
                        "task_title": task.title,
                        "at": task.updated_at.isoformat(),
                        "project_id": task.project_id,
                        "task_id": task.id,
                    }
                )

        for comment in (
            Comment.objects.filter(task__project__portal=portal)
            .select_related("task", "task__project", "author")
            .order_by("-created_at")[:30]
        ):
            author = comment.author_name or (
                comment.author.display_name if comment.author else "Участник"
            )
            excerpt = (comment.text or "").strip().replace("\n", " ")
            if len(excerpt) > 100:
                excerpt = excerpt[:97] + "…"
            events.append(
                {
                    "id": f"comment-{comment.id}",
                    "type": "comment",
                    "title": f"Комментарий от {author}",
                    "subtitle": excerpt or None,
                    "project_name": comment.task.project.name,
                    "task_title": comment.task.title,
                    "at": comment.created_at.isoformat(),
                    "project_id": comment.task.project_id,
                    "task_id": comment.task_id,
                }
            )

        for attachment in (
            Attachment.objects.filter(task__project__portal=portal)
            .select_related("task", "task__project")
            .order_by("-created_at")[:20]
        ):
            if not attachment.task_id:
                continue
            file_name = display_attachment_name(attachment) or "Файл"
            events.append(
                {
                    "id": f"file-{attachment.id}",
                    "type": "attachment",
                    "title": "Загружен файл",
                    "subtitle": file_name,
                    "project_name": attachment.task.project.name,
                    "task_title": attachment.task.title,
                    "at": attachment.created_at.isoformat(),
                    "project_id": attachment.task.project_id,
                    "task_id": attachment.task_id,
                }
            )

        events.sort(key=lambda e: e["at"], reverse=True)
        # Deduplicate near-identical task create+update at same second preference: keep both but cap
        return Response(events[:40])


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsPortalAuthenticated]
    filterset_fields = ["portal", "is_active"]
    search_fields = ["name", "description"]

    def get_queryset(self):
        from django.db.models import (
            Case,
            Count,
            DateTimeField,
            Exists,
            F,
            IntegerField,
            OuterRef,
            Q,
            Subquery,
            Sum,
            Value,
            When,
        )
        from django.db.models.functions import Coalesce

        ids = accessible_portal_ids(self.request.user)
        active_work = Task.objects.filter(
            project_id=OuterRef("pk"),
            status=Task.Status.IN_PROGRESS,
        )
        open_tasks = Task.objects.filter(project_id=OuterRef("pk")).exclude(
            status=Task.Status.DONE
        )
        nearest_due = (
            Task.objects.filter(project_id=OuterRef("pk"))
            .exclude(status=Task.Status.DONE)
            .exclude(due_date=None)
            .order_by("due_date")
            .values("due_date")[:1]
        )
        latest_due = (
            Task.objects.filter(project_id=OuterRef("pk"))
            .exclude(due_date=None)
            .order_by("-due_date")
            .values("due_date")[:1]
        )
        latest_completed = (
            Task.objects.filter(project_id=OuterRef("pk"))
            .exclude(completed_at=None)
            .order_by("-completed_at")
            .values("completed_at")[:1]
        )
        tracked = (
            TimeEntry.objects.filter(
                task__project_id=OuterRef("pk"),
                ended_at__isnull=False,
            )
            .order_by()
            .values("task__project_id")
            .annotate(total=Sum("duration_seconds"))
            .values("total")[:1]
        )
        return (
            Project.objects.filter(portal_id__in=ids)
            .select_related("portal")
            .annotate(
                _tasks_count=Count("tasks", distinct=True),
                _done_count=Count(
                    "tasks",
                    filter=Q(tasks__status=Task.Status.DONE),
                    distinct=True,
                ),
                _has_active_work=Exists(active_work),
                _due_date=Coalesce(
                    Subquery(nearest_due, output_field=DateTimeField()),
                    Subquery(latest_due, output_field=DateTimeField()),
                ),
                _tracked_seconds=Coalesce(
                    Subquery(tracked, output_field=IntegerField()),
                    0,
                ),
                _completed_at=Case(
                    When(
                        Exists(open_tasks),
                        then=Value(None, output_field=DateTimeField()),
                    ),
                    default=Subquery(
                        latest_completed, output_field=DateTimeField()
                    ),
                    output_field=DateTimeField(),
                ),
            )
            .order_by(
                Case(
                    When(
                        _tasks_count__gt=0,
                        _tasks_count=F("_done_count"),
                        then=1,
                    ),
                    default=0,
                    output_field=IntegerField(),
                ),
                "name",
                "id",
            )
        )

    def filter_queryset(self, queryset):
        from django.db.models import F

        qs = super().filter_queryset(queryset)
        complete = (self.request.query_params.get("complete") or "").strip().lower()
        if complete in ("1", "true", "yes", "done"):
            return qs.filter(_tasks_count__gt=0, _tasks_count=F("_done_count"))
        if complete in ("0", "false", "no", "open"):
            return qs.exclude(_tasks_count__gt=0, _tasks_count=F("_done_count"))
        return qs

    @action(detail=False, methods=["get"], url_path="counts")
    def counts(self, request):
        from django.db.models import F

        qs = super().filter_queryset(self.get_queryset())
        all_n = qs.count()
        done_n = qs.filter(_tasks_count__gt=0, _tasks_count=F("_done_count")).count()
        return Response(
            {
                "all": all_n,
                "done": done_n,
                "open": max(0, all_n - done_n),
            }
        )

    def list(self, request, *args, **kwargs):
        # Soft realtime / first open: pull parent tasks from Bitrix company project
        if (
            settings.BITRIX_AGENCY_TASK_SYNC
            and request.query_params.get("pull") in ("1", "true", "yes")
        ):
            portal_id = request.query_params.get("portal")
            if portal_id:
                try:
                    from board.project_sync import pull_projects_from_bitrix

                    portal = Portal.objects.filter(pk=portal_id).first()
                    if portal and can_access_client_portal(request.user, portal):
                        pull_projects_from_bitrix(portal)
                except Exception:
                    logger.exception("Bitrix project pull failed for portal %s", portal_id)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not request.user.is_agency:
            raise PermissionDenied("Создавать проекты может только агентство")
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        if not self.request.user.is_agency:
            raise PermissionDenied("Создавать проекты может только агентство")
        portal = serializer.validated_data["portal"]
        if not can_access_client_portal(self.request.user, portal):
            raise PermissionDenied("No access to this portal")
        project = serializer.save()
        enqueue_project_sync(project.id)
        publish_portal_event(project.portal_id, {"kind": "project_create", "project_id": project.id})
        project.refresh_from_db()
        serializer.instance = project

    def perform_update(self, serializer):
        project = self.get_object()
        if not can_access_client_portal(self.request.user, project.portal):
            raise PermissionDenied("No access to this portal")
        if self.request.user.is_client:
            raise PermissionDenied("Клиент не может изменять проекты")
        project = serializer.save()
        enqueue_project_sync(project.id)
        project.refresh_from_db()
        serializer.instance = project

    def perform_destroy(self, instance):
        if not can_access_client_portal(self.request.user, instance.portal):
            raise PermissionDenied("No access to this portal")
        if self.request.user.is_client:
            raise PermissionDenied("Клиент не может удалять проекты")
        from board.deletion import project_is_app_deletable
        from portals.bitrix import BitrixClient

        if not project_is_app_deletable(instance):
            raise ValidationError(
                {"detail": "Можно удалить только пустой проект без задач"}
            )
        bitrix_id = (instance.bitrix_task_id or "").strip()
        portal_id = instance.portal_id
        project_id = instance.id
        agency = (
            PortalLink.objects.filter(client_portal_id=portal_id)
            .select_related("agency_portal")
            .first()
        )
        agency_portal = agency.agency_portal if agency else None
        instance.delete()
        publish_portal_event(
            portal_id,
            {
                "kind": "ontaskdelete",
                "deleted": "project",
                "project_id": project_id,
            },
        )
        if bitrix_id and agency_portal and agency_portal.access_token:
            try:
                BitrixClient(agency_portal).delete_task(bitrix_id)
            except Exception:
                logger.exception(
                    "Bitrix project delete failed project=%s bitrix=%s",
                    project_id,
                    bitrix_id,
                )


class ProjectMeetingViewSet(viewsets.ModelViewSet):
    """Shared project calendar. A client and an agency can both schedule a meeting."""

    serializer_class = ProjectMeetingSerializer
    permission_classes = [IsPortalAuthenticated]
    filterset_fields = ["project", "format"]
    search_fields = ["title", "notes", "location"]

    def get_queryset(self):
        ids = accessible_portal_ids(self.request.user)
        return ProjectMeeting.objects.filter(project__portal_id__in=ids).select_related(
            "project", "created_by", "created_by__portal"
        )

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not can_access_client_portal(self.request.user, project.portal):
            raise PermissionDenied("No access to this project")
        serializer.save(created_by=self.request.user.bitrix_user)

    @action(detail=False, methods=["get"])
    def availability(self, request):
        from .due_dates import portal_zone
        from .meeting_slots import (
            DEFAULT_MEETING_MINUTES,
            WORKDAY_END_HOUR,
            WORKDAY_START_HOUR,
            has_meeting_conflict,
        )

        project = get_object_or_404(Project.objects.select_related("portal"), pk=request.query_params.get("project"))
        if not can_access_client_portal(request.user, project.portal):
            raise PermissionDenied("No access to this project")
        try:
            requested_date = date.fromisoformat(str(request.query_params.get("date") or ""))
        except ValueError as exc:
            raise ValidationError({"date": "Укажите дату в формате YYYY-MM-DD"}) from exc

        zone = portal_zone(project.portal)
        now = timezone.now()
        is_workday = requested_date.weekday() < 5
        exclude_raw = request.query_params.get("exclude")
        try:
            exclude_meeting_id = int(exclude_raw) if exclude_raw else None
        except (TypeError, ValueError):
            exclude_meeting_id = None
        slots = []
        cursor = datetime.combine(requested_date, datetime_time(WORKDAY_START_HOUR, 0))
        workday_end = datetime.combine(requested_date, datetime_time(WORKDAY_END_HOUR, 0))
        while cursor + timedelta(minutes=DEFAULT_MEETING_MINUTES) <= workday_end:
            starts_at = timezone.make_aware(cursor, zone)
            is_past = starts_at <= now
            is_occupied = False if is_past or not is_workday else has_meeting_conflict(
                project.portal_id,
                starts_at,
                DEFAULT_MEETING_MINUTES,
                exclude_meeting_id=exclude_meeting_id,
            )
            slots.append({
                "starts_at": starts_at.isoformat(),
                "label": cursor.strftime("%H:%M"),
                "available": is_workday and not is_past and not is_occupied,
                "reason": "weekend" if not is_workday else "past" if is_past else "occupied" if is_occupied else "",
            })
            cursor += timedelta(minutes=DEFAULT_MEETING_MINUTES)

        return Response({
            "date": requested_date.isoformat(),
            "timezone": getattr(zone, "key", str(zone)),
            "duration_minutes": DEFAULT_MEETING_MINUTES,
            "slots": slots,
        })

    def perform_update(self, serializer):
        meeting = self.get_object()
        if not can_access_client_portal(self.request.user, meeting.project.portal):
            raise PermissionDenied("No access")
        serializer.save()

    def perform_destroy(self, instance):
        if self.request.user.is_agency or instance.created_by_id == self.request.user.bitrix_user.id:
            instance.delete()
            return
        raise PermissionDenied("Удалить встречу может её организатор или агентство")


def default_task_board_ordering():
    """Board order shared by the list view (and its tests).

    Active before done; important floats to the top within each group; soonest
    deadline first; newest as the final tie-breaker (also stabilises the
    pagination cursor so pages line up with what the UI renders).
    """
    from django.db.models import Case, F, IntegerField, When

    return (
        Case(
            When(status=Task.Status.DONE, then=1),
            default=0,
            output_field=IntegerField(),
        ),
        Case(
            When(is_important=True, then=0),
            default=1,
            output_field=IntegerField(),
        ),
        F("due_date").asc(nulls_last=True),
        "-created_at",
    )


def set_task_working(task, author):
    """Mark live presence on this task. Other started tasks stay in work."""
    from django.utils import timezone

    fields = ["working_by", "updated_at"]
    task.working_by = author
    if task.working_started_at is None:
        task.working_started_at = timezone.now()
        fields.append("working_started_at")
    task.save(update_fields=fields)


def clear_task_working(task):
    """Drop live presence on this task if it was set."""
    if task.working_started_at is None and task.working_by_id is None:
        return False
    task.working_by = None
    task.working_started_at = None
    task.save(update_fields=["working_by", "working_started_at", "updated_at"])
    return True


class TaskViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPortalAuthenticated]
    filterset_fields = ["project", "status", "sync_status"]
    search_fields = ["title", "description"]
    ordering_fields = ["due_date", "created_at", "status", "title"]

    def get_serializer_class(self):
        if self.action == "list":
            return TaskListSerializer
        return TaskSerializer

    def get_queryset(self):
        from django.db.models import Count, F, IntegerField, Q, Sum, Value
        from django.db.models.functions import Coalesce

        ids = accessible_portal_ids(self.request.user)
        qs = Task.objects.filter(project__portal_id__in=ids).select_related(
            "project",
            "project__portal",
            "created_by",
            "created_by__portal",
            "working_by",
        )
        if self.action == "list":
            qs = qs.annotate(
                _comments_count=Count("comments", distinct=True),
                _tracked_seconds=Coalesce(
                    Sum(
                        "time_entries__duration_seconds",
                        filter=Q(time_entries__ended_at__isnull=False),
                    ),
                    Value(0),
                    output_field=IntegerField(),
                ),
            )
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(project__portal_id=portal_id)
        if self.request.query_params.get("open") in ("1", "true", "yes"):
            qs = qs.exclude(status=Task.Status.DONE)
        working = self.request.query_params.get("working") in ("1", "true", "yes")
        if working:
            qs = qs.filter(status=Task.Status.IN_PROGRESS)
        attention = self.request.query_params.get("attention") in ("1", "true", "yes")
        if attention:
            qs = qs.filter(
                Q(status=Task.Status.DONE, outcome_seen_at__isnull=True)
                | Q(awaiting_client_at__isnull=False)
            )
        if self.action == "list" and not self.request.query_params.get("ordering"):
            if working:
                qs = qs.order_by(
                    F("working_started_at").desc(nulls_last=True),
                    "-updated_at",
                    "-id",
                )
            elif attention:
                qs = qs.order_by("-updated_at", "-id")
            else:
                qs = qs.order_by(*default_task_board_ordering())
        return qs

    def list(self, request, *args, **kwargs):
        # Queue Bitrix status catch-up without blocking the task board response.
        if request.query_params.get("pull") in ("1", "true", "yes"):
            project_id = request.query_params.get("project")
            if project_id:
                try:
                    task_ids = list(
                        self.filter_queryset(self.get_queryset())
                        .filter(project_id=project_id)
                        .exclude(agency_bitrix_task_id="")
                        .values_list("id", flat=True)[:20]
                    )
                    for task_id in task_ids:
                        enqueue_task_pull(
                            task_id,
                            include_status=True,
                            include_comments=False,
                            include_files=False,
                        )
                except Exception:
                    logger.exception("Bitrix task pull enqueue failed project=%s", project_id)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if (
            request.user.is_client
            and instance.status == Task.Status.DONE
            and instance.outcome_seen_at is None
        ):
            from django.utils import timezone

            instance.outcome_seen_at = timezone.now()
            instance.save(update_fields=["outcome_seen_at", "updated_at"])
            publish_task_event(instance, kind="task_update")
        # Return local DB immediately; slow Bitrix calls run in Celery.
        if request.query_params.get("pull") in ("1", "true", "yes"):
            enqueue_task_pull(instance.id)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not can_access_client_portal(self.request.user, project.portal):
            raise PermissionDenied("No access to this project")
        if self.request.user.is_client:
            raise PermissionDenied(
                "Клиент может только отправить задачу на согласование"
            )
        extras = {
            "created_by": self.request.user.bitrix_user,
            "sync_status": Task.SyncStatus.PENDING,
            # New tasks always start as waiting — status is chosen later via
            # Start / Pause / Complete, never at create time.
            "status": Task.Status.TODO,
        }
        task = serializer.save(**extras)
        enqueue_bitrix_sync(task.id)
        publish_task_event(task, kind="task_create")
        task.refresh_from_db()
        serializer.instance = task

    def perform_update(self, serializer):
        task = self.get_object()
        if not can_access_client_portal(self.request.user, task.project.portal):
            raise PermissionDenied("No access")
        old_status = task.status
        old_due = task.due_date
        requested_status = serializer.validated_data.get("status", old_status)
        if old_status == Task.Status.DONE and requested_status != Task.Status.DONE:
            raise ValidationError({"status": "Завершённую задачу нельзя возобновить"})
        if self.request.user.is_client:
            new_status = serializer.validated_data.get("status", old_status)
            if new_status != old_status:
                raise PermissionDenied("Only agency can change task status")
            if "due_date" in serializer.validated_data:
                new_due = serializer.validated_data.get("due_date")
                if new_due != old_due:
                    me = self.request.user.bitrix_user
                    if (
                        not task.created_by_id
                        or not me
                        or task.created_by_id != me.id
                    ):
                        raise PermissionDenied(
                            "Срок можно менять только у задач, которые вы создали"
                        )
        new_status = requested_status
        locally_paused = task.is_locally_paused
        if new_status != old_status:
            locally_paused = (
                old_status == Task.Status.IN_PROGRESS
                and new_status == Task.Status.TODO
            )
        task = serializer.save(
            sync_status=Task.SyncStatus.PENDING,
            is_locally_paused=locally_paused,
        )

        if old_status != task.status:
            author = self.request.user.bitrix_user
            if (
                task.status == Task.Status.IN_PROGRESS
                and self.request.user.is_agency
                and author
            ):
                set_task_working(task, author)
            else:
                clear_task_working(task)

        if self.request.user.is_agency and old_status != task.status:
            author = self.request.user.bitrix_user
            # Close leftover live timers (legacy). Bitrix учёта is pushed below /
            # on finalize so we do not race two elapseditem.add calls.
            if task.status in (Task.Status.TODO, Task.Status.DONE):
                try:
                    for running in task.time_entries.filter(ended_at__isnull=True):
                        stop_time_entry(running, sync_bitrix=False)
                except Exception:
                    logger.exception(
                        "stop leftover timers failed task=%s", task.id
                    )
            if task.status == Task.Status.DONE and old_status != Task.Status.DONE:
                try:
                    from board.completion import finalize_task_completion

                    finalize_task_completion(task, author=author)
                except Exception:
                    logger.exception("finalize_task_completion failed task=%s", task.id)
            elif task.status == Task.Status.TODO:
                try:
                    from board.timeutils import enqueue_unsynced_elapsed_for_task

                    enqueue_unsynced_elapsed_for_task(task)
                except Exception:
                    logger.exception("enqueue elapsed on pause failed task=%s", task.id)

        append_task_change_events(
            task=task,
            author=self.request.user.bitrix_user,
            old_status=old_status,
            old_due=old_due,
        )
        try:
            enqueue_bitrix_sync(task.id)
        except Exception:
            logger.exception("enqueue_bitrix_sync failed task=%s", task.id)
        publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        serializer.instance = task

    def perform_destroy(self, instance):
        if not can_access_client_portal(self.request.user, instance.project.portal):
            raise PermissionDenied("No access")
        if not self.request.user.is_agency:
            raise PermissionDenied("Удалять задачи может только агентство")
        from board.deletion import task_is_app_deletable
        from portals.bitrix import BitrixClient

        if not task_is_app_deletable(instance):
            raise ValidationError(
                {
                    "detail": (
                        "Можно удалить только незавершённую задачу без описания, "
                        "комментариев, файлов, срока и учёта времени"
                    )
                }
            )
        agency_bx = (instance.agency_bitrix_task_id or "").strip()
        client_bx = (instance.bitrix_task_id or "").strip()
        portal_id = instance.project.portal_id
        project_id = instance.project_id
        task_id = instance.id
        agency = (
            PortalLink.objects.filter(client_portal_id=portal_id)
            .select_related("agency_portal")
            .first()
        )
        agency_portal = agency.agency_portal if agency else None
        instance.delete()
        publish_portal_event(
            portal_id,
            {
                "kind": "ontaskdelete",
                "deleted": "task",
                "task_id": task_id,
                "project_id": project_id,
            },
        )
        if agency_bx and agency_portal and agency_portal.access_token:
            try:
                BitrixClient(agency_portal).delete_task(agency_bx)
            except Exception:
                logger.exception(
                    "Bitrix task delete failed task=%s bitrix=%s", task_id, agency_bx
                )
        # Legacy client Bitrix copy (rare)
        if client_bx:
            try:
                client_portal = Portal.objects.filter(pk=portal_id).first()
                if client_portal and client_portal.access_token:
                    BitrixClient(client_portal).delete_task(client_bx)
            except Exception:
                logger.exception(
                    "Bitrix client task delete failed task=%s bitrix=%s",
                    task_id,
                    client_bx,
                )

    @action(detail=True, methods=["post"], url_path="time")
    def add_time(self, request, pk=None):
        """Set spent time to absolute hours + minutes (replaces previous total)."""
        from board.timeutils import set_manual_time_entry

        if not request.user.is_agency:
            raise PermissionDenied("Only agency can track time")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")

        try:
            hours = int(request.data.get("hours") or 0)
            minutes = int(request.data.get("minutes") or 0)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Укажите часы и минуты целыми числами"},
                status=400,
            )
        if hours < 0 or minutes < 0:
            return Response({"detail": "Время не может быть отрицательным"}, status=400)
        if minutes >= 60:
            return Response({"detail": "Минуты должны быть от 0 до 59"}, status=400)
        seconds = hours * 3600 + minutes * 60
        if seconds > 7 * 24 * 3600:
            return Response({"detail": "Слишком большой интервал (макс. 7 суток)"}, status=400)

        note = request.data.get("note") or ""
        if not isinstance(note, str):
            note = str(note)

        set_manual_time_entry(
            task,
            author=request.user.bitrix_user,
            duration_seconds=seconds,
            note=note,
        )
        publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="working/start")
    def working_start(self, request, pk=None):
        """Mark live presence «работаю прямо сейчас» (agency only)."""
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can set working presence")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        if task.status == Task.Status.DONE:
            return Response(
                {"detail": "Нельзя отметить работу над завершённой задачей"},
                status=400,
            )
        author = request.user.bitrix_user
        if not author:
            return Response({"detail": "Пользователь не найден"}, status=400)

        set_task_working(task, author)
        publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="working/stop")
    def working_stop(self, request, pk=None):
        """Clear live presence «работаю прямо сейчас» (agency only)."""
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can clear working presence")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        if clear_task_working(task):
            publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="awaiting-client/start")
    def awaiting_client_start(self, request, pk=None):
        """Ask the client to reply — shows the task on their attention list."""
        from django.utils import timezone

        if not request.user.is_agency:
            raise PermissionDenied("Only agency can wait for a client reply")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        if task.status == Task.Status.DONE:
            return Response(
                {"detail": "Нельзя ждать ответ по завершённой задаче"},
                status=400,
            )
        if task.awaiting_client_at is None:
            task.awaiting_client_at = timezone.now()
            task.save(update_fields=["awaiting_client_at", "updated_at"])
            publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="awaiting-client/stop")
    def awaiting_client_stop(self, request, pk=None):
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can clear the wait")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        if task.awaiting_client_at is not None:
            task.awaiting_client_at = None
            task.save(update_fields=["awaiting_client_at", "updated_at"])
            publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="timer/start")
    def timer_start(self, request, pk=None):
        """Deprecated: live stopwatch removed. Use POST …/time/."""
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="timer/stop")
    def timer_stop(self, request, pk=None):
        """Deprecated: closes leftover open entries only."""
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can track time")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")
        for running in task.time_entries.filter(ended_at__isnull=True):
            stop_time_entry(running)
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="counts")
    def counts(self, request):
        """Per-status task totals for a project (independent of pagination).

        Lets the filter chips show true totals even when only the first page
        of tasks has been loaded on the client.
        """
        from django.db.models import Count, Q

        ids = accessible_portal_ids(request.user)
        qs = Task.objects.filter(project__portal_id__in=ids)
        project_id = request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        portal_id = request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(project__portal_id=portal_id)
        agg = qs.aggregate(
            all=Count("id"),
            todo=Count("id", filter=Q(status=Task.Status.TODO)),
            in_progress=Count("id", filter=Q(status=Task.Status.IN_PROGRESS)),
            done=Count("id", filter=Q(status=Task.Status.DONE)),
        )
        return Response(
            {
                "all": agg["all"] or 0,
                "todo": agg["todo"] or 0,
                "in_progress": agg["in_progress"] or 0,
                "done": agg["done"] or 0,
            }
        )

    @action(detail=True, methods=["get"], url_path="thread")
    def thread(self, request, pk=None):
        """Paginated chat thread (comments + standalone files) for a task.

        Modes:
          * default        → newest `limit` items (chronological order)
          * ?before=<iso>  → the `limit` items strictly older than the cursor
                             (for infinite scroll upward)
          * ?after=<iso>   → all items strictly newer than the cursor
                             (for live delta after new activity)
          * ?pull=1        → pull fresh comments from Bitrix first
          * ?files=1       → download missing Bitrix attachments (slow; safe alone
                             or combined with ?pull=1)
        """
        from django.utils.dateparse import parse_datetime

        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")

        want_pull = request.query_params.get("pull") in ("1", "true", "yes")
        want_files = request.query_params.get("files") in ("1", "true", "yes")
        if want_pull or want_files:
            enqueue_task_pull(
                task.id,
                include_status=want_pull,
                include_comments=want_pull,
                include_files=want_files,
            )

        try:
            limit = int(request.query_params.get("limit", 30))
        except (TypeError, ValueError):
            limit = 30
        limit = max(1, min(limit, 100))

        before = parse_datetime(request.query_params.get("before") or "")
        after = parse_datetime(request.query_params.get("after") or "")

        comments_qs = task.comments.select_related("author").prefetch_related("attachments")
        files_qs = task.attachments.filter(comment__isnull=True)

        # Live delta: everything strictly newer than the cursor, ascending.
        if after:
            comments = list(comments_qs.filter(created_at__gt=after))
            files = list(files_qs.filter(created_at__gt=after))
            items = serialize_thread_items(comments, files)
            items.sort(key=lambda x: x["at"])
            return Response({"items": items, "has_more": False})

        # History page: newest `limit`, optionally older than `before`.
        if before:
            comments_qs = comments_qs.filter(created_at__lt=before)
            files_qs = files_qs.filter(created_at__lt=before)

        # Over-fetch by 1 from each source so the merged newest-N is exact and
        # we can reliably tell whether older items remain.
        comments = list(comments_qs.order_by("-created_at")[: limit + 1])
        files = list(files_qs.order_by("-created_at")[: limit + 1])
        merged = serialize_thread_items(comments, files)
        merged.sort(key=lambda x: x["at"], reverse=True)  # newest first
        has_more = len(merged) > limit
        page = merged[:limit]
        page.sort(key=lambda x: x["at"])  # chronological for rendering
        return Response({"items": page, "has_more": has_more})


class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer
    permission_classes = [IsPortalAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]
    filterset_fields = ["task"]

    def get_queryset(self):
        ids = accessible_portal_ids(self.request.user)
        return Comment.objects.filter(task__project__portal_id__in=ids).select_related(
            "author", "task"
        )

    def perform_create(self, serializer):
        task = serializer.validated_data["task"]
        if not can_access_client_portal(self.request.user, task.project.portal):
            raise PermissionDenied("No access")
        author = self.request.user.bitrix_user
        comment = serializer.save(
            author=author, author_name=author.display_name, is_system=False
        )
        if (
            self.request.user.is_client
            and task.awaiting_client_at is not None
        ):
            task.awaiting_client_at = None
            task.save(update_fields=["awaiting_client_at", "updated_at"])
            publish_task_event(task, kind="task_update")
        enqueue_comment_sync(comment.id)
        publish_task_event(task, kind="comment")

    def perform_destroy(self, instance):
        user = self.request.user
        if user.is_agency or (instance.author_id == user.bitrix_user.id):
            instance.delete()
            return
        raise PermissionDenied("Cannot delete this comment")


class AttachmentViewSet(viewsets.ModelViewSet):
    serializer_class = AttachmentSerializer
    permission_classes = [IsPortalAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["get", "post", "delete", "head", "options"]
    filterset_fields = ["task", "comment"]

    def get_queryset(self):
        from django.db.models import Q

        ids = accessible_portal_ids(self.request.user)
        return Attachment.objects.filter(
            Q(task__project__portal_id__in=ids)
            | Q(comment__task__project__portal_id__in=ids)
        ).select_related("task", "comment")

    def perform_create(self, serializer):
        task = serializer.validated_data.get("task")
        comment = serializer.validated_data.get("comment")
        if comment and not task:
            task = comment.task
        if not task:
            raise PermissionDenied("task or comment required")
        if not can_access_client_portal(self.request.user, task.project.portal):
            raise PermissionDenied("No access")
        uploaded = self.request.FILES.get("file")
        from board.naming import client_filename

        name = client_filename(getattr(uploaded, "name", None) if uploaded else None)
        serializer.save(
            task=task,
            uploaded_by=self.request.user.bitrix_user,
            original_name=name,
        )
        attachment = serializer.instance
        # Belt-and-suspenders: never leave original_name as a storage basename
        if name and attachment.original_name != name:
            attachment.original_name = name
            attachment.save(update_fields=["original_name"])
        publish_task_event(task, kind="attachment")
        if not settings.BITRIX_AGENCY_TASK_SYNC:
            return
        from board.tasks import sync_attachment_to_bitrix

        # Never fail the HTTP upload if Bitrix/Celery is down
        try:
            _defer_bitrix_job(sync_attachment_to_bitrix, attachment.id)
        except Exception:
            logger.exception(
                "Failed to enqueue Bitrix sync for attachment %s", attachment.id
            )

    @action(
        detail=True,
        methods=["get"],
        url_path="download",
        permission_classes=[permissions.AllowAny],
        authentication_classes=[],
    )
    def download(self, request, pk=None):
        """Serve an uploaded file behind a signed, expiring capability token.

        Files are otherwise unreachable (nginx only exposes them via an
        `internal` X-Accel location). The token is minted by the serializer
        only for callers who already passed portal-access scoping, and it
        expires after ATTACHMENT_URL_TTL so leaked links go dead.
        """
        token = request.query_params.get("t", "")
        try:
            signed_id = signing.loads(
                token, salt=ATTACHMENT_SIGN_SALT, max_age=settings.ATTACHMENT_URL_TTL
            )
        except signing.BadSignature:
            raise PermissionDenied("Ссылка недействительна или устарела")
        if str(signed_id) != str(pk):
            raise PermissionDenied("Ссылка недействительна")

        attachment = Attachment.objects.filter(pk=pk).first()
        if not attachment or not attachment.file:
            raise Http404("Файл не найден")

        filename = attachment.original_name or Path(attachment.file.name).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        disposition = f"inline; filename*=UTF-8''{quote(filename)}"

        if settings.MEDIA_USE_X_ACCEL:
            # nginx streams the bytes from the internal location; Django only
            # authorises and points at the file.
            resp = HttpResponse(content_type=content_type)
            resp["X-Accel-Redirect"] = settings.MEDIA_X_ACCEL_PREFIX + attachment.file.name
            resp["Content-Disposition"] = disposition
            return resp

        resp = FileResponse(attachment.file.open("rb"), content_type=content_type)
        resp["Content-Disposition"] = disposition
        return resp


class WorkReportViewSet(viewsets.ModelViewSet):
    """Multi-project work reports for a client portal."""

    permission_classes = [IsPortalAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def _report_portal(self, report: WorkReport):
        from board.reports import report_portal_id

        portal_id = report_portal_id(report)
        if not portal_id:
            return None
        return Portal.objects.filter(pk=portal_id).first()

    def get_queryset(self):
        from django.db.models import Case, Count, IntegerField, Q, When

        from board.reports import BUCKET_STATUSES, normalize_report_bucket

        ids = accessible_portal_ids(self.request.user)
        qs = (
            WorkReport.objects.filter(
                Q(deal_binding__client_portal_id__in=ids)
                | Q(portal_id__in=ids)
                | Q(project__portal_id__in=ids)
            )
            .select_related(
                "portal", "project", "project__portal", "created_by",
                "deal_binding", "deal_binding__client_portal",
            )
            .annotate(_dispute_count=Count("dispute_items", distinct=True))
            .distinct()
        )
        if self.action == "list":
            qs = qs.prefetch_related("projects", "lines")
        else:
            qs = qs.prefetch_related(
                "projects", "lines", "events__actor", "dispute_items__task"
            )
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(
                Q(deal_binding__client_portal_id=portal_id)
                | Q(portal_id=portal_id)
                | Q(project__portal_id=portal_id)
            )
        if self.request.user.is_client:
            qs = qs.exclude(status=WorkReport.Status.DRAFT)
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(Q(projects__id=project_id) | Q(project_id=project_id))
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        bucket = normalize_report_bucket(self.request.query_params.get("bucket"))
        if bucket in BUCKET_STATUSES:
            qs = qs.filter(status__in=BUCKET_STATUSES[bucket])
        active = self.request.query_params.get("active")
        if active in ("1", "true", "yes"):
            qs = qs.filter(status__in=WorkReport.ACTIVE_STATUSES)
        return qs.order_by(
            Case(
                When(status__in=WorkReport.ACTIVE_STATUSES, then=0),
                default=1,
                output_field=IntegerField(),
            ),
            "-created_at",
        )

    def get_serializer_class(self):
        if self.action == "list":
            return WorkReportListSerializer
        return WorkReportSerializer

    def _actor(self):
        return getattr(self.request.user, "bitrix_user", None)

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed(
            "POST", detail="Отчёты создаются автоматически при привязке CRM-сделки."
        )

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Отчёты изменяются только через действия")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Отчёты изменяются только через действия")

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Удаление отчётов отключено")

    def retrieve(self, request, *args, **kwargs):
        from board.reports import sync_draft_report_completed_tasks

        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = sync_draft_report_completed_tasks(report)
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """Download the work report as a PDF (same live data as the detail page)."""
        from board.report_pdf import build_report_pdf

        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        pdf_bytes, filename = build_report_pdf(report)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    def list(self, request, *args, **kwargs):
        from board.reports import ensure_reports_for_portals

        if request.user.is_agency:
            ensure_reports_for_portals(accessible_portal_ids(request.user))

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        reports = list(page) if page is not None else list(queryset)

        context = self.get_serializer_context()
        serializer = self.get_serializer(reports, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="available-tasks")
    def available_tasks(self, request, pk=None):
        from board.reports import available_tasks_payload, require_agency

        require_agency(request.user)
        report = self.get_object()
        return Response(available_tasks_payload(report))

    @action(detail=True, methods=["post"], url_path="tasks")
    def tasks(self, request, pk=None):
        from board.reports import require_agency, set_report_tasks

        require_agency(request.user)
        task_ids = request.data.get("task_ids")
        if not isinstance(task_ids, list):
            raise ValidationError({"task_ids": "Передайте список task_ids."})
        try:
            task_ids = [int(value) for value in task_ids]
        except (TypeError, ValueError):
            raise ValidationError({"task_ids": "task_ids должны быть целыми числами."})
        report = set_report_tasks(self.get_object(), task_ids)
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="weekly")
    def weekly(self, request):
        from board.reports import weekly_reports_payload

        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        try:
            portal = Portal.objects.get(pk=portal_id)
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        try:
            weeks = int(request.query_params.get("weeks", 12))
        except (TypeError, ValueError):
            raise ValidationError({"weeks": "weeks должен быть целым числом."})
        return Response(weekly_reports_payload(portal, weeks=weeks))

    @action(detail=False, methods=["get"], url_path="activity")
    def activity(self, request):
        from board.reports import period_activity_payload

        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        try:
            portal = Portal.objects.get(pk=portal_id)
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        return Response(
            period_activity_payload(
                portal,
                date_from=request.query_params.get("from"),
                date_to=request.query_params.get("to"),
            )
        )

    @action(detail=False, methods=["get"])
    def counts(self, request):
        """Lightweight bucket counts for the reports hub (one round-trip)."""
        from django.db.models import Q

        from board.reports import BUCKET_STATUSES

        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        try:
            portal = Portal.objects.get(pk=portal_id)
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")

        ids = accessible_portal_ids(request.user)
        if request.user.is_agency:
            from board.reports import ensure_reports_for_portals

            ensure_reports_for_portals([portal.id])
        qs = WorkReport.objects.filter(
            Q(deal_binding__client_portal_id__in=ids)
            | Q(portal_id__in=ids)
            | Q(project__portal_id__in=ids)
        ).filter(
            Q(deal_binding__client_portal_id=portal_id)
            | Q(portal_id=portal_id)
            | Q(project__portal_id=portal_id)
        )
        if request.user.is_client:
            qs = qs.exclude(status=WorkReport.Status.DRAFT)
        from django.db.models import Count

        from board.models import WorkReport as WR

        agg = qs.aggregate(
            all=Count("id"),
            current=Count("id", filter=Q(status__in=BUCKET_STATUSES["current"])),
            review=Count("id", filter=Q(status__in=BUCKET_STATUSES["review"])),
            accepted=Count("id", filter=Q(status__in=BUCKET_STATUSES["accepted"])),
            draft=Count("id", filter=Q(status=WR.Status.DRAFT)),
            disputed=Count("id", filter=Q(status=WR.Status.DISPUTED)),
        )
        accepted_count = agg["accepted"] or 0
        return Response(
            {
                "all": agg["all"] or 0,
                "current": agg["current"] or 0,
                "review": agg["review"] or 0,
                "accepted": accepted_count,
                # Legacy key for older clients
                "paid": accepted_count,
                "draft": agg["draft"] or 0,
                "disputed": agg["disputed"] or 0,
            }
        )

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        from board.reports import require_agency, send_to_client

        require_agency(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = send_to_client(report, self._actor())
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        from board.reports import accept_report, require_client

        require_client(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = accept_report(report, self._actor())
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def dispute(self, request, pk=None):
        from board.reports import dispute_report, require_client

        require_client(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        ser = WorkReportDisputeInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        notes_raw = ser.validated_data.get("notes") or {}
        notes_by_task = {}
        for key, value in notes_raw.items():
            try:
                notes_by_task[int(key)] = value
            except (TypeError, ValueError):
                continue
        report = dispute_report(
            report,
            self._actor(),
            comment=ser.validated_data["client_comment"],
            task_ids=ser.validated_data["task_ids"],
            notes_by_task=notes_by_task,
        )
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        from board.reports import reopen_to_draft, require_agency

        require_agency(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = reopen_to_draft(report, self._actor())
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="dismiss")
    def dismiss(self, request, pk=None):
        from board.reports import dismiss_dispute, require_agency

        require_agency(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = dismiss_dispute(report, self._actor())
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="mark_paid")
    def mark_paid(self, request, pk=None):
        from board.reports import mark_paid, require_agency

        require_agency(request.user)
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        report = mark_paid(report, self._actor())
        return Response(WorkReportSerializer(report, context={"request": request}).data)


class SupportTicketViewSet(viewsets.ModelViewSet):
    """Support tickets for a client portal (Aeza-style open/closed)."""

    permission_classes = [IsPortalAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        from django.db.models import OuterRef, Subquery

        ids = accessible_portal_ids(self.request.user)
        last_author_role = (
            SupportTicketMessage.objects.filter(ticket_id=OuterRef("pk"))
            .order_by("-id")
            .values("author__portal__role")[:1]
        )
        qs = (
            SupportTicket.objects.filter(portal_id__in=ids)
            .select_related("portal", "project", "task", "created_by", "created_by__portal")
            .annotate(_last_author_role=Subquery(last_author_role))
        )
        # Messages only on retrieve / message actions — list stays light.
        if self.action != "list" and self.action != "counts":
            qs = qs.prefetch_related("messages__author__portal")
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(portal_id=portal_id)
        bucket = self.request.query_params.get("bucket")
        if bucket == "closed":
            qs = qs.filter(status=SupportTicket.Status.CLOSED)
        elif bucket == "open" or bucket == "current":
            qs = qs.filter(status=SupportTicket.Status.OPEN)
        status = self.request.query_params.get("status")
        if status in (SupportTicket.Status.OPEN, SupportTicket.Status.CLOSED):
            qs = qs.filter(status=status)

        awaiting = (self.request.query_params.get("awaiting") or "").strip().lower()
        if awaiting in ("agency", "client") and (
            bucket in ("open", "current") or status == SupportTicket.Status.OPEN or not bucket
        ):
            from portals.models import Portal

            # Last writer is the opposite party.
            if awaiting == "agency":
                # Waiting for agency ⇒ last message was NOT from agency (client/null).
                qs = qs.exclude(_last_author_role=Portal.Role.AGENCY)
            else:
                qs = qs.filter(_last_author_role=Portal.Role.AGENCY)

        return qs.order_by("-updated_at", "-id")

    def get_serializer_class(self):
        if self.action == "list":
            return SupportTicketListSerializer
        return SupportTicketSerializer

    def _actor(self):
        return getattr(self.request.user, "bitrix_user", None)

    def _ensure_access(self, ticket: SupportTicket):
        if not can_access_client_portal(self.request.user, ticket.portal):
            raise PermissionDenied("No access to this portal")

    def list(self, request, *args, **kwargs):
        portal_id = request.query_params.get("portal")
        if portal_id:
            try:
                portal = Portal.objects.get(pk=portal_id)
            except Portal.DoesNotExist:
                return Response({"detail": "Portal not found"}, status=404)
            if not can_access_client_portal(request.user, portal):
                raise PermissionDenied("No access to this portal")
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        """Lightweight open/closed/awaiting counts (badge + hub)."""
        from django.db.models import Count, OuterRef, Q, Subquery

        from portals.models import Portal as PortalModel

        ids = accessible_portal_ids(request.user)
        last_author_role = (
            SupportTicketMessage.objects.filter(ticket_id=OuterRef("pk"))
            .order_by("-id")
            .values("author__portal__role")[:1]
        )
        qs = SupportTicket.objects.filter(portal_id__in=ids).annotate(
            _last_author_role=Subquery(last_author_role)
        )
        portal_id = request.query_params.get("portal")
        if portal_id:
            try:
                portal = Portal.objects.get(pk=portal_id)
            except Portal.DoesNotExist:
                return Response({"detail": "Portal not found"}, status=404)
            if not can_access_client_portal(request.user, portal):
                raise PermissionDenied("No access to this portal")
            qs = qs.filter(portal_id=portal_id)

        agg = qs.aggregate(
            open=Count("id", filter=Q(status=SupportTicket.Status.OPEN)),
            closed=Count("id", filter=Q(status=SupportTicket.Status.CLOSED)),
            awaiting_agency=Count(
                "id",
                filter=Q(status=SupportTicket.Status.OPEN)
                & ~Q(_last_author_role=PortalModel.Role.AGENCY),
            ),
            awaiting_client=Count(
                "id",
                filter=Q(
                    status=SupportTicket.Status.OPEN,
                    _last_author_role=PortalModel.Role.AGENCY,
                ),
            ),
        )
        return Response(
            {
                "open": agg["open"] or 0,
                "closed": agg["closed"] or 0,
                "awaiting_agency": agg["awaiting_agency"] or 0,
                "awaiting_client": agg["awaiting_client"] or 0,
            }
        )

    def retrieve(self, request, *args, **kwargs):
        ticket = self.get_object()
        self._ensure_access(ticket)
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)

    def create(self, request, *args, **kwargs):
        from board.tickets import create_ticket, require_client

        require_client(request.user)
        ser = SupportTicketCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            portal = Portal.objects.get(pk=data["portal"])
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        # Client may only create on their own portal
        if request.user.portal_id != portal.id:
            raise PermissionDenied("Клиент может создавать тикеты только в своём портале")

        project = None
        task = None
        if data.get("project"):
            try:
                project = Project.objects.get(pk=data["project"], portal=portal)
            except Project.DoesNotExist:
                return Response({"detail": "Project not found"}, status=404)
        if data.get("task"):
            try:
                task = Task.objects.select_related("project").get(
                    pk=data["task"], project__portal=portal
                )
            except Task.DoesNotExist:
                return Response({"detail": "Task not found"}, status=404)

        ticket = create_ticket(
            portal,
            subject=data["subject"],
            body=data["body"],
            actor=self._actor(),
            project=project,
            task=task,
        )
        from django.db.models import OuterRef, Subquery

        ticket = (
            SupportTicket.objects.select_related(
                "portal", "project", "task", "created_by", "created_by__portal"
            )
            .annotate(
                _last_author_role=Subquery(
                    SupportTicketMessage.objects.filter(ticket_id=OuterRef("pk"))
                    .order_by("-id")
                    .values("author__portal__role")[:1]
                )
            )
            .get(pk=ticket.pk)
        )
        # List payload is enough to open the thread; detail loads messages once.
        return Response(
            SupportTicketListSerializer(ticket, context={"request": request}).data,
            status=201,
        )

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Тикеты изменяются только через действия")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Тикеты изменяются только через действия")

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Удаление тикетов отключено")

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        from board.tickets import add_message

        ticket = self.get_object()
        self._ensure_access(ticket)
        ser = SupportTicketMessageCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = add_message(ticket, text=ser.validated_data["text"], actor=self._actor())
        return Response(
            SupportTicketMessageSerializer(msg, context={"request": request}).data,
            status=201,
        )

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        from board.tickets import close_ticket, require_agency

        require_agency(request.user)
        ticket = self.get_object()
        self._ensure_access(ticket)
        ticket = close_ticket(ticket, self._actor())
        ticket = (
            SupportTicket.objects.select_related("portal", "project", "task", "created_by")
            .prefetch_related("messages__author")
            .get(pk=ticket.pk)
        )
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        from board.tickets import reopen_ticket, require_agency

        require_agency(request.user)
        ticket = self.get_object()
        self._ensure_access(ticket)
        ticket = reopen_ticket(ticket, self._actor())
        ticket = (
            SupportTicket.objects.select_related("portal", "project", "task", "created_by")
            .prefetch_related("messages__author")
            .get(pk=ticket.pk)
        )
        return Response(SupportTicketSerializer(ticket, context={"request": request}).data)


class BacklogItemViewSet(viewsets.ModelViewSet):
    """Agency backlog notes, plus client task requests awaiting approval."""

    permission_classes = [IsPortalAuthenticated]
    serializer_class = BacklogItemSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        ids = accessible_portal_ids(self.request.user)
        qs = BacklogItem.objects.filter(portal_id__in=ids).select_related(
            "portal", "created_by", "assignee", "converted_project", "converted_task"
        )
        if not getattr(self.request.user, "is_agency", False):
            qs = qs.filter(source=BacklogItem.Source.CLIENT)
        return qs

    def _require_agency(self):
        if not getattr(self.request.user, "is_agency", False):
            raise PermissionDenied("Только агентство")

    def _portal_from_request(self, portal_id):
        try:
            portal = Portal.objects.get(pk=portal_id)
        except (Portal.DoesNotExist, TypeError, ValueError):
            return None
        if not can_access_client_portal(self.request.user, portal):
            raise PermissionDenied("No access to this portal")
        return portal

    def _ensure_item_access(self, item: BacklogItem):
        if not can_access_client_portal(self.request.user, item.portal):
            raise PermissionDenied("No access to this portal")
        if (
            not getattr(self.request.user, "is_agency", False)
            and item.source != BacklogItem.Source.CLIENT
        ):
            raise PermissionDenied("No access to this item")

    def _resolve_assignee(self, assignee_id, agency_portal: Portal):
        if assignee_id in (None, "", 0, "0"):
            return None
        try:
            aid = int(assignee_id)
        except (TypeError, ValueError):
            raise ValidationError({"assignee": "Некорректный ответственный"})
        from portals.models import BitrixUser

        user = BitrixUser.objects.filter(pk=aid, portal=agency_portal).first()
        if not user:
            raise ValidationError({"assignee": "Пользователь не найден в портале агентства"})
        return user

    def _serialize(self, item: BacklogItem):
        return self.get_serializer(item).data

    def _publish_backlog(self, portal_id: int, item_id: int | None = None):
        payload = {"kind": "backlog_update"}
        if item_id is not None:
            payload["item_id"] = item_id
        publish_portal_event(portal_id, payload)

    def list(self, request, *args, **kwargs):
        portal_id = request.query_params.get("portal")
        if not portal_id and not request.user.is_agency:
            portal_id = request.user.portal_id
        if not portal_id:
            return Response({"detail": "Query parameter portal is required"}, status=400)
        portal = self._portal_from_request(portal_id)
        if portal is None:
            return Response({"detail": "Portal not found"}, status=404)
        qs = self.get_queryset().filter(portal=portal)
        status = (request.query_params.get("status") or "").strip()
        if status == "active":
            qs = qs.exclude(
                status__in=[BacklogItem.Status.DONE, BacklogItem.Status.CONVERTED]
            )
        elif status in {c.value for c in BacklogItem.Status}:
            qs = qs.filter(status=status)
        source = (request.query_params.get("source") or "").strip()
        if source in {c.value for c in BacklogItem.Source} and request.user.is_agency:
            qs = qs.filter(source=source)
        tag = (request.query_params.get("tag") or "").strip().lower()
        if tag:
            qs = qs.filter(tags__contains=[tag])
        assignee = request.query_params.get("assignee")
        if assignee == "me":
            me = getattr(request.user, "bitrix_user", None)
            if me:
                qs = qs.filter(assignee=me)
            else:
                qs = qs.none()
        elif assignee and assignee not in ("", "all"):
            qs = qs.filter(assignee_id=assignee)
        return Response(self.get_serializer(qs, many=True).data)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        is_agency = bool(getattr(request.user, "is_agency", False))
        portal_obj = ser.validated_data.get("portal")
        if is_agency:
            if portal_obj is None:
                return Response({"portal": ["Обязательное поле."]}, status=400)
            portal = self._portal_from_request(portal_obj.pk)
        else:
            portal = request.user.portal
            if portal_obj is not None and portal_obj.pk != portal.id:
                raise PermissionDenied("No access to this portal")
        if portal is None:
            return Response({"detail": "Portal not found"}, status=404)
        max_order = (
            BacklogItem.objects.filter(portal=portal)
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        assignee = None
        if is_agency and "assignee" in ser.validated_data:
            assignee = self._resolve_assignee(
                ser.validated_data["assignee"].pk if ser.validated_data["assignee"] else None,
                request.user.portal,
            )
        item = BacklogItem.objects.create(
            portal=portal,
            title=ser.validated_data["title"].strip(),
            notes=(ser.validated_data.get("notes") or "").strip(),
            source=BacklogItem.Source.AGENCY if is_agency else BacklogItem.Source.CLIENT,
            status=(
                ser.validated_data.get("status") or BacklogItem.Status.IDEA
                if is_agency
                else BacklogItem.Status.IDEA
            ),
            priority=(
                ser.validated_data.get("priority", BacklogItem.Priority.NORMAL)
                if is_agency
                else BacklogItem.Priority.NORMAL
            ),
            is_pinned=bool(ser.validated_data.get("is_pinned", False)) if is_agency else False,
            tags=(ser.validated_data.get("tags") or []) if is_agency else [],
            assignee=assignee,
            sort_order=(max_order + 1) if max_order is not None else 0,
            created_by=getattr(request.user, "bitrix_user", None),
        )
        self._publish_backlog(item.portal_id, item.id)
        return Response(self._serialize(item), status=201)

    def retrieve(self, request, *args, **kwargs):
        item = self.get_object()
        self._ensure_item_access(item)
        return Response(self._serialize(item))

    def partial_update(self, request, *args, **kwargs):
        item = self.get_object()
        self._ensure_item_access(item)
        is_agency = bool(getattr(request.user, "is_agency", False))
        if not is_agency:
            if item.source != BacklogItem.Source.CLIENT or item.is_accepted():
                raise PermissionDenied("Заявку уже приняли в работу, изменить нельзя")
        ser = self.get_serializer(item, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        update_fields = ["updated_at"]
        if "title" in data:
            title = data["title"].strip()
            if not title:
                raise ValidationError({"title": "Название не может быть пустым"})
            item.title = title
            update_fields.append("title")
        if "notes" in data:
            item.notes = (data["notes"] or "").strip()
            update_fields.append("notes")
        if not is_agency and "status" in data:
            allowed = {
                BacklogItem.Status.IDEA,
                BacklogItem.Status.IN_PROGRESS,
                BacklogItem.Status.DEFERRED,
            }
            if data["status"] not in allowed:
                raise ValidationError({"status": "Этот этап назначает агентство"})
            item.status = data["status"]
            update_fields.append("status")
        if is_agency:
            if "status" in data:
                item.status = data["status"]
                update_fields.append("status")
            if "priority" in data:
                item.priority = data["priority"]
                update_fields.append("priority")
            if "is_pinned" in data:
                item.is_pinned = bool(data["is_pinned"])
                update_fields.append("is_pinned")
            if "tags" in data:
                item.tags = data["tags"]
                update_fields.append("tags")
            if "assignee" in data:
                item.assignee = self._resolve_assignee(
                    data["assignee"].pk if data["assignee"] else None,
                    request.user.portal,
                )
                update_fields.append("assignee")
        item.save(update_fields=update_fields)
        item = self.get_queryset().get(pk=item.pk)
        self._publish_backlog(item.portal_id, item.id)
        return Response(self._serialize(item))

    def destroy(self, request, *args, **kwargs):
        item = self.get_object()
        self._ensure_item_access(item)
        if not getattr(request.user, "is_agency", False):
            if item.source != BacklogItem.Source.CLIENT or item.is_accepted():
                raise PermissionDenied("Заявку уже приняли в работу, удалить нельзя")
        portal_id = item.portal_id
        item_id = item.id
        item.delete()
        self._publish_backlog(portal_id, item_id)
        return Response(status=204)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        portal_id = request.query_params.get("portal")
        if not portal_id and not request.user.is_agency:
            portal_id = request.user.portal_id
        if not portal_id:
            return Response({"detail": "Query parameter portal is required"}, status=400)
        portal = self._portal_from_request(portal_id)
        if portal is None:
            return Response({"detail": "Portal not found"}, status=404)
        qs = (
            self.get_queryset()
            .filter(portal=portal, source=BacklogItem.Source.CLIENT)
            .exclude(status=BacklogItem.Status.CONVERTED)
        )
        return Response({"pending": qs.count()})

    @action(detail=False, methods=["get"])
    def assignees(self, request):
        """Agency portal users available as backlog assignees."""
        self._require_agency()
        from portals.models import BitrixUser

        users = BitrixUser.objects.filter(portal=request.user.portal).order_by(
            "name", "last_name", "id"
        )
        return Response(
            [
                {"id": u.id, "display_name": u.display_name, "bitrix_id": u.bitrix_id}
                for u in users
            ]
        )

    @action(detail=False, methods=["post"])
    def reorder(self, request):
        self._require_agency()
        portal_id = request.data.get("portal")
        ordered_ids = request.data.get("ordered_ids") or []
        portal = self._portal_from_request(portal_id)
        if portal is None:
            return Response({"detail": "Portal not found"}, status=404)
        if not isinstance(ordered_ids, list) or not ordered_ids:
            return Response({"detail": "ordered_ids required"}, status=400)
        try:
            ids = [int(x) for x in ordered_ids]
        except (TypeError, ValueError):
            return Response({"detail": "ordered_ids invalid"}, status=400)
        items = list(
            BacklogItem.objects.filter(
                portal=portal,
                id__in=ids,
                source=BacklogItem.Source.AGENCY,
            )
        )
        if len(items) != len(set(ids)):
            return Response({"detail": "Some items not found"}, status=400)
        by_id = {it.id: it for it in items}
        for index, item_id in enumerate(ids):
            it = by_id[item_id]
            if it.sort_order != index:
                it.sort_order = index
                it.save(update_fields=["sort_order", "updated_at"])
        qs = self.get_queryset().filter(portal=portal)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="convert-project")
    def convert_project(self, request, pk=None):
        self._require_agency()
        item = self.get_object()
        self._ensure_item_access(item)
        if item.source == BacklogItem.Source.CLIENT:
            return Response(
                {"detail": "Заявку клиента нужно добавить в существующий проект"},
                status=400,
            )
        if item.status == BacklogItem.Status.CONVERTED and item.converted_project_id:
            return Response(
                {
                    **self._serialize(item),
                    "project_id": item.converted_project_id,
                }
            )
        project = Project.objects.create(
            portal=item.portal,
            name=item.title[:255],
            description=item.notes or "",
        )
        try:
            enqueue_project_sync(project.id)
        except Exception:
            logger.exception("Bitrix project sync failed after backlog convert project=%s", project.id)
        publish_portal_event(
            project.portal_id, {"kind": "project_create", "project_id": project.id}
        )
        item.converted_project = project
        item.status = BacklogItem.Status.CONVERTED
        item.save(update_fields=["converted_project", "status", "updated_at"])
        item = self.get_queryset().get(pk=item.pk)
        self._publish_backlog(item.portal_id, item.id)
        return Response({**self._serialize(item), "project_id": project.id}, status=201)

    @action(detail=True, methods=["post"], url_path="convert-task")
    def convert_task(self, request, pk=None):
        self._require_agency()
        item = self.get_object()
        self._ensure_item_access(item)
        project_id = request.data.get("project")
        if not project_id:
            return Response({"detail": "project required"}, status=400)
        try:
            project = Project.objects.get(pk=project_id, portal=item.portal)
        except Project.DoesNotExist:
            return Response({"detail": "Project not found"}, status=404)
        if item.status == BacklogItem.Status.CONVERTED and item.converted_task_id:
            return Response(
                {**self._serialize(item), "task_id": item.converted_task_id, "project_id": project.id}
            )
        task = Task.objects.create(
            project=project,
            title=item.title[:500],
            description=item.notes or "",
            created_by=item.created_by or getattr(request.user, "bitrix_user", None),
            sync_status=Task.SyncStatus.PENDING,
            status=Task.Status.TODO,
            is_important=item.priority == BacklogItem.Priority.HIGH,
        )
        try:
            enqueue_bitrix_sync(task.id)
        except Exception:
            logger.exception("Bitrix task sync failed after backlog convert task=%s", task.id)
        publish_task_event(task, kind="task_create")
        item.converted_task = task
        item.converted_project = project
        item.status = BacklogItem.Status.CONVERTED
        item.save(
            update_fields=["converted_task", "converted_project", "status", "updated_at"]
        )
        item = self.get_queryset().get(pk=item.pk)
        self._publish_backlog(item.portal_id, item.id)
        return Response(
            {**self._serialize(item), "task_id": task.id, "project_id": project.id},
            status=201,
        )
