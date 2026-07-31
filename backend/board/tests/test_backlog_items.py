"""Agency client backlog API tests."""

from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from board.models import BacklogItem
from board.tests.helpers import make_link, make_portal, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class BacklogItemApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-bl", name="Agency")
        self.client_a = make_portal(Portal.Role.CLIENT, member_id="client-a-bl", name="Client A")
        self.client_b = make_portal(Portal.Role.CLIENT, member_id="client-b-bl", name="Client B")
        make_link(self.agency, self.client_a)
        self.agency_user = make_user(self.agency, bitrix_id="a1", name="Agency", last_name="User")
        self.client_user = make_user(
            self.client_a, bitrix_id="c1", name="Client", last_name="User"
        )
        self.agency_client = APIClient()
        self.client_client = APIClient()
        agency_tokens = issue_tokens(self.agency, self.agency_user)
        client_tokens = issue_tokens(self.client_a, self.client_user)
        self.agency_client.credentials(HTTP_AUTHORIZATION=f"Bearer {agency_tokens['access']}")
        self.client_client.credentials(HTTP_AUTHORIZATION=f"Bearer {client_tokens['access']}")

    def test_client_forbidden(self):
        r = self.client_client.get(f"/api/backlog-items/?portal={self.client_a.id}")
        self.assertEqual(r.status_code, 403)

        r = self.client_client.post(
            "/api/backlog-items/",
            {"portal": self.client_a.id, "title": "X", "notes": ""},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_agency_crud_scoped_to_linked_portal(self):
        create = self.agency_client.post(
            "/api/backlog-items/",
            {"portal": self.client_a.id, "title": "Идея", "notes": "черновик"},
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.content)
        item_id = create.data["id"]
        self.assertEqual(create.data["title"], "Идея")
        self.assertEqual(create.data["notes"], "черновик")
        self.assertEqual(create.data["portal"], self.client_a.id)
        self.assertEqual(create.data["created_by"], self.agency_user.id)

        listed = self.agency_client.get(f"/api/backlog-items/?portal={self.client_a.id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(listed.data[0]["id"], item_id)

        # Unlinked client B — no access
        denied = self.agency_client.get(f"/api/backlog-items/?portal={self.client_b.id}")
        self.assertEqual(denied.status_code, 403)
        create_b = self.agency_client.post(
            "/api/backlog-items/",
            {"portal": self.client_b.id, "title": "Чужой", "notes": ""},
            format="json",
        )
        self.assertEqual(create_b.status_code, 403)

        patched = self.agency_client.patch(
            f"/api/backlog-items/{item_id}/",
            {"title": "Идея 2", "notes": "обновлено"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.data["title"], "Идея 2")
        self.assertEqual(patched.data["notes"], "обновлено")

        deleted = self.agency_client.delete(f"/api/backlog-items/{item_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(BacklogItem.objects.filter(pk=item_id).exists())

    def test_list_requires_portal(self):
        r = self.agency_client.get("/api/backlog-items/")
        self.assertEqual(r.status_code, 400)
