"""Completion: stop timers, announce duration, sync unsynced time to Bitrix."""

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
    @patch("board.timeutils.enqueue_timer_bitrix_sync")
    @patch("board.views.enqueue_comment_sync")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_stops_timer_and_posts_completion_message(
        self, enqueue_comment, enqueue_elapsed
    ):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        start = timezone.now() - timedelta(seconds=120)
        closed = TimeEntry.objects.create(
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
        self.assertTrue(result["elapsed_sync_enqueued"])
        enqueue_comment.assert_called_once_with(comment.id)
        # Closed row without bitrix_elapsed_id must be pushed to учёта времени.
        enqueue_elapsed.assert_any_call(closed.id, "set")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("board.timeutils.enqueue_timer_bitrix_sync")
    @patch("board.views.enqueue_comment_sync")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_skips_elapsed_already_synced(
        self, enqueue_comment, enqueue_elapsed
    ):
        task = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE
        )
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=timezone.now() - timedelta(seconds=60),
            ended_at=timezone.now(),
            duration_seconds=60,
            bitrix_elapsed_id="99",
        )
        result = finalize_task_completion(task, author=self.user)
        self.assertFalse(result["elapsed_sync_enqueued"])
        enqueue_elapsed.assert_not_called()
        enqueue_comment.assert_called_once()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("board.timeutils.enqueue_timer_bitrix_sync")
    @patch("board.views.enqueue_comment_sync")
    @patch("board.realtime.publish_task_event", lambda *a, **k: None)
    def test_finalize_is_idempotent_for_completion_message(
        self, enqueue_comment, enqueue_elapsed
    ):
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
        # Elapsed re-queue is ok twice until bitrix_elapsed_id is set.
        self.assertGreaterEqual(enqueue_elapsed.call_count, 1)

    def test_sync_completion_time_is_noop(self):
        task = make_task(
            self.project,
            created_by=self.user,
            status=Task.Status.DONE,
            agency_bitrix_task_id="108",
        )
        result = board_tasks.sync_completion_time_to_bitrix(task.id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "per_entry_sync")

    def test_save_sets_completed_at_when_task_is_done(self):
        task = make_task(self.project, created_by=self.user)
        self.assertIsNone(task.completed_at)
        task.status = Task.Status.DONE
        task.outcome = "Готово"
        task.save()
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)
        first = task.completed_at
        task.title = "Updated"
        task.save()
        task.refresh_from_db()
        self.assertEqual(task.completed_at, first)

    def test_project_completed_at_is_last_task_when_all_done(self):
        from board.serializers import ProjectSerializer

        first = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE, outcome="a"
        )
        second = make_task(
            self.project, created_by=self.user, status=Task.Status.DONE, outcome="b"
        )
        later = timezone.now() + timedelta(hours=2)
        Task.objects.filter(pk=second.pk).update(completed_at=later)
        data = ProjectSerializer(self.project).data
        self.assertEqual(data["completed_at"], later)
        self.assertNotEqual(first.completed_at, later)

    def test_project_completed_at_empty_while_open_tasks_remain(self):
        from board.serializers import ProjectSerializer

        make_task(
            self.project, created_by=self.user, status=Task.Status.DONE, outcome="a"
        )
        make_task(self.project, created_by=self.user, status=Task.Status.TODO)
        data = ProjectSerializer(self.project).data
        self.assertIsNone(data["completed_at"])
