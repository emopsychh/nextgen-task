"""Tests for board.status_sync — status mapping, deadlines, timers, inbound apply."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.test import TestCase, override_settings
from django.utils import timezone

from board.models import Task, TimeEntry
from board.status_sync import (
    apply_inbound_status,
    bitrix_timer_is_running,
    deadlines_equal,
    local_status_from_bitrix_task,
    parse_bitrix_deadline,
)
from portals.bitrix import (
    BITRIX_STATUS_COMPLETED,
    BITRIX_STATUS_IN_PROGRESS,
    BITRIX_STATUS_PENDING,
)
from portals.models import Portal

from .helpers import make_link, make_portal, make_project, make_task, make_user


class LocalStatusFromBitrixTests(TestCase):
    def test_completed_status_maps_done(self):
        self.assertEqual(
            local_status_from_bitrix_task({"status": BITRIX_STATUS_COMPLETED}), "done"
        )

    def test_pending_status_maps_todo(self):
        self.assertEqual(
            local_status_from_bitrix_task({"status": BITRIX_STATUS_PENDING}), "todo"
        )

    def test_in_progress_status_maps_in_progress(self):
        self.assertEqual(
            local_status_from_bitrix_task({"status": BITRIX_STATUS_IN_PROGRESS}),
            "in_progress",
        )

    def test_action_pause_true_means_in_progress(self):
        # STATUS may lag; action.pause=true means work is active (can be paused)
        data = {"status": BITRIX_STATUS_PENDING, "action": {"pause": True}}
        self.assertEqual(local_status_from_bitrix_task(data), "in_progress")

    def test_action_start_true_and_no_pause_means_todo(self):
        data = {"status": BITRIX_STATUS_IN_PROGRESS, "action": {"start": True, "pause": False}}
        self.assertEqual(local_status_from_bitrix_task(data), "todo")

    def test_non_dict_returns_none(self):
        self.assertIsNone(local_status_from_bitrix_task("nope"))


class DeadlineTests(TestCase):
    def test_equal_at_minute_precision(self):
        a = datetime(2026, 5, 1, 10, 30, 15, tzinfo=dt_timezone.utc)
        b = datetime(2026, 5, 1, 10, 30, 59, tzinfo=dt_timezone.utc)
        self.assertTrue(deadlines_equal(a, b))

    def test_not_equal_different_minute(self):
        a = datetime(2026, 5, 1, 10, 30, tzinfo=dt_timezone.utc)
        b = datetime(2026, 5, 1, 10, 31, tzinfo=dt_timezone.utc)
        self.assertFalse(deadlines_equal(a, b))

    def test_both_none_equal(self):
        self.assertTrue(deadlines_equal(None, None))

    def test_one_none_not_equal(self):
        self.assertFalse(deadlines_equal(datetime.now(dt_timezone.utc), None))

    def test_date_only_treated_as_end_of_day(self):
        d = date(2026, 5, 1)
        same = datetime(2026, 5, 1, 23, 59, tzinfo=dt_timezone.utc)
        self.assertTrue(deadlines_equal(d, same))

    def test_parse_iso_datetime(self):
        parsed = parse_bitrix_deadline({"deadline": "2026-05-01T10:30:00"})
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.year, parsed.month, parsed.day, parsed.hour), (2026, 5, 1, 10))

    def test_parse_empty_returns_none(self):
        self.assertIsNone(parse_bitrix_deadline({"deadline": ""}))
        self.assertIsNone(parse_bitrix_deadline({"DEADLINE": "false"}))

    def test_parse_ru_format(self):
        parsed = parse_bitrix_deadline({"deadline": "01.05.2026 10:30:00"})
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.month, parsed.day), (5, 1))


class TimerRunningTests(TestCase):
    def test_none_payload_unknown(self):
        self.assertIsNone(bitrix_timer_is_running({}, None))

    def test_empty_list_is_stopped(self):
        self.assertFalse(bitrix_timer_is_running({}, []))

    def test_empty_dict_is_stopped(self):
        self.assertFalse(bitrix_timer_is_running({}, {}))

    def test_timer_for_other_task_is_stopped(self):
        payload = {"TASK_ID": "555"}
        self.assertFalse(bitrix_timer_is_running({}, payload, bitrix_task_id="999"))

    def test_timer_for_same_task_is_running(self):
        payload = {"TASK_ID": "999", "TIMER_STARTED_AT": "1700000000"}
        self.assertTrue(bitrix_timer_is_running({}, payload, bitrix_task_id="999"))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ApplyInboundStatusTests(TestCase):
    """Client-only portal (no agency link) so timer stops never hit a broker/Bitrix."""

    def setUp(self):
        self.portal = make_portal()
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    def _running_task(self):
        task = make_task(
            self.project,
            status=Task.Status.IN_PROGRESS,
            sync_status=Task.SyncStatus.SYNCED,
            created_by=self.user,
        )
        TimeEntry.objects.create(task=task, author=self.user, started_at=timezone.now())
        return task

    def test_pause_stops_running_timer(self):
        task = self._running_task()
        changed = apply_inbound_status(task, "todo", force=True)
        task.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(task.status, Task.Status.TODO)
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_done_stops_running_timer(self):
        task = self._running_task()
        apply_inbound_status(task, "done", force=True)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DONE)
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_start_creates_local_timer(self):
        # Timers are owned by agency users: an agency link + user must exist for
        # _start_local_timer_from_inbound to pick an author.
        agency = make_portal(role=Portal.Role.AGENCY)
        make_link(agency, self.portal)
        make_user(agency, bitrix_id="99")
        task = make_task(
            self.project,
            status=Task.Status.TODO,
            sync_status=Task.SyncStatus.SYNCED,
            created_by=self.user,
        )
        apply_inbound_status(task, "in_progress", force=True)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)
        self.assertTrue(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_pending_local_push_not_clobbered_without_force(self):
        task = make_task(
            self.project,
            status=Task.Status.IN_PROGRESS,
            sync_status=Task.SyncStatus.PENDING,
            created_by=self.user,
        )
        # A stale Bitrix echo (force=False) must not regress a pending local push.
        changed = apply_inbound_status(task, "todo", force=False)
        task.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)

    def test_force_pending_recent_push_still_skipped(self):
        task = make_task(
            self.project,
            status=Task.Status.IN_PROGRESS,
            sync_status=Task.SyncStatus.PENDING,
            created_by=self.user,
        )
        # Fresh update_at (<12s): even force=True defers to avoid regressing start().
        changed = apply_inbound_status(task, "todo", force=True)
        self.assertFalse(changed)

    def test_force_pending_old_push_applies(self):
        task = make_task(
            self.project,
            status=Task.Status.IN_PROGRESS,
            sync_status=Task.SyncStatus.PENDING,
            created_by=self.user,
        )
        Task.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(seconds=30)
        )
        task.refresh_from_db()
        changed = apply_inbound_status(task, "todo", force=True)
        task.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(task.status, Task.Status.TODO)
