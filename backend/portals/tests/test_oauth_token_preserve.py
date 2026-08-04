"""Portal OAuth must stay on the installer — not churn on every employee login."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from portals.models import Portal
from portals.serializers import upsert_portal_from_auth


class OAuthTokenPreserveTests(TestCase):
    def test_employee_login_does_not_overwrite_installer_tokens(self):
        portal = Portal.objects.create(
            member_id="m1",
            domain="agency.bitrix24.ru",
            role=Portal.Role.AGENCY,
            access_token="installer-access",
            refresh_token="installer-refresh",
            expires_at=timezone.now(),
        )
        upsert_portal_from_auth(
            {
                "member_id": "m1",
                "domain": "agency.bitrix24.ru",
                "access_token": "employee-access",
                "refresh_token": "employee-refresh",
                "expires_in": 3600,
            },
            update_oauth_tokens=False,
        )
        portal.refresh_from_db()
        self.assertEqual(portal.access_token, "installer-access")
        self.assertEqual(portal.refresh_token, "installer-refresh")

    def test_bootstraps_tokens_when_portal_has_none(self):
        upsert_portal_from_auth(
            {
                "member_id": "m2",
                "domain": "fresh.bitrix24.ru",
                "access_token": "first-access",
                "refresh_token": "first-refresh",
                "expires_in": 3600,
            },
            update_oauth_tokens=False,
        )
        portal = Portal.objects.get(member_id="m2")
        self.assertEqual(portal.access_token, "first-access")
        self.assertEqual(portal.refresh_token, "first-refresh")

    def test_install_updates_tokens(self):
        Portal.objects.create(
            member_id="m3",
            domain="agency.bitrix24.ru",
            role=Portal.Role.AGENCY,
            access_token="old-access",
            refresh_token="old-refresh",
        )
        upsert_portal_from_auth(
            {
                "member_id": "m3",
                "domain": "agency.bitrix24.ru",
                "access_token": "new-installer-access",
                "refresh_token": "new-installer-refresh",
                "expires_in": 3600,
            },
            update_oauth_tokens=True,
        )
        portal = Portal.objects.get(member_id="m3")
        self.assertEqual(portal.access_token, "new-installer-access")
        self.assertEqual(portal.refresh_token, "new-installer-refresh")

    def test_empty_refresh_does_not_wipe_existing(self):
        Portal.objects.create(
            member_id="m4",
            domain="agency.bitrix24.ru",
            role=Portal.Role.AGENCY,
            access_token="keep-access",
            refresh_token="keep-refresh",
        )
        upsert_portal_from_auth(
            {
                "member_id": "m4",
                "domain": "agency.bitrix24.ru",
                "access_token": "reinstall-access",
                "refresh_token": "",
                "expires_in": 3600,
            },
            update_oauth_tokens=True,
        )
        portal = Portal.objects.get(member_id="m4")
        self.assertEqual(portal.access_token, "reinstall-access")
        self.assertEqual(portal.refresh_token, "keep-refresh")
