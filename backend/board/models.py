from pathlib import Path
from uuid import uuid4

from django.db import models
from django.utils import timezone

from portals.models import BitrixUser, Portal


def attachment_upload_to(instance, filename: str) -> str:
    """Store on disk under a unique name; keep the real name in original_name."""
    ext = Path(filename or "").suffix.lower()[:20]
    stamp = timezone.now().strftime("%Y/%m")
    return f"attachments/{stamp}/{uuid4().hex}{ext}"


class Project(models.Model):
    portal = models.ForeignKey(Portal, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Agency Bitrix: parent task inside company workgroup (GROUP_ID)
    bitrix_task_id = models.CharField(max_length=64, blank=True)
    bitrix_group_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.portal})"


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SYNCED = "synced", "Synced"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.TODO)
    bitrix_task_id = models.CharField(max_length=64, blank=True)
    agency_bitrix_task_id = models.CharField(max_length=64, blank=True)
    sync_status = models.CharField(
        max_length=16, choices=SyncStatus.choices, default=SyncStatus.PENDING
    )
    sync_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def portal(self):
        return self.project.portal


class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comments",
    )
    author_name = models.CharField(max_length=255, blank=True)
    text = models.TextField()
    is_system = models.BooleanField(default=False)
    # Bitrix comment ids (client / agency task copies) — prevent echo duplicates
    bitrix_comment_id = models.CharField(max_length=64, blank=True, db_index=True)
    agency_bitrix_comment_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.task_id}"


class Attachment(models.Model):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="attachments", null=True, blank=True
    )
    comment = models.ForeignKey(
        Comment, on_delete=models.CASCADE, related_name="attachments", null=True, blank=True
    )
    file = models.FileField(upload_to=attachment_upload_to)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    # Bitrix Disk / task file ids (client / agency copies)
    bitrix_file_id = models.CharField(max_length=64, blank=True, db_index=True)
    agency_bitrix_file_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or self.file.name


class TimeEntry(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="time_entries")
    author = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_entries",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=500, blank=True)
    billed_to_deal_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this session was deducted from the CRM deal remaining hours",
    )
    # Bitrix task.elapseditem id when session was posted as a closed record
    bitrix_elapsed_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"TimeEntry task={self.task_id} {self.duration_seconds}s"

    @property
    def is_running(self) -> bool:
        return self.ended_at is None
