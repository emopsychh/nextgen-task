"""Sync attachments between Nextgen and Bitrix task files / task chat."""

from __future__ import annotations

import base64
import logging
from django.core.files.base import ContentFile

from portals.bitrix import BitrixAPIError, BitrixClient

from board.status_sync import resolve_all_bitrix_task_sources

logger = logging.getLogger(__name__)


def _extract_file_id(result) -> str:
    """Prefer Drive object ID over internal FILE_ID (required by tasks.task.files.attach)."""
    if result is None:
        return ""
    if isinstance(result, (int, float)):
        return str(int(result))
    if isinstance(result, str) and result.isdigit():
        return result
    if isinstance(result, dict):
        # Drive object id first — FILE_ID is a different namespace and breaks attach/chat.
        for key in (
            "ID",
            "id",
            "OBJECT_ID",
            "objectId",
            "FILE",
            "file",
            "result",
        ):
            val = result.get(key)
            if isinstance(val, (int, float)):
                return str(int(val))
            if isinstance(val, str) and val.isdigit():
                return val
            if isinstance(val, dict):
                nested = _extract_file_id(val)
                if nested:
                    return nested
        # Last resort only
        for key in ("FILE_ID", "fileId"):
            val = result.get(key)
            if isinstance(val, (int, float)):
                return str(int(val))
            if isinstance(val, str) and val.isdigit():
                return val
    return ""


def _disk_doc_ref(disk_file_id: str) -> str:
    """UF_FORUM_MESSAGE_DOC needs `n{driveId}` for newly uploaded Disk files."""
    fid = str(disk_file_id or "").strip()
    if not fid:
        return ""
    if fid[0] in "nNfF" and fid[1:].isdigit():
        return fid
    return f"n{fid}"


def _resolve_upload_folder(client: BitrixClient) -> str:
    """Find a Disk folder we can upload into (app storage or first available)."""
    try:
        storage = client.get_app_storage()
        folder_id = (
            storage.get("ROOT_OBJECT_ID")
            or storage.get("rootObjectId")
            or storage.get("ID")
            or storage.get("id")
            or ""
        )
        if folder_id:
            return str(folder_id)
    except BitrixAPIError as exc:
        logger.info("disk.storage.getforapp: %s", exc)

    try:
        rows = client.call("disk.storage.getlist") or []
        if isinstance(rows, dict):
            rows = rows.get("result") or rows.get("storages") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                folder_id = (
                    row.get("ROOT_OBJECT_ID")
                    or row.get("rootObjectId")
                    or row.get("ID")
                    or row.get("id")
                    or ""
                )
                if folder_id:
                    return str(folder_id)
    except BitrixAPIError as exc:
        logger.info("disk.storage.getlist: %s", exc)
    return ""


def _post_file_to_task_chat(
    *,
    client: BitrixClient,
    bitrix_task_id: str,
    disk_file_id: str,
    name: str,
    message: str | None = None,
) -> None:
    """
    Make the file visible in the Bitrix task comment/chat stream
    (not only on the Files tab).
    """
    # Marker lets comment_sync skip echo when pulling Bitrix → app.
    body = (message or "").strip()
    text = f"[Файл из Nextgen] {body}" if body else f"[Файл из Nextgen] {name}"
    doc_ref = _disk_doc_ref(disk_file_id)
    errors: list[str] = []

    # 1) Classic task comment with Disk attachment (shows in task chat)
    if doc_ref:
        for fields in (
            {"POST_MESSAGE": text, "UF_FORUM_MESSAGE_DOC": [doc_ref]},
            {"POST_MESSAGE": text, "UF_FORUM_MESSAGE_DOC": [disk_file_id]},
        ):
            try:
                client.call(
                    "task.commentitem.add",
                    {"TASKID": bitrix_task_id, "FIELDS": fields},
                )
                return
            except BitrixAPIError as exc:
                errors.append(f"commentitem: {exc}")

    # 2) IM disk commit into the task chat (modern Bitrix builds)
    try:
        task_data = client.get_task(bitrix_task_id) or {}
        chat_id = (
            task_data.get("chatId")
            or task_data.get("CHAT_ID")
            or task_data.get("chat_id")
            or ""
        )
        if chat_id:
            try:
                client.call(
                    "im.disk.file.commit",
                    {
                        "CHAT_ID": int(chat_id) if str(chat_id).isdigit() else chat_id,
                        "FILE_ID": [int(disk_file_id) if str(disk_file_id).isdigit() else disk_file_id],
                        "MESSAGE": text,
                    },
                )
                return
            except BitrixAPIError as exc:
                errors.append(f"im.disk.file.commit: {exc}")
            try:
                client.call(
                    "im.disk.file.commit",
                    {
                        "DIALOG_ID": f"chat{chat_id}",
                        "FILE_ID": [int(disk_file_id) if str(disk_file_id).isdigit() else disk_file_id],
                        "MESSAGE": text,
                    },
                )
                return
            except BitrixAPIError as exc:
                errors.append(f"im.disk.file.commit dialog: {exc}")
    except BitrixAPIError as exc:
        errors.append(f"get_task chat: {exc}")

    if errors:
        logger.info(
            "post file to task chat failed task=%s file=%s: %s",
            bitrix_task_id,
            disk_file_id,
            "; ".join(errors),
        )


