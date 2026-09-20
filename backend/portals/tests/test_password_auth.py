from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from portals.models import BitrixUser, Portal


@override_settings(DEV_AUTH_BYPASS=False)
class PasswordAuthTests(TestCase):
    def setUp(self):
        self.agency = Portal.objects.create(
            member_id="agency-pw",
            domain="agency.example",
            role=Portal.Role.AGENCY,
            name="Agency",
        )
        self.client_portal = Portal.objects.create(
            member_id="client-pw",
            domain="client.example",
            role=Portal.Role.CLIENT,
            name="Client",
        )
        self.user = BitrixUser.objects.create(
            portal=self.agency,
            bitrix_id="local-alice",
            username="alice",
            name="Alice",
            last_name="Agency",
            is_admin=True,
        )
        self.user.set_password("secret-pass")
        self.user.save(update_fields=["password"])
        self.api = APIClient()

    def test_agency_password_login(self):
        resp = self.api.post(
            "/api/auth/login/",
            {"username": "alice", "password": "secret-pass"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertEqual(resp.data["portal"]["role"], "agency")
        self.assertEqual(resp.data["user"]["id"], self.user.id)

    def test_wrong_password(self):
        resp = self.api.post(
            "/api/auth/login/",
            {"username": "alice", "password": "nope"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_client_password_login(self):
        client_user = BitrixUser.objects.create(
            portal=self.client_portal,
            bitrix_id="local-bob",
            username="bob",
            name="Bob",
        )
        client_user.set_password("secret-pass")
        client_user.save(update_fields=["password"])
        resp = self.api.post(
            "/api/auth/login/",
            {"username": "bob", "password": "secret-pass"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["portal"]["role"], "client")
        self.assertEqual(resp.data["user"]["id"], client_user.id)
