from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
import logging

from portals.models import Portal, PortalLink
from portals.permissions import IsPortalAuthenticated, can_access_client_portal

from .events import append_task_change_events
from .models import Attachment, Comment, Project, Task, TimeEntry
from .serializers import (
    AttachmentSerializer,
    CommentSerializer,
    ProjectSerializer,
    TaskListSerializer,
    TaskSerializer,
)
from .tasks import sync_comment_to_bitrix, sync_project_to_bitrix, sync_task_to_bitrix
from .timeutils import stop_time_entry

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
            file_name = attachment.original_name or "Файл"
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
        ids = accessible_portal_ids(self.request.user)
        return Project.objects.filter(portal_id__in=ids).select_related("portal")

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

    def perform_create(self, serializer):
        if not self.request.user.is_agency:
            raise PermissionDenied("Создавать проекты может только агентство")
        portal = serializer.validated_data["portal"]
        if not can_access_client_portal(self.request.user, portal):
            raise PermissionDenied("No access to this portal")
        project = serializer.save()
        enqueue_project_sync(project.id)
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
        ids = accessible_portal_ids(self.request.user)
        qs = Task.objects.filter(project__portal_id__in=ids).select_related(
            "project", "project__portal", "created_by"
        )
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                Prefetch("comments", queryset=Comment.objects.select_related("author")),
                "attachments",
                "comments__attachments",
                Prefetch("time_entries", queryset=TimeEntry.objects.select_related("author")),
            )
        portal_id = self.request.query_params.get("portal")
        if portal_id:
            qs = qs.filter(project__portal_id=portal_id)
        return qs

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Optional Bitrix pull (open task). Live poll should omit ?pull=1.
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
        }
        # Client places work for the agency — always starts as waiting.
        if self.request.user.is_client:
            extras["status"] = Task.Status.TODO
        task = serializer.save(**extras)
        enqueue_bitrix_sync(task.id)
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
        task = serializer.save(sync_status=Task.SyncStatus.PENDING)

        if self.request.user.is_agency and old_status != task.status:
            author = self.request.user.bitrix_user
            # Pause / complete → stop timer
            if old_status == Task.Status.IN_PROGRESS and task.status in (
                Task.Status.TODO,
                Task.Status.DONE,
            ):
                for running in task.time_entries.filter(ended_at__isnull=True):
                    stop_time_entry(running)
            # Start / resume → start timer
            if task.status == Task.Status.IN_PROGRESS:
                for running in TimeEntry.objects.filter(author=author, ended_at__isnull=True):
                    stop_time_entry(running)
                if not task.time_entries.filter(ended_at__isnull=True).exists():
                    TimeEntry.objects.create(
                        task=task,
                        author=author,
                        started_at=timezone.now(),
                    )

        created_events = append_task_change_events(
            task=task,
            author=self.request.user.bitrix_user,
            old_status=old_status,
            old_due=old_due,
        )
        enqueue_bitrix_sync(task.id)
        for event in created_events:
            enqueue_comment_sync(event.id)
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
        name = uploaded.name if uploaded else ""
        serializer.save(
            task=task,
            uploaded_by=self.request.user.bitrix_user,
            original_name=name,
        )
