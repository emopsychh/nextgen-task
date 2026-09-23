from __future__ import annotations

from datetime import datetime, timedelta

from .models import ProjectMeeting


DEFAULT_MEETING_MINUTES = 60
WORKDAY_START_HOUR = 9
WORKDAY_END_HOUR = 18


def has_meeting_conflict(
    portal_id: int,
    starts_at: datetime,
    duration_minutes: int = DEFAULT_MEETING_MINUTES,
    *,
    exclude_meeting_id: int | None = None,
) -> bool:
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    queryset = ProjectMeeting.objects.filter(
        project__portal_id=portal_id,
        cancelled_at__isnull=True,
        scheduled_at__lt=ends_at,
        scheduled_at__gte=starts_at - timedelta(days=1),
    )
    if exclude_meeting_id:
        queryset = queryset.exclude(pk=exclude_meeting_id)
    return any(
        meeting.scheduled_at + timedelta(minutes=meeting.duration_minutes) > starts_at
        for meeting in queryset.only("id", "scheduled_at", "duration_minutes")
    )
