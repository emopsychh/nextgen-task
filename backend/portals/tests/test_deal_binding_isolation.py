from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from board.tests.helpers import make_link, make_portal
from portals.deal_resolve import (
    deactivate_bindings_for_deal,
    portal_link_matches,
    resolve_or_refresh_binding,
)
from portals.models import PortalDealBinding


class PortalLinkMatchTests(TestCase):
    def test_exact_host_only(self):
        self.assertTrue(portal_link_matches("https://client.bitrix24.ru", "client.bitrix24.ru"))
        self.assertTrue(portal_link_matches("client.bitrix24.ru", "client.bitrix24.ru"))
        self.assertFalse(portal_link_matches("https://bitrix24.ru", "client.bitrix24.ru"))
        self.assertFalse(portal_link_matches("other.bitrix24.ru", "client.bitrix24.ru"))
        self.assertFalse(portal_link_matches("client", "client.bitrix24.ru"))


@override_settings(BITRIX_DEAL_PORTAL_LINK_FIELD="UF_CRM_PORTAL")
class DealBindingIsolationTests(TestCase):
    def setUp(self):
        self.agency = make_portal(role="agency", domain="agency.bitrix24.ru", token="a-tok")
        self.test = make_portal(role="client", domain="test.bitrix24.ru", token="t-tok")
        self.newbie = make_portal(role="client", domain="newbie.bitrix24.ru", token="n-tok")
        make_link(self.agency, self.test)
        make_link(self.agency, self.newbie)

    def test_claim_deal_deactivates_other_client(self):
        PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.test,
            deal_id="158",
            deal_title="Test deal",
            is_active=True,
            remaining_hours=21,
            paid_hours=10,
        )
        n = deactivate_bindings_for_deal(
            agency_portal=self.agency,
            deal_id="158",
            except_client_portal=self.newbie,
        )
        self.assertEqual(n, 1)
        self.assertFalse(
            PortalDealBinding.objects.get(client_portal=self.test, deal_id="158").is_active
        )

    @patch("portals.deal_resolve.BitrixClient")
    def test_resolve_clears_stale_binding_without_matching_link(self, client_cls):
        PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.newbie,
            deal_id="158",
            deal_title="Someone else's deal",
            is_active=True,
            remaining_hours=21,
            paid_hours=10,
        )
        bx = MagicMock()
        client_cls.return_value = bx
        # No open deal for newbie; CRM deal 158 points at test portal
        bx.call.return_value = []
        bx.get_deal.return_value = {
            "ID": "158",
            "TITLE": "Новая сделочка",
            "UF_CRM_PORTAL": "https://test.bitrix24.ru",
        }

        with self.assertRaises(Exception) as ctx:
            resolve_or_refresh_binding(
                agency_portal=self.agency,
                client_portal=self.newbie,
            )
        self.assertIn("Не найдена открытая сделка", str(ctx.exception))
        binding = PortalDealBinding.objects.get(client_portal=self.newbie, deal_id="158")
        self.assertFalse(binding.is_active)

    @patch("portals.deal_resolve.BitrixClient")
    def test_resolve_claims_deal_exclusively(self, client_cls):
        PortalDealBinding.objects.create(
            agency_portal=self.agency,
            client_portal=self.test,
            deal_id="158",
            is_active=True,
            remaining_hours=21,
            paid_hours=10,
        )
        bx = MagicMock()
        client_cls.return_value = bx
        deal = {
            "ID": "158",
            "TITLE": "Новая сделочка",
            "CATEGORY_ID": "1",
            "UF_CRM_PORTAL": "https://newbie.bitrix24.ru",
            "CLOSED": "N",
        }

        def call(method, params=None):
            if method == "crm.deal.list":
                return [deal]
            return {}

        bx.call.side_effect = call
        bx.get_deal.return_value = deal
        bx.get_company.return_value = {}

        with patch("portals.deal_resolve.sync_deal_hours_meta") as sync_meta:
            sync_meta.return_value = {
                "deal_title": "Новая сделочка",
                "category_id": "1",
                "stage_id": "",
                "stage_semantic": "",
                "paid_hours": 10,
                "remaining_hours": 21,
            }
            with patch("portals.deal_resolve.apply_hours_credit_to_new_deal", return_value=None):
                with patch("portals.deal_resolve.cache_company_and_group_on_link"):
                    binding = resolve_or_refresh_binding(
                        agency_portal=self.agency,
                        client_portal=self.newbie,
                    )

        self.assertIsNotNone(binding)
        self.assertEqual(binding.deal_id, "158")
        self.assertTrue(binding.is_active)
        self.assertFalse(
            PortalDealBinding.objects.get(client_portal=self.test, deal_id="158").is_active
        )
