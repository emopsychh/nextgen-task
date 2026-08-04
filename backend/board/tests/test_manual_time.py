"""Manual time sets absolute hours/minutes (does not accumulate)."""

from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from board.models import TimeEntry
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ManualTimeApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-t", name="Agency")
        self.client_portal = make_portal(Portal.Role.CLIENT, member_id="client-t", name="Client")
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(self.agency, bitrix_id="a1", name="Agency", last_name="User")
        self.client_user = make_user(
            self.client_portal, bitrix_id="c1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Проект")
        self.task = make_task(self.project, title="Задача", created_by=self.agency_user)
        self.agency_client = APIClient()
        self.client_client = APIClient()
        agency_tokens = issue_tokens(self.agency, self.agency_user)
        client_tokens = issue_tokens(self.client_portal, self.client_user)
        self.agency_client.credentials(HTTP_AUTHORIZATION=f"Bearer {agency_tokens['access']}")
        self.client_client.credentials(HTTP_AUTHORIZATION=f"Bearer {client_tokens['access']}")

    def test_agency_sets_manual_time(self):
        from unittest.mock import patch

        with patch("board.timeutils._bill_absolute_time"), patch(
            "board.timeutils.enqueue_timer_bitrix_sync"
        ) as enqueue_bx, patch("board.views.publish_task_event"):
            res = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 1, "minutes": 30},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["total_tracked_seconds"], 5400)
        self.assertIsNone(res.data.get("active_timer"))
        entry = TimeEntry.objects.get(task=self.task)
        self.assertEqual(entry.duration_seconds, 5400)
        self.assertIsNotNone(entry.ended_at)
        enqueue_bx.assert_called_once_with(entry.id, "set")

    def test_set_time_replaces_previous_total(self):
        from unittest.mock import patch

        with patch("board.timeutils._bill_absolute_time"), patch(
            "board.timeutils.enqueue_timer_bitrix_sync"
        ), patch("board.views.publish_task_event"):
            first = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 0, "minutes": 30},
                format="json",
            )
            self.assertEqual(first.status_code, 200, first.content)
            second = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 1, "minutes": 0},
                format="json",
            )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.data["total_tracked_seconds"], 3600)
        self.assertEqual(TimeEntry.objects.filter(task=self.task).count(), 1)
        self.assertEqual(TimeEntry.objects.get(task=self.task).duration_seconds, 3600)

    def test_manual_time_pushes_elapsed_to_bitrix(self):
        from unittest.mock import MagicMock, patch

        from board import tasks as board_tasks

        self.agency.access_token = "tok"
        self.agency.save(update_fields=["access_token"])
        self.task.agency_bitrix_task_id = "108"
        self.task.save(update_fields=["agency_bitrix_task_id"])

        client = MagicMock()
        client.get_current_user.return_value = {"ID": "42"}
        client.add_elapsed_item.return_value = "501"

        with patch("board.timeutils._bill_absolute_time"), patch(
            "board.views.publish_task_event"
        ), patch.object(board_tasks, "BitrixClient", return_value=client):
            res = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 0, "minutes": 45},
                format="json",
            )

        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(client.update_task.called or client.call.called)
        client.add_elapsed_item.assert_called_once()
        add_args, add_kwargs = client.add_elapsed_item.call_args
        self.assertEqual(add_args[0], "108")
        self.assertEqual(add_args[1], 2700)
        # Docs happy path: omit USER_ID so Bitrix attributes to the OAuth user.
        self.assertIsNone(add_kwargs.get("user_id"))
        self.assertTrue(add_kwargs.get("date_start"))
        self.assertTrue(add_kwargs.get("date_stop"))
        entry = TimeEntry.objects.get(task=self.task)
        self.assertEqual(entry.bitrix_elapsed_id, "501")

        # Second set updates the same Bitrix elapsed row.
        client.reset_mock()
        with patch("board.timeutils._bill_absolute_time"), patch(
            "board.views.publish_task_event"
        ), patch.object(board_tasks, "BitrixClient", return_value=client):
            res2 = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 1, "minutes": 0},
                format="json",
            )
        self.assertEqual(res2.status_code, 200, res2.content)
        client.update_elapsed_item.assert_called_once()
        upd_args, upd_kwargs = client.update_elapsed_item.call_args
        self.assertEqual(upd_args[:3], ("108", "501", 3600))
        client.add_elapsed_item.assert_not_called()

    def test_start_status_does_not_open_timer(self):
        from unittest.mock import patch

        with patch("board.views.enqueue_bitrix_sync"), patch(
            "board.views.append_task_change_events"
        ), patch("board.views.publish_task_event"):
            res = self.agency_client.patch(
                f"/api/tasks/{self.task.id}/",
                {"status": "in_progress"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["status"], "in_progress")
        self.assertFalse(TimeEntry.objects.filter(task=self.task, ended_at__isnull=True).exists())
        self.assertEqual(res.data["total_tracked_seconds"], 0)

    def test_client_cannot_add_time(self):
        res = self.client_client.post(
            f"/api/tasks/{self.task.id}/time/",
            {"hours": 0, "minutes": 15},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_zero_time_clears(self):
        from unittest.mock import patch

        with patch("board.timeutils._bill_absolute_time"), patch(
            "board.timeutils.enqueue_timer_bitrix_sync"
        ), patch("board.timeutils._enqueue_bitrix_elapsed_cleanup"), patch(
            "board.views.publish_task_event"
        ):
            self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 0, "minutes": 15},
                format="json",
            )
            res = self.agency_client.post(
                f"/api/tasks/{self.task.id}/time/",
                {"hours": 0, "minutes": 0},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["total_tracked_seconds"], 0)
        self.assertFalse(TimeEntry.objects.filter(task=self.task).exists())
