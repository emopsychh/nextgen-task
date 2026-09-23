import sys

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from portals.models import Portal
from portals.permissions import can_access_client_portal

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
    WorkReportDisputeItem,
    WorkReportEvent,
)
from .naming import display_attachment_name


def task_creator_display_name(created_by) -> str | None:
    """Label for постановщик; client creators include portal company name."""
    if not created_by:
        return None
    name = (created_by.display_name or "").strip()
    portal = getattr(created_by, "portal", None)
    if portal is not None and portal.role == Portal.Role.CLIENT:
        company = (portal.name or "").strip()
        if company and name and company.lower() not in name.lower():
            return f"{name} ({company})"
        return name or company or "Клиент"
    return name or None


def _clean_task_title(instance: Task) -> str:
    """Strip legacy [portal] prefixes from title; persist if dirty and push to Bitrix."""
    from django.conf import settings

    from board.tasks import sync_task_to_bitrix
    from board.titles import strip_portal_title_prefix

    client_portal = instance.project.portal if instance.project_id else None
    cleaned = strip_portal_title_prefix(instance.title or "", client_portal)
    if cleaned and cleaned != instance.title:
        Task.objects.filter(pk=instance.pk).update(title=cleaned)
        instance.title = cleaned
        try:
            if not settings.CELERY_TASK_ALWAYS_EAGER:
                sync_task_to_bitrix.delay(instance.id)
            elif "test" in sys.argv:
                sync_task_to_bitrix(instance.id)
            else:
                import threading

                from django.db import close_old_connections

                task_id = instance.id

                def _worker() -> None:
                    try:
                        close_old_connections()
                        sync_task_to_bitrix(task_id)
                    finally:
                        close_old_connections()

                threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            pass
    return instance.title or ""


class TaskDueDateField(serializers.Field):
    """UTC storage; API emits ISO-Z; naive writes use the client portal timezone."""

    default_error_messages = {
        "invalid": "Некорректная дата срока",
    }

    def to_representation(self, value):
        from board.due_dates import format_utc_z

        return format_utc_z(value)

    def to_internal_value(self, data):
        from board.due_dates import parse_due_value, portal_zone

        if data in (None, "", "null"):
            return None
        portal = self._resolve_portal()
        tz = portal_zone(portal)
        parsed = parse_due_value(data, portal_tz=tz)
        if parsed is None and data not in (None, "", "null"):
            self.fail("invalid")
        return parsed

    def _resolve_portal(self):
        parent = getattr(self, "parent", None)
        if parent is None:
            return None
        instance = getattr(parent, "instance", None)
        if instance is not None and getattr(instance, "project_id", None):
            return instance.project.portal
        initial = getattr(parent, "initial_data", None) or {}
        project_id = initial.get("project")
        if project_id:
            project = Project.objects.filter(pk=project_id).select_related("portal").first()
            return project.portal if project else None
        return None


# Salt for the signed, expiring capability token embedded in attachment URLs.
# The token proves the caller was handed the link by an access-scoped API
# response; the download endpoint needs no separate auth header (so plain
# <img>/<a download> work) yet leaked links stop working after ATTACHMENT_URL_TTL.
ATTACHMENT_SIGN_SALT = "board.attachment.download.v1"


def sign_attachment_id(att_id: int) -> str:
    from django.core import signing

    return signing.dumps(int(att_id), salt=ATTACHMENT_SIGN_SALT)


class AttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    original_name = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = (
            "id",
            "task",
            "comment",
            "file",
            "url",
            "original_name",
            "uploaded_by",
            "created_at",
        )
        read_only_fields = ("id", "uploaded_by", "created_at", "url", "original_name")

    def get_url(self, obj):
        if not obj.file:
            return None
        # Access-controlled, signed, expiring URL — NEVER the raw /media path.
        # Same-origin relative URL keeps https and avoids Mixed Content.
        return f"/api/attachments/{obj.id}/download/?t={sign_attachment_id(obj.id)}"

    def get_original_name(self, obj):
        return display_attachment_name(obj)

