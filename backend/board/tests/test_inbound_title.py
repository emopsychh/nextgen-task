"""Inbound title must not clobber a pending local rename."""

from __future__ import annotations

from django.test import TestCase

from board.models import Task
from board.status_sync import apply_inbound_title

from .helpers import make_portal, make_project, make_task


class ApplyInboundTitleTests(TestCase):
    def setUp(self):
        self.portal = make_portal()
        self.project = make_project(self.portal)

    def test_applies_when_synced(self):
        task = make_task(self.project, title="Old", sync_status=Task.SyncStatus.SYNCED)
        self.assertTrue(apply_inbound_title(task, "New", allow_while_pending=False))
        task.refresh_from_db()
        self.assertEqual(task.title, "New")

    def test_pending_local_rename_not_clobbered(self):
        task = make_task(
            self.project, title="Local rename", sync_status=Task.SyncStatus.PENDING
        )
        changed = apply_inbound_title(task, "Stale Bitrix title", allow_while_pending=False)
        task.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(task.title, "Local rename")

    def test_empty_title_noop(self):
        task = make_task(self.project, title="Keep", sync_status=Task.SyncStatus.SYNCED)
        self.assertFalse(apply_inbound_title(task, "  ", allow_while_pending=False))
        task.refresh_from_db()
        self.assertEqual(task.title, "Keep")
