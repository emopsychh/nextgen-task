"""Sync attachments between Nextgen and Bitrix task files / task chat."""

from __future__ import annotations

import base64
import logging
from django.core.files.base import ContentFile

from portals.bitrix import BitrixAPIError, BitrixClient

from board.status_sync import resolve_all_bitrix_task_sources

logger = logging.getLogger(__name__)


def _as_int_id(value) -> int | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    text = str(value).strip()
    if text.isdigit():
        num = int(text)
        return num if num > 0 else None
    return None


def _extract_drive_object_id(result) -> str:
    """
    Drive object ID only (result.ID) — never FILE_ID.

    Bitrix docs: FILE_ID is a different namespace; using it with
    tasks.task.files.attach attaches nothing or the wrong object.
    """
    if result is None:
        return ""
    if isinstance(result, (int, float)):
        return str(int(result)) if result else ""
    if isinstance(result, str) and result.isdigit():
        return result
    if not isinstance(result, dict):
        return ""

    for key in ("ID", "id", "OBJECT_ID", "objectId"):
        num = _as_int_id(result.get(key))
        if num:
            return str(num)

    nested = result.get("FILE") or result.get("file") or result.get("result")
    if isinstance(nested, dict):
        return _extract_drive_object_id(nested)
    if isinstance(nested, (int, float, str)):
        num = _as_int_id(nested)
        if num:
            return str(num)
    return ""


def _disk_doc_ref(disk_file_id: str) -> str:
    """UF_FORUM_MESSAGE_DOC needs `n{driveId}` for newly uploaded Disk files."""
    fid = str(disk_file_id or "").strip()
    if not fid:
        return ""
    if fid[0] in "nNfF" and fid[1:].isdigit():
        return fid
    return f"n{fid}"


def _read_access_task_id(client: BitrixClient) -> int | None:
    """disk.rights.getTasks → disk_access_read id (usually 71)."""
    try:
        rows = client.call("disk.rights.getTasks") or []
    except BitrixAPIError:
        return 71
    if isinstance(rows, dict):
        rows = rows.get("result") or rows.get("tasks") or []
    if not isinstance(rows, list):
        return 71
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("NAME") or row.get("name") or "").lower()
        if name in ("disk_access_read", "read"):
            return _as_int_id(row.get("ID") or row.get("id")) or 71
    return 71


def _resolve_upload_target(client: BitrixClient) -> tuple[str, str]:
    """
    Returns (kind, id) where kind is 'folder' or 'storage'.
    Prefer app storage root folder; never pass storage ID to disk.folder.uploadfile.
    """
    try:
        storage = client.get_app_storage()
        root = _as_int_id(
            storage.get("ROOT_OBJECT_ID") or storage.get("rootObjectId")
        )
        storage_id = _as_int_id(storage.get("ID") or storage.get("id"))
        if root:
            return "folder", str(root)
        if storage_id:
            return "storage", str(storage_id)
    except BitrixAPIError as exc:
        logger.warning("disk.storage.getforapp failed: %s", exc)

    try:
        rows = client.call("disk.storage.getlist") or []
        if isinstance(rows, dict):
            rows = rows.get("result") or rows.get("storages") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                root = _as_int_id(
                    row.get("ROOT_OBJECT_ID") or row.get("rootObjectId")
                )
                storage_id = _as_int_id(row.get("ID") or row.get("id"))
                if root:
                    return "folder", str(root)
                if storage_id:
                    return "storage", str(storage_id)
    except BitrixAPIError as exc:
        logger.warning("disk.storage.getlist failed: %s", exc)
    return "", ""


def _upload_to_disk(client: BitrixClient, name: str, content: bytes) -> str:
    kind, target_id = _resolve_upload_target(client)
    if not kind or not target_id:
        raise BitrixAPIError("no disk folder/storage available (check disk scope)")

    read_task = _read_access_task_id(client)
    params = {
        "id": int(target_id) if target_id.isdigit() else target_id,
        "data": {"NAME": name},
        "fileContent": [name, base64.b64encode(content).decode("ascii")],
        "generateUniqueName": True,
        # So tasks.task.files.attach can see the file (Insufficient permissions otherwise)
        "rights": [{"TASK_ID": read_task, "ACCESS_CODE": "*"}],
    }
    method = "disk.folder.uploadfile" if kind == "folder" else "disk.storage.uploadfile"
    uploaded = client.call(method, params, timeout=120)
    drive_id = _extract_drive_object_id(uploaded)
    if not drive_id:
        raise BitrixAPIError(f"{method} returned no Drive ID: {uploaded!r}")
    logger.info(
        "disk upload ok method=%s target=%s drive_id=%s name=%s",
        method,
        target_id,
        drive_id,
        name,
    )
    return drive_id


