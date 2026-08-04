"""Outbound status push: start/complete reach agency Bitrix; pause is local.

Client Bitrix tasks are never created — sync targets agency_bitrix_task_id only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from board import tasks as board_tasks
from board.models import Task
from portals.models import Portal

from .helpers import make_link, make_portal, make_project, make_task, make_user


def _mock_client(bitrix_status: str = "3", *, after_complete: str | None = "5") -> MagicMock:
    client = MagicMock()

    def get_task(*_a, **_k):
        # After complete succeeds, verification/later reads see the new status.
        if after_complete is not None and client.complete_task.called:
            return {"status": after_complete}
        return {"status": bitrix_status}

    client.get_task.side_effect = get_task
    client.update_task.return_value = {"task": {"id": "108"}}
    client.get_current_user.return_value = {"ID": "42"}
    client.pause_task.return_value = {}
    client.complete_task.return_value = {}
    client.start_task.return_value = {}
    client.add_elapsed_item.return_value = "1"
    return client


class OutboundStatusPushTests(TestCase):
    def setUp(self):
        group_patch = patch(
            "portals.deal_resolve.resolve_bitrix_group_id",
            return_value="G",
        )
        group_patch.start()
        self.addCleanup(group_patch.stop)
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        make_link(self.agency, self.client_portal, bitrix_group_id="G")
        self.user = make_user(self.client_portal, bitrix_id="7")
        self.agency_user = make_user(self.agency, bitrix_id="42")
        self.project = make_project(
            self.client_portal, bitrix_task_id="P", bitrix_group_id="G"
        )

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_pause_does_not_pause_bitrix(self):
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
        client = _mock_client(bitrix_status="3", after_complete="5")
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res = board_tasks.sync_task_to_bitrix(task.id)
        self.assertTrue(res["ok"])
        self.assertEqual(
            client.update_task.call_args.args[1]["ALLOW_TIME_TRACKING"],
            "Y",
        )
        client.complete_task.assert_called()
        client.pause_task.assert_called()
        client.pause_task_timer.assert_called()
        client.add_elapsed_item.assert_not_called()

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_complete_errors_if_bitrix_stays_in_progress(self):
        from portals.bitrix import BitrixAPIError

        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            sync_status=Task.SyncStatus.PENDING,
            agency_bitrix_task_id="108",
        )
        # Stay at status 3 forever → must not mark SYNCED.
        client = _mock_client(bitrix_status="3", after_complete=None)
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            with self.assertRaises(BitrixAPIError):
                board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertEqual(task.sync_status, Task.SyncStatus.ERROR)
        self.assertIn("не завершилась", task.sync_error)

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
        self.assertEqual(
            client.update_task.call_args.args[1]["ALLOW_TIME_TRACKING"],
            "N",
        )
        client.start_task.assert_called()

    @override_settings(
        BITRIX_DEFAULT_RESPONSIBLE_ID="",
        BITRIX_CLIENT_TASK_AUTHOR_ID="99",
    )
    def test_cross_portal_uses_configured_responsible_not_oauth(self):
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
        self.assertEqual(resolved, "99")
        agency_client.get_current_user.assert_not_called()

    @override_settings(
        BITRIX_DEFAULT_RESPONSIBLE_ID="55",
        BITRIX_CLIENT_TASK_AUTHOR_ID="99",
    )
    def test_cross_portal_prefers_default_responsible_over_author_id(self):
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
        self.assertEqual(resolved, "55")

    def test_same_portal_responsible_is_the_author(self):
        client = MagicMock()
        task = make_task(self.project, created_by=self.user, status=Task.Status.TODO)
        resolved = board_tasks._resolve_responsible_id(
            client, task, self.client_portal
        )
        self.assertEqual(resolved, "7")
        client.get_current_user.assert_not_called()

    def test_agency_author_is_responsible_on_agency_portal(self):
        task = make_task(
            self.project,
            created_by=self.agency_user,
            status=Task.Status.TODO,
        )
        agency_client = MagicMock()
        agency_client.get_current_user.return_value = {"ID": "99"}
        resolved = board_tasks._resolve_responsible_id(
            agency_client, task, self.agency
        )
        self.assertEqual(resolved, "42")
        agency_client.get_current_user.assert_not_called()

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
