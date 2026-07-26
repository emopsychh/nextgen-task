"""Finalize task timers and synchronize total elapsed time to Bitrix."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

TIME_SPENT_MARKER = "Затрачено на задачу:"


def finalize_task_completion(task, *, author=None) -> dict:
    """
    After completion from either app or Bitrix: stop local timers and enqueue
    synchronization of the final total into Bitrix «Учёт времени».
    Safe to call more than once.
    """
    del author  # Kept for call-site compatibility.
    from board.realtime import publish_task_event
    from board.tasks import sync_completion_time_to_bitrix
    from board.timeutils import stop_time_entry

    with transaction.atomic():
        from board.models import Task

        task = Task.objects.select_for_update().get(pk=task.pk)
        for running in task.time_entries.select_for_update().filter(
            ended_at__isnull=True
        ):
            stop_time_entry(running, sync_bitrix=False)

    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_completion_time_to_bitrix(task.id)
    else:
        sync_completion_time_to_bitrix.delay(task.id)

    try:
        publish_task_event(task, kind="task_update")
    except Exception:
        pass

    return {"elapsed_sync_enqueued": True}
