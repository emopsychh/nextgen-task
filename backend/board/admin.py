from django.contrib import admin

from .models import Attachment, Comment, Project, Task, TimeEntry


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


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "portal", "is_active", "created_at")
    list_filter = ("portal", "is_active")
    search_fields = ("name",)


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