import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from portals.models import Portal, PortalLink
from portals.permissions import IsPortalAuthenticated, can_access_client_portal

from .events import append_task_change_events
from .models import Attachment, Comment, Project, SupportTicket, SupportTicketMessage, Task, TimeEntry, WorkReport
from .naming import display_attachment_name
from .serializers import (
    ATTACHMENT_SIGN_SALT,
    AttachmentSerializer,
    CommentSerializer,
    ProjectSerializer,
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
from .tasks import sync_comment_to_bitrix, sync_project_to_bitrix, sync_task_to_bitrix
from .timeutils import stop_time_entry
from .realtime import publish_portal_event, publish_task_event

logger = logging.getLogger(__name__)


def enqueue_bitrix_sync(task_id: int) -> None:
    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_task_to_bitrix(task_id)
    else:
        sync_task_to_bitrix.delay(task_id)


def enqueue_project_sync(project_id: int) -> None:
    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_project_to_bitrix(project_id)
    else:
        sync_project_to_bitrix.delay(project_id)


def enqueue_comment_sync(comment_id: int) -> None:
    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_comment_to_bitrix(comment_id)
    else:
        sync_comment_to_bitrix.delay(comment_id)


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
        from django.db.models import Count, Q

        ids = accessible_portal_ids(self.request.user)
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
            )
        )

    def list(self, request, *args, **kwargs):
        # Soft realtime / first open: pull parent tasks from Bitrix company project
        if request.query_params.get("pull") in ("1", "true", "yes"):
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
        instance.delete()


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
        from django.db.models import Count, IntegerField, Q, Sum, Value
        from django.db.models.functions import Coalesce

        ids = accessible_portal_ids(self.request.user)
        qs = Task.objects.filter(project__portal_id__in=ids).select_related(
            "project", "project__portal", "created_by", "created_by__portal"
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
        if self.action == "retrieve":
            # Comments/attachments are loaded lazily via the `thread` action,
            # so we only prefetch what the task payload itself needs.
            qs = qs.prefetch_related(
                Prefetch("time_entries", queryset=TimeEntry.objects.select_related("author")),
            )
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(project__portal_id=portal_id)
        if self.request.query_params.get("open") in ("1", "true", "yes"):
            qs = qs.exclude(status=Task.Status.DONE)
        if self.action == "list" and not self.request.query_params.get("ordering"):
            qs = qs.order_by(*default_task_board_ordering())
        return qs

    def list(self, request, *args, **kwargs):
        # Soft pull deadlines/status from agency Bitrix when opening a project board
        if request.query_params.get("pull") in ("1", "true", "yes"):
            project_id = request.query_params.get("project")
            if project_id:
                try:
                    from board.status_sync import pull_task_status_from_bitrix

                    # Cap per request so board paint stays responsive; FE soft-polls.
                    qs = (
                        self.filter_queryset(self.get_queryset())
                        .filter(project_id=project_id)
                        .exclude(agency_bitrix_task_id="")[:20]
                    )
                    for task in qs:
                        try:
                            pull_task_status_from_bitrix(task)
                        except Exception:
                            logger.exception("Bitrix pull failed for task %s", task.id)
                except Exception:
                    logger.exception("Bitrix task list pull failed for project %s", project_id)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Live sync ?pull=1: status + comments only (no file downloads / deal CRM).
        if request.query_params.get("pull") in ("1", "true", "yes"):
            try:
                from board.comment_sync import pull_comments_from_bitrix
                from board.status_sync import pull_task_status_from_bitrix

                changed = pull_task_status_from_bitrix(instance)
                pulled = pull_comments_from_bitrix(instance)
                if changed or pulled:
                    instance.refresh_from_db()
            except Exception:
                logger.exception("Bitrix status pull failed for task %s", instance.id)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not can_access_client_portal(self.request.user, project.portal):
            raise PermissionDenied("No access to this project")
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
        task = serializer.save(sync_status=Task.SyncStatus.PENDING)

        if self.request.user.is_agency and old_status != task.status:
            author = self.request.user.bitrix_user
            # Pause / complete → stop timer (local only; Bitrix pause is never pushed)
            if old_status == Task.Status.IN_PROGRESS and task.status in (
                Task.Status.TODO,
                Task.Status.DONE,
            ):
                for running in task.time_entries.filter(ended_at__isnull=True):
                    stop_time_entry(running)
            # Start / resume → start local timer; Bitrix start is pushed by sync
            # only when Bitrix is not already in progress.
            if task.status == Task.Status.IN_PROGRESS:
                for running in TimeEntry.objects.filter(author=author, ended_at__isnull=True):
                    stop_time_entry(running)
                if not task.time_entries.filter(ended_at__isnull=True).exists():
                    TimeEntry.objects.create(
                        task=task,
                        author=author,
                        started_at=timezone.now(),
                    )
            if task.status == Task.Status.DONE and old_status != Task.Status.DONE:
                try:
                    from board.completion import finalize_task_completion

                    finalize_task_completion(task, author=author)
                except Exception:
                    logger.exception("finalize_task_completion failed task=%s", task.id)

        append_task_change_events(
            task=task,
            author=self.request.user.bitrix_user,
            old_status=old_status,
            old_due=old_due,
        )
        enqueue_bitrix_sync(task.id)
        publish_task_event(task, kind="task_update")
        task.refresh_from_db()
        serializer.instance = task

    def perform_destroy(self, instance):
        if not can_access_client_portal(self.request.user, instance.project.portal):
            raise PermissionDenied("No access")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="timer/start")
    def timer_start(self, request, pk=None):
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can track time")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")

        author = request.user.bitrix_user
        # Stop any other running timers for this agency user
        for running in TimeEntry.objects.filter(author=author, ended_at__isnull=True):
            stop_time_entry(running)

        existing = task.time_entries.filter(ended_at__isnull=True).first()
        if existing:
            return Response(TaskSerializer(task, context={"request": request}).data)

        TimeEntry.objects.create(
            task=task,
            author=author,
            started_at=timezone.now(),
        )
        # App-only timer — Bitrix «Учёт времени» is filled manually.
        task.refresh_from_db()
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="timer/stop")
    def timer_stop(self, request, pk=None):
        if not request.user.is_agency:
            raise PermissionDenied("Only agency can track time")
        task = self.get_object()
        if not can_access_client_portal(request.user, task.project.portal):
            raise PermissionDenied("No access")

        running = task.time_entries.filter(ended_at__isnull=True).order_by("-started_at").first()
        if running:
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
            try:
                if want_pull:
                    from board.comment_sync import pull_comments_from_bitrix

                    pull_comments_from_bitrix(task)
                if want_files:
                    from board.file_sync import pull_attachments_from_bitrix

                    pull_attachments_from_bitrix(task)
            except Exception:
                logger.exception("Bitrix thread pull failed for task %s", task.id)

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
        from board.tasks import sync_attachment_to_bitrix

        # Never fail the HTTP upload if Bitrix/Celery is down
        try:
            if settings.CELERY_TASK_ALWAYS_EAGER:
                sync_attachment_to_bitrix(attachment.id)
            else:
                sync_attachment_to_bitrix.delay(attachment.id)
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
        from django.db.models import Count, Q

        from board.reports import BUCKET_STATUSES

        ids = accessible_portal_ids(self.request.user)
        qs = (
            WorkReport.objects.filter(
                Q(portal_id__in=ids) | Q(project__portal_id__in=ids)
            )
            .select_related("portal", "project", "project__portal", "created_by")
            .annotate(_dispute_count=Count("dispute_items", distinct=True))
            .distinct()
        )
        if self.action == "list":
            qs = qs.prefetch_related("projects")
        else:
            qs = qs.prefetch_related("projects", "events__actor", "dispute_items__task")
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(Q(portal_id=portal_id) | Q(project__portal_id=portal_id))
        project_id = self.request.query_params.get("project")
        if project_id:
            qs = qs.filter(Q(projects__id=project_id) | Q(project_id=project_id))
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        bucket = self.request.query_params.get("bucket")
        if bucket in BUCKET_STATUSES:
            qs = qs.filter(status__in=BUCKET_STATUSES[bucket])
        active = self.request.query_params.get("active")
        if active in ("1", "true", "yes"):
            qs = qs.filter(status__in=WorkReport.ACTIVE_STATUSES)
        return qs.order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return WorkReportListSerializer
        return WorkReportSerializer

    def _actor(self):
        return getattr(self.request.user, "bitrix_user", None)

    def create(self, request, *args, **kwargs):
        from board.reports import create_report, require_agency

        require_agency(request.user)
        portal_id = request.data.get("portal")
        project_ids = request.data.get("project_ids") or []
        # Back-compat: single project
        if not project_ids and request.data.get("project"):
            project_ids = [request.data.get("project")]
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        try:
            portal = Portal.objects.get(pk=portal_id)
        except Portal.DoesNotExist:
            return Response({"detail": "Portal not found"}, status=404)
        if not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        try:
            ids = [int(x) for x in project_ids]
        except (TypeError, ValueError):
            return Response({"detail": "project_ids invalid"}, status=400)
        report = create_report(portal, ids, self._actor())
        serializer = WorkReportSerializer(report, context={"request": request})
        return Response(serializer.data, status=201)

    def update(self, request, *args, **kwargs):
        raise PermissionDenied("Отчёты изменяются только через действия")

    def partial_update(self, request, *args, **kwargs):
        raise PermissionDenied("Отчёты изменяются только через действия")

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied("Удаление отчётов отключено")

    def retrieve(self, request, *args, **kwargs):
        report = self.get_object()
        portal = self._report_portal(report)
        if not portal or not can_access_client_portal(request.user, portal):
            raise PermissionDenied("No access to this portal")
        return Response(WorkReportSerializer(report, context={"request": request}).data)

    def list(self, request, *args, **kwargs):
        from django.db.models import Sum

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        reports = list(page) if page is not None else list(queryset)

        all_pids: set[int] = set()
        for report in reports:
            projects = list(report.projects.all())
            if projects:
                all_pids.update(p.id for p in projects)
            elif report.project_id:
                all_pids.add(report.project_id)
        seconds_by_project: dict[int, int] = {}
        if all_pids:
            seconds_by_project = {
                int(row["task__project_id"]): int(row["total"] or 0)
                for row in TimeEntry.objects.filter(task__project_id__in=all_pids)
                .values("task__project_id")
                .annotate(total=Sum("duration_seconds"))
            }

        context = self.get_serializer_context()
        context["seconds_by_project"] = seconds_by_project
        serializer = self.get_serializer(reports, many=True, context=context)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

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
        qs = WorkReport.objects.filter(
            Q(portal_id__in=ids) | Q(project__portal_id__in=ids)
        ).filter(Q(portal_id=portal_id) | Q(project__portal_id=portal_id))
        return Response(
            {
                "all": qs.count(),
                "current": qs.filter(status__in=BUCKET_STATUSES["current"]).count(),
                "review": qs.filter(status__in=BUCKET_STATUSES["review"]).count(),
                "paid": qs.filter(status__in=BUCKET_STATUSES["paid"]).count(),
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
        from django.db.models import Count, OuterRef, Subquery

        ids = accessible_portal_ids(self.request.user)
        last_author_role = (
            SupportTicketMessage.objects.filter(ticket_id=OuterRef("pk"))
            .order_by("-id")
            .values("author__portal__role")[:1]
        )
        qs = (
            SupportTicket.objects.filter(portal_id__in=ids)
            .select_related("portal", "project", "task", "created_by", "created_by__portal")
            .annotate(
                _message_count=Count("messages"),
                _last_author_role=Subquery(last_author_role),
            )
        )
        # Messages are only needed on retrieve / message actions — annotate covers list count.
        if self.action != "list":
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
        ticket = (
            SupportTicket.objects.select_related("portal", "project", "task", "created_by")
            .prefetch_related("messages__author")
            .get(pk=ticket.pk)
        )
        return Response(
            SupportTicketSerializer(ticket, context={"request": request}).data,
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
