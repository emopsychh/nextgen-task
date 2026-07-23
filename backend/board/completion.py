"""Task completion: app time → Bitrix «Учёт времени» + shared chat line.

Model (locked):
  • Start  ↔ Bitrix (bidirectional)
  • Pause  → app only (Bitrix stays «in progress»)
  • Done   ↔ Bitrix (bidirectional)
  • Elapsed time always comes from the app and is written into Bitrix
    time tracking on both copies when the task becomes done.
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


def _ensure_time_tracking(client, bitrix_task_id: str) -> None:
    from portals.bitrix import BitrixAPIError

    try:
        client.update_task(bitrix_task_id, {"ALLOW_TIME_TRACKING": "Y"})
    except BitrixAPIError as exc:
        logger.info(
            "ALLOW_TIME_TRACKING enable failed task=%s: %s", bitrix_task_id, exc
        )


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
    Comment.objects.create(
        task=task,
        author=author,
        author_name=author_name,
        text=text,
        is_system=True,
    )
    return True


def post_app_elapsed_to_bitrix(task) -> dict:
    """
    Push closed TimeEntry rows into Bitrix «Учёт времени» on the agency subtask.

    Enables ALLOW_TIME_TRACKING if it was off. Idempotent via bitrix_elapsed_id.
    Client Bitrix tasks are not used.
    """
    from board.status_sync import resolve_all_bitrix_task_sources
    from board.tasks import _post_time_entries_elapsed
    from portals.bitrix import BitrixAPIError, BitrixClient

    posted = {"agency": False}
    for portal, bitrix_id in resolve_all_bitrix_task_sources(task):
        client = BitrixClient(portal)
        _ensure_time_tracking(client, bitrix_id)
        try:
            _post_time_entries_elapsed(
                client, str(bitrix_id), task, portal, id_attr="bitrix_elapsed_id"
            )
            posted["agency"] = True
        except BitrixAPIError as exc:
            logger.info(
                "post elapsed failed task=%s portal=%s: %s",
                task.id,
                portal.id,
                exc,
            )
    return posted


def finalize_task_completion(task, *, author=None) -> dict:
    """
    After a task becomes done (from app or Bitrix): stop timers, chat line,
    push app time into Bitrix Учёт времени.
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

    elapsed = {}
    try:
        elapsed = post_app_elapsed_to_bitrix(task)
    except Exception:
        logger.exception("post_app_elapsed_to_bitrix failed task=%s", task.id)

    try:
        publish_task_event(task, kind="task_update")
    except Exception:
        pass

    return {"chat": chat, "elapsed": elapsed}
