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


def stop_time_entry(entry, ended_at=None) -> int:
    """Close a running entry and return duration_seconds."""
    if entry.ended_at is not None:
        return entry.duration_seconds
    end = ended_at or timezone.now()
    duration = max(0, int((end - entry.started_at).total_seconds()))
    entry.ended_at = end
    entry.duration_seconds = duration
    entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
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
