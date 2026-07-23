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
    bitrix_task_id = models.CharField(max_length=64, blank=True, db_index=True)
    bitrix_group_id = models.CharField(max_length=64, blank=True, db_index=True)
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
    # Bitrix «Важная задача» → PRIORITY high (2). Two-way synced.
    is_important = models.BooleanField(default=False)
    # Outcome filled when completing the task; shown in work reports.
    outcome = models.TextField(blank=True)
    bitrix_task_id = models.CharField(max_length=64, blank=True, db_index=True)
    agency_bitrix_task_id = models.CharField(max_length=64, blank=True, db_index=True)
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
    # Bitrix task.elapseditem id on the agency copy (closed record)
    bitrix_elapsed_id = models.CharField(max_length=64, blank=True)
    # Same for the client portal Bitrix copy (idempotent dual-post)
    client_bitrix_elapsed_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"TimeEntry task={self.task_id} {self.duration_seconds}s"

    @property
    def is_running(self) -> bool:
        return self.ended_at is None


class WorkReport(models.Model):
    """Live work report for one project — agency sends, client agrees or disputes."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Manager review"
        PENDING_CLIENT = "pending_client", "Pending client"
        DISPUTED = "disputed", "Disputed"
        ACCEPTED = "accepted", "Accepted"
        PAID = "paid", "Paid"

    ACTIVE_STATUSES = (Status.DRAFT, Status.PENDING_CLIENT, Status.DISPUTED)

    # Client portal this report belongs to (may cover several projects).
    portal = models.ForeignKey(
        Portal,
        on_delete=models.CASCADE,
        related_name="work_reports",
        null=True,
        blank=True,
    )
    projects = models.ManyToManyField(Project, related_name="work_reports_m2m", blank=True)
    # Legacy single-project link — kept briefly for migration; prefer `projects`.
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="work_reports",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    created_by = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_work_reports",
    )
    client_comment = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"WorkReport#{self.pk} portal={self.portal_id} {self.status}"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES


class WorkReportEvent(models.Model):
    class Kind(models.TextChoices):
        CREATED = "created", "Created"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        DISPUTED = "disputed", "Disputed"
        PAID = "paid", "Paid"
        REOPENED = "reopened", "Reopened"

    report = models.ForeignKey(WorkReport, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_report_events",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"WorkReportEvent#{self.pk} {self.kind}"


class WorkReportDisputeItem(models.Model):
    report = models.ForeignKey(
        WorkReport, on_delete=models.CASCADE, related_name="dispute_items"
    )
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="work_report_disputes"
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("report", "task")]

    def __str__(self):
        return f"Dispute report={self.report_id} task={self.task_id}"


class WorkReportLine(models.Model):
    """Per-task narrative on a work report (editable in draft)."""

    report = models.ForeignKey(WorkReport, on_delete=models.CASCADE, related_name="lines")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="work_report_lines")
    work_done = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("report", "task")]

    def __str__(self):
        return f"WorkReportLine report={self.report_id} task={self.task_id}"


class WorkReportLineAttachment(models.Model):
    line = models.ForeignKey(
        WorkReportLine, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=attachment_upload_to)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        BitrixUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_report_attachments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name or self.file.name
