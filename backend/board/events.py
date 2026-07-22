from datetime import date

from .models import Comment, Task
from .timeutils import format_duration_ru, task_tracked_seconds


def _format_due(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y")


def status_event_text(old: str, new: str) -> str | None:
    if old == new:
        return None
    if new == Task.Status.IN_PROGRESS:
        return "начал выполнять задачу"
    if new == Task.Status.DONE:
        return "завершил задачу"
    if old == Task.Status.DONE and new == Task.Status.TODO:
        return "возобновил задачу"
    if old == Task.Status.IN_PROGRESS and new == Task.Status.TODO:
        return "приостановил работу над задачей"
    return "изменил статус задачи"


def due_event_text(old: date | None, new: date | None) -> str | None:
    if old == new:
        return None
    if new and not old:
        return f"установил крайний срок: {_format_due(new)}"
    if old and not new:
        return "снял крайний срок"
    return f"изменил крайний срок: {_format_due(new)}"


def append_task_change_events(
    *,
    task: Task,
    author,
    old_status: str,
    old_due: date | None,
) -> list[Comment]:
    """Create Bitrix-like system chat lines for status / deadline changes."""
    author_name = ""
    if author is not None:
        author_name = getattr(author, "display_name", None) or ""
    created: list[Comment] = []

    status_text = status_event_text(old_status, task.status)
    if status_text:
        if task.status == Task.Status.DONE and old_status != Task.Status.DONE:
            seconds = task_tracked_seconds(task)
            if seconds > 0:
                status_text = f"{status_text} · затрачено {format_duration_ru(seconds)}"
        created.append(
            Comment.objects.create(
                task=task,
                author=author,
                author_name=author_name,
                text=status_text,
                is_system=True,
            )
        )

    due_text = due_event_text(old_due, task.due_date)
    if due_text:
        created.append(
            Comment.objects.create(
                task=task,
                author=author,
                author_name=author_name,
                text=due_text,
                is_system=True,
            )
        )

    return created