def _attach_drive_file_to_task(
    client: BitrixClient, bitrix_task_id: str, drive_file_id: str
) -> None:
    """Attach Drive object to task Files tab. Raises if all strategies fail."""
    task_id = _as_int_id(bitrix_task_id) or bitrix_task_id
    file_id = _as_int_id(drive_file_id) or drive_file_id
    errors: list[str] = []

    for method, params in (
        ("tasks.task.files.attach", {"taskId": task_id, "fileId": file_id}),
        ("tasks.task.file.attach", {"taskId": task_id, "fileIds": [file_id]}),
        ("tasks.task.files.attach", {"TASK_ID": task_id, "FILE_ID": file_id}),
    ):
        try:
            client.call(method, params)
            logger.info(
                "task attach ok method=%s task=%s file=%s",
                method,
                task_id,
                file_id,
            )
            return
        except BitrixAPIError as exc:
            errors.append(f"{method}: {exc}")

    # Fallback: UF_TASK_WEBDAV_FILES (Drive field on the task)
    try:
        task_data = client.get_task(bitrix_task_id) or {}
        existing = task_data.get("UF_TASK_WEBDAV_FILES") or []
        if not isinstance(existing, list):
            existing = [existing] if existing else []
        refs = []
        for item in existing:
            if item is None or item is False or item == "":
                continue
            refs.append(str(item))
        doc = _disk_doc_ref(str(file_id))
        if doc and doc not in refs and str(file_id) not in refs:
            refs.append(doc)
        client.update_task(bitrix_task_id, {"UF_TASK_WEBDAV_FILES": refs})
        logger.info(
            "task attach via UF_TASK_WEBDAV_FILES ok task=%s file=%s",
            task_id,
            file_id,
        )
        return
    except BitrixAPIError as exc:
        errors.append(f"UF_TASK_WEBDAV_FILES: {exc}")

    raise BitrixAPIError("; ".join(errors) or "attach failed")


def _post_file_to_task_chat(
    *,
    client: BitrixClient,
    bitrix_task_id: str,
    disk_file_id: str,
    name: str,
    message: str | None = None,
) -> None:
    """Best-effort: show the file in the Bitrix task comment/chat stream."""
    body = (message or "").strip()
    text = f"[Файл из Nextgen] {body}" if body else f"[Файл из Nextgen] {name}"
    doc_ref = _disk_doc_ref(disk_file_id)
    errors: list[str] = []

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

    try:
        task_data = client.get_task(bitrix_task_id) or {}
        chat_id = (
            task_data.get("chatId")
            or task_data.get("CHAT_ID")
            or task_data.get("chat_id")
            or ""
        )
        if chat_id:
            file_id = _as_int_id(disk_file_id) or disk_file_id
            for params in (
                {
                    "CHAT_ID": int(chat_id) if str(chat_id).isdigit() else chat_id,
                    "FILE_ID": [file_id],
                    "MESSAGE": text,
                },
                {
                    "DIALOG_ID": f"chat{chat_id}",
                    "FILE_ID": [file_id],
                    "MESSAGE": text,
                },
            ):
                try:
                    client.call("im.disk.file.commit", params)
                    return
                except BitrixAPIError as exc:
                    errors.append(f"im.disk.file.commit: {exc}")
    except BitrixAPIError as exc:
        errors.append(f"get_task chat: {exc}")

    if errors:
        logger.warning(
            "post file to task chat failed task=%s file=%s: %s",
            bitrix_task_id,
            disk_file_id,
            "; ".join(errors),
        )


def upload_and_attach(*, client: BitrixClient, bitrix_task_id: str, attachment) -> str:
    """
    Upload local file to Bitrix Disk and attach to the task Files tab.
    Returns Drive object id. Raises if upload or attach fails.
    """
    from board.naming import display_attachment_name

    name = display_attachment_name(attachment) or "file"
    with attachment.file.open("rb") as fh:
        content = fh.read()
    if not content:
        raise BitrixAPIError("empty file")

    drive_id = ""
    upload_errors: list[str] = []

    try:
        drive_id = _upload_to_disk(client, name, content)
    except BitrixAPIError as exc:
        upload_errors.append(str(exc))

    # Legacy fallback if Disk upload is unavailable
    if not drive_id:
        b64 = base64.b64encode(content).decode("ascii")
        for params in (
            {"TASK_ID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
            {"TASKID": bitrix_task_id, "FILE": {"name": name, "content": b64}},
        ):
            try:
                result = client.call("task.item.addfile", params, timeout=120)
                drive_id = _extract_drive_object_id(result)
                if drive_id:
                    logger.info(
                        "legacy task.item.addfile ok task=%s file=%s",
                        bitrix_task_id,
                        drive_id,
                    )
                    break
                upload_errors.append(f"task.item.addfile no id: {result!r}")
            except BitrixAPIError as exc:
                upload_errors.append(f"task.item.addfile: {exc}")

    if not drive_id:
        raise BitrixAPIError("; ".join(upload_errors) or "disk upload failed")

    # Attach to Files tab is required for "Проекты → задача → подзадача"
    _attach_drive_file_to_task(client, bitrix_task_id, drive_id)

    # Chat is best-effort (does not fail the sync)
    try:
        _post_file_to_task_chat(
            client=client,
            bitrix_task_id=bitrix_task_id,
            disk_file_id=drive_id,
            name=name,
        )
    except Exception:
        logger.exception(
            "chat post after attach failed task=%s file=%s", bitrix_task_id, drive_id
        )

    return drive_id


def pull_attachments_from_bitrix(task) -> int:
    """Import Bitrix task files missing locally. Returns created count."""
    from board.models import Attachment
    from board.naming import client_filename

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
                or row.get("ATTACHMENT_ID")
                or ""
            )
            # Prefer Drive object id; skip internal FILE_ID-only rows when ID missing
            if not fid or fid == "0":
                fid = str(row.get("FILE_ID") or row.get("fileId") or "")
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

            att = Attachment(task=task, original_name=client_filename(name))
            if is_client:
                att.bitrix_file_id = fid
            else:
                att.agency_bitrix_file_id = fid
            att.file.save(client_filename(name), ContentFile(content), save=False)
            att.save()
            created += 1
    return created
