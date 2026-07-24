"""Completion artifacts: stop timers + spent-time system chat line."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from board.completion import (
    TIME_SPENT_MARKER,
    append_time_spent_chat,
    finalize_task_completion,
    format_tracked_duration,
)
from board.models import Comment, Task, TimeEntry
from portals.models import Portal

from .helpers import make_portal, make_project, make_task, make_user


class CompletionHelpersTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)

    def test_format_duration(self):
        self.assertEqual(format_tracked_duration(3661), "1 ч 1 мин")
        self.assertEqual(format_tracked_duration(120), "2 мин")
        self.assertEqual(format_tracked_duration(7200), "2 ч")

    def test_chat_line_once(self):
        task = make_task(self.project, created_by=self.user, status=Task.Status.DONE)
        start = timezone.now() - timedelta(seconds=90)
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=start,
            ended_at=start + timedelta(seconds=90),
            duration_seconds=90,
        )
        self.assertTrue(append_time_spent_chat(task, author=self.user))
        self.assertFalse(append_time_spent_chat(task, author=self.user))
        row = Comment.objects.get(task=task, is_system=True)
        self.assertTrue(row.text.startswith(TIME_SPENT_MARKER))
        self.assertIn("1 мин", row.text)

    def test_chat_line_bitrix_completion_uses_team_label(self):
        task = make_task(self.project, created_by=self.user, status=Task.Status.DONE)
        self.assertTrue(append_time_spent_chat(task, author=None))
        row = Comment.objects.get(task=task, is_system=True)
        self.assertEqual(row.author_name, "Команда")
        self.assertIsNone(row.author_id)

    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_stops_timer_and_chats(self):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        TimeEntry.objects.create(
            task=task, author=self.user, started_at=timezone.now()
        )
        finalize_task_completion(task, author=self.user)
        task.refresh_from_db()
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())
        self.assertTrue(
            Comment.objects.filter(
                task=task, is_system=True, text__startswith=TIME_SPENT_MARKER
            ).exists()
        )
