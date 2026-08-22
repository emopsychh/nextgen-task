"""Due-date timezone: Moscow wall clock ↔ UTC ↔ Bitrix."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from board.due_dates import (
    AGENCY_DISPLAY_TZ,
    format_wall,
    parse_due_value,
    portal_zone,
    resolve_zone,
    wall_to_utc,
)
from board.status_sync import format_bitrix_deadline, parse_bitrix_deadline
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


class DueDateHelpersTests(TestCase):
    def test_moscow_18_stores_as_15_utc(self):
        tz = resolve_zone(AGENCY_DISPLAY_TZ)
        wall = datetime(2026, 5, 1, 18, 0, 0)
        utc = wall_to_utc(wall, tz)
        self.assertEqual(utc.hour, 15)
        self.assertEqual(utc.tzinfo, dt_timezone.utc)

    def test_bitrix_offset_preserves_instant(self):
        parsed = parse_bitrix_deadline({"deadline": "2026-05-01T18:00:00+03:00"})
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.astimezone(dt_timezone.utc).hour, 15)

    def test_naive_bitrix_uses_portal_tz(self):
        portal = make_portal(timezone="Europe/Moscow")
        parsed = parse_bitrix_deadline(
            {"deadline": "2026-05-01T18:00:00"}, portal=portal
        )
        self.assertEqual(parsed.astimezone(dt_timezone.utc).hour, 15)

    def test_format_bitrix_emits_portal_wall(self):
        portal = make_portal(timezone="Europe/Moscow")
        due = datetime(2026, 5, 1, 15, 0, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(
            format_bitrix_deadline(due, portal=portal),
            "2026-05-01T18:00:00",
        )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class DueDateApiTimezoneTests(TestCase):
    def setUp(self):
        self.agency = make_portal(
            Portal.Role.AGENCY, member_id="agency-tz", name="Agency"
        )
        self.client_portal = make_portal(
            Portal.Role.CLIENT,
            member_id="client-tz",
            name="Client",
            timezone="Europe/Moscow",
        )
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(
            self.agency, bitrix_id="atz1", name="Agency", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Проект")
        self.task = make_task(self.project, title="Срок", created_by=self.agency_user)
        self.api = APIClient()
        tokens = issue_tokens(self.agency, self.agency_user)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def test_agency_sets_18_moscow_round_trip(self):
        from unittest.mock import patch

        with patch("board.views.publish_task_event"), patch(
            "board.views.enqueue_bitrix_sync"
        ), patch("board.views.append_task_change_events"):
            res = self.api.patch(
                f"/api/tasks/{self.task.id}/",
                {"due_date": "2026-05-01T18:00:00"},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(str(res.data["due_date"]).endswith("Z"))
        self.assertEqual(res.data["due_date"], "2026-05-01T15:00:00Z")
        self.assertEqual(res.data["due_timezone"], "Europe/Moscow")

        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date.astimezone(dt_timezone.utc).hour, 15)

        # Format for Bitrix client portal must show 18 again
        self.assertEqual(
            format_wall(self.task.due_date, portal_zone(self.client_portal)),
            "2026-05-01T18:00:00",
        )