def upload_and_attach(*, client: BitrixClient, bitrix_task_id: str, attachment) -> str:
    """
    Upload local file to Bitrix Disk, attach to the task Files tab,
    and post into the task chat stream.
    Returns Bitrix Drive file id when known.
    """
    from board.naming import display_attachment_name

    name = display_attachment_name(attachment)
    with attachment.file.open("rb") as fh:
        content = fh.read()
    if not content:
        raise BitrixAPIError("empty file")

    b64 = base64.b64encode(content).decode("ascii")
    errors: list[str] = []
    disk_file_id = ""

    # 1) Disk upload
    folder_id = _resolve_upload_folder(client)
    if folder_id:
        try:
            uploaded = client.upload_file_to_folder(folder_id, name, content)
            disk_file_id = _extract_file_id(uploaded)
            if not disk_file_id:
                errors.append(f"upload no id: {uploaded}")
        except BitrixAPIError as exc:
            errors.append(f"disk upload: {exc}")

    # 2) Attach to Files tab (best-effort)
    if disk_file_id:
        try:
            client.attach_file_to_task(bitrix_task_id, disk_file_id)
        except BitrixAPIError as exc:
            errors.append(f"attach: {exc}")

    # 3) Always try to show the file in task chat
    if disk_file_id:
        _post_file_to_task_chat(
            client=client,
            bitrix_task_id=bitrix_task_id,
            disk_file_id=disk_file_id,
            name=name,
        )
        return disk_file_id

    # 4) Legacy task.item.addfile with base64 (no separate Drive id)
    for params in (
        {"TASK_ID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
        {"TASKID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
        {"TASK_ID": bitrix_task_id, "NAME": name, "CONTENT": b64},
        {"taskId": bitrix_task_id, "file": {"name": name, "content": b64}},
    ):
        try:
            result = client.call("task.item.addfile", params)
            fid = _extract_file_id(result)
            if fid:
                _post_file_to_task_chat(
                    client=client,
                    bitrix_task_id=bitrix_task_id,
                    disk_file_id=fid,
                    name=name,
                )
                return fid
            errors.append(f"task.item.addfile no id: {result}")
        except BitrixAPIError as exc:
            errors.append(f"task.item.addfile: {exc}")

    raise BitrixAPIError("; ".join(errors) or "attach failed")


def pull_attachments_from_bitrix(task) -> int:
    """Import Bitrix task files missing locally. Returns created count."""
    from board.models import Attachment

    sources = resolve_all_bitrix_task_sources(task)
    if not sources:
        return 0

    created = 0
    for portal, bitrix_id in sources:
        client = BitrixClient(portal)
        try:
            rows = client.list_task_files(bitrix_id)
        except BitrixAPIError as exc:
            logger.info("list task files failed task=%s: %s", task.id, exc)
            continue
        is_client = portal.id == task.project.portal_id
        for row in rows:
            fid = str(
                row.get("ID")
                or row.get("id")
                or row.get("FILE_ID")
                or row.get("fileId")
                or row.get("ATTACHMENT_ID")
                or ""
            )
            if not fid or fid == "0":
                continue
            exists = (
                Attachment.objects.filter(task=task)
                .filter(
                    **(
                        {"bitrix_file_id": fid}
                        if is_client
                        else {"agency_bitrix_file_id": fid}
                    )
                )
                .exists()
            )
            if exists:
                continue
            name = str(
                row.get("NAME")
                or row.get("name")
                or row.get("ORIGINAL_NAME")
                or row.get("FILE_NAME")
                or f"file-{fid}"
            )
            try:
                content = client.download_disk_file(fid)
            except BitrixAPIError as exc:
                logger.info("download file %s failed: %s", fid, exc)
                continue
            if not content:
                continue
            from board.naming import client_filename

            att = Attachment(task=task, original_name=client_filename(name))
            if is_client:
                att.bitrix_file_id = fid
            else:
                att.agency_bitrix_file_id = fid
            att.file.save(client_filename(name), ContentFile(content), save=False)
            att.save()
            created += 1
    return created
