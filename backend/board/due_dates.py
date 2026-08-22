"""Due-date timezone helpers.

Canonical storage is UTC-aware DateTime. Wall-clock interpretation uses the
client portal's IANA timezone. Agency UI displays Europe/Moscow.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.utils.dateparse import parse_datetime

AGENCY_DISPLAY_TZ = "Europe/Moscow"
DEFAULT_PORTAL_TZ = "Europe/Moscow"


def resolve_zone(name: str | None) -> ZoneInfo:
    raw = (name or "").strip() or DEFAULT_PORTAL_TZ
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_PORTAL_TZ)


def portal_zone(portal) -> ZoneInfo:
    if portal is None:
        return resolve_zone(DEFAULT_PORTAL_TZ)
    return resolve_zone(getattr(portal, "timezone", None))


def as_utc(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc)


def wall_to_utc(wall: datetime, tz: ZoneInfo) -> datetime:
    """Interpret a naive (or ignore-tz) wall clock in ``tz`` as UTC."""
    if timezone.is_aware(wall):
        wall = wall.replace(tzinfo=None)
    return timezone.make_aware(wall, tz).astimezone(dt_timezone.utc)


def utc_to_wall(dt: datetime, tz: ZoneInfo) -> datetime:
    """Return naive wall-clock components of ``dt`` in ``tz``."""
    aware = as_utc(dt).astimezone(tz)
    return aware.replace(tzinfo=None)


def format_utc_z(dt: datetime | date | None) -> str | None:
    if not dt:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, dtime(23, 59, 59), tzinfo=dt_timezone.utc)
    else:
        dt = as_utc(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_wall(dt: datetime | date | None, tz: ZoneInfo) -> str:
    """Bitrix-style wall clock without offset in ``tz``."""
    if not dt:
        return ""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return f"{dt.isoformat()}T23:59:59"
    wall = utc_to_wall(dt, tz)
    return wall.strftime("%Y-%m-%dT%H:%M:%S")


def parse_due_value(raw, *, portal_tz: ZoneInfo) -> datetime | None:
    """Parse API/Bitrix due value into UTC-aware datetime.

    - Aware ISO / offset → absolute instant (kept).
    - Naive ISO / RU formats → wall clock in ``portal_tz``.
    - Date-only → end of that day in ``portal_tz``.
    """
    if raw in (None, "", False, "false", "0"):
        return None
    if isinstance(raw, datetime):
        if timezone.is_naive(raw):
            return wall_to_utc(raw, portal_tz)
        return as_utc(raw)
    if isinstance(raw, date):
        return wall_to_utc(datetime.combine(raw, dtime(23, 59, 59)), portal_tz)

    text = str(raw).strip()
    if not text or text.lower() in ("false", "none", "null"):
        return None

    normalized = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    dt = parse_datetime(normalized)
    if dt is None:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(text[:19] if len(text) >= 19 else text, fmt)
                break
            except ValueError:
                continue
    if dt is None and len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            d = date.fromisoformat(text[:10])
            dt = datetime.combine(d, dtime(23, 59, 59))
        except ValueError:
            return None
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return wall_to_utc(dt, portal_tz)
    return as_utc(dt)
