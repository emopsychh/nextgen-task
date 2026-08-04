"""Seed remaining hours from paid when remaining is empty or stuck at 0."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from board.models import TimeEntry
from board.tests.helpers import make_portal, make_project, make_task, make_user
from portals.deal_resolve import sync_deal_hours_meta
from portals.models import Portal, PortalDealBinding


@override_settings(
    BITRIX_DEAL_PAID_HOURS_FIELD="UF_PAID",
    BITRIX_DEAL_REMAINING_HOURS_FIELD="UF_REMAINING",
)
@patch("portals.deal_resolve.read_deal_stage_fields", return_value=("", "", ""))
class DealHoursSeedTests(TestCase):
    def setUp(self):
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.agency, bitrix_id="1")
        self.project = make_project(self.client_portal)
        PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id="160",
            is_active=True,
        )

    def test_seeds_when_remaining_null(self, _stages):
        client = MagicMock()
        deal = {"TITLE": "Pkg", "UF_PAID": "10", "UF_REMAINING": None}
        meta = sync_deal_hours_meta(client, "160", deal)
        client.update_deal.assert_called_once_with("160", {"UF_REMAINING": 10.0})
        self.assertEqual(meta["remaining_hours"], Decimal("10.00"))
        self.assertEqual(meta["paid_hours"], Decimal("10.00"))

    def test_seeds_zero_remaining_without_billings(self, _stages):
        client = MagicMock()
        deal = {"TITLE": "Pkg", "UF_PAID": "10", "UF_REMAINING": "0"}
        meta = sync_deal_hours_meta(client, "160", deal)
        client.update_deal.assert_called_once_with("160", {"UF_REMAINING": 10.0})
        self.assertEqual(meta["remaining_hours"], Decimal("10.00"))

    def test_does_not_seed_zero_remaining_after_billings(self, _stages):
        task = make_task(self.project, created_by=self.user)
        now = timezone.now()
        TimeEntry.objects.create(
            task=task,
            author=self.user,
            started_at=now,
            ended_at=now,
            duration_seconds=3600,
            billed_to_deal_at=now,
        )
        client = MagicMock()
        deal = {"TITLE": "Pkg", "UF_PAID": "10", "UF_REMAINING": "0"}
        meta = sync_deal_hours_meta(client, "160", deal)
        client.update_deal.assert_not_called()
        self.assertEqual(meta["remaining_hours"], Decimal("0.00"))
        self.assertEqual(meta["paid_hours"], Decimal("10.00"))
