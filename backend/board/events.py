from datetime import date

from .models import Comment, Task


def _format_due(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y")


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
    """Create system chat lines for deadline changes only.

    Status transitions are logged by Bitrix itself after sync — do not
    duplicate them in our chat (or as Bitrix comments).
    """
    _ = old_status  # status changes are logged by Bitrix after sync
    author_name = ""
    if author is not None:
        author_name = getattr(author, "display_name", None) or ""
    created: list[Comment] = []

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
