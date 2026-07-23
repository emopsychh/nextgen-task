"""Split-brain status resolution: the copy whose STATUS changed most recently wins.

Regression for a self-pause / ping-pong bug: when a task is started on one Bitrix
portal while the other copy still shows "todo", the app must NOT resolve to todo
and push a pause back.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from board import status_sync
from board.models import Task
from portals.models import Portal

from .helpers import make_portal, make_project, make_task, make_user


def _in_progress(status_changed: str) -> dict:
    return {
        "status": "3",
        "action": {"pause": True, "start": False},
        "statusChangedDate": status_changed,
    }


def _todo(status_changed: str) -> dict:
    return {
        "status": "2",
        "action": {"pause": False, "start": True},
        "statusChangedDate": status_changed,
    }


class SplitBrainResolutionTests(TestCase):
    def setUp(self):
        self.portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.portal, bitrix_id="7")
        self.project = make_project(self.portal)
        self.task = make_task(self.project, created_by=self.user, status=Task.Status.TODO)
        self.agency = make_portal(role=Portal.Role.AGENCY, domain="agency.bitrix24.ru")

    def _resolve(self, agency_data: dict, client_data: dict) -> str | None:
        sources = [(self.agency, "108"), (self.portal, "55")]

        def _client_factory(portal):
            c = MagicMock()
            c.get_task.return_value = agency_data if portal is self.agency else client_data
            return c

        with patch.object(status_sync, "resolve_all_bitrix_task_sources", return_value=sources), \
             patch.object(status_sync, "BitrixClient", side_effect=_client_factory), \
             patch("board.comment_sync.latest_bitrix_work_activity", return_value=None):
            status, _data, _portal, _bid = status_sync.resolve_inbound_status_from_sources(
                self.task
            )
        return status

    def test_fresh_start_beats_stale_todo(self):
        # Agency just started (newer); client still todo (older) → in_progress wins.
        status = self._resolve(
            agency_data=_in_progress("2026-07-23T16:30:00+03:00"),
            client_data=_todo("2026-07-23T16:00:00+03:00"),
        )
        self.assertEqual(status, "in_progress")

    def test_fresh_pause_beats_stale_in_progress(self):
        # Agency just paused (newer); client still in_progress (older) → todo wins.
        status = self._resolve(
            agency_data=_todo("2026-07-23T16:30:00+03:00"),
            client_data=_in_progress("2026-07-23T16:00:00+03:00"),
        )
        self.assertEqual(status, "todo")

    def test_tie_prefers_pause(self):
        # Equal status-change time → keep the safe "pause wins" default.
        status = self._resolve(
            agency_data=_in_progress("2026-07-23T16:00:00+03:00"),
            client_data=_todo("2026-07-23T16:00:00+03:00"),
        )
        self.assertEqual(status, "todo")
