from django.contrib import admin

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
    WorkReportLine,
    WorkReportLineAttachment,
)


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    readonly_fields = ("created_at",)


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ("created_at",)


class TimeEntryInline(admin.TabularInline):
    model = TimeEntry
    extra = 0
    readonly_fields = ("created_at", "updated_at")


class WorkReportEventInline(admin.TabularInline):
    model = WorkReportEvent
    extra = 0
    readonly_fields = ("kind", "actor", "payload", "created_at")


class WorkReportDisputeInline(admin.TabularInline):
    model = WorkReportDisputeItem
    extra = 0
    readonly_fields = ("task", "note", "created_at")


class WorkReportLineInline(admin.TabularInline):
    model = WorkReportLine
    extra = 0
    readonly_fields = ("task", "work_done", "updated_at")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "portal", "bitrix_task_id", "bitrix_group_id", "is_active", "created_at")
    list_filter = ("portal", "is_active")
    search_fields = ("name", "bitrix_task_id")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "project",
        "status",
        "sync_status",
        "bitrix_task_id",
        "due_date",
        "created_at",
    )
    list_filter = ("status", "sync_status", "project__portal")
    search_fields = ("title", "bitrix_task_id")
    inlines = [CommentInline, AttachmentInline, TimeEntryInline]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("task", "author_name", "created_at")
    search_fields = ("text", "author_name")


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "task", "comment", "created_at")


@admin.register(TimeEntry)
class TimeEntryAdmin(admin.ModelAdmin):
    list_display = ("task", "author", "started_at", "ended_at", "duration_seconds")
    list_filter = ("ended_at",)
    search_fields = ("task__title", "note")


@admin.register(WorkReport)
class WorkReportAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "created_by", "sent_at", "accepted_at", "paid_at")
    list_filter = ("status",)
    search_fields = ("project__name",)
    inlines = [WorkReportLineInline, WorkReportEventInline, WorkReportDisputeInline]


@admin.register(WorkReportLineAttachment)
class WorkReportLineAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "line", "created_at")


class SupportTicketMessageInline(admin.TabularInline):
    model = SupportTicketMessage
    extra = 0
    readonly_fields = ("author", "text", "created_at")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "portal", "status", "created_by", "created_at", "closed_at")
    list_filter = ("status",)
    search_fields = ("subject", "body")
    inlines = [SupportTicketMessageInline]
