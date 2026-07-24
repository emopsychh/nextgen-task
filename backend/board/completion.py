"""Task completion: stop timers + shared chat line with spent time.

Model (locked):
  • Start  ↔ Bitrix (bidirectional status)
  • Pause  → app only (Bitrix stays «in progress»)
  • Done   ↔ Bitrix (bidirectional status)
  • Time tracking stays in the app for clients. Bitrix «Учёт времени»
    is filled manually in Bitrix — the app never posts elapsed items.
  • On done, always write a system chat line with app-tracked time.
"""

from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

TIME_SPENT_MARKER = "Затрачено на задачу:"


def format_tracked_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    if minutes:
        return f"{minutes} мин"
    return f"{total} сек"


def append_time_spent_chat(task, *, author=None) -> bool:
    """One shared system line in the app chat (agency + client both see it)."""
    from board.models import Comment
    from board.timeutils import task_tracked_seconds

    if Comment.objects.filter(
        task=task, is_system=True, text__startswith=TIME_SPENT_MARKER
    ).exists():
        return False

    seconds = int(task_tracked_seconds(task, include_running=False))
    text = f"{TIME_SPENT_MARKER} {format_tracked_duration(seconds)}"
    author_name = ""
    if author is not None:
        author_name = getattr(author, "display_name", None) or ""
    # Bitrix inbound completion has no local actor — show agency team label.
    if not author_name:
        author_name = "Команда"
    Comment.objects.create(
        task=task,
        author=author,
        author_name=author_name,
        text=text,
        is_system=True,
    )
    return True


def finalize_task_completion(task, *, author=None) -> dict:
    """
    After a task becomes done (from app or Bitrix): stop timers and post
    the spent-time system line in the shared app chat.
    Safe to call more than once.
    """
    from board.realtime import publish_task_event
    from board.timeutils import stop_time_entry

    with transaction.atomic():
        task.refresh_from_db()
        for running in task.time_entries.select_for_update().filter(
            ended_at__isnull=True
        ):
            stop_time_entry(running, sync_bitrix=False)

        chat = append_time_spent_chat(task, author=author)

    try:
        publish_task_event(task, kind="task_update")
    except Exception:
        pass

    return {"chat": chat}
