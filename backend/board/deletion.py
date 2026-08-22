"""Rules for deleting tasks/projects from the app (agency)."""

from __future__ import annotations

from board.models import Task


def task_has_work_content(task: Task) -> bool:
    """True when the task has meaningful activity beyond a bare title."""
    if (task.description or "").strip():
        return True
    if (task.outcome or "").strip():
        return True
    if task.due_date is not None:
        return True
    if task.is_important:
        return True
    if task.working_started_at is not None:
        return True
    if task.comments.filter(is_system=False).exists():
        return True
    if task.attachments.exists():
        return True
    if task.time_entries.filter(ended_at__isnull=False, duration_seconds__gt=0).exists():
        return True
    if task.time_entries.filter(ended_at__isnull=True).exists():
        return True
    return False


def task_is_app_deletable(task: Task) -> bool:
    """Unfinished / not-started shell tasks may be removed by agency.

    Done tasks are kept for history. Anything with description, comments,
    time, files, due date, importance, or live presence is not deletable.
    """
    if task.status == Task.Status.DONE:
        return False
    return not task_has_work_content(task)


def project_is_app_deletable(project) -> bool:
    """Empty projects (no tasks) may be removed by agency."""
    return not project.tasks.exists()
