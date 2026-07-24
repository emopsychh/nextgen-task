"""Support ticket API tests."""

from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from board.models import SupportTicket, SupportTicketMessage
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class SupportTicketApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-t", name="Agency")
        self.client_portal = make_portal(Portal.Role.CLIENT, member_id="client-t", name="Client")
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(self.agency, bitrix_id="a1", name="Agency", last_name="User")
        self.client_user = make_user(
            self.client_portal, bitrix_id="c1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Модуль")
        self.task = make_task(self.project, title="Сверстать")
        self.agency_client = APIClient()
        self.client_client = APIClient()
        agency_tokens = issue_tokens(self.agency, self.agency_user)
        client_tokens = issue_tokens(self.client_portal, self.client_user)
        self.agency_client.credentials(HTTP_AUTHORIZATION=f"Bearer {agency_tokens['access']}")
        self.client_client.credentials(HTTP_AUTHORIZATION=f"Bearer {client_tokens['access']}")

    def test_client_create_message_agency_close_reopen(self):
        create = self.client_client.post(
            "/api/tickets/",
            {
                "portal": self.client_portal.id,
                "subject": "Не работает кнопка",
                "body": "На главной не кликается CTA",
                "project": self.project.id,
                "task": self.task.id,
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.content)
        ticket_id = create.data["id"]
        self.assertEqual(create.data["status"], "open")
        self.assertEqual(create.data["subject"], "Не работает кнопка")
        self.assertEqual(create.data["body"], "На главной не кликается CTA")
        self.assertEqual(create.data["project"], self.project.id)
        self.assertEqual(create.data["task"], self.task.id)
        self.assertEqual(len(create.data["messages"]), 1)
        self.assertEqual(create.data["messages"][0]["text"], "На главной не кликается CTA")
        self.assertEqual(create.data["messages"][0]["author"], self.client_user.id)

        # Agency cannot create tickets
        agency_create = self.agency_client.post(
            "/api/tickets/",
            {
                "portal": self.client_portal.id,
                "subject": "Agency ticket",
                "body": "Should fail",
            },
            format="json",
        )
        self.assertEqual(agency_create.status_code, 403)

        # Agency can list open tickets for client portal
        listing = self.agency_client.get(
            f"/api/tickets/?portal={self.client_portal.id}&bucket=open"
        )
        self.assertEqual(listing.status_code, 200)
        rows = listing.data["results"] if isinstance(listing.data, dict) else listing.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], ticket_id)

        # Reply from agency
        msg = self.agency_client.post(
            f"/api/tickets/{ticket_id}/messages/",
            {"text": "Смотрим, ответим сегодня"},
            format="json",
        )
        self.assertEqual(msg.status_code, 201, msg.content)
        self.assertEqual(msg.data["text"], "Смотрим, ответим сегодня")

        # Client reply
        msg2 = self.client_client.post(
            f"/api/tickets/{ticket_id}/messages/",
            {"text": "Спасибо"},
            format="json",
        )
        self.assertEqual(msg2.status_code, 201, msg2.content)

        detail = self.client_client.get(f"/api/tickets/{ticket_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data["messages"]), 3)

        # Client cannot close
        bad_close = self.client_client.post(f"/api/tickets/{ticket_id}/close/", {}, format="json")
        self.assertEqual(bad_close.status_code, 403)

        closed = self.agency_client.post(f"/api/tickets/{ticket_id}/close/", {}, format="json")
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.data["status"], "closed")
        self.assertIsNotNone(closed.data["closed_at"])

        # No messages on closed ticket
        blocked = self.agency_client.post(
            f"/api/tickets/{ticket_id}/messages/",
            {"text": "late"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400)

        archived = self.agency_client.get(
            f"/api/tickets/?portal={self.client_portal.id}&bucket=closed"
        )
        rows = archived.data["results"] if isinstance(archived.data, dict) else archived.data
        self.assertEqual(len(rows), 1)

        reopened = self.agency_client.post(
            f"/api/tickets/{ticket_id}/reopen/", {}, format="json"
        )
        self.assertEqual(reopened.status_code, 200, reopened.content)
        self.assertEqual(reopened.data["status"], "open")
        self.assertIsNone(reopened.data["closed_at"])

        self.assertEqual(SupportTicket.objects.filter(status="open").count(), 1)
        self.assertEqual(SupportTicketMessage.objects.filter(ticket_id=ticket_id).count(), 3)

    def test_bucket_open_empty_for_other_portal(self):
        other = make_portal(Portal.Role.CLIENT, member_id="other-t", name="Other")
        make_user(other, bitrix_id="o1", name="Other")
        create = self.client_client.post(
            "/api/tickets/",
            {
                "portal": self.client_portal.id,
                "subject": "Тема",
                "body": "Текст",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)

        # Agency without link cannot access other portal — and own client list is scoped
        listing = self.agency_client.get(f"/api/tickets/?portal={other.id}&bucket=open")
        self.assertEqual(listing.status_code, 403)
