from rest_framework import serializers

from portals.models import Portal
from portals.permissions import can_access_client_portal

from .models import Attachment, Comment, Project, Task, TimeEntry


class AttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

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
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        if obj.file:
            return obj.file.url
        return None


class CommentSerializer(serializers.ModelSerializer):
    author_display = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)

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
    comments = CommentSerializer(many=True, read_only=True)
    attachments = AttachmentSerializer(many=True, read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    portal_id = serializers.IntegerField(source="project.portal_id", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    total_tracked_seconds = serializers.SerializerMethodField()
    active_timer = serializers.SerializerMethodField()
    deal_paid_hours = serializers.SerializerMethodField()
    deal_remaining_hours = serializers.SerializerMethodField()

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
            "bitrix_task_id",
            "agency_bitrix_task_id",
            "sync_status",
            "sync_error",
            "created_by",
            "created_by_name",
            "comments",
            "attachments",
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


class TaskListSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    portal_id = serializers.IntegerField(source="project.portal_id", read_only=True)
    comments_count = serializers.IntegerField(source="comments.count", read_only=True)
    total_tracked_seconds = serializers.SerializerMethodField()

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
            "bitrix_task_id",
            "sync_status",
            "comments_count",
            "total_tracked_seconds",
            "created_at",
            "updated_at",
        )

    def get_total_tracked_seconds(self, obj):
        from .timeutils import task_tracked_seconds

        return task_tracked_seconds(obj, include_running=False)


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
