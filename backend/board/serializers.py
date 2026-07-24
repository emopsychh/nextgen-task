from django.conf import settings
from rest_framework import serializers

from portals.models import Portal
from portals.permissions import can_access_client_portal

from .models import (
    Attachment,
    Comment,
    Project,
    SupportTicket,
    SupportTicketMessage,
    Task,
    TimeEntry,
    WorkReport,
    WorkReportDisputeItem,
    WorkReportEvent,
)
from .naming import display_attachment_name


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
            if settings.CELERY_TASK_ALWAYS_EAGER:
                sync_task_to_bitrix(instance.id)
            else:
                sync_task_to_bitrix.delay(instance.id)
        except Exception:
            pass
    return instance.title or ""


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
        return obj.author_name or "Unknown"


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
    due_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        format="%Y-%m-%dT%H:%M:%S",
        input_formats=[
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d",
        ],
    )

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
            "created_at",
            "updated_at",
        )

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return None

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
        running = obj.time_entries.filter(ended_at__isnull=True).order_by("-started_at").first()
        if not running:
            return None
        return TimeEntrySerializer(running).data

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
    due_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        format="%Y-%m-%dT%H:%M:%S",
        input_formats=[
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d",
        ],
    )

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
            "status",
            "is_important",
            "bitrix_task_id",
            "sync_status",
            "created_by",
            "created_by_name",
            "created_by_role",
            "comments_count",
            "total_tracked_seconds",
            "created_at",
            "updated_at",
        )

    def get_comments_count(self, obj):
        annotated = getattr(obj, "_comments_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.comments.count()

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return None

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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["title"] = _clean_task_title(instance)
        return data


class ProjectSerializer(serializers.ModelSerializer):
    tasks_count = serializers.SerializerMethodField()
    done_count = serializers.SerializerMethodField()
    portal_name = serializers.CharField(source="portal.name", read_only=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "portal",
            "portal_name",
            "name",
            "description",
            "is_active",
            "bitrix_task_id",
            "bitrix_group_id",
            "tasks_count",
            "done_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "bitrix_task_id",
            "bitrix_group_id",
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

    def validate_portal(self, portal: Portal):
        request = self.context.get("request")
        if request and not can_access_client_portal(request.user, portal):
            raise serializers.ValidationError("No access to this portal")
        if portal.role not in (Portal.Role.CLIENT, Portal.Role.AGENCY):
            # Allow creating projects on client portals primarily
            pass
        return portal


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
    deal_hours = serializers.SerializerMethodField()
    events = WorkReportEventSerializer(many=True, read_only=True)
    dispute_items = WorkReportDisputeItemSerializer(many=True, read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkReport
        fields = (
            "id",
            "portal_id",
            "portal_name",
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
            "deal_hours",
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
        from board.reports import report_project_ids

        return report_project_ids(obj)

    def get_project_names(self, obj):
        ids = self.get_project_ids(obj)
        if not ids:
            return []
        return list(
            Project.objects.filter(id__in=ids).order_by("name").values_list("name", flat=True)
        )

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.display_name
        return ""

    def get_projects_detail(self, obj):
        from board.reports import report_projects_payload

        return report_projects_payload(obj)

    def get_total_tracked_seconds(self, obj):
        from board.reports import live_total_seconds_for_projects, report_project_ids

        return live_total_seconds_for_projects(report_project_ids(obj))

    def get_deal_hours(self, obj):
        from board.reports import deal_hours_for_portal, report_portal_id

        pid = report_portal_id(obj)
        return deal_hours_for_portal(pid) if pid else None


class WorkReportListSerializer(serializers.ModelSerializer):
    portal_id = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    project_ids = serializers.SerializerMethodField()
    project_names = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    dispute_count = serializers.SerializerMethodField()
    projects_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkReport
        fields = (
            "id",
            "portal_id",
            "portal_name",
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
            "dispute_count",
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
        by_project = self.context.get("seconds_by_project")
        ids = self.get_project_ids(obj)
        if by_project is not None:
            return sum(int(by_project.get(pid, 0)) for pid in ids)
        from board.reports import live_total_seconds_for_projects

        return live_total_seconds_for_projects(ids)

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
    project_name = serializers.SerializerMethodField()
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "portal",
            "portal_name",
            "subject",
            "status",
            "project",
            "project_name",
            "task",
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

    def get_project_name(self, obj):
        if obj.project_id:
            return obj.project.name
        return ""

    def get_message_count(self, obj):
        if hasattr(obj, "_message_count"):
            return obj._message_count
        return obj.messages.count()


class SupportTicketSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    portal_name = serializers.SerializerMethodField()
    project_name = serializers.SerializerMethodField()
    task_title = serializers.SerializerMethodField()
    messages = SupportTicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "portal",
            "portal_name",
            "subject",
            "body",
            "status",
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

    def get_project_name(self, obj):
        if obj.project_id:
            return obj.project.name
        return ""

    def get_task_title(self, obj):
        if obj.task_id:
            return obj.task.title
        return ""


class SupportTicketCreateSerializer(serializers.Serializer):
    portal = serializers.IntegerField(min_value=1)
    subject = serializers.CharField(max_length=500, trim_whitespace=True)
    body = serializers.CharField(trim_whitespace=True)
    project = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    task = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class SupportTicketMessageCreateSerializer(serializers.Serializer):
    text = serializers.CharField(trim_whitespace=True)
