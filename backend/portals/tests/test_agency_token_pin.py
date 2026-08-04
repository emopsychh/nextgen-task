"""Agency portal must keep a stable Bitrix service token across employee logins."""

from __future__ import annotations

from django.test import TestCase, override_settings

from portals.models import Portal
from portals.serializers import upsert_portal_from_auth


@override_settings(BITRIX_AGENCY_MEMBER_IDS="agency-member")
class AgencyTokenPinTests(TestCase):
    def test_employee_login_does_not_steal_agency_token(self):
        portal = Portal.objects.create(
            member_id="agency-member",
            domain="agency.bitrix24.ru",
            role=Portal.Role.AGENCY,
            access_token="installer-access",
            refresh_token="installer-refresh",
        )
        upsert_portal_from_auth(
            {
                "member_id": "agency-member",
                "domain": "agency.bitrix24.ru",
                "access_token": "nikita-access",
                "refresh_token": "nikita-refresh",
                "expires_in": 3600,
            }
        )
        portal.refresh_from_db()
        self.assertEqual(portal.access_token, "installer-access")
        self.assertEqual(portal.refresh_token, "installer-refresh")

    def test_install_replaces_agency_token(self):
        Portal.objects.create(
            member_id="agency-member",
            domain="agency.bitrix24.ru",
            role=Portal.Role.AGENCY,
            access_token="old-access",
            refresh_token="old-refresh",
        )
        upsert_portal_from_auth(
            {
                "member_id": "agency-member",
                "domain": "agency.bitrix24.ru",
                "access_token": "alexander-access",
                "refresh_token": "alexander-refresh",
                "expires_in": 3600,
            },
            replace_tokens=True,
        )
        portal = Portal.objects.get(member_id="agency-member")
        self.assertEqual(portal.access_token, "alexander-access")
        self.assertEqual(portal.refresh_token, "alexander-refresh")
