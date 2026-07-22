from datetime import timedelta
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from .models import Portal


class BitrixAPIError(Exception):
    def __init__(self, message: str, response: dict | None = None):
        super().__init__(message)
        self.response = response or {}


class BitrixClient:
    def __init__(self, portal: Portal):
        self.portal = portal

    @property
    def base_url(self) -> str:
        domain = self.portal.domain.replace("https://", "").replace("http://", "").rstrip("/")
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
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        if "access_token" not in data:
            raise BitrixAPIError("Failed to refresh Bitrix token", data)
        self.portal.access_token = data["access_token"]
        self.portal.refresh_token = data.get("refresh_token", self.portal.refresh_token)
        expires_in = int(data.get("expires_in", 3600))
        self.portal.expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.portal.save(
            update_fields=["access_token", "refresh_token", "expires_at", "updated_at"]
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict:
        self._ensure_token()
        url = f"{self.base_url}/{method}"
        payload = dict(params or {})
        payload["auth"] = self.portal.access_token
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()
        if "error" in data:
            if data.get("error") in ("expired_token", "invalid_token"):
                self.refresh_tokens()
                payload["auth"] = self.portal.access_token
                resp = requests.post(url, json=payload, timeout=30)
                data = resp.json()
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

    def add_elapsed_item(
        self,
        task_id: int | str,
        seconds: int,
        *,
        comment: str = "",
        user_id: str | None = None,
    ) -> dict | str | int:
        """Post a closed time record into Bitrix task elapsed items."""
        fields: dict = {
            "SECONDS": max(0, int(seconds)),
            "COMMENT_TEXT": comment or "Nextgen timer",
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