class CommentSerializer(serializers.ModelSerializer):
    author_display = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)
    # Allow empty text when the message is file-only
    text = serializers.CharField(allow_blank=True, required=False, default="")

    class Meta:
        model = Comment
        fields = (
            "id",
            "task",
            "author",
            "author_name",
            "author_display",
            "text",
            "is_system",
            "attachments",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "author",
            "author_name",
            "is_system",
            "created_at",
            "updated_at",
        )

    def get_author_display(self, obj):
        if obj.author:
            return obj.author.display_name
        if obj.author_name:
            return obj.author_name
        # System lines from Bitrix (no local actor) — never show English "Unknown".
        if obj.is_system:
            return "Команда"
        return "Участник"


def serialize_thread_items(comments, files) -> list[dict]:
    """Build the chat-thread payload the frontend expects (ThreadItem[]).

    `comments` and `files` are iterables of Comment / standalone Attachment.
    The result is NOT sorted here; callers sort by `at`.
    """
    items: list[dict] = []
    for c in comments:
        items.append(
            {
                "kind": "comment",
                "at": c.created_at.isoformat(),
                "comment": CommentSerializer(c).data,
            }
        )
    for f in files:
        items.append(
            {
                "kind": "file",
                "at": f.created_at.isoformat(),
                "file": AttachmentSerializer(f).data,
            }
        )
    return items


class TimeEntrySerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    is_running = serializers.BooleanField(read_only=True)

    class Meta:
        model = TimeEntry
        fields = (
            "id",
            "task",
            "author",
            "author_name",
            "started_at",
            "ended_at",
            "duration_seconds",
            "note",
            "is_running",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_author_name(self, obj):
        if obj.author:
            return obj.author.display_name
        return None


class TaskSerializer(serializers.ModelSerializer):
    # NOTE: full comments/attachments are intentionally NOT nested here.
    # The chat thread is loaded lazily (paginated) via the `thread` action so
    # that opening a task and the 2s live-poll never ship the whole history.
    # These lightweight signals let the client cheaply detect new activity.
    comments_count = serializers.IntegerField(source="comments.count", read_only=True)
    last_comment_id = serializers.SerializerMethodField()
    files_count = serializers.SerializerMethodField()
    last_file_id = serializers.SerializerMethodField()
    project_name = serializers.CharField(source="project.name", read_only=True)
    portal_id = serializers.IntegerField(source="project.portal_id", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    created_by_role = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    active_timer = serializers.SerializerMethodField()
    deal_paid_hours = serializers.SerializerMethodField()
    deal_remaining_hours = serializers.SerializerMethodField()
    is_working = serializers.SerializerMethodField()
    working_by_name = serializers.SerializerMethodField()
    due_timezone = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    awaiting_client = serializers.SerializerMethodField()
    due_date = TaskDueDateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = (
            "id",
            "project",
            "project_name",
            "portal_id",
            "title",
            "description",
            "outcome",
            "due_date",
            "status",
            "is_important",
            "bitrix_task_id",
            "agency_bitrix_task_id",
            "sync_status",
            "sync_error",
            "created_by",
            "created_by_name",
            "created_by_role",
            "comments_count",
            "last_comment_id",
            "files_count",
            "last_file_id",
            "total_tracked_seconds",
            "active_timer",
            "deal_paid_hours",
            "deal_remaining_hours",
            "is_working",
            "working_started_at",
            "working_by_name",
            "due_timezone",
            "can_delete",
            "completed_at",
            "awaiting_client",
            "awaiting_client_at",
            "outcome_seen_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "bitrix_task_id",
            "agency_bitrix_task_id",
            "sync_status",
            "sync_error",
            "created_by",
            "created_by_name",
            "created_by_role",
            "total_tracked_seconds",
            "active_timer",
            "deal_paid_hours",
            "deal_remaining_hours",
            "is_working",
            "working_started_at",
            "working_by_name",
            "due_timezone",
            "can_delete",
            "completed_at",
            "awaiting_client",
            "awaiting_client_at",
            "outcome_seen_at",
            "created_at",
            "updated_at",
        )

    def get_created_by_name(self, obj):
        return task_creator_display_name(obj.created_by)

    def get_created_by_role(self, obj):
        if obj.created_by_id and obj.created_by and obj.created_by.portal_id:
            return obj.created_by.portal.role
        return None

    def get_last_comment_id(self, obj):
        return (
            obj.comments.order_by("-id").values_list("id", flat=True).first() or 0
        )

    def get_files_count(self, obj):
        return obj.attachments.filter(comment__isnull=True).count()

    def get_last_file_id(self, obj):
        return (
            obj.attachments.filter(comment__isnull=True)
            .order_by("-id")
            .values_list("id", flat=True)
            .first()
            or 0
        )

    def get_total_tracked_seconds(self, obj):
        from .timeutils import task_tracked_seconds

        return task_tracked_seconds(obj, include_running=False)

    def get_active_timer(self, obj):
        running = (
            obj.time_entries.filter(ended_at__isnull=True)
            .select_related("author")
            .order_by("-started_at")
            .first()
        )
        if not running:
            return None
        return TimeEntrySerializer(running).data

    def get_is_working(self, obj):
        return obj.status == Task.Status.IN_PROGRESS or obj.working_started_at is not None

    def get_working_by_name(self, obj):
        try:
            person = obj.working_by if obj.working_by_id else None
        except Exception:
            person = None
        if person:
            return person.display_name
        return None

    def get_due_timezone(self, obj):
        from board.due_dates import DEFAULT_PORTAL_TZ

        portal = obj.project.portal if obj.project_id else None
        if portal is None:
            return DEFAULT_PORTAL_TZ
        return (portal.timezone or "").strip() or DEFAULT_PORTAL_TZ

    def get_awaiting_client(self, obj):
        return obj.awaiting_client_at is not None

    def get_can_delete(self, obj):
        from board.deletion import task_is_app_deletable

        request = self.context.get("request")
        if not request or not getattr(request.user, "is_agency", False):
            return False
        return task_is_app_deletable(obj)

    def _deal_binding(self, obj):
        cache = self.context.setdefault("_deal_binding_by_portal", {})
        portal_id = obj.project.portal_id
        if portal_id in cache:
            return cache[portal_id]
        from portals.models import PortalDealBinding

        binding = (
            PortalDealBinding.objects.filter(
                client_portal_id=portal_id,
                is_active=True,
            )
            .order_by("-updated_at")
            .first()
        )
        cache[portal_id] = binding
        return binding

    def get_deal_paid_hours(self, obj):
        binding = self._deal_binding(obj)
        if not binding or binding.paid_hours is None:
            return None
        return float(binding.paid_hours)

    def get_deal_remaining_hours(self, obj):
        binding = self._deal_binding(obj)
        if not binding or binding.remaining_hours is None:
            return None
        return float(binding.remaining_hours)

    def validate(self, attrs):
        instance = self.instance
        new_status = attrs.get("status", instance.status if instance else None)
        if new_status == Task.Status.DONE:
            old_status = instance.status if instance else None
            if old_status != Task.Status.DONE:
                outcome = attrs.get("outcome", None)
                if outcome is None and instance is not None:
                    outcome = instance.outcome
                if not (outcome or "").strip():
                    raise serializers.ValidationError(
                        {"outcome": "Укажите итог работы перед завершением задачи."}
                    )
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["title"] = _clean_task_title(instance)
        return data


class TaskListSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    portal_id = serializers.IntegerField(source="project.portal_id", read_only=True)
    comments_count = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    created_by_role = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    is_working = serializers.SerializerMethodField()
    working_by_name = serializers.SerializerMethodField()
    due_timezone = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    awaiting_client = serializers.SerializerMethodField()
    due_date = TaskDueDateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = (
            "id",
            "project",
            "project_name",
            "portal_id",
            "title",
            "description",
            "due_date",
            "due_timezone",
            "status",
            "is_important",
            "bitrix_task_id",
            "sync_status",
            "created_by",
            "created_by_name",
            "created_by_role",
            "comments_count",
            "total_tracked_seconds",
            "is_working",
            "working_started_at",
            "working_by_name",
            "can_delete",
            "completed_at",
            "awaiting_client",
            "awaiting_client_at",
            "outcome_seen_at",
            "created_at",
            "updated_at",
        )

    def get_comments_count(self, obj):
        annotated = getattr(obj, "_comments_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.comments.count()

    def get_created_by_name(self, obj):
        return task_creator_display_name(obj.created_by)

    def get_created_by_role(self, obj):
        if obj.created_by_id and obj.created_by and obj.created_by.portal_id:
            return obj.created_by.portal.role
        return None

    def get_total_tracked_seconds(self, obj):
        annotated = getattr(obj, "_tracked_seconds", None)
        if annotated is not None:
            return int(annotated)
        from .timeutils import task_tracked_seconds

        return task_tracked_seconds(obj, include_running=False)

    def get_is_working(self, obj):
        return obj.status == Task.Status.IN_PROGRESS or obj.working_started_at is not None

    def get_working_by_name(self, obj):
        try:
            person = obj.working_by if obj.working_by_id else None
        except Exception:
            person = None
        if person:
            return person.display_name
        return None

    def get_due_timezone(self, obj):
        from board.due_dates import DEFAULT_PORTAL_TZ

        portal = obj.project.portal if obj.project_id else None
        if portal is None:
            return DEFAULT_PORTAL_TZ
        return (portal.timezone or "").strip() or DEFAULT_PORTAL_TZ

    def get_awaiting_client(self, obj):
        return obj.awaiting_client_at is not None

    def get_can_delete(self, obj):
        from board.deletion import task_is_app_deletable

        request = self.context.get("request")
        if not request or not getattr(request.user, "is_agency", False):
            return False
        return task_is_app_deletable(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["title"] = _clean_task_title(instance)
        return data


class ProjectSerializer(serializers.ModelSerializer):
    tasks_count = serializers.SerializerMethodField()
    done_count = serializers.SerializerMethodField()
    has_active_work = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    due_date = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    completed_at = serializers.SerializerMethodField()
    portal_name = serializers.CharField(source="portal.name", read_only=True)
    team_members = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = (
            "id",
            "portal",
            "portal_name",
            "team_members",
            "name",
            "description",
            "is_active",
            "bitrix_task_id",
            "bitrix_group_id",
            "tasks_count",
            "done_count",
            "has_active_work",
            "can_delete",
            "due_date",
            "total_tracked_seconds",
            "completed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "bitrix_task_id",
            "bitrix_group_id",
            "has_active_work",
            "can_delete",
            "due_date",
            "total_tracked_seconds",
            "completed_at",
            "created_at",
            "updated_at",
        )

    def get_tasks_count(self, obj):
        annotated = getattr(obj, "_tasks_count", None)
        if annotated is not None:
            return annotated
        return obj.tasks.count()

    def get_done_count(self, obj):
        annotated = getattr(obj, "_done_count", None)
        if annotated is not None:
            return annotated
        return obj.tasks.filter(status=Task.Status.DONE).count()

    def get_has_active_work(self, obj):
        annotated = getattr(obj, "_has_active_work", None)
        if annotated is not None:
            return bool(annotated)
        return obj.tasks.filter(status=Task.Status.IN_PROGRESS).exists()

    def get_can_delete(self, obj):
        from board.deletion import project_is_app_deletable

        request = self.context.get("request")
        if not request or not getattr(request.user, "is_agency", False):
            return False
        annotated = getattr(obj, "_tasks_count", None)
        if annotated is not None:
            return int(annotated) == 0
        return project_is_app_deletable(obj)

    def get_due_date(self, obj):
        if hasattr(obj, "_due_date"):
            return obj._due_date
        open_due = (
            obj.tasks.exclude(status=Task.Status.DONE)
            .exclude(due_date=None)
            .order_by("due_date")
            .values_list("due_date", flat=True)
            .first()
        )
        if open_due:
            return open_due
        return (
            obj.tasks.exclude(due_date=None)
            .order_by("-due_date")
            .values_list("due_date", flat=True)
            .first()
        )

    def get_total_tracked_seconds(self, obj):
        if hasattr(obj, "_tracked_seconds"):
            return int(obj._tracked_seconds or 0)
        total = TimeEntry.objects.filter(
            task__project=obj, ended_at__isnull=False
        ).aggregate(total=Sum("duration_seconds"))["total"]
        return int(total or 0)

    def get_completed_at(self, obj):
        if hasattr(obj, "_completed_at"):
            return obj._completed_at
        if obj.tasks.exclude(status=Task.Status.DONE).exists():
            return None
        return (
            obj.tasks.exclude(completed_at=None)
            .order_by("-completed_at")
            .values_list("completed_at", flat=True)
            .first()
        )

    def get_team_members(self, obj):
        request = self.context.get("request")
        view = getattr(getattr(request, "parser_context", {}), "get", lambda *_: None)("view")
        if getattr(view, "action", None) != "retrieve":
            return []

        members = []
        seen = set()
        for task in obj.tasks.select_related("working_by__portal", "created_by__portal"):
            user = task.working_by or task.created_by
            if not user or user.id in seen:
                continue
            seen.add(user.id)
            members.append(
                {
                    "id": user.id,
                    "name": user.display_name,
                    "role": "agency" if user.portal.role == Portal.Role.AGENCY else "client",
                }
            )
        return members

    def validate_portal(self, portal: Portal):
        request = self.context.get("request")
        if request and not can_access_client_portal(request.user, portal):
            raise serializers.ValidationError("No access to this portal")
        if portal.role not in (Portal.Role.CLIENT, Portal.Role.AGENCY):
            # Allow creating projects on client portals primarily
            pass
        return portal


class ProjectMeetingSerializer(serializers.ModelSerializer):
    organizer_name = serializers.SerializerMethodField()
    organizer_role = serializers.SerializerMethodField()

    class Meta:
        model = ProjectMeeting
        fields = (
            "id",
            "project",
            "title",
            "scheduled_at",
            "duration_minutes",
            "format",
            "location",
            "notes",
            "cancelled_at",
            "outcome",
            "organizer_name",
            "organizer_role",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "organizer_name",
            "organizer_role",
            "created_at",
            "updated_at",
        )

    def get_organizer_name(self, obj):
        return obj.created_by.display_name if obj.created_by else ""

    def get_organizer_role(self, obj):
        if not obj.created_by:
            return "unknown"
        return "agency" if obj.created_by.portal.role == Portal.Role.AGENCY else "client"

    def validate(self, attrs):
        from .meeting_slots import has_meeting_conflict

        request = self.context.get("request")
        if "outcome" in attrs and request is not None and not request.user.is_agency:
            raise serializers.ValidationError({"outcome": "Итог встречи заполняет агентство"})

        instance = self.instance
        project = attrs.get("project") or getattr(instance, "project", None)
        starts_at = attrs.get("scheduled_at", getattr(instance, "scheduled_at", None))
        duration = attrs.get("duration_minutes", getattr(instance, "duration_minutes", 60))
        cancelled_at = attrs.get("cancelled_at", getattr(instance, "cancelled_at", None))
        time_changed = instance is None or "scheduled_at" in attrs or "duration_minutes" in attrs
        if cancelled_at or not time_changed:
            return attrs
        if duration < 15 or duration > 480:
            raise serializers.ValidationError({"duration_minutes": "Допустимая длительность встречи: от 15 минут до 8 часов"})
        if starts_at and starts_at <= timezone.now():
            raise serializers.ValidationError({"scheduled_at": "Выберите время в будущем"})
        if project and starts_at and has_meeting_conflict(
            project.portal_id,
            starts_at,
            duration,
            exclude_meeting_id=getattr(instance, "id", None),
        ):
            raise serializers.ValidationError({"scheduled_at": "Это время уже занято. Выберите другой слот"})
        return attrs


class WorkReportEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkReportEvent
        fields = ("id", "kind", "actor", "actor_name", "payload", "created_at")
        read_only_fields = fields

    def get_actor_name(self, obj):
        if obj.actor:
            return obj.actor.display_name
        return ""


class WorkReportDisputeItemSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", read_only=True)

    class Meta:
        model = WorkReportDisputeItem
        fields = ("id", "task", "task_title", "note", "created_at")
        read_only_fields = fields


class WorkReportSerializer(serializers.ModelSerializer):
    portal_id = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    project_ids = serializers.SerializerMethodField()
    project_names = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    projects_detail = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    task_tracked_seconds = serializers.SerializerMethodField()
    carried_overage_seconds = serializers.SerializerMethodField()
    deal_hours = serializers.SerializerMethodField()
    deal_binding_id = serializers.IntegerField(read_only=True)
    deal_id = serializers.CharField(source="deal_binding.deal_id", read_only=True)
    deal_title = serializers.CharField(source="deal_binding.deal_title", read_only=True)
    selected_task_ids = serializers.SerializerMethodField()
    tasks_count = serializers.SerializerMethodField()
    events = WorkReportEventSerializer(many=True, read_only=True)
    dispute_items = WorkReportDisputeItemSerializer(many=True, read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkReport
        fields = (
            "id",
            "portal_id",
            "portal_name",
            "deal_binding_id",
            "deal_id",
            "deal_title",
            "project",
            "project_ids",
            "project_names",
            "status",
            "created_by",
            "created_by_name",
            "client_comment",
            "sent_at",
            "accepted_at",
            "paid_at",
            "created_at",
            "updated_at",
            "is_active",
            "projects_detail",
            "total_tracked_seconds",
            "task_tracked_seconds",
            "carried_overage_seconds",
            "deal_hours",
            "selected_task_ids",
            "tasks_count",
            "events",
            "dispute_items",
        )
        read_only_fields = fields

    def get_portal_id(self, obj):
        from board.reports import report_portal_id

        return report_portal_id(obj)

    def get_portal_name(self, obj):
        if obj.portal_id:
            return obj.portal.name or obj.portal.domain
        if obj.project_id:
            return obj.project.portal.name or obj.project.portal.domain
        return ""

    def get_project_ids(self, obj):
        return self._metrics(obj)["project_ids"]

    def get_project_names(self, obj):
        return self._metrics(obj)["project_names"]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return ""

    def _metrics(self, obj):
        cache = self.context.setdefault("_report_metrics", {})
        key = obj.pk
        if key not in cache:
            from board.reports import report_detail_metrics

            cache[key] = report_detail_metrics(obj)
        return cache[key]

    def get_projects_detail(self, obj):
        return self._metrics(obj)["projects_detail"]

    def get_total_tracked_seconds(self, obj):
        return self._metrics(obj)["total_tracked_seconds"]

    def get_task_tracked_seconds(self, obj):
        return self._metrics(obj)["task_tracked_seconds"]

    def get_carried_overage_seconds(self, obj):
        return self._metrics(obj)["carried_overage_seconds"]

    def get_deal_hours(self, obj):
        from board.reports import deal_hours_for_report

        return deal_hours_for_report(obj)

    def get_selected_task_ids(self, obj):
        return self._metrics(obj)["selected_task_ids"]

    def get_tasks_count(self, obj):
        return self._metrics(obj)["tasks_count"]


class WorkReportListSerializer(serializers.ModelSerializer):
    portal_id = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    project_ids = serializers.SerializerMethodField()
    project_names = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    task_tracked_seconds = serializers.SerializerMethodField()
    carried_overage_seconds = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    dispute_count = serializers.SerializerMethodField()
    projects_count = serializers.SerializerMethodField()
    deal_binding_id = serializers.IntegerField(read_only=True)
    deal_id = serializers.CharField(source="deal_binding.deal_id", read_only=True)
    deal_title = serializers.CharField(source="deal_binding.deal_title", read_only=True)
    deal_hours = serializers.SerializerMethodField()
    selected_task_ids = serializers.SerializerMethodField()
    tasks_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkReport
        fields = (
            "id",
            "portal_id",
            "portal_name",
            "deal_binding_id",
            "deal_id",
            "deal_title",
            "deal_hours",
            "project",
            "project_ids",
            "project_names",
            "projects_count",
            "status",
            "created_by",
            "created_by_name",
            "client_comment",
            "sent_at",
            "accepted_at",
            "paid_at",
            "created_at",
            "updated_at",
            "is_active",
            "total_tracked_seconds",
            "task_tracked_seconds",
            "carried_overage_seconds",
            "dispute_count",
            "selected_task_ids",
            "tasks_count",
        )
        read_only_fields = fields

    def get_portal_id(self, obj):
        from board.reports import report_portal_id

        return report_portal_id(obj)

    def get_portal_name(self, obj):
        if obj.portal_id:
            return obj.portal.name or obj.portal.domain
        if obj.project_id:
            return obj.project.portal.name or obj.project.portal.domain
        return ""

    def get_project_ids(self, obj):
        # Prefer prefetched M2M to avoid per-row queries on list.
        projects = list(obj.projects.all())
        if projects:
            return [p.id for p in projects]
        if obj.project_id:
            return [obj.project_id]
        return []

    def get_project_names(self, obj):
        projects = list(obj.projects.all())
        if projects:
            return sorted(p.name for p in projects)
        if obj.project_id and getattr(obj, "project", None):
            return [obj.project.name]
        return []

    def get_projects_count(self, obj):
        return len(self.get_project_ids(obj))

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return ""

    def get_total_tracked_seconds(self, obj):
        return self._metrics(obj)["total_tracked_seconds"]

    def get_task_tracked_seconds(self, obj):
        return self._metrics(obj)["task_tracked_seconds"]

    def get_carried_overage_seconds(self, obj):
        return self._metrics(obj)["carried_overage_seconds"]

    def _metrics(self, obj):
        cache = self.context.setdefault("_report_metrics", {})
        if obj.pk not in cache:
            from board.reports import report_detail_metrics

            cache[obj.pk] = report_detail_metrics(obj)
        return cache[obj.pk]

    def get_deal_hours(self, obj):
        from board.reports import deal_hours_for_report

        return deal_hours_for_report(obj)

    def get_selected_task_ids(self, obj):
        return self._metrics(obj)["selected_task_ids"]

    def get_tasks_count(self, obj):
        return self._metrics(obj)["tasks_count"]

    def get_dispute_count(self, obj):
        if hasattr(obj, "_dispute_count"):
            return obj._dispute_count
        return obj.dispute_items.count()


class WorkReportDisputeInputSerializer(serializers.Serializer):
    client_comment = serializers.CharField(allow_blank=False, trim_whitespace=True)
    task_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )
    notes = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )


class SupportTicketMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketMessage
        fields = ("id", "ticket", "author", "author_name", "text", "created_at")
        read_only_fields = fields

    def get_author_name(self, obj):
        if obj.author:
            return obj.author.display_name
        return ""


class SupportTicketListSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    portal_domain = serializers.SerializerMethodField()
    project_name = serializers.SerializerMethodField()
    task_title = serializers.SerializerMethodField()
    message_count = serializers.SerializerMethodField()
    awaiting_party = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "portal",
            "portal_name",
            "portal_domain",
            "subject",
            "status",
            "awaiting_party",
            "project",
            "project_name",
            "task",
            "task_title",
            "created_by",
            "created_by_name",
            "message_count",
            "created_at",
            "updated_at",
            "closed_at",
        )
        read_only_fields = fields

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return ""

    def get_portal_name(self, obj):
        if obj.portal_id:
            return obj.portal.name or obj.portal.domain
        return ""

    def get_portal_domain(self, obj):
        if obj.portal_id:
            return (obj.portal.domain or "").strip()
        return ""

    def get_project_name(self, obj):
        if obj.project_id:
            return obj.project.name
        return ""

    def get_task_title(self, obj):
        if obj.task_id:
            return obj.task.title
        return ""

    def get_message_count(self, obj):
        if hasattr(obj, "_message_count"):
            return obj._message_count
        # List path skips Count() annotate for speed — UI does not show this yet.
        return 0

    def get_awaiting_party(self, obj):
        from board.tickets import ticket_awaiting_party

        return ticket_awaiting_party(obj)


class SupportTicketSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    portal_domain = serializers.SerializerMethodField()
    project_name = serializers.SerializerMethodField()
    task_title = serializers.SerializerMethodField()
    awaiting_party = serializers.SerializerMethodField()
    messages = SupportTicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "portal",
            "portal_name",
            "portal_domain",
            "subject",
            "body",
            "status",
            "awaiting_party",
            "project",
            "project_name",
            "task",
            "task_title",
            "created_by",
            "created_by_name",
            "messages",
            "created_at",
            "updated_at",
            "closed_at",
        )
        read_only_fields = fields

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return ""

    def get_portal_name(self, obj):
        if obj.portal_id:
            return obj.portal.name or obj.portal.domain
        return ""

    def get_portal_domain(self, obj):
        if obj.portal_id:
            return (obj.portal.domain or "").strip()
        return ""

    def get_project_name(self, obj):
        if obj.project_id:
            return obj.project.name
        return ""

    def get_task_title(self, obj):
        if obj.task_id:
            return obj.task.title
        return ""

    def get_awaiting_party(self, obj):
        from board.tickets import ticket_awaiting_party

        return ticket_awaiting_party(obj)

class BacklogItemSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    assignee_name = serializers.SerializerMethodField()
    converted_project_name = serializers.SerializerMethodField()
    converted_task_title = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    tags = serializers.ListField(
        child=serializers.CharField(max_length=40, trim_whitespace=True),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = BacklogItem
        fields = (
            "id",
            "portal",
            "title",
            "notes",
            "source",
            "status",
            "priority",
            "sort_order",
            "is_pinned",
            "tags",
            "assignee",
            "assignee_name",
            "converted_project",
            "converted_project_name",
            "converted_task",
            "converted_task_title",
            "can_delete",
            "can_edit",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "source",
            "sort_order",
            "converted_project",
            "converted_project_name",
            "converted_task",
            "converted_task_title",
            "can_delete",
            "can_edit",
            "created_by",
            "created_by_name",
            "assignee_name",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {"portal": {"required": False}}

    def get_created_by_name(self, obj):
        if obj.created_by_id:
            return obj.created_by.display_name
        return ""

    def get_assignee_name(self, obj):
        if obj.assignee_id:
            return obj.assignee.display_name
        return ""

    def get_converted_project_name(self, obj):
        if obj.converted_project_id:
            return obj.converted_project.name
        return ""

    def get_converted_task_title(self, obj):
        if obj.converted_task_id:
            return obj.converted_task.title
        return ""

    def get_can_delete(self, obj):
        return self._client_can_change_request(obj)

    def get_can_edit(self, obj):
        return self._client_can_change_request(obj)

    def _client_can_change_request(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_agency", False):
            return True
        return obj.source == BacklogItem.Source.CLIENT and not obj.is_accepted()

    def validate_tags(self, value):
        cleaned = []
        seen = set()
        for raw in value or []:
            tag = (raw or "").strip().lower()[:40]
            if not tag or tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
            if len(cleaned) >= 8:
                break
        return cleaned

    def validate_status(self, value):
        allowed = {c.value for c in BacklogItem.Status}
        if value not in allowed:
            raise serializers.ValidationError("Некорректный статус")
        return value

    def validate_priority(self, value):
        allowed = {c.value for c in BacklogItem.Priority}
        if value not in allowed:
            raise serializers.ValidationError("Некорректный приоритет")
        return value


class SupportTicketCreateSerializer(serializers.Serializer):
    portal = serializers.IntegerField(min_value=1)
    subject = serializers.CharField(max_length=500, trim_whitespace=True)
    body = serializers.CharField(trim_whitespace=True)
    project = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    task = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class SupportTicketMessageCreateSerializer(serializers.Serializer):
    text = serializers.CharField(trim_whitespace=True)
