"""Finalize task completion: stop timers and announce duration in chat."""

from __future__ import annotations

from django.db import transaction

# Posted to app + Bitrix task chat on complete (not to «Учёт времени»).
COMPLETED_FOR_MARKER = "Задача была завершена за:"
# Legacy marker — still skip on inbound echo.
TIME_SPENT_MARKER = "Затрачено на задачу:"


def is_completion_time_message(text: str) -> bool:
    raw = (text or "").strip()
    return raw.startswith(COMPLETED_FOR_MARKER) or raw.startswith(TIME_SPENT_MARKER)


def finalize_task_completion(task, *, author=None) -> dict:
    """
    After completion from either app or Bitrix:
      1) stop local timers
      2) post «Задача была завершена за: …» to app chat + Bitrix chat
    Does NOT write Bitrix «Учёт времени» — that stays manual.
    Safe to call more than once.
    """
    from board.models import Comment
    from board.realtime import publish_task_event
    from board.timeutils import format_duration_ru, task_tracked_seconds
    from board.views import enqueue_comment_sync

    comment_id = None
    with transaction.atomic():
        from board.models import Task
        from board.timeutils import stop_time_entry

        task = Task.objects.select_for_update().get(pk=task.pk)
        for running in task.time_entries.select_for_update().filter(
            ended_at__isnull=True
        ):
            stop_time_entry(running, sync_bitrix=False)

        # Idempotent: one completion-time line per task.
        already = (
            Comment.objects.filter(task=task, is_system=True)
            .filter(text__startswith=COMPLETED_FOR_MARKER)
            .exists()
        )
        if not already:
            secs = int(task_tracked_seconds(task, include_running=False))
            text = f"{COMPLETED_FOR_MARKER} {format_duration_ru(secs)}"
            author_name = ""
            if author is not None:
                author_name = getattr(author, "display_name", "") or ""
            comment = Comment.objects.create(
                task=task,
                author=author if author is not None else None,
                author_name=author_name or "Система",
                text=text,
                is_system=True,
            )
            comment_id = comment.id

    if comment_id:
        enqueue_comment_sync(comment_id)

    try:
        publish_task_event(task, kind="task_update")
    except Exception:
        pass

    return {"elapsed_sync_enqueued": False, "completion_comment_id": comment_id}
