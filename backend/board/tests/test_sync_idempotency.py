"""Regression tests for the duplicate-task guard in sync_task_to_bitrix.

Agency-only sync: client Bitrix tasks are not created. Overlapping sync runs
must never both call create_task on the agency subtask.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import TestCase

from board import tasks as board_tasks
from board.models import Task
from portals.models import Portal

from .helpers import make_link, make_portal, make_project, make_task, make_user


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.create_task.return_value = {"task": {"id": "999"}}
    client.update_task.return_value = {"task": {"id": "999"}}
    client.get_task.return_value = {"status": "2"}
    client.get_current_user.return_value = {"ID": "42"}
    return client


class SyncTaskIdempotencyTests(TestCase):
    def setUp(self):
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        make_link(self.agency, self.client_portal, bitrix_group_id="G")
        self.user = make_user(self.client_portal, bitrix_id="7")
        self.project = make_project(
            self.client_portal, bitrix_task_id="P", bitrix_group_id="G"
        )

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_rerun_creates_agency_once_then_updates(self):
        task = make_task(
            self.project, created_by=self.user, sync_status=Task.SyncStatus.PENDING
        )
        client = _mock_client()

        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res1 = board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertTrue(res1["ok"])
        self.assertEqual(task.agency_bitrix_task_id, "999")
        self.assertEqual(task.bitrix_task_id, "")  # client Bitrix never created
        self.assertEqual(task.sync_status, Task.SyncStatus.SYNCED)
        self.assertEqual(client.create_task.call_count, 1)

        Task.objects.filter(pk=task.pk).update(sync_status=Task.SyncStatus.PENDING)
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res2 = board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertTrue(res2["ok"])
        self.assertEqual(client.create_task.call_count, 1)
        self.assertGreaterEqual(client.update_task.call_count, 1)
        self.assertEqual(task.agency_bitrix_task_id, "999")

    def test_agency_failure_persists_no_client_create(self):
        """Agency has no token → error; client Bitrix create must not happen."""
        agency = make_portal(role=Portal.Role.AGENCY, token="")
        make_link(agency, self.client_portal, bitrix_group_id="G")
        self.project.bitrix_task_id = "P"
        self.project.bitrix_group_id = "G"
        self.project.save(update_fields=["bitrix_task_id", "bitrix_group_id"])

        task = make_task(
            self.project, created_by=self.user, sync_status=Task.SyncStatus.PENDING
        )
        client = _mock_client()

        with patch.object(board_tasks, "BitrixClient", return_value=client):
            with transaction.atomic():
                locked = Task.objects.select_for_update(of=("self",)).select_related(
                    "project", "project__portal", "created_by"
                ).get(pk=task.id)
                outcome = board_tasks._sync_task_locked(locked)

        task.refresh_from_db()
        self.assertTrue(outcome["errors"])
        self.assertEqual(task.bitrix_task_id, "")
        self.assertEqual(task.sync_status, Task.SyncStatus.ERROR)
        # No successful create on a usable portal (token empty → BitrixAPIError before create)
        self.assertEqual(client.create_task.call_count, 0)

    def test_no_agency_link_errors_without_client_create(self):
        lonely = make_portal(role=Portal.Role.CLIENT, domain="lonely.bitrix24.ru")
        project = make_project(lonely)
        user = make_user(lonely, bitrix_id="1")
        task = make_task(project, created_by=user, sync_status=Task.SyncStatus.PENDING)
        client = _mock_client()
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            with transaction.atomic():
                locked = Task.objects.select_for_update(of=("self",)).select_related(
                    "project", "project__portal", "created_by"
                ).get(pk=task.id)
                outcome = board_tasks._sync_task_locked(locked)
        task.refresh_from_db()
        self.assertTrue(outcome["errors"])
        self.assertEqual(client.create_task.call_count, 0)
        self.assertIn("агентство", task.sync_error)
