"""Completion: stop timers and fill Bitrix elapsed time without chat spam."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from board import tasks as board_tasks
from board.completion import finalize_task_completion
from board.models import Comment, Task, TimeEntry
from portals.models import Portal

from .helpers import make_link, make_portal, make_project, make_task, make_user


class CompletionHelpersTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("board.tasks.sync_completion_time_to_bitrix.delay")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_stops_timer_enqueues_elapsed_without_chat(self, enqueue):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        TimeEntry.objects.create(
            task=task, author=self.user, started_at=timezone.now()
        )
        finalize_task_completion(task, author=self.user)
        task.refresh_from_db()
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())
        self.assertFalse(Comment.objects.filter(task=task).exists())
        enqueue.assert_called_once_with(task.id)

    def test_completion_posts_only_missing_elapsed_time(self):
        agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        make_link(agency, self.portal)
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
        client.get_task_elapsed_seconds.return_value = 30
        client.get_current_user.return_value = {"ID": "42"}
        client.add_elapsed_item.return_value = "501"

        with patch.object(board_tasks, "BitrixClient", return_value=client):
            result = board_tasks.sync_completion_time_to_bitrix(task.id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["added_seconds"], 90)
        client.update_task.assert_called_once_with(
            "108",
            {"ALLOW_TIME_TRACKING": "Y"},
        )
        client.add_elapsed_item.assert_called_once_with(
            "108",
            90,
            comment="",
            user_id="42",
        )

        client.reset_mock()
        client.get_task_elapsed_seconds.return_value = 120
        with patch.object(board_tasks, "BitrixClient", return_value=client):
            repeated = board_tasks.sync_completion_time_to_bitrix(task.id)
        self.assertEqual(repeated["skipped"], "already_filled")
        client.add_elapsed_item.assert_not_called()
