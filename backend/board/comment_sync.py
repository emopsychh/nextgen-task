"""Pull Bitrix task comments into Nextgen (Bitrix → app)."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from portals.bitrix import BitrixAPIError, BitrixClient

from board.status_sync import find_local_task_for_bitrix

logger = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r"^[^:\n]{1,80}:\s+")

# Bitrix auto activity lines (status / deadline / timer) — do not import into app chat.
_BITRIX_SYSTEM_LOG_RE = re.compile(
    r"(?i)("
    r"изменил(?:а)?\s+крайний\s+срок|"
    r"установил(?:а)?\s+крайний\s+срок|"
    r"снял(?:а)?\s+крайний\s+срок|"
    r"остановил(?:а)?\s+работу|"
    r"приостановил(?:а)?\s+работу|"
    r"начал(?:а)?\s+работу|"
    r"возобновил(?:а)?\s+работу|"
    r"завершил(?:а)?\s+(?:работу|задач)|"
    r"время\s+выполнения|"
    r"changed\s+the\s+deadline|"
    r"paused\s+the\s+task|"
    r"started\s+work|"
    r"completed\s+the\s+task|"
    r"time\s+spent"
    r")"
)

# Outbound file posts from sync_attachment_to_bitrix — already in app as Attachment.
_NEXTGEN_FILE_MARKER_RE = re.compile(r"(?i)\[Файл из Nextgen\]")


def is_bitrix_system_log_comment(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return bool(_BITRIX_SYSTEM_LOG_RE.search(cleaned))


def is_nextgen_file_echo(text: str) -> bool:
    return bool(_NEXTGEN_FILE_MARKER_RE.search(text or ""))


def _extract_comment_id(result) -> str:
    if result is None:
        return ""
    if isinstance(result, (int, float)):
        return str(int(result))
    if isinstance(result, str) and result.isdigit():
        return result
    if isinstance(result, dict):
        for key in ("id", "ID", "COMMENT_ID", "commentId", "result"):
            val = result.get(key)
            if val is not None and val != "" and val != result:
                extracted = _extract_comment_id(val)
                if extracted:
                    return extracted
            if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
                return str(int(val)) if not isinstance(val, str) else val
    return ""


def _parse_post_date(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if timezone.is_aware(raw) else timezone.make_aware(raw, timezone.utc)
    text = str(raw).strip()
    dt = parse_datetime(text.replace(" ", "T", 1) if " " in text and "T" not in text else text)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _normalize_message(author_name: str, post_message: str) -> str:
    text = (post_message or "").strip()
    if not text:
        return ""
    # Outbound sync posts "Name: message" — strip when AUTHOR_NAME already set
    if author_name and text.lower().startswith(author_name.lower() + ":"):
        text = text[len(author_name) + 1 :].lstrip()
    elif _PREFIX_RE.match(text) and author_name:
        text = _PREFIX_RE.sub("", text, count=1)
    return text.strip()


def _already_have_comment(task, bitrix_comment_id: str) -> bool:
    if not bitrix_comment_id or bitrix_comment_id == "0":
        return False
    from django.db.models import Q

    from board.models import Comment

    return Comment.objects.filter(task=task).filter(
        Q(bitrix_comment_id=bitrix_comment_id) | Q(agency_bitrix_comment_id=bitrix_comment_id)
    ).exists()


def upsert_comment_from_bitrix_payload(
    *,
    task,
    portal,
    payload: dict,
    bitrix_comment_id: str = "",
) -> bool:
    """Create local Comment from Bitrix commentitem payload. Returns True if created."""
    from board.models import Comment

    cid = bitrix_comment_id or str(payload.get("ID") or payload.get("id") or "")
    if cid and cid != "0" and _already_have_comment(task, cid):
        return False

    author_name = str(
        payload.get("AUTHOR_NAME")
        or payload.get("AUTHOR_NAME_FORMATTED")
        or payload.get("authorName")
        or ""
    ).strip()
    raw_text = str(payload.get("POST_MESSAGE") or payload.get("MESSAGE") or payload.get("text") or "")
    text = _normalize_message(author_name, raw_text)
    if not text:
        return False

    # Skip Bitrix built-in status/deadline/timer log lines — they loop in chat.
    if is_bitrix_system_log_comment(text):
        return False

    # Skip our own file-sync posts (file already exists as Attachment in app).
    if is_nextgen_file_echo(text):
        return False

    # Echo guard: we just posted this outbound (same text, very recent, still without id)
    recent = (
        Comment.objects.filter(task=task, text=text, is_system=False)
        .order_by("-created_at")
        .first()
    )
    if recent and not recent.bitrix_comment_id and not recent.agency_bitrix_comment_id:
        age = (timezone.now() - recent.created_at).total_seconds()
        if age < 120:
            client_portal_id = task.project.portal_id
            if portal.id == client_portal_id:
                recent.bitrix_comment_id = cid if cid != "0" else recent.bitrix_comment_id
            else:
                recent.agency_bitrix_comment_id = cid if cid != "0" else recent.agency_bitrix_comment_id
            recent.save(update_fields=["bitrix_comment_id", "agency_bitrix_comment_id", "updated_at"])
            return False

    created_at = _parse_post_date(payload.get("POST_DATE") or payload.get("CREATED") or payload.get("postDate"))

    comment = Comment(
        task=task,
        author=None,
        author_name=author_name or "Bitrix",
        text=text,
        is_system=False,
        bitrix_comment_id=cid if (cid and cid != "0" and portal.id == task.project.portal_id) else "",
        agency_bitrix_comment_id=cid
        if (cid and cid != "0" and portal.id != task.project.portal_id)
        else "",
    )
    comment.save()
    if created_at:
        Comment.objects.filter(pk=comment.pk).update(created_at=created_at)
    return True


def ingest_bitrix_comment_event(*, portal, bitrix_task_id: str, data: dict) -> dict:
    after = data.get("FIELDS_AFTER") or data.get("fields_after") or {}
    if not isinstance(after, dict):
        after = {}
    comment_id = str(after.get("ID") or after.get("id") or data.get("ID") or "")
    task_id = str(
        after.get("TASK_ID")
        or after.get("TASKID")
        or after.get("taskId")
        or bitrix_task_id
        or ""
    )
    message_id = str(after.get("MESSAGE_ID") or after.get("messageId") or "")

    task = find_local_task_for_bitrix(portal=portal, bitrix_task_id=task_id)
    if not task:
        return {"ok": False, "reason": "unknown_task"}

    client = BitrixClient(portal)

    if comment_id and comment_id != "0":
        if _already_have_comment(task, comment_id):
            return {"ok": True, "skipped": "exists", "task_id": task.id}
        try:
            payload = client.get_task_comment(task_id, comment_id)
        except BitrixAPIError as exc:
            logger.info("comment get failed task=%s comment=%s: %s", task_id, comment_id, exc)
            payload = {}
        if payload:
            created = upsert_comment_from_bitrix_payload(
                task=task, portal=portal, payload=payload, bitrix_comment_id=comment_id
            )
            return {"ok": True, "task_id": task.id, "created": created, "comment_id": comment_id}

    # New task card / fallback: pull full list
    created_n = pull_comments_from_bitrix(task, portal=portal, bitrix_task_id=task_id)
    return {
        "ok": True,
        "task_id": task.id,
        "pulled": created_n,
        "message_id": message_id or None,
    }


def pull_comments_from_bitrix(task, *, portal=None, bitrix_task_id: str = "") -> int:
    """Import missing comments from Bitrix into local chat. Returns created count."""
    from board.status_sync import resolve_bitrix_task_source

    if portal is None or not bitrix_task_id:
        portal, bitrix_task_id = resolve_bitrix_task_source(task)
    if not portal or not bitrix_task_id:
        return 0

    client = BitrixClient(portal)
    try:
        rows = client.list_task_comments(bitrix_task_id)
    except BitrixAPIError as exc:
        logger.info("comment list failed task=%s: %s", task.id, exc)
        return 0

    created = 0
    for row in rows:
        cid = str(row.get("ID") or row.get("id") or "")
        if upsert_comment_from_bitrix_payload(
            task=task, portal=portal, payload=row, bitrix_comment_id=cid
        ):
            created += 1
    return created
