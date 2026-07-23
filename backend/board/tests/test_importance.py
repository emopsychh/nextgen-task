"""Tests for task importance (Bitrix PRIORITY) two-way mapping."""

from __future__ import annotations

from django.test import TestCase

from board import tasks as board_tasks
from board.models import Task
from board.status_sync import apply_inbound_importance, bitrix_task_is_important

from .helpers import make_portal, make_project, make_task


class BitrixImportanceParseTests(TestCase):
    def test_high_priority_is_important(self):
        self.assertTrue(bitrix_task_is_important({"priority": "2"}))
        self.assertTrue(bitrix_task_is_important({"PRIORITY": 2}))

    def test_normal_and_low_not_important(self):
        self.assertFalse(bitrix_task_is_important({"priority": "1"}))
        self.assertFalse(bitrix_task_is_important({"priority": 0}))

    def test_missing_priority_returns_none(self):
        self.assertIsNone(bitrix_task_is_important({}))
        self.assertIsNone(bitrix_task_is_important({"priority": ""}))

    def test_non_dict_returns_none(self):
        self.assertIsNone(bitrix_task_is_important("x"))


class ApplyInboundImportanceTests(TestCase):
    def setUp(self):
        self.portal = make_portal()
        self.project = make_project(self.portal)

    def test_sets_flag_and_reports_changed(self):
        task = make_task(self.project, sync_status=Task.SyncStatus.SYNCED)
        self.assertTrue(apply_inbound_importance(task, True))
        task.refresh_from_db()
        self.assertTrue(task.is_important)

    def test_no_change_when_same(self):
        task = make_task(self.project, is_important=True, sync_status=Task.SyncStatus.SYNCED)
        self.assertFalse(apply_inbound_importance(task, True))

    def test_none_signal_is_noop(self):
        task = make_task(self.project, is_important=True, sync_status=Task.SyncStatus.SYNCED)
        self.assertFalse(apply_inbound_importance(task, None))
        task.refresh_from_db()
        self.assertTrue(task.is_important)

    def test_pending_push_not_clobbered_when_disallowed(self):
        task = make_task(self.project, sync_status=Task.SyncStatus.PENDING)
        self.assertFalse(
            apply_inbound_importance(task, True, allow_while_pending=False)
        )
        task.refresh_from_db()
        self.assertFalse(task.is_important)


class OutboundPriorityFieldTests(TestCase):
    def setUp(self):
        self.portal = make_portal()
        self.project = make_project(self.portal)

    def test_important_maps_to_priority_high(self):
        task = make_task(self.project, is_important=True)
        fields = board_tasks._task_fields(task)
        self.assertEqual(fields["PRIORITY"], "2")

    def test_not_important_maps_to_priority_normal(self):
        task = make_task(self.project, is_important=False)
        fields = board_tasks._task_fields(task)
        self.assertEqual(fields["PRIORITY"], "1")
