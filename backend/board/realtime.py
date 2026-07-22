"""Redis fan-out for soft realtime (SSE + cursor polling)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "nextgen:portal:"
CURSOR_PREFIX = "nextgen:cursor:"


def _redis():
    try:
        import redis

        url = getattr(settings, "REDIS_URL", None) or "redis://localhost:6379/0"
        return redis.Redis.from_url(url, decode_responses=True)
    except Exception as exc:
        logger.info("realtime redis unavailable: %s", exc)
        return None


def portal_channel(portal_id: int) -> str:
    return f"{CHANNEL_PREFIX}{int(portal_id)}"


def bump_cursor(portal_id: int) -> int:
    client = _redis()
    if not client:
        return int(time.time())
    try:
        return int(client.incr(f"{CURSOR_PREFIX}{int(portal_id)}"))
    except Exception as exc:
        logger.info("cursor bump failed: %s", exc)
        return int(time.time())


def get_cursor(portal_id: int) -> int:
    client = _redis()
    if not client:
        return 0
    try:
        val = client.get(f"{CURSOR_PREFIX}{int(portal_id)}")
        return int(val or 0)
    except Exception:
        return 0


def publish_portal_event(portal_id: int | None, payload: dict[str, Any] | None = None) -> None:
    """Notify SSE listeners that portal data changed."""
    if not portal_id:
        return
    version = bump_cursor(int(portal_id))
    body = {"v": version, "ts": int(time.time()), **(payload or {})}
    client = _redis()
    if not client:
        return
    try:
        client.publish(portal_channel(int(portal_id)), json.dumps(body, default=str))
    except Exception as exc:
        logger.info("publish_portal_event failed: %s", exc)


def publish_task_event(task, *, kind: str = "task") -> None:
    try:
        portal_id = task.project.portal_id
    except Exception:
        return
    publish_portal_event(
        portal_id,
        {"kind": kind, "task_id": getattr(task, "id", None), "project_id": getattr(task, "project_id", None)},
    )
