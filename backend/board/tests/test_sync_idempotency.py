"""Regression tests for the duplicate-task guard in sync_task_to_bitrix.

Two overlapping sync runs (rapid create+edit, or a retry racing a fresh enqueue)
must never both call create_task and duplicate the Bitrix task. The row lock plus
committing ids before any retry guarantees a second run UPDATES instead of creating.
True concurrency needs Postgres row locks; here we assert the equivalent invariant
that re-running (and partial-failure retry) never creates a second Bitrix task.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import TestCase

from board import tasks as board_tasks
from board.models import Task

from .helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.create_task.return_value = {"task": {"id": "999"}}
    client.update_task.return_value = {"task": {"id": "999"}}
    client.get_task.return_value = {}
    client.get_current_user.return_value = {"ID": "7"}
    return client


class SyncTaskIdempotencyTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_rerun_creates_once_then_updates(self):
        task = make_task(
            self.project, created_by=self.user, sync_status=Task.SyncStatus.PENDING
        )
        client = _mock_client()

        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res1 = board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertTrue(res1["ok"])
        self.assertEqual(task.bitrix_task_id, "999")
        self.assertEqual(task.sync_status, Task.SyncStatus.SYNCED)
        self.assertEqual(client.create_task.call_count, 1)

        # Simulate a second enqueue (edit / retry) — must NOT create again.
        Task.objects.filter(pk=task.pk).update(sync_status=Task.SyncStatus.PENDING)
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            res2 = board_tasks.sync_task_to_bitrix(task.id)
        task.refresh_from_db()
        self.assertTrue(res2["ok"])
        self.assertEqual(client.create_task.call_count, 1)  # still exactly one
        self.assertGreaterEqual(client.update_task.call_count, 1)
        self.assertEqual(task.bitrix_task_id, "999")

    def test_partial_failure_persists_client_id_before_retry(self):
        """Client succeeds, agency fails (no token). The client id must be committed
        so any retry updates rather than re-creating."""
        agency = make_portal(role=Portal.Role.AGENCY, token="")  # no token → agency fails
        make_link(agency, self.portal, bitrix_group_id="G")
        # Pre-set project parent/group so _ensure_project_agency_parent needs no Bitrix.
        self.project.bitrix_task_id = "P"
        self.project.bitrix_group_id = "G"
        self.project.save(update_fields=["bitrix_task_id", "bitrix_group_id"])

        task = make_task(
            self.project, created_by=self.user, sync_status=Task.SyncStatus.PENDING
        )
        client = _mock_client()

        # Call the locked worker directly to avoid Celery retry timing.
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            with transaction.atomic():
                locked = Task.objects.select_for_update().select_related(
                    "project", "project__portal", "created_by"
                ).get(pk=task.id)
                outcome = board_tasks._sync_task_locked(locked)

        task.refresh_from_db()
        self.assertTrue(outcome["errors"])          # agency failed
        self.assertEqual(task.bitrix_task_id, "999")  # client id persisted
        self.assertEqual(task.sync_status, Task.SyncStatus.ERROR)
        self.assertEqual(client.create_task.call_count, 1)  # only the client create
