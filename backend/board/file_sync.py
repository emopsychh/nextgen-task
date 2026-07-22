"""Sync attachments between Nextgen and Bitrix task files."""

from __future__ import annotations

import base64
import logging
from django.core.files.base import ContentFile

from portals.bitrix import BitrixAPIError, BitrixClient

from board.status_sync import resolve_all_bitrix_task_sources

logger = logging.getLogger(__name__)


def _extract_file_id(result) -> str:
    if result is None:
        return ""
    if isinstance(result, (int, float)):
        return str(int(result))
    if isinstance(result, str) and result.isdigit():
        return result
    if isinstance(result, dict):
        # Prefer nested FILE / file / ID
        for key in (
            "ID",
            "id",
            "FILE_ID",
            "fileId",
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
    return ""


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


def upload_and_attach(*, client: BitrixClient, bitrix_task_id: str, attachment) -> str:
    """
    Upload local file to Bitrix and attach to the task (Files tab).
    Tries several REST methods used across Bitrix Cloud builds.
    Returns Bitrix file id when known.
    """
    name = attachment.original_name or attachment.file.name.split("/")[-1] or "file"
    with attachment.file.open("rb") as fh:
        content = fh.read()
    if not content:
        raise BitrixAPIError("empty file")

    b64 = base64.b64encode(content).decode("ascii")
    errors: list[str] = []

    # 1) Disk upload → attach to task Files tab
    folder_id = _resolve_upload_folder(client)
    disk_file_id = ""
    if folder_id:
        try:
            uploaded = client.upload_file_to_folder(folder_id, name, content)
            disk_file_id = _extract_file_id(uploaded)
            if disk_file_id:
                try:
                    client.attach_file_to_task(bitrix_task_id, disk_file_id)
                    return disk_file_id
                except BitrixAPIError as exc:
                    errors.append(f"attach: {exc}")
            else:
                errors.append(f"upload no id: {uploaded}")
        except BitrixAPIError as exc:
            errors.append(f"disk upload: {exc}")

    # 2) Legacy task.item.addfile with base64 payload (works for docs + images)
    for params in (
        {"TASK_ID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
        {"TASKID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
        {"TASK_ID": bitrix_task_id, "NAME": name, "CONTENT": b64},
        {"taskId": bitrix_task_id, "file": {"name": name, "content": b64}},
    ):
        try:
            result = client.call("task.item.addfile", params)
            fid = _extract_file_id(result) or disk_file_id or "ok"
            return fid
        except BitrixAPIError as exc:
            errors.append(f"task.item.addfile: {exc}")

    # 3) Comment with attached Disk file (shows in Bitrix task chat)
    if disk_file_id or folder_id:
        try:
            file_id = disk_file_id
            if not file_id and folder_id:
                uploaded = client.upload_file_to_folder(folder_id, name, content)
                file_id = _extract_file_id(uploaded)
            if file_id:
                try:
                    client.call(
                        "task.commentitem.add",
                        {
                            "TASKID": bitrix_task_id,
                            "FIELDS": {
                                "POST_MESSAGE": f"[Файл из Nextgen] {name}",
                                "UF_FORUM_MESSAGE_DOC": [file_id],
                            },
                        },
                    )
                    return file_id
                except BitrixAPIError:
                    try:
                        client.attach_file_to_task(bitrix_task_id, file_id)
                        return file_id
                    except BitrixAPIError as exc:
                        errors.append(f"comment/attach: {exc}")
                        # File is on Disk even if not linked to task UI
                        return file_id
        except BitrixAPIError as exc:
            errors.append(f"comment upload: {exc}")

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
            att = Attachment(task=task, original_name=name)
            if is_client:
                att.bitrix_file_id = fid
            else:
                att.agency_bitrix_file_id = fid
            att.file.save(name, ContentFile(content), save=False)
            att.save()
            created += 1
    return created
