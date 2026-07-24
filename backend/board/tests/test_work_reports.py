"""Work report lifecycle API tests."""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from board.models import TimeEntry, WorkReport, WorkReportEvent
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class WorkReportApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-r", name="Agency")
        self.client_portal = make_portal(Portal.Role.CLIENT, member_id="client-r", name="Client")
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(self.agency, bitrix_id="a1", name="Agency", last_name="User")
        self.client_user = make_user(
            self.client_portal, bitrix_id="c1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Модуль")
        self.project2 = make_project(self.client_portal, name="Модуль 2")
        self.task = make_task(
            self.project, title="Сверстать", status="done", outcome="Сверстали главную"
        )
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=timezone.now(),
            ended_at=timezone.now(),
            duration_seconds=3600,
        )
        self.agency_client = APIClient()
        self.client_client = APIClient()
        agency_tokens = issue_tokens(self.agency, self.agency_user)
        client_tokens = issue_tokens(self.client_portal, self.client_user)
        self.agency_client.credentials(HTTP_AUTHORIZATION=f"Bearer {agency_tokens['access']}")
        self.client_client.credentials(HTTP_AUTHORIZATION=f"Bearer {client_tokens['access']}")

    def test_agency_create_multi_project_send_accept_paid(self):
        res = self.agency_client.post(
            "/api/reports/",
            {
                "portal": self.client_portal.id,
                "project_ids": [self.project.id, self.project2.id],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        report_id = res.data["id"]
        self.assertEqual(res.data["status"], "draft")
        self.assertEqual(sorted(res.data["project_ids"]), sorted([self.project.id, self.project2.id]))
        self.assertEqual(res.data["total_tracked_seconds"], 3600)
        blocks = res.data["projects_detail"]
        self.assertEqual(len(blocks), 2)
        first = next(b for b in blocks if b["id"] == self.project.id)
        self.assertEqual(first["tasks"][0]["outcome"], "Сверстали главную")

        bad = self.client_client.post(
            "/api/reports/",
            {"portal": self.client_portal.id, "project_ids": [self.project.id]},
            format="json",
        )
        self.assertEqual(bad.status_code, 403)

        dup = self.agency_client.post(
            "/api/reports/",
            {"portal": self.client_portal.id, "project_ids": [self.project.id]},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)

        sent = self.agency_client.post(f"/api/reports/{report_id}/send/", {}, format="json")
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertEqual(sent.data["status"], "pending_client")

        accepted = self.client_client.post(
            f"/api/reports/{report_id}/accept/", {}, format="json"
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.data["status"], "accepted")

        paid = self.agency_client.post(
            f"/api/reports/{report_id}/mark_paid/", {}, format="json"
        )
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(paid.data["status"], "paid")

        again = self.agency_client.post(
            "/api/reports/",
            {"portal": self.client_portal.id, "project_ids": [self.project.id]},
            format="json",
        )
        self.assertEqual(again.status_code, 201)

        kinds = list(
            WorkReportEvent.objects.filter(report_id=report_id)
            .order_by("id")
            .values_list("kind", flat=True)
        )
        self.assertEqual(kinds, ["created", "sent", "accepted", "paid"])

    def test_bucket_filters_and_outcome_live(self):
        create = self.agency_client.post(
            "/api/reports/",
            {"portal": self.client_portal.id, "project_ids": [self.project.id]},
            format="json",
        )
        report_id = create.data["id"]

        # Live outcome from task, not report text
        self.task.outcome = "Обновили итог"
        self.task.save(update_fields=["outcome"])
        detail = self.agency_client.get(f"/api/reports/{report_id}/")
        self.assertEqual(detail.data["projects_detail"][0]["tasks"][0]["outcome"], "Обновили итог")

        current = self.agency_client.get(
            f"/api/reports/?portal={self.client_portal.id}&bucket=current"
        )
        self.assertEqual(current.status_code, 200)
        self.assertTrue(any(r["id"] == report_id for r in current.data["results"]))

        self.agency_client.post(f"/api/reports/{report_id}/send/", {}, format="json")
        review = self.agency_client.get(
            f"/api/reports/?portal={self.client_portal.id}&bucket=review"
        )
        self.assertTrue(any(r["id"] == report_id for r in review.data["results"]))
        current2 = self.agency_client.get(
            f"/api/reports/?portal={self.client_portal.id}&bucket=current"
        )
        self.assertFalse(any(r["id"] == report_id for r in current2.data["results"]))

    def test_dispute_shows_only_flagged_tasks(self):
        other = make_task(self.project, title="Ок задача", status="done", outcome="Всё ок")
        create = self.agency_client.post(
            "/api/reports/",
            {"portal": self.client_portal.id, "project_ids": [self.project.id]},
            format="json",
        )
        report_id = create.data["id"]
        self.agency_client.post(f"/api/reports/{report_id}/send/", {}, format="json")

        disputed = self.client_client.post(
            f"/api/reports/{report_id}/dispute/",
            {
                "client_comment": "Нужно переделать",
                "task_ids": [self.task.id],
            },
            format="json",
        )
        self.assertEqual(disputed.status_code, 200, disputed.content)
        self.assertEqual(disputed.data["status"], "disputed")

        tasks = disputed.data["projects_detail"][0]["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], self.task.id)
        self.assertTrue(tasks[0]["disputed"])
        self.assertNotIn(other.id, [t["id"] for t in tasks])

        agency_view = self.agency_client.get(f"/api/reports/{report_id}/")
        self.assertEqual(len(agency_view.data["projects_detail"][0]["tasks"]), 1)
        self.assertEqual(agency_view.data["projects_detail"][0]["tasks"][0]["id"], self.task.id)

    def test_task_outcome_on_complete_patch(self):
        from unittest.mock import patch

        task = make_task(self.project, title="В работе", status="in_progress")
        with patch("board.views.enqueue_bitrix_sync"), patch(
            "board.views.append_task_change_events"
        ), patch("board.completion.finalize_task_completion"):
            res = self.agency_client.patch(
                f"/api/tasks/{task.id}/",
                {"status": "done", "outcome": "Сделали интеграцию"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["status"], "done")
        self.assertEqual(res.data["outcome"], "Сделали интеграцию")
        task.refresh_from_db()
        self.assertEqual(task.outcome, "Сделали интеграцию")
