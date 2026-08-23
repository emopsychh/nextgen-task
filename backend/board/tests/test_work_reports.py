"""CRM deal-bound work report invariants."""

from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from board.models import TimeEntry, WorkReport, WorkReportLine
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal, PortalDealBinding
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class WorkReportApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-r")
        self.client_portal = make_portal(Portal.Role.CLIENT, member_id="client-r")
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(self.agency, bitrix_id="a1")
        self.client_user = make_user(self.client_portal, bitrix_id="c1")
        self.project = make_project(self.client_portal, name="Модуль")
        self.task = make_task(self.project, title="Сверстать", status="done", outcome="Готово")
        self.agency_client = APIClient()
        self.client_client = APIClient()
        self.agency_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {issue_tokens(self.agency, self.agency_user)['access']}"
        )
        self.client_client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {issue_tokens(self.client_portal, self.client_user)['access']}"
            )
        )

    def create_binding(self, deal_id="101", remaining=Decimal("0"), paid=Decimal("10")):
        return PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id=deal_id,
            deal_title=f"Сделка {deal_id}",
            paid_hours=paid,
            remaining_hours=remaining,
            is_active=True,
        )

    def test_binding_auto_creates_report_and_post_is_forbidden(self):
        binding = self.create_binding()
        report = WorkReport.objects.get(deal_binding=binding)
        self.assertEqual(report.portal_id, self.client_portal.id)
        self.assertEqual(report.status, WorkReport.Status.DRAFT)

        denied = self.agency_client.post("/api/reports/", {}, format="json")
        self.assertEqual(denied.status_code, 405)
        agency_list = self.agency_client.get(f"/api/reports/?portal={self.client_portal.id}")
        self.assertEqual(agency_list.status_code, 200)
        self.assertEqual(agency_list.data["results"][0]["deal_binding_id"], binding.id)
        client_list = self.client_client.get(f"/api/reports/?portal={self.client_portal.id}")
        self.assertEqual(client_list.data["count"], 0)

    def test_weekly_reports_include_current_and_empty_calendar_weeks(self):
        self.task.completed_at = timezone.now()
        self.task.save(update_fields=["completed_at", "updated_at"])
        response = self.client_client.get(
            f"/api/reports/weekly/?portal={self.client_portal.id}&weeks=3"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.data), 3)
        self.assertTrue(response.data[0]["is_current"])
        self.assertEqual(response.data[0]["tasks_count"], 1)
        self.assertEqual(response.data[0]["tasks"][0]["id"], self.task.id)
        self.assertEqual(response.data[1]["tasks_count"], 0)

    def test_activity_report_accepts_custom_period_and_groups_projects(self):
        now = timezone.now()
        self.task.completed_at = now
        self.task.save(update_fields=["completed_at", "updated_at"])
        TimeEntry.objects.create(
            task=self.task, started_at=now, duration_seconds=3600
        )
        other = make_project(self.client_portal, name="Сайт")
        extra = make_task(other, title="Форма", status="done", outcome="Отправлена")
        extra.completed_at = now
        extra.save(update_fields=["completed_at", "updated_at"])

        today = now.date().isoformat()
        response = self.client_client.get(
            f"/api/reports/activity/?portal={self.client_portal.id}&from={today}&to={today}"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["date_from"], today)
        self.assertEqual(response.data["date_to"], today)
        self.assertEqual(response.data["tasks_count"], 2)
        self.assertEqual(response.data["projects_count"], 2)
        self.assertEqual(response.data["total_tracked_seconds"], 3600)
        self.assertEqual({row["name"] for row in response.data["projects"]}, {"Модуль", "Сайт"})

        empty = self.client_client.get(
            f"/api/reports/activity/?portal={self.client_portal.id}&from=2020-01-01&to=2020-01-31"
        )
        self.assertEqual(empty.data["tasks_count"], 0)
        bad = self.client_client.get(
            f"/api/reports/activity/?portal={self.client_portal.id}&from=2026-08-20&to=2026-08-01"
        )
        self.assertEqual(bad.status_code, 400)

    def test_task_selection_reserves_reactivates_and_syncs_projects(self):
        binding = self.create_binding()
        report = binding.work_report
        selected = self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [self.task.id]},
            format="json",
        )
        self.assertEqual(selected.status_code, 200, selected.content)
        self.assertEqual(selected.data["selected_task_ids"], [self.task.id])
        self.assertEqual(selected.data["tasks_count"], 1)
        self.assertEqual(selected.data["project_ids"], [self.project.id])

        cleared = self.agency_client.post(
            f"/api/reports/{report.id}/tasks/", {"task_ids": []}, format="json"
        )
        self.assertEqual(cleared.status_code, 200)
        line = WorkReportLine.objects.get(report=report, task=self.task)
        self.assertFalse(line.is_reserved)

        readded = self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [self.task.id]},
            format="json",
        )
        self.assertEqual(readded.status_code, 200)
        line.refresh_from_db()
        self.assertTrue(line.is_reserved)

    def test_partial_unique_reservation_and_available_tasks(self):
        first = self.create_binding()
        first_report = first.work_report
        WorkReportLine.objects.create(report=first_report, task=self.task, is_reserved=True)
        first_report.projects.add(self.project)
        first.is_active = False
        first.save(update_fields=["is_active", "updated_at"])
        second = self.create_binding(deal_id="102")
        second_report = second.work_report

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkReportLine.objects.create(
                    report=second_report, task=self.task, is_reserved=True
                )
        payload = self.agency_client.get(
            f"/api/reports/{second_report.id}/available-tasks/"
        )
        row = payload.data["projects"][0]["tasks"][0]
        self.assertTrue(row["unavailable"])
        self.assertEqual(row["unavailable_report_id"], first_report.id)

    def test_available_and_selected_tasks_must_be_done(self):
        binding = self.create_binding()
        report = binding.work_report
        open_task = make_task(self.project, title="Ещё в работе", status="in_progress")
        listed = self.agency_client.get(f"/api/reports/{report.id}/available-tasks/")
        titles = [
            task["title"]
            for project in listed.data["projects"]
            for task in project["tasks"]
        ]
        self.assertIn(self.task.title, titles)
        self.assertNotIn(open_task.title, titles)

        rejected = self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [open_task.id]},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("завершённые", str(rejected.data["task_ids"]))

    def test_send_requires_tasks_and_exhausted_known_hours_then_freezes(self):
        binding = self.create_binding(remaining=Decimal("1"))
        report = binding.work_report
        no_tasks = self.agency_client.post(f"/api/reports/{report.id}/send/", {})
        self.assertEqual(no_tasks.status_code, 400)
        self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [self.task.id]},
            format="json",
        )
        hours_left = self.agency_client.post(f"/api/reports/{report.id}/send/", {})
        self.assertEqual(hours_left.status_code, 400)
        self.assertIn("не весь пакет", str(hours_left.data["detail"]))

        now = timezone.now()
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=10 * 3600,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        with patch("portals.deal_stage_move.schedule_deal_stage_move"):
            sent = self.agency_client.post(f"/api/reports/{report.id}/send/", {})
        self.assertEqual(sent.status_code, 200, sent.content)
        frozen = self.agency_client.post(
            f"/api/reports/{report.id}/tasks/", {"task_ids": []}, format="json"
        )
        self.assertEqual(frozen.status_code, 400)

    def test_report_lists_result_files_not_chat_comments(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from board.models import Attachment, Comment

        binding = self.create_binding()
        report = binding.work_report
        WorkReportLine.objects.create(report=report, task=self.task, is_reserved=True)
        report.projects.add(self.project)
        Comment.objects.create(
            task=self.task,
            author=self.agency_user,
            author_name="Мария",
            text="Макет собрали в Figma. Без вашего ОК дальше не двигаем.",
        )
        Attachment.objects.create(
            task=self.task,
            uploaded_by=self.agency_user,
            original_name="akt.pdf",
            file=SimpleUploadedFile("akt.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )
        detail = self.agency_client.get(f"/api/reports/{report.id}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        row = detail.data["projects_detail"][0]["tasks"][0]
        self.assertEqual([item["name"] for item in row["files"]], ["akt.pdf"])
        self.assertNotIn("Макет собрали", str(row.get("comment") or ""))
        self.assertNotIn("Макет собрали", " ".join(item["name"] for item in row["files"]))

    def test_metrics_use_selected_tasks_and_exact_billing_binding(self):
        binding = self.create_binding()
        report = binding.work_report
        other = make_task(self.project, title="Другая", status="in_progress")
        WorkReportLine.objects.create(report=report, task=self.task, is_reserved=True)
        report.projects.add(self.project)
        now = timezone.now()
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=3600,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        TimeEntry.objects.create(
            task=other,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=7200,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        detail = self.agency_client.get(f"/api/reports/{report.id}/")
        self.assertEqual(detail.data["total_tracked_seconds"], 3600)
        self.assertEqual(detail.data["selected_task_ids"], [self.task.id])
        self.assertEqual(detail.data["deal_id"], binding.deal_id)

    def test_billing_flow_snapshots_binding(self):
        binding = self.create_binding()
        now = timezone.now()
        entry = TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=60,
        )
        with patch("board.tasks.BitrixClient") as client_cls:
            client_cls.return_value.get_deal.return_value = {}
            client_cls.return_value.add_deal_timeline_comment.return_value = {"id": 1}
            from board.tasks import post_time_entry_to_deal

            result = post_time_entry_to_deal(entry.id)
        self.assertTrue(result["ok"])
        entry.refresh_from_db()
        self.assertEqual(entry.billed_deal_binding_id, binding.id)
        self.assertIsNotNone(entry.billed_to_deal_at)

    def test_completing_task_attaches_to_active_deal_report(self):
        from board.reports import attach_completed_task_to_report

        binding = self.create_binding()
        report = binding.work_report
        open_task = make_task(self.project, title="Закрыть и прикрепить", status="in_progress")
        self.assertIsNone(attach_completed_task_to_report(open_task))
        open_task.status = "done"
        open_task.outcome = "Сделано"
        open_task.save()
        attached = attach_completed_task_to_report(open_task)
        self.assertEqual(attached.id, report.id)
        line = WorkReportLine.objects.get(report=report, task=open_task)
        self.assertTrue(line.is_reserved)
        detail = self.agency_client.get(f"/api/reports/{report.id}/")
        self.assertEqual(detail.data["selected_task_ids"], [open_task.id])

    def test_retrieve_attaches_done_tasks_billed_to_deal(self):
        binding = self.create_binding()
        report = binding.work_report
        now = timezone.now()
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=1800,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        self.assertFalse(
            WorkReportLine.objects.filter(report=report, task=self.task, is_reserved=True).exists()
        )
        detail = self.agency_client.get(f"/api/reports/{report.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["selected_task_ids"], [self.task.id])

    def test_completed_task_follows_billed_deal_not_active_one(self):
        from board.reports import attach_completed_task_to_report

        billed = self.create_binding(deal_id="101")
        billed.is_active = False
        billed.save(update_fields=["is_active", "updated_at"])
        active = self.create_binding(deal_id="102")
        task = make_task(self.project, title="Списано в старую сделку", status="done", outcome="Готово")
        now = timezone.now()
        TimeEntry.objects.create(
            task=task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=600,
            billed_to_deal_at=now,
            billed_deal_binding=billed,
        )
        attach_completed_task_to_report(task)
        self.assertTrue(
            WorkReportLine.objects.filter(
                report=billed.work_report, task=task, is_reserved=True
            ).exists()
        )
        self.assertFalse(
            WorkReportLine.objects.filter(report=active.work_report, task=task, is_reserved=True).exists()
        )

    def test_send_parks_overage_and_next_deal_receives_it(self):
        from portals.models import PortalLink

        binding = self.create_binding(remaining=Decimal("0"), paid=Decimal("6"))
        report = binding.work_report
        self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [self.task.id]},
            format="json",
        )
        now = timezone.now()
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=6 * 3600 + 20 * 60,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        with patch("portals.deal_stage_move.schedule_deal_stage_move"):
            sent = self.agency_client.post(f"/api/reports/{report.id}/send/", {})
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertGreater(sent.data["total_tracked_seconds"], 6 * 3600)

        link = PortalLink.objects.get(
            agency_portal=self.agency, client_portal=self.client_portal
        )
        self.assertEqual(link.hours_overage, Decimal("0.33"))
        self.assertEqual(link.hours_overage_source_deal_id, binding.deal_id)

        binding.is_active = False
        binding.save(update_fields=["is_active", "updated_at"])
        nxt = self.create_binding(deal_id="202", remaining=Decimal("10"), paid=Decimal("10"))
        nxt.refresh_from_db()
        self.assertEqual(nxt.hours_overage_applied, Decimal("0.33"))
        self.assertEqual(nxt.remaining_hours, Decimal("9.67"))

        link.refresh_from_db()
        self.assertEqual(link.hours_overage, Decimal("0.00"))
        self.assertEqual(link.hours_overage_applied_to_deal_id, "202")

        nxt.save(update_fields=["updated_at"])
        nxt.refresh_from_db()
        self.assertEqual(nxt.remaining_hours, Decimal("9.67"))

        detail = self.agency_client.get(f"/api/reports/{nxt.work_report.id}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.data["carried_overage_seconds"], int(0.33 * 3600))
        self.assertEqual(detail.data["task_tracked_seconds"], 0)
        self.assertEqual(detail.data["total_tracked_seconds"], int(0.33 * 3600))
        self.assertEqual(detail.data["deal_hours"]["carried_overage_hours"], 0.33)

    def test_send_applies_overage_to_existing_inactive_deal(self):
        old = self.create_binding(deal_id="130", remaining=Decimal("7.83"), paid=Decimal("10"))
        old.is_active = False
        old.save(update_fields=["is_active", "updated_at"])
        binding = self.create_binding(deal_id="practice-close", remaining=Decimal("0"), paid=Decimal("6"))
        report = binding.work_report
        self.agency_client.post(
            f"/api/reports/{report.id}/tasks/",
            {"task_ids": [self.task.id]},
            format="json",
        )
        now = timezone.now()
        TimeEntry.objects.create(
            task=self.task,
            author=self.agency_user,
            started_at=now,
            ended_at=now,
            duration_seconds=6 * 3600 + 20 * 60,
            billed_to_deal_at=now,
            billed_deal_binding=binding,
        )
        with patch("portals.deal_stage_move.schedule_deal_stage_move"):
            sent = self.agency_client.post(f"/api/reports/{report.id}/send/", {})
        self.assertEqual(sent.status_code, 200, sent.content)
        old.refresh_from_db()
        self.assertEqual(old.hours_overage_applied, Decimal("0.33"))
        self.assertEqual(old.remaining_hours, Decimal("7.50"))
        next_report = self.agency_client.get(f"/api/reports/{old.work_report.id}/")
        self.assertEqual(next_report.data["carried_overage_seconds"], int(0.33 * 3600))
        self.assertEqual(next_report.data["deal_hours"]["carried_overage_hours"], 0.33)

    def test_sent_report_does_not_auto_attach_new_tasks(self):
        from board.reports import attach_completed_task_to_report

        binding = self.create_binding()
        report = binding.work_report
        report.status = WorkReport.Status.PENDING_CLIENT
        report.sent_at = timezone.now()
        report.save(update_fields=["status", "sent_at", "updated_at"])
        extra = make_task(self.project, title="После отправки", status="done", outcome="Готово")
        self.assertIsNone(attach_completed_task_to_report(extra))
        self.assertFalse(WorkReportLine.objects.filter(report=report, task=extra).exists())
