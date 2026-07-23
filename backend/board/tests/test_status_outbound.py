"""Outbound status push: pause/complete must reach Bitrix via sync_task_to_bitrix.

These prove the worker-side code path is correct, so a status that does NOT
appear in Bitrix points to the worker/queue, not this logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from board import tasks as board_tasks
from board.models import Task
from portals.models import Portal

from .helpers import make_portal, make_project, make_task, make_user


def _mock_client(bitrix_status: str = "3") -> MagicMock:
    """bitrix_status: current Bitrix STATUS ('3'=in progress)."""
    client = MagicMock()
    client.get_task.return_value = {"status": bitrix_status}
    client.update_task.return_value = {"task": {"id": "106"}}
    client.get_current_user.return_value = {"ID": "7"}
    client.pause_task.return_value = {}
    client.complete_task.return_value = {}
    client.start_task.return_value = {}
    return client


class OutboundStatusPushTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_pause_calls_bitrix_pause(self):
        # App paused an in-progress task → Bitrix should be paused.
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.TODO,  # local already paused
            sync_status=Task.SyncStatus.PENDING,
            bitrix_task_id="106",
        )
        client = _mock_client(bitrix_status="3")  # Bitrix still in progress
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        client.pause_task.assert_called_once()
        task.refresh_from_db()
        self.assertEqual(task.sync_status, Task.SyncStatus.SYNCED)

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_complete_calls_bitrix_complete(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            sync_status=Task.SyncStatus.PENDING,
            bitrix_task_id="106",
        )
        client = _mock_client(bitrix_status="3")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        client.complete_task.assert_called_once()

    def test_agency_responsible_is_agency_oauth_user_not_client_author(self):
        # Regression: agency subtask must be owned by the agency OAuth user, so
        # the app can edit/complete it. A client-portal author id is invalid on
        # the agency portal and causes "Действие над задачей не разрешено".
        agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        # task author lives on the CLIENT portal
        task = make_task(
            self.project,
            created_by=self.user,  # client-portal user, bitrix_id=7
            status=Task.Status.TODO,
        )
        agency_client = MagicMock()
        agency_client.get_current_user.return_value = {"ID": "42"}  # agency user
        resolved = board_tasks._resolve_responsible_id(agency_client, task, agency)
        self.assertEqual(resolved, "42")

    def test_client_responsible_is_the_author(self):
        client = MagicMock()
        task = make_task(self.project, created_by=self.user, status=Task.Status.TODO)
        resolved = board_tasks._resolve_responsible_id(client, task, self.portal)
        self.assertEqual(resolved, "7")
        client.get_current_user.assert_not_called()

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_synced_status_not_pushed(self):
        # Not a local push (SYNCED) → we must not call action methods.
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.TODO,
            sync_status=Task.SyncStatus.SYNCED,
            bitrix_task_id="106",
        )
        client = _mock_client(bitrix_status="3")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            board_tasks.sync_task_to_bitrix(task.id)
        client.pause_task.assert_not_called()
        client.complete_task.assert_not_called()
