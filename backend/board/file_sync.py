"""Sync attachments between Nextgen and Bitrix task files."""

from __future__ import annotations

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
        for key in ("ID", "id", "FILE_ID", "fileId", "OBJECT_ID", "objectId"):
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


def upload_and_attach(*, client: BitrixClient, bitrix_task_id: str, attachment) -> str:
    """Upload local file to Bitrix Disk and attach to task. Returns file id."""
    storage = client.get_app_storage()
    folder_id = (
        storage.get("ROOT_OBJECT_ID")
        or storage.get("rootObjectId")
        or storage.get("ID")
        or storage.get("id")
        or ""
    )
    if not folder_id:
        raise BitrixAPIError("disk.storage.getforapp: no folder id")

    name = attachment.original_name or attachment.file.name.split("/")[-1] or "file"
    with attachment.file.open("rb") as fh:
        content = fh.read()
    uploaded = client.upload_file_to_folder(folder_id, name, content)
    file_id = _extract_file_id(uploaded)
    if not file_id:
        raise BitrixAPIError(f"upload returned no file id: {uploaded}")
    client.attach_file_to_task(bitrix_task_id, file_id)
    return file_id


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
                or ""
            )
            if not fid or fid == "0":
                continue
            exists = Attachment.objects.filter(task=task).filter(
                **(
                    {"bitrix_file_id": fid}
                    if is_client
                    else {"agency_bitrix_file_id": fid}
                )
            ).exists()
            if exists:
                continue
            name = str(
                row.get("NAME") or row.get("name") or row.get("ORIGINAL_NAME") or f"file-{fid}"
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
