"""Completion: stop timers and announce duration in chat (no Bitrix elapsed sync)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from board import tasks as board_tasks
from board.completion import COMPLETED_FOR_MARKER, finalize_task_completion
from board.models import Comment, Task, TimeEntry
from portals.models import Portal

from .helpers import make_portal, make_project, make_task, make_user


class CompletionHelpersTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("board.views.enqueue_comment_sync")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_stops_timer_and_posts_completion_message(self, enqueue_comment):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        start = timezone.now() - timedelta(seconds=120)
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=start,
            ended_at=start + timedelta(seconds=120),
            duration_seconds=120,
        )
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=timezone.now(),
        )
        result = finalize_task_completion(task, author=self.user)
        task.refresh_from_db()
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())
        comment = Comment.objects.get(task=task, is_system=True)
        self.assertTrue(comment.text.startswith(COMPLETED_FOR_MARKER))
        self.assertIn("2 мин", comment.text)
        self.assertEqual(result["completion_comment_id"], comment.id)
        self.assertFalse(result["elapsed_sync_enqueued"])
        enqueue_comment.assert_called_once_with(comment.id)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("board.views.enqueue_comment_sync")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_is_idempotent_for_completion_message(self, enqueue_comment):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=timezone.now() - timedelta(seconds=60),
            ended_at=timezone.now(),
            duration_seconds=60,
        )
        finalize_task_completion(task, author=self.user)
        finalize_task_completion(task, author=self.user)
        self.assertEqual(
            Comment.objects.filter(
                task=task, is_system=True, text__startswith=COMPLETED_FOR_MARKER
            ).count(),
            1,
        )
        self.assertEqual(enqueue_comment.call_count, 1)

    def test_sync_completion_time_is_noop(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            agency_bitrix_task_id="108",
        )
        result = board_tasks.sync_completion_time_to_bitrix(task.id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "bitrix_time_manual")
