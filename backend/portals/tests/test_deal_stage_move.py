"""Deal stage moves driven by work-report send/accept."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from board.models import WorkReport
from board.reports import accept_report, send_to_client
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.deal_stage_move import (
    STAGE_ACT_SIGNING,
    STAGE_REPORT_REVIEW,
    move_client_deal_stage,
    resolve_stage_id,
)
from portals.models import Portal, PortalDealBinding


class DealStageMoveHelpersTests(TestCase):
    def test_resolve_prefers_env(self):
        client = MagicMock()
        with override_settings(BITRIX_DEAL_STAGE_REPORT_REVIEW="C5:UC_REVIEW"):
            self.assertEqual(
                resolve_stage_id(client, "5", STAGE_REPORT_REVIEW), "C5:UC_REVIEW"
            )
        client.call.assert_not_called()

    def test_resolve_by_name(self):
        client = MagicMock()
        client.call.return_value = [
            {"STATUS_ID": "C5:NEW", "NAME": "Новая"},
            {"STATUS_ID": "C5:UC_REP", "NAME": "Согласование отчета"},
            {"STATUS_ID": "C5:UC_ACT", "NAME": "Подписание акта"},
        ]
        with override_settings(
            BITRIX_DEAL_STAGE_REPORT_REVIEW="",
            BITRIX_DEAL_STAGE_ACT_SIGNING="",
        ):
            self.assertEqual(
                resolve_stage_id(client, "5", STAGE_REPORT_REVIEW), "C5:UC_REP"
            )
            self.assertEqual(
                resolve_stage_id(client, "5", STAGE_ACT_SIGNING), "C5:UC_ACT"
            )


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    BITRIX_DEAL_STAGE_REPORT_REVIEW="C5:UC_REVIEW",
    BITRIX_DEAL_STAGE_ACT_SIGNING="C5:UC_ACT",
    BITRIX_ACCOMPANIMENT_CATEGORY_ID="5",
)
class DealStageMoveOnReportTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="ag-stage", name="Agency")
        self.client_portal = make_portal(
            Portal.Role.CLIENT, member_id="cl-stage", name="Client"
        )
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(self.agency, bitrix_id="a9")
        self.client_user = make_user(self.client_portal, bitrix_id="c9")
        self.project = make_project(self.client_portal)
        make_task(self.project, title="T", status="done", outcome="ok")
        self.binding = PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id="9001",
            deal_title="Accompany",
            category_id="5",
            stage_id="C5:NEW",
            is_active=True,
        )

    def test_move_updates_deal_and_binding(self):
        client = MagicMock()
        client.get_deal.return_value = {
            "ID": "9001",
            "STAGE_ID": "C5:NEW",
            "CATEGORY_ID": "5",
        }
        client.update_deal.return_value = {}
        with patch("portals.deal_stage_move.BitrixClient", return_value=client):
            with patch(
                "portals.deal_stage_move.read_deal_stage_fields",
                return_value=("C5:NEW", "5", ""),
            ):
                res = move_client_deal_stage(self.client_portal.id, STAGE_REPORT_REVIEW)
        self.assertTrue(res["ok"])
        client.update_deal.assert_called_once_with("9001", {"STAGE_ID": "C5:UC_REVIEW"})
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.stage_id, "C5:UC_REVIEW")

    def test_skip_won_deal(self):
        client = MagicMock()
        client.get_deal.return_value = {
            "ID": "9001",
            "STAGE_ID": "C5:WON",
            "CATEGORY_ID": "5",
        }
        with patch("portals.deal_stage_move.BitrixClient", return_value=client):
            with patch(
                "portals.deal_stage_move.read_deal_stage_fields",
                return_value=("C5:WON", "5", "S"),
            ):
                res = move_client_deal_stage(self.client_portal.id, STAGE_ACT_SIGNING)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "deal_closed")
        client.update_deal.assert_not_called()

    @patch("portals.deal_stage_move.move_client_deal_stage")
    def test_send_and_accept_schedule_moves(self, move_mock):
        move_mock.return_value = {"ok": True}
        report = WorkReport.objects.create(
            portal=self.client_portal,
            project=self.project,
            status=WorkReport.Status.DRAFT,
            created_by=self.agency_user,
        )
        report.projects.set([self.project])

        send_to_client(report, self.agency_user)
        move_mock.assert_called_with(self.client_portal.id, STAGE_REPORT_REVIEW)

        report.refresh_from_db()
        accept_report(report, self.client_user)
        move_mock.assert_called_with(self.client_portal.id, STAGE_ACT_SIGNING)
