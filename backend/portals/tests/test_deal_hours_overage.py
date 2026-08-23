"""Package overspend rolls from a closed report onto the next deal."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from board.tests.helpers import make_link, make_portal
from portals.deal_hours_overage import (
    apply_hours_overage_to_binding,
    apply_hours_overage_to_new_deal,
    capture_hours_overage,
)
from portals.models import Portal, PortalDealBinding


class DealHoursOverageTests(TestCase):
    def setUp(self):
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.link = make_link(self.agency, self.client_portal)
        self.source = PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id="close-1",
            deal_title="Пакет августа",
            paid_hours=Decimal("6"),
            remaining_hours=Decimal("0"),
            is_active=True,
        )

    def test_capture_is_idempotent_for_the_same_deal(self):
        self.assertTrue(
            capture_hours_overage(link=self.link, binding=self.source, overage=Decimal("0.33"))
        )
        self.link.refresh_from_db()
        self.assertEqual(self.link.hours_overage, Decimal("0.33"))
        self.assertFalse(
            capture_hours_overage(link=self.link, binding=self.source, overage=Decimal("0.50"))
        )
        self.link.refresh_from_db()
        self.assertEqual(self.link.hours_overage, Decimal("0.33"))

    def test_does_not_apply_overage_to_the_source_deal(self):
        capture_hours_overage(link=self.link, binding=self.source, overage=Decimal("0.33"))
        self.assertIsNone(apply_hours_overage_to_binding(self.source))
        self.source.refresh_from_db()
        self.assertEqual(self.source.remaining_hours, Decimal("0"))
        self.assertEqual(self.source.hours_overage_applied, Decimal("0"))

    def test_applies_overage_to_existing_inactive_sibling(self):
        from portals.deal_hours_overage import apply_pending_hours_overage

        sibling = PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id="130",
            deal_title="Сопровождение — август",
            paid_hours=Decimal("10"),
            remaining_hours=Decimal("7.83"),
            is_active=False,
        )
        capture_hours_overage(link=self.link, binding=self.source, overage=Decimal("0.33"))
        applied = apply_pending_hours_overage(self.link)
        self.assertEqual(applied, Decimal("0.33"))
        sibling.refresh_from_db()
        self.assertEqual(sibling.hours_overage_applied, Decimal("0.33"))
        self.assertEqual(sibling.remaining_hours, Decimal("7.50"))
        self.link.refresh_from_db()
        self.assertEqual(self.link.hours_overage, Decimal("0.00"))
        self.assertEqual(self.link.hours_overage_applied_to_deal_id, "130")

    def test_applies_overage_to_the_next_active_deal(self):
        capture_hours_overage(link=self.link, binding=self.source, overage=Decimal("0.33"))
        self.source.is_active = False
        self.source.save(update_fields=["is_active", "updated_at"])
        nxt = PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.client_portal,
            deal_id="next-1",
            deal_title="Пакет сентября",
            paid_hours=Decimal("10"),
            remaining_hours=Decimal("10"),
            is_active=True,
        )
        nxt.refresh_from_db()
        self.assertEqual(nxt.hours_overage_applied, Decimal("0.33"))
        self.assertEqual(nxt.remaining_hours, Decimal("9.67"))
        self.link.refresh_from_db()
        self.assertEqual(self.link.hours_overage, Decimal("0.00"))
        self.assertEqual(self.link.hours_overage_applied_to_deal_id, "next-1")

        nxt.remaining_hours = Decimal("10")
        nxt.save(update_fields=["remaining_hours", "updated_at"])
        nxt.refresh_from_db()
        self.assertEqual(nxt.remaining_hours, Decimal("9.67"))


@override_settings(
    BITRIX_DEAL_PAID_HOURS_FIELD="UF_PAID",
    BITRIX_DEAL_REMAINING_HOURS_FIELD="UF_REMAINING",
)
class DealHoursOverageCrmTests(TestCase):
    def setUp(self):
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.link = make_link(self.agency, self.client_portal)
        self.link.hours_overage = Decimal("0.33")
        self.link.hours_overage_source_deal_id = "close-1"
        self.link.hours_overage_source_title = "Пакет августа"
        self.link.save()

    def test_crm_apply_subtracts_remaining(self):
        client = MagicMock()
        client.get_deal.return_value = {"UF_PAID": "10", "UF_REMAINING": "10"}
        remaining = apply_hours_overage_to_new_deal(
            link=self.link,
            client=client,
            new_deal_id="next-1",
            current_remaining=Decimal("10"),
        )
        self.assertEqual(remaining, Decimal("9.67"))
        client.update_deal.assert_called_once_with("next-1", {"UF_REMAINING": 9.67})
        self.link.refresh_from_db()
        self.assertEqual(self.link.hours_overage, Decimal("0.00"))
        self.assertEqual(self.link.hours_overage_applied_to_deal_id, "next-1")
        self.assertEqual(self.link.hours_overage_last_amount, Decimal("0.33"))
