import ipaddress
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.utils import timezone

from .models import Portal

# Transient HTTP statuses that are worth retrying with backoff.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _host_is_public(host: str) -> bool:
    """Reject internal/loopback/link-local targets to blunt SSRF.

    IP literals must be globally routable; hostnames must look like real FQDNs.
    (DNS-rebinding to a private IP is still possible in theory — this stops the
    common `localhost` / `169.254.169.254` / `10.x` style attacks.)
    """
    host = (host or "").strip().lower().strip(".")
    if not host or host == "localhost":
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


class BitrixAPIError(Exception):
    def __init__(self, message: str, response: dict | None = None):
        super().__init__(message)
        self.response = response or {}


class BitrixClient:
    def __init__(self, portal: Portal):
        self.portal = portal

    @property
    def portal_host(self) -> str:
        raw = (self.portal.domain or "").replace("https://", "").replace("http://", "").rstrip("/")
        return raw.split("/")[0].split(":")[0].lower()

    @property
    def base_url(self) -> str:
        domain = self.portal.domain.replace("https://", "").replace("http://", "").rstrip("/")
        if not _host_is_public(self.portal_host):
            raise BitrixAPIError(f"Refusing to contact non-public Bitrix host: {self.portal_host!r}")
        return f"https://{domain}/rest"

    def _ensure_token(self):
        if self.portal.expires_at and self.portal.expires_at <= timezone.now() + timedelta(minutes=2):
            self.refresh_tokens()

    def refresh_tokens(self):
        if not self.portal.refresh_token:
            raise BitrixAPIError("Missing refresh token")
        url = "https://oauth.bitrix.info/oauth/token/"
        params = {
            "grant_type": "refresh_token",
            "client_id": settings.BITRIX_CLIENT_ID,
            "client_secret": settings.BITRIX_CLIENT_SECRET,
            "refresh_token": self.portal.refresh_token,
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise BitrixAPIError(f"Token refresh request failed: {exc}")
        try:
            data = resp.json()
        except ValueError:
            raise BitrixAPIError(f"Token refresh returned HTTP {resp.status_code}")
        if not isinstance(data, dict) or "access_token" not in data:
            raise BitrixAPIError("Failed to refresh Bitrix token", data if isinstance(data, dict) else None)
        self.portal.access_token = data["access_token"]
        self.portal.refresh_token = data.get("refresh_token", self.portal.refresh_token)
        try:
            expires_in = int(data.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        self.portal.expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.portal.save(
            update_fields=["access_token", "refresh_token", "expires_at", "updated_at"]
        )

    def _post(
        self, url: str, payload: dict, timeout: int, *, attempts: int = 3
    ) -> dict:
        """POST to Bitrix with bounded retry/backoff for transient failures.

        Returns the parsed JSON dict. Raises BitrixAPIError on network errors,
        non-JSON bodies, or HTTP errors that carry no JSON error payload — so
        callers never crash on a 502 HTML page or a timeout.
        """
        last_status = None
        for attempt in range(attempts):
            try:
                resp = requests.post(url, json=payload, timeout=timeout)
            except requests.RequestException as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise BitrixAPIError(f"Bitrix request failed: {exc}")

            last_status = resp.status_code
            if resp.status_code in _RETRYABLE_STATUSES and attempt < attempts - 1:
                time.sleep(0.5 * (2**attempt))
                continue

            try:
                data = resp.json()
            except ValueError:
                # Non-JSON body (e.g. 502 HTML) — retry if transient, else fail.
                if resp.status_code in _RETRYABLE_STATUSES and attempt < attempts - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise BitrixAPIError(
                    f"Bitrix returned non-JSON HTTP {resp.status_code}",
                    {"status_code": resp.status_code, "body": (resp.text or "")[:500]},
                )
            if not isinstance(data, dict):
                raise BitrixAPIError(
                    "Bitrix returned unexpected payload",
                    {"status_code": resp.status_code},
                )
            return data

        raise BitrixAPIError(f"Bitrix request failed after retries (HTTP {last_status})")

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: int = 30,
    ) -> dict:
        self._ensure_token()
        url = f"{self.base_url}/{method}"
        payload = dict(params or {})
        payload["auth"] = self.portal.access_token
        data = self._post(url, payload, timeout)
        if "error" in data:
            if data.get("error") in ("expired_token", "invalid_token"):
                self.refresh_tokens()
                payload["auth"] = self.portal.access_token
                data = self._post(url, payload, timeout)
            if "error" in data:
                raise BitrixAPIError(data.get("error_description") or data["error"], data)
        return data.get("result", data)

    def get_current_user(self) -> dict:
        return self.call("user.current")

    def create_task(self, fields: dict) -> dict:
        return self.call("tasks.task.add", {"fields": fields})

    def update_task(self, task_id: int | str, fields: dict) -> dict:
        return self.call("tasks.task.update", {"taskId": task_id, "fields": fields})

    def get_task(self, task_id: int | str) -> dict:
        # Do not pass select — Bitrix may omit deadline/parentId when select names mismatch.
        result = self.call("tasks.task.get", {"taskId": task_id})
        if isinstance(result, dict) and "task" in result and isinstance(result["task"], dict):
            return result["task"]
        return result if isinstance(result, dict) else {}

    def start_task(self, task_id: int | str) -> dict:
        return self.call("tasks.task.start", {"taskId": task_id})

    def complete_task(self, task_id: int | str) -> dict:
        return self.call("tasks.task.complete", {"taskId": task_id})

    def pause_task(self, task_id: int | str) -> dict:
        return self.call("tasks.task.pause", {"taskId": task_id})

    def renew_task(self, task_id: int | str) -> dict:
        return self.call("tasks.task.renew", {"taskId": task_id})

    def start_task_timer(self, task_id: int | str) -> dict:
        """Start Bitrix task time tracking (Учёт времени)."""
        try:
            return self.call("tasks.task.startTimer", {"taskId": task_id})
        except BitrixAPIError:
            return self.call("task.timer.start", {"taskId": task_id})

    def pause_task_timer(self, task_id: int | str) -> dict:
        """Pause Bitrix task time tracking."""
        try:
            return self.call("tasks.task.pauseTimer", {"taskId": task_id})
        except BitrixAPIError:
            return self.call("task.timer.stop", {"taskId": task_id})

    def get_task_timer(self, task_id: int | str) -> dict | list | None:
        """
        Current live timer for the app user, if any.
        Empty / missing → timer is not running (for this auth user).
        """
        try:
            return self.call("task.timer.get", {"taskId": task_id})
        except BitrixAPIError:
            try:
                return self.call("task.timer.get", {"TASK_ID": task_id})
            except BitrixAPIError:
                return None

    def get_task_elapsed_seconds(self, task_id: int | str) -> int | None:
        """Sum closed elapsed items for a Bitrix task (seconds)."""
        try:
            rows = self.call(
                "task.elapseditem.getlist",
                {"TASKID": task_id, "ORDER": {"ID": "ASC"}},
            )
        except BitrixAPIError:
            return None
        if isinstance(rows, dict):
            rows = rows.get("result") or rows.get("tasks") or []
        if not isinstance(rows, list):
            return None
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("SECONDS") or row.get("seconds") or 0
            try:
                total += max(0, int(raw))
            except (TypeError, ValueError):
                continue
        return total

    def add_elapsed_item(
        self,
        task_id: int | str,
        seconds: int,
        *,
        comment: str = "",
        user_id: str | None = None,
    ) -> dict | str | int:
        """Unused by the app: Bitrix «Учёт времени» is filled manually.

        Kept on the client for ad-hoc/admin use. Prefer empty COMMENT_TEXT —
        a non-empty comment makes Bitrix spam the task chat.
        """
        fields: dict = {
            "SECONDS": max(0, int(seconds)),
            "COMMENT_TEXT": (comment or "").strip(),
        }
        if user_id:
            fields["USER_ID"] = user_id
        return self.call(
            "task.elapseditem.add",
            {"TASKID": task_id, "FIELDS": fields},
        )

    def add_task_comment(self, task_id: int | str, message: str, author_id: str | None = None) -> dict | str | int:
        fields: dict = {"POST_MESSAGE": message}
        if author_id:
            fields["AUTHOR_ID"] = author_id
        return self.call(
            "task.commentitem.add",
            {"TASKID": task_id, "FIELDS": fields},
        )

    def get_task_comment(self, task_id: int | str, comment_id: int | str) -> dict:
        result = self.call(
            "task.commentitem.get",
            {"TASKID": task_id, "ITEMID": comment_id},
        )
        return result if isinstance(result, dict) else {}

    def list_task_comments(self, task_id: int | str) -> list[dict]:
        result = self.call(
            "task.commentitem.getlist",
            {"TASKID": task_id, "ORDER": {"ID": "ASC"}},
        )
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        if isinstance(result, dict):
            for key in ("comments", "items", "result"):
                val = result.get(key)
                if isinstance(val, list):
                    return [r for r in val if isinstance(r, dict)]
        return []

    def list_tasks(
        self,
        *,
        group_id: int | str | None = None,
        parent_id: int | str | None = None,
        start: int = 0,
    ) -> list[dict]:
        """List Bitrix tasks (optional GROUP_ID / PARENT_ID filter)."""
        filt: dict = {}
        if group_id is not None and str(group_id).strip() not in ("", "0"):
            filt["GROUP_ID"] = group_id
        if parent_id is not None:
            # 0 = top-level tasks only
            filt["PARENT_ID"] = parent_id
        params: dict = {
            "order": {"ID": "ASC"},
            "select": [
                "ID",
                "TITLE",
                "DESCRIPTION",
                "STATUS",
                "REAL_STATUS",
                "GROUP_ID",
                "PARENT_ID",
                "DEADLINE",
                "PRIORITY",
            ],
            "start": start,
        }
        if filt:
            params["filter"] = filt
        result = self.call("tasks.task.list", params)
        rows: list = []
        if isinstance(result, list):
            rows = result
        elif isinstance(result, dict):
            rows = result.get("tasks") or result.get("items") or result.get("result") or []
            if isinstance(rows, dict):
                rows = list(rows.values())
        out: list[dict] = []
        for row in rows:
            if isinstance(row, dict):
                # tasks.task.list sometimes nests under "task"
                if "task" in row and isinstance(row["task"], dict):
                    out.append(row["task"])
                else:
                    out.append(row)
        return out

    def get_company(self, company_id: int | str) -> dict:
        result = self.call("crm.company.get", {"id": company_id})
        return result if isinstance(result, dict) else {}

    def get_deal(self, deal_id: int | str) -> dict:
        result = self.call("crm.deal.get", {"id": deal_id})
        return result if isinstance(result, dict) else {}

    def update_deal(self, deal_id: int | str, fields: dict) -> dict:
        return self.call("crm.deal.update", {"id": deal_id, "fields": fields})

    def add_deal_timeline_comment(self, deal_id: int | str, comment: str) -> dict:
        return self.call(
            "crm.timeline.comment.add",
            {
                "fields": {
                    "ENTITY_ID": deal_id,
                    "ENTITY_TYPE": "deal",
                    "COMMENT": comment,
                }
            },
        )

    def notify_user(self, user_id: int | str, message: str) -> dict:
        """Best-effort Bitrix IM notification (requires `im` scope)."""
        try:
            return self.call(
                "im.notify.system.add",
                {"USER_ID": user_id, "MESSAGE": message},
            )
        except BitrixAPIError:
            return self.call(
                "im.notify",
                {"to": user_id, "message": message, "type": "SYSTEM"},
            )

    def get_app_storage(self) -> dict:
        result = self.call("disk.storage.getforapp")
        return result if isinstance(result, dict) else {}

    def upload_file_to_folder(
        self, folder_id: int | str, filename: str, content: bytes
    ) -> dict:
        import base64

        fid = int(folder_id) if str(folder_id).isdigit() else folder_id
        result = self.call(
            "disk.folder.uploadfile",
            {
                "id": fid,
                "data": {"NAME": filename},
                "fileContent": [filename, base64.b64encode(content).decode("ascii")],
                "generateUniqueName": True,
            },
            timeout=120,
        )
        return result if isinstance(result, dict) else {"ID": result}

    def attach_file_to_task(self, task_id: int | str, file_id: int | str) -> dict:
        tid = int(task_id) if str(task_id).isdigit() else task_id
        fid = int(file_id) if str(file_id).isdigit() else file_id
        last_exc: BitrixAPIError | None = None
        for method, params in (
            ("tasks.task.files.attach", {"taskId": tid, "fileId": fid}),
            ("tasks.task.file.attach", {"taskId": tid, "fileIds": [fid]}),
            ("tasks.task.files.attach", {"TASK_ID": tid, "FILE_ID": fid}),
        ):
            try:
                return self.call(method, params)
            except BitrixAPIError as exc:
                last_exc = exc
                continue
        raise last_exc or BitrixAPIError("attach failed")

    def list_task_files(self, task_id: int | str) -> list[dict]:
        try:
            result = self.call("tasks.task.files.getlist", {"taskId": task_id})
        except BitrixAPIError:
            try:
                result = self.call("task.item.getfiles", {"TASKID": task_id})
            except BitrixAPIError:
                return []
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        if isinstance(result, dict):
            for key in ("files", "result", "items"):
                val = result.get(key)
                if isinstance(val, list):
                    return [r for r in val if isinstance(r, dict)]
        return []

    def download_disk_file(self, file_id: int | str) -> bytes:
        meta = self.call("disk.file.get", {"id": file_id})
        if not isinstance(meta, dict):
            return b""
        url = (
            meta.get("DOWNLOAD_URL")
            or meta.get("downloadUrl")
            or meta.get("DOWNLOAD_URL_INTERNAL")
            or ""
        )
        if not url:
            return b""
        # Bitrix may hand back a relative DOWNLOAD_URL — resolve against the portal.
        if "://" not in url:
            url = f"https://{self.portal_host}{url if url.startswith('/') else '/' + url}"
        dl_host = (urlparse(url).hostname or "").lower()
        if not _host_is_public(dl_host):
            raise BitrixAPIError(f"Refusing to download from non-public host: {dl_host!r}")
        self._ensure_token()
        # Only forward the portal auth token to the portal's own Bitrix host, so a
        # malicious/misconfigured DOWNLOAD_URL can never exfiltrate our token.
        params = {"auth": self.portal.access_token} if dl_host == self.portal_host else None
        resp = requests.get(url, params=params, timeout=60)
        if resp.status_code >= 400:
            raise BitrixAPIError(f"download failed {resp.status_code}")
        return resp.content


# Bitrix task status: 2=Pending, 3=In progress, 4=Supposedly completed, 5=Completed, 6=Deferred
BITRIX_STATUS_PENDING = 2
BITRIX_STATUS_IN_PROGRESS = 3
BITRIX_STATUS_SUPPOSEDLY_COMPLETED = 4
BITRIX_STATUS_COMPLETED = 5
BITRIX_STATUS_DEFERRED = 6

BITRIX_TO_LOCAL = {
    BITRIX_STATUS_PENDING: "todo",
    BITRIX_STATUS_IN_PROGRESS: "in_progress",
    BITRIX_STATUS_SUPPOSEDLY_COMPLETED: "done",
    BITRIX_STATUS_COMPLETED: "done",
    BITRIX_STATUS_DEFERRED: "todo",
}


def parse_bitrix_status(raw) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def bitrix_status_code(task_data: dict) -> int | None:
    """Prefer realStatus (canonical) over display status."""
    if not isinstance(task_data, dict):
        return None
    for key in ("realStatus", "REAL_STATUS", "status", "STATUS"):
        if key in task_data and task_data[key] not in (None, ""):
            code = parse_bitrix_status(task_data[key])
            if code is not None:
                return code
    return None

