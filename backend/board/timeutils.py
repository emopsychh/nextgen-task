from django.conf import settings
from django.db.models import Sum
from django.utils import timezone


def format_duration_ru(total_seconds: int) -> str:
    """Human-readable Russian duration, e.g. «2 ч 15 мин»."""
    seconds = max(0, int(total_seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes or (hours and not secs):
        parts.append(f"{minutes} мин")
    elif not hours:
        if secs and not minutes:
            parts.append(f"{secs} сек")
        else:
            parts.append(f"{minutes} мин")
    if hours and secs and not minutes:
        parts.append(f"{secs} сек")
    return " ".join(parts) if parts else "0 мин"


def enqueue_time_entry_billing(entry_id: int) -> None:
    from board.tasks import post_time_entry_to_deal

    if settings.CELERY_TASK_ALWAYS_EAGER:
        post_time_entry_to_deal(entry_id)
    else:
        post_time_entry_to_deal.delay(entry_id)


def enqueue_timer_bitrix_sync(entry_id: int, action: str) -> None:
    """Mirror timer start/stop onto Bitrix «Учёт времени» (agency subtask)."""
    from board.tasks import sync_timer_to_bitrix

    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_timer_to_bitrix(entry_id, action)
    else:
        sync_timer_to_bitrix.delay(entry_id, action)


def stop_time_entry(entry, ended_at=None, *, bill: bool = True, sync_bitrix: bool = True) -> int:
    """Close a running entry, optionally bill its duration to the CRM deal."""
    if entry.ended_at is not None:
        return entry.duration_seconds
    end = ended_at or timezone.now()
    duration = max(0, int((end - entry.started_at).total_seconds()))
    entry.ended_at = end
    entry.duration_seconds = duration
    entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
    # No live Bitrix timer sync here: elapsed time is posted to Bitrix «Учёт
    # времени» only when the task is completed (board.tasks._post_time_entries_elapsed).
    # `sync_bitrix` is kept for call-site compatibility but no longer pushes a timer.
    _ = sync_bitrix
    if bill and duration > 0 and getattr(entry, "billed_to_deal_at", None) is None:
        enqueue_time_entry_billing(entry.id)
    return duration


def task_tracked_seconds(task, *, include_running: bool = True) -> int:
    """Sum closed entries; optionally add live elapsed for a running entry."""
    closed = (
        task.time_entries.filter(ended_at__isnull=False).aggregate(total=Sum("duration_seconds"))[
            "total"
        ]
        or 0
    )
    if not include_running:
        return int(closed)
    running = task.time_entries.filter(ended_at__isnull=True).first()
    if running:
        closed += max(0, int((timezone.now() - running.started_at).total_seconds()))
    return int(closed)
