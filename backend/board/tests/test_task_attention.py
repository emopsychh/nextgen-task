"""Client homepage «Требует вашего внимания»: unseen outcomes and reply waits."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from board.models import Task
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class TaskAttentionApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-att", name="Agency")
        self.client_portal = make_portal(
            Portal.Role.CLIENT, member_id="client-att", name="Client"
        )
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(
            self.agency, bitrix_id="aa1", name="Agency", last_name="User"
        )
        self.client_user = make_user(
            self.client_portal, bitrix_id="ca1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Проект")
        self.task = make_task(self.project, title="Макет отчёта", created_by=self.agency_user)
        self.agency_client = APIClient()
        self.client_client = APIClient()
        agency_tokens = issue_tokens(self.agency, self.agency_user)
        client_tokens = issue_tokens(self.client_portal, self.client_user)
        self.agency_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {agency_tokens['access']}"
        )
        self.client_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {client_tokens['access']}"
        )

    def _attention_ids(self):
        res = self.client_client.get(
            f"/api/tasks/?portal={self.client_portal.id}&attention=1"
        )
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        return [row["id"] for row in rows]

    def test_completed_task_enters_attention_until_client_opens_it(self):
        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"), patch(
            "board.completion.finalize_task_completion"
        ):
            done = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "done", "outcome": "Макет готов"},
                format="json",
            )
        self.assertEqual(done.status_code, 200, done.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.DONE)
        self.assertIsNone(self.task.outcome_seen_at)
        self.assertIn(self.task.id, self._attention_ids())

        with patch("board.views.publish_task_event"):
            opened = self.client_client.get(f"/api/tasks/{self.task.id}/")
        self.assertEqual(opened.status_code, 200, opened.content)
        self.assertIsNotNone(opened.data["outcome_seen_at"])
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.outcome_seen_at)
        self.assertNotIn(self.task.id, self._attention_ids())

    def test_agency_open_does_not_mark_outcome_seen(self):
        self.task.status = Task.Status.DONE
        self.task.outcome = "Готово"
        self.task.save()
        self.task.refresh_from_db()
        self.assertIsNone(self.task.outcome_seen_at)

        with patch("board.views.publish_task_event"):
            opened = self.agency_client.get(f"/api/tasks/{self.task.id}/")
        self.assertEqual(opened.status_code, 200, opened.content)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.outcome_seen_at)
        self.assertIn(self.task.id, self._attention_ids())

    def test_previously_seen_done_task_stays_out_of_attention(self):
        self.task.status = Task.Status.DONE
        self.task.outcome = "Старое"
        self.task.outcome_seen_at = timezone.now()
        self.task.save()
        self.assertNotIn(self.task.id, self._attention_ids())

    def test_agency_wait_for_client_and_client_reply_clears_it(self):
        with patch("board.views.publish_task_event"):
            start = self.agency_client.post(
                f"/api/tasks/{self.task.id}/awaiting-client/start/",
                format="json",
            )
        self.assertEqual(start.status_code, 200, start.content)
        self.assertTrue(start.data["awaiting_client"])
        self.assertIsNotNone(start.data["awaiting_client_at"])
        self.assertIn(self.task.id, self._attention_ids())

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_comment_sync"
        ):
            agency_note = self.agency_client.post(
                "/api/comments/",
                {"task": self.task.id, "text": "Нужен ответ"},
                format="json",
            )
        self.assertEqual(agency_note.status_code, 201, agency_note.content)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.awaiting_client_at)

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_comment_sync"
        ):
            reply = self.client_client.post(
                "/api/comments/",
                {"task": self.task.id, "text": "Вот доступы"},
                format="json",
            )
        self.assertEqual(reply.status_code, 201, reply.content)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.awaiting_client_at)
        self.assertNotIn(self.task.id, self._attention_ids())

    def test_agency_can_stop_waiting(self):
        self.task.awaiting_client_at = timezone.now()
        self.task.save(update_fields=["awaiting_client_at"])
        with patch("board.views.publish_task_event"):
            stop = self.agency_client.post(
                f"/api/tasks/{self.task.id}/awaiting-client/stop/",
                format="json",
            )
        self.assertEqual(stop.status_code, 200, stop.content)
        self.assertFalse(stop.data["awaiting_client"])
        self.assertNotIn(self.task.id, self._attention_ids())

    def test_client_cannot_start_waiting(self):
        res = self.client_client.post(
            f"/api/tasks/{self.task.id}/awaiting-client/start/",
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_cannot_wait_on_done_task(self):
        self.task.status = Task.Status.DONE
        self.task.outcome = "Готово"
        self.task.save()
        res = self.agency_client.post(
            f"/api/tasks/{self.task.id}/awaiting-client/start/",
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_completing_clears_awaiting_flag(self):
        self.task.awaiting_client_at = timezone.now()
        self.task.save(update_fields=["awaiting_client_at"])
        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"), patch(
            "board.completion.finalize_task_completion"
        ):
            done = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "done", "outcome": "Закрыли"},
                format="json",
            )
        self.assertEqual(done.status_code, 200, done.content)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.awaiting_client_at)
        self.assertIsNone(self.task.outcome_seen_at)
        self.assertIn(self.task.id, self._attention_ids())
