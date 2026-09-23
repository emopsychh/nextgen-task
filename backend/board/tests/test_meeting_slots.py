from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from board.meeting_slots import has_meeting_conflict
from board.models import ProjectMeeting
from board.serializers import ProjectMeetingSerializer
from board.tests.helpers import make_portal, make_project


class MeetingSlotTests(TestCase):
    def setUp(self):
        self.portal = make_portal(name="Client")
        self.project = make_project(self.portal)
        self.starts_at = (timezone.now() + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        ProjectMeeting.objects.create(
            project=self.project,
            title="Existing meeting",
            scheduled_at=self.starts_at,
            duration_minutes=30,
        )

    def test_overlap_is_busy_but_adjacent_slot_is_free(self):
        self.assertTrue(
            has_meeting_conflict(
                self.portal.id,
                self.starts_at + timedelta(minutes=15),
                duration_minutes=30,
            )
        )
        self.assertFalse(
            has_meeting_conflict(
                self.portal.id,
                self.starts_at + timedelta(minutes=30),
                duration_minutes=30,
            )
        )

    def test_serializer_rejects_occupied_slot(self):
        serializer = ProjectMeetingSerializer(
            data={
                "project": self.project.id,
                "title": "New meeting",
                "scheduled_at": self.starts_at + timedelta(minutes=15),
                "duration_minutes": 30,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("scheduled_at", serializer.errors)

    def test_cancelled_meeting_frees_the_slot(self):
        meeting = ProjectMeeting.objects.get()
        meeting.cancelled_at = timezone.now()
        meeting.save(update_fields=["cancelled_at"])

        self.assertFalse(
            has_meeting_conflict(
                self.portal.id,
                self.starts_at,
                duration_minutes=30,
            )
        )

    def test_agency_can_save_outcome_after_the_meeting(self):
        meeting = ProjectMeeting.objects.get()
        meeting.scheduled_at = timezone.now() - timedelta(hours=2)
        meeting.save(update_fields=["scheduled_at"])
        serializer = ProjectMeetingSerializer(
            meeting,
            data={"outcome": "Итог https://example.com/doc"},
            partial=True,
            context={"request": SimpleNamespace(user=SimpleNamespace(is_agency=True))},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["outcome"], "Итог https://example.com/doc")

    def test_client_cannot_write_meeting_outcome(self):
        meeting = ProjectMeeting.objects.get()
        serializer = ProjectMeetingSerializer(
            meeting,
            data={"outcome": "Свой итог"},
            partial=True,
            context={"request": SimpleNamespace(user=SimpleNamespace(is_agency=False))},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("outcome", serializer.errors)
