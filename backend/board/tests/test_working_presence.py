"""Live presence «Работаю прямо сейчас» API tests."""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from board.models import Task
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class WorkingPresenceApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-w", name="Agency")
        self.client_portal = make_portal(
            Portal.Role.CLIENT, member_id="client-w", name="Client"
        )
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(
            self.agency, bitrix_id="aw1", name="Agency", last_name="Worker"
        )
        self.client_user = make_user(
            self.client_portal, bitrix_id="cw1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Проект")
        self.task = make_task(self.project, title="Задача A", created_by=self.agency_user)
        self.task_b = make_task(
            self.project, title="Задача B", created_by=self.agency_user
        )
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

    def test_agency_start_and_stop_working(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"):
            start = self.agency_client.post(
                f"/api/tasks/{self.task.id}/working/start/", format="json"
            )
        self.assertEqual(start.status_code, 200, start.content)
        self.assertTrue(start.data["is_working"])
        self.assertIsNotNone(start.data["working_started_at"])
        self.assertEqual(start.data["working_by_name"], self.agency_user.display_name)

        self.task.refresh_from_db()
        self.assertEqual(self.task.working_by_id, self.agency_user.id)
        self.assertIsNotNone(self.task.working_started_at)

        with patch("board.views.publish_task_event"):
            stop = self.agency_client.post(
                f"/api/tasks/{self.task.id}/working/stop/", format="json"
            )
        self.assertEqual(stop.status_code, 200, stop.content)
        self.assertFalse(stop.data["is_working"])
        self.assertIsNone(stop.data["working_started_at"])
        self.assertIsNone(stop.data["working_by_name"])

        self.task.refresh_from_db()
        self.assertIsNone(self.task.working_by_id)
        self.assertIsNone(self.task.working_started_at)

    def test_client_cannot_start_working(self):
        res = self.client_client.post(
            f"/api/tasks/{self.task.id}/working/start/", format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_start_keeps_other_started_tasks(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            first = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )
            second = self.agency_client.patch(
                f"/api/tasks/{self.task_b.id}/",
                {"status": "in_progress"},
                format="json",
            )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(first.data["is_working"])
        self.assertTrue(second.data["is_working"])

        self.task.refresh_from_db()
        self.task_b.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        self.assertEqual(self.task_b.status, Task.Status.IN_PROGRESS)
        self.assertIsNotNone(self.task.working_started_at)
        self.assertIsNotNone(self.task_b.working_started_at)

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            pause = self.agency_client.patch(
                f"/api/tasks/{self.task_b.id}/",
                {"status": "todo"},
                format="json",
            )
        self.assertEqual(pause.status_code, 200, pause.content)
        self.assertFalse(pause.data["is_working"])

        res = self.agency_client.get(
            f"/api/tasks/?portal={self.client_portal.id}&working=1"
        )
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        ids = [row["id"] for row in rows]
        self.assertEqual(ids, [self.task.id])
        self.assertTrue(rows[0]["is_working"])

    def test_completing_task_clears_working(self):
        from unittest.mock import patch

        self.task.working_by = self.agency_user
        self.task.working_started_at = timezone.now()
        self.task.save(update_fields=["working_by", "working_started_at"])

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"), patch(
            "board.completion.finalize_task_completion"
        ):
            res = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "done", "outcome": "Сделано"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.data["is_working"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.DONE)
        self.assertIsNone(self.task.working_started_at)
        self.assertIsNone(self.task.working_by_id)

    def test_start_status_sets_working(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            res = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data["is_working"])
        self.assertEqual(res.data["working_by_name"], self.agency_user.display_name)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        self.assertEqual(self.task.working_by_id, self.agency_user.id)
        self.assertIsNotNone(self.task.working_started_at)

    def test_pause_status_clears_working(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )
            res = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "todo"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.data["is_working"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.TODO)
        self.assertIsNone(self.task.working_started_at)
        self.assertIsNone(self.task.working_by_id)

    def test_project_has_active_work_flag(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )

        res = self.agency_client.get(
            f"/api/projects/?portal={self.client_portal.id}"
        )
        self.assertEqual(res.status_code, 200, res.content)
        projects = res.data["results"] if isinstance(res.data, dict) else res.data
        match = next(p for p in projects if p["id"] == self.project.id)
        self.assertTrue(match["has_active_work"])

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "todo"},
                format="json",
            )

        res2 = self.agency_client.get(
            f"/api/projects/?portal={self.client_portal.id}"
        )
        projects2 = res2.data["results"] if isinstance(res2.data, dict) else res2.data
        match2 = next(p for p in projects2 if p["id"] == self.project.id)
        self.assertFalse(match2["has_active_work"])

    def test_cannot_start_on_done_task(self):
        self.task.status = Task.Status.DONE
        self.task.outcome = "Готово"
        self.task.save(update_fields=["status", "outcome"])
        res = self.agency_client.post(
            f"/api/tasks/{self.task.id}/working/start/", format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_list_working_filter(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )
            self.agency_client.patch(
                f"/api/tasks/{self.task_b.id}/",
                {"status": "in_progress"},
                format="json",
            )
        res = self.agency_client.get(
            f"/api/tasks/?portal={self.client_portal.id}&working=1"
        )
        self.assertEqual(res.status_code, 200)
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        ids = {row["id"] for row in rows}
        self.assertEqual(ids, {self.task.id, self.task_b.id})
        self.assertTrue(all(row["is_working"] for row in rows))
