from datetime import timedelta

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


def enqueue_timer_bitrix_sync(entry_id: int, action: str = "set") -> None:
    """Push a closed TimeEntry into Bitrix «Учёт времени»."""
    from board.tasks import sync_timer_to_bitrix

    if settings.CELERY_TASK_ALWAYS_EAGER:
        sync_timer_to_bitrix(entry_id, action)
    else:
        sync_timer_to_bitrix.delay(entry_id, action)


def stop_time_entry(entry, ended_at=None, *, bill: bool = True, sync_bitrix: bool = True) -> int:
    """Close a leftover running entry (legacy); bill its duration to the CRM deal."""
    if entry.ended_at is not None:
        return entry.duration_seconds
    end = ended_at or timezone.now()
    duration = max(0, int((end - entry.started_at).total_seconds()))
    entry.ended_at = end
    entry.duration_seconds = duration
    entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
    _ = sync_bitrix
    if bill and duration > 0 and getattr(entry, "billed_to_deal_at", None) is None:
        enqueue_time_entry_billing(entry.id)
    return duration


def set_manual_time_entry(
    task,
    *,
    author,
    duration_seconds: int,
    note: str = "",
    bill: bool = True,
):
    """
    Set the task's tracked time to an absolute hours/minutes value (not add).

    Collapses closed TimeEntry rows into a single record matching the total,
    then syncs that value to Bitrix «Учёт времени».
    """
    from board.models import TimeEntry

    seconds = max(0, int(duration_seconds or 0))

    for running in task.time_entries.filter(ended_at__isnull=True):
        stop_time_entry(running, bill=False, sync_bitrix=False)

    closed = list(
        TimeEntry.objects.filter(task=task, ended_at__isnull=False).order_by("started_at")
    )
    old_billed = sum(
        int(e.duration_seconds or 0) for e in closed if e.billed_to_deal_at is not None
    )

    # Prefer the row already mirrored to Bitrix, else the newest closed row.
    survivor = next((e for e in closed if e.bitrix_elapsed_id), None)
    if survivor is None and closed:
        survivor = closed[-1]

    stale_bitrix_ids: list[str] = []
    for e in closed:
        if survivor and e.pk == survivor.pk:
            continue
        if e.bitrix_elapsed_id:
            stale_bitrix_ids.append(str(e.bitrix_elapsed_id))
        e.delete()

    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=seconds) if seconds else ended_at
    note_clean = (note or "").strip()[:500]

    if seconds <= 0:
        if survivor:
            if survivor.bitrix_elapsed_id:
                stale_bitrix_ids.append(str(survivor.bitrix_elapsed_id))
            survivor.delete()
        if stale_bitrix_ids:
            _enqueue_bitrix_elapsed_cleanup(task.id, stale_bitrix_ids)
        return None

    if survivor:
        survivor.author = author
        survivor.started_at = started_at
        survivor.ended_at = ended_at
        survivor.duration_seconds = seconds
        if note_clean:
            survivor.note = note_clean
        survivor.save(
            update_fields=[
                "author",
                "started_at",
                "ended_at",
                "duration_seconds",
                "note",
                "updated_at",
            ]
        )
        entry = survivor
    else:
        entry = TimeEntry.objects.create(
            task=task,
            author=author,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=seconds,
            note=note_clean,
        )

    if stale_bitrix_ids:
        _enqueue_bitrix_elapsed_cleanup(task.id, stale_bitrix_ids)

    if bill:
        _bill_absolute_time(entry, total_seconds=seconds, old_billed=old_billed)

    enqueue_timer_bitrix_sync(entry.id, "set")
    return entry


def _bill_absolute_time(entry, *, total_seconds: int, old_billed: int) -> None:
    """Bill only the unbilled growth when the absolute total changes."""
    from board.tasks import post_time_entry_to_deal

    delta = max(0, int(total_seconds) - max(0, int(old_billed)))
    if delta <= 0:
        if entry.billed_to_deal_at is None and old_billed > 0:
            entry.billed_to_deal_at = timezone.now()
            entry.save(update_fields=["billed_to_deal_at", "updated_at"])
        return

    # post_time_entry_to_deal spends entry.duration_seconds — bill delta, then restore.
    entry.duration_seconds = delta
    entry.billed_to_deal_at = None
    entry.save(update_fields=["duration_seconds", "billed_to_deal_at", "updated_at"])
    post_time_entry_to_deal(entry.id)
    entry.refresh_from_db()
    entry.duration_seconds = int(total_seconds)
    if entry.billed_to_deal_at is None:
        entry.billed_to_deal_at = timezone.now()
    entry.save(update_fields=["duration_seconds", "billed_to_deal_at", "updated_at"])


# Backwards-compatible name — callers used "add" when the product meant set.
add_manual_time_entry = set_manual_time_entry


def _enqueue_bitrix_elapsed_cleanup(task_id: int, elapsed_ids: list[str]) -> None:
    from board.tasks import cleanup_bitrix_elapsed_items

    ids = [x for x in elapsed_ids if x]
    if not ids:
        return
    if settings.CELERY_TASK_ALWAYS_EAGER:
        cleanup_bitrix_elapsed_items(task_id, ids)
    else:
        cleanup_bitrix_elapsed_items.delay(task_id, ids)


def task_tracked_seconds(task, *, include_running: bool = True) -> int:
    """Sum closed entries; optionally add live elapsed for a leftover running entry."""
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
