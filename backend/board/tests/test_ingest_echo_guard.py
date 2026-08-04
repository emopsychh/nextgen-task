"""Inbound OnTaskAdd must not duplicate a PENDING outbound task."""

from __future__ import annotations

from django.test import TestCase

from board.models import Task
from board.project_sync import upsert_task_from_bitrix_subtask
from portals.models import Portal

from .helpers import make_portal, make_project, make_task, make_user


class IngestEchoGuardTests(TestCase):
    def setUp(self):
        self.client_portal = make_portal(role=Portal.Role.CLIENT)
        self.user = make_user(self.client_portal, bitrix_id="7")
        self.project = make_project(
            self.client_portal, bitrix_task_id="P1", bitrix_group_id="G1"
        )

    def test_upsert_claims_pending_outbound_task(self):
        local = make_task(
            self.project,
            title="Клиентская задача",
            created_by=self.user,
            sync_status=Task.SyncStatus.PENDING,
            agency_bitrix_task_id="",
        )
        bitrix_payload = {
            "id": "555",
            "title": "Клиентская задача",
            "description": "",
            "status": "2",
            "parentId": "P1",
        }
        task, created = upsert_task_from_bitrix_subtask(
            project=self.project, task_data=bitrix_payload, agency=True
        )
        self.assertFalse(created)
        self.assertEqual(task.id, local.id)
        task.refresh_from_db()
        self.assertEqual(task.agency_bitrix_task_id, "555")
        self.assertEqual(Task.objects.filter(project=self.project).count(), 1)

    def test_second_ingest_updates_same_row(self):
        make_task(
            self.project,
            title="Одна",
            created_by=self.user,
            sync_status=Task.SyncStatus.PENDING,
        )
        payload = {"id": "777", "title": "Одна", "status": "2", "parentId": "P1"}
        t1, c1 = upsert_task_from_bitrix_subtask(
            project=self.project, task_data=payload, agency=True
        )
        t2, c2 = upsert_task_from_bitrix_subtask(
            project=self.project, task_data=payload, agency=True
        )
        self.assertFalse(c1)
        self.assertFalse(c2)
        self.assertEqual(t1.id, t2.id)
        self.assertEqual(Task.objects.filter(project=self.project).count(), 1)
