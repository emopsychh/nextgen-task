"""Tests for board.comment_sync — system-log detection, normalisation, echo guard."""

from __future__ import annotations

from django.test import TestCase, override_settings

from board.comment_sync import (
    _normalize_message,
    is_bitrix_system_log_comment,
    is_nextgen_file_echo,
    resolve_status_with_timer_activity,
    status_from_bitrix_system_comment,
    upsert_comment_from_bitrix_payload,
)
from board.models import Comment, Task

from .helpers import make_portal, make_project, make_task, make_user


class SystemLogDetectionTests(TestCase):
    def test_pause_line_is_system(self):
        self.assertTrue(is_bitrix_system_log_comment("Иван остановил работу над задачей"))

    def test_deadline_line_is_system(self):
        self.assertTrue(is_bitrix_system_log_comment("Пётр изменил крайний срок"))

    def test_plain_message_is_not_system(self):
        self.assertFalse(is_bitrix_system_log_comment("Привет, посмотри макет"))

    def test_empty_not_system(self):
        self.assertFalse(is_bitrix_system_log_comment(""))

    def test_status_from_pause(self):
        self.assertEqual(status_from_bitrix_system_comment("остановил работу"), "todo")

    def test_status_from_start(self):
        self.assertEqual(status_from_bitrix_system_comment("начал работу"), "in_progress")

    def test_status_from_complete(self):
        self.assertEqual(status_from_bitrix_system_comment("завершил задачу"), "done")

    def test_status_from_plain_none(self):
        self.assertIsNone(status_from_bitrix_system_comment("обычный комментарий"))


class ResolveActivityTests(TestCase):
    def test_done_activity_wins(self):
        self.assertEqual(resolve_status_with_timer_activity("in_progress", "done"), "done")

    def test_pause_activity_over_in_progress(self):
        self.assertEqual(resolve_status_with_timer_activity("in_progress", "todo"), "todo")

    def test_pause_activity_over_none(self):
        self.assertEqual(resolve_status_with_timer_activity(None, "todo"), "todo")

    def test_in_progress_activity_does_not_override_todo(self):
        # Stale «включил учёт» must not resurrect work over an already-detected pause.
        self.assertEqual(resolve_status_with_timer_activity("todo", "in_progress"), "todo")


class NormalizeMessageTests(TestCase):
    def test_strips_author_prefix(self):
        self.assertEqual(_normalize_message("Иван", "Иван: привет"), "привет")

    def test_keeps_message_without_prefix(self):
        self.assertEqual(_normalize_message("Иван", "привет всем"), "привет всем")

    def test_nextgen_file_echo_detected(self):
        self.assertTrue(is_nextgen_file_echo("[Файл из Nextgen] doc.pdf"))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class UpsertCommentTests(TestCase):
    def setUp(self):
        self.portal = make_portal()
        self.project = make_project(self.portal)
        self.task = make_task(self.project)

    def test_creates_plain_comment(self):
        created = upsert_comment_from_bitrix_payload(
            task=self.task,
            portal=self.portal,
            payload={"ID": "10", "AUTHOR_NAME": "Иван", "POST_MESSAGE": "Иван: привет"},
        )
        self.assertTrue(created)
        c = Comment.objects.get(task=self.task)
        self.assertEqual(c.text, "привет")
        self.assertEqual(c.bitrix_comment_id, "10")

    def test_duplicate_id_not_created_twice(self):
        payload = {"ID": "11", "AUTHOR_NAME": "Иван", "POST_MESSAGE": "текст"}
        self.assertTrue(
            upsert_comment_from_bitrix_payload(task=self.task, portal=self.portal, payload=payload)
        )
        self.assertFalse(
            upsert_comment_from_bitrix_payload(task=self.task, portal=self.portal, payload=payload)
        )
        self.assertEqual(Comment.objects.filter(task=self.task).count(), 1)

    def test_system_log_not_imported_but_sets_status(self):
        # SYNCED so the "don't clobber a fresh pending push" guard does not apply.
        Task.objects.filter(pk=self.task.pk).update(
            status=Task.Status.IN_PROGRESS, sync_status=Task.SyncStatus.SYNCED
        )
        self.task.refresh_from_db()
        created = upsert_comment_from_bitrix_payload(
            task=self.task,
            portal=self.portal,
            payload={"ID": "12", "AUTHOR_NAME": "Иван", "POST_MESSAGE": "Иван: остановил работу"},
        )
        self.assertFalse(created)
        self.assertEqual(Comment.objects.filter(task=self.task).count(), 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.TODO)

    def test_echo_guard_attaches_id_instead_of_duplicating(self):
        # Local outbound comment posted moments ago, still without a Bitrix id.
        local = Comment.objects.create(task=self.task, author_name="Иван", text="эхо-текст")
        created = upsert_comment_from_bitrix_payload(
            task=self.task,
            portal=self.portal,
            payload={"ID": "13", "AUTHOR_NAME": "Иван", "POST_MESSAGE": "Иван: эхо-текст"},
        )
        self.assertFalse(created)
        self.assertEqual(Comment.objects.filter(task=self.task).count(), 1)
        local.refresh_from_db()
        self.assertEqual(local.bitrix_comment_id, "13")
