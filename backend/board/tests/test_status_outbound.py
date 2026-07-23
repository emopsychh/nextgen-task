"""Outbound status push: start/complete reach agency Bitrix; pause is a no-op.

Client Bitrix tasks are never created — sync targets agency_bitrix_task_id only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from board import tasks as board_tasks
from board.models import Task
from portals.models import Portal

from .helpers import make_link, make_portal, make_project, make_task, make_user


def _mock_client(bitrix_status: str = "3") -> MagicMock:
    client = MagicMock()
    client.get_task.return_value = {"status": bitrix_status}
    client.update_task.return_value = {"task": {"id": "108"}}
    client.get_current_user.return_value = {"ID": "42"}
    client.pause_task.return_value = {}
    client.complete_task.return_value = {}
    client.start_task.return_value = {}
    client.add_elapsed_item.return_value = "1"
    return client


class OutboundStatusPushTests(TestCase):
    def setUp(self):
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        make_link(self.agency, self.client_portal, bitrix_group_id="G")
        self.user = make_user(self.client_portal, bitrix_id="7")
        self.agency_user = make_user(self.agency, bitrix_id="42")
        self.project = make_project(
            self.client_portal, bitrix_task_id="P", bitrix_group_id="G"
        )

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_pause_is_noop_in_bitrix(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.TODO,
            sync_status=Task.SyncStatus.PENDING,
            agency_bitrix_task_id="108",
        )
        client = _mock_client(bitrix_status="3")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        client.pause_task.assert_not_called()
        client.pause_task_timer.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.sync_status, Task.SyncStatus.SYNCED)

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_complete_calls_bitrix_complete(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            sync_status=Task.SyncStatus.PENDING,
            agency_bitrix_task_id="108",
        )
        client = _mock_client(bitrix_status="3")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        client.complete_task.assert_called_once()
        client.pause_task_timer.assert_called()

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_start_calls_bitrix_start_when_pending(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.IN_PROGRESS,
            sync_status=Task.SyncStatus.PENDING,
            agency_bitrix_task_id="108",
        )
        client = _mock_client(bitrix_status="2")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        client.start_task.assert_called()

    def test_post_elapsed_is_idempotent(self):
        from datetime import timedelta

        from django.utils import timezone

        from board.models import TimeEntry

        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            agency_bitrix_task_id="108",
        )
        start = timezone.now() - timedelta(seconds=120)
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=start,
            ended_at=start + timedelta(seconds=120),
            duration_seconds=120,
        )
        client = MagicMock()
        client.add_elapsed_item.return_value = "555"

        board_tasks._post_time_entries_elapsed(client, "108", task, self.agency)
        client.add_elapsed_item.assert_called_once()
        args, kwargs = client.add_elapsed_item.call_args
        self.assertEqual(args[0], "108")
        self.assertEqual(args[1], 120)
        self.assertEqual(kwargs.get("comment"), "")

        board_tasks._post_time_entries_elapsed(client, "108", task, self.agency)
        client.add_elapsed_item.assert_called_once()

    def test_agency_responsible_is_agency_oauth_user_not_client_author(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.TODO,
        )
        agency_client = MagicMock()
        agency_client.get_current_user.return_value = {"ID": "42"}
        resolved = board_tasks._resolve_responsible_id(
            agency_client, task, self.agency
        )
        self.assertEqual(resolved, "42")

    def test_client_responsible_is_the_author(self):
        client = MagicMock()
        task = make_task(self.project, created_by=self.user, status=Task.Status.TODO)
        resolved = board_tasks._resolve_responsible_id(
            client, task, self.client_portal
        )
        self.assertEqual(resolved, "7")
        client.get_current_user.assert_not_called()

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_synced_status_not_pushed(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.TODO,
            sync_status=Task.SyncStatus.SYNCED,
            agency_bitrix_task_id="108",
        )
        client = _mock_client(bitrix_status="3")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            board_tasks.sync_task_to_bitrix(task.id)
        client.pause_task.assert_not_called()
        client.complete_task.assert_not_called()

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_never_creates_client_bitrix_task(self):
        task = make_task(
            self.project, created_by=self.user, sync_status=Task.SyncStatus.PENDING
        )
        client = _mock_client(bitrix_status="2")
        client.create_task.return_value = {"task": {"id": "200"}}
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertEqual(task.bitrix_task_id, "")
        self.assertEqual(task.agency_bitrix_task_id, "200")
