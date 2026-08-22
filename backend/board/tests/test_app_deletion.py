"""App-side delete rules for empty projects and shell tasks."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from board.deletion import project_is_app_deletable, task_is_app_deletable
from board.models import Comment, Project, Task
from board.tests.helpers import make_link, make_portal, make_project, make_task, make_user
from portals.models import Portal
from portals.serializers import issue_tokens


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class AppDeletionApiTests(TestCase):
    def setUp(self):
        self.agency = make_portal(Portal.Role.AGENCY, member_id="agency-del", name="Agency")
        self.client_portal = make_portal(
            Portal.Role.CLIENT, member_id="client-del", name="Client"
        )
        make_link(self.agency, self.client_portal)
        self.agency_user = make_user(
            self.agency, bitrix_id="ad1", name="Agency", last_name="User"
        )
        self.client_user = make_user(
            self.client_portal, bitrix_id="cd1", name="Client", last_name="User"
        )
        self.project = make_project(self.client_portal, name="Пустой")
        self.agency_api = APIClient()
        self.client_api = APIClient()
        self.agency_api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {issue_tokens(self.agency, self.agency_user)['access']}"
        )
        self.client_api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {issue_tokens(self.client_portal, self.client_user)['access']}"
        )

    def test_delete_empty_project(self):
        self.assertTrue(project_is_app_deletable(self.project))
        with patch("portals.bitrix.BitrixClient"):
            res = self.agency_api.delete(f"/api/projects/{self.project.id}/")
        self.assertEqual(res.status_code, 204, getattr(res, "content", b""))
        self.assertFalse(Project.objects.filter(pk=self.project.id).exists())

    def test_cannot_delete_project_with_tasks(self):
        make_task(self.project, title="Есть задача")
        res = self.agency_api.delete(f"/api/projects/{self.project.id}/")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Project.objects.filter(pk=self.project.id).exists())

    def test_delete_shell_task(self):
        task = make_task(self.project, title="Черновик", created_by=self.agency_user)
        self.assertTrue(task_is_app_deletable(task))
        with patch("portals.bitrix.BitrixClient"):
            res = self.agency_api.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(res.status_code, 204, getattr(res, "content", b""))
        self.assertFalse(Task.objects.filter(pk=task.id).exists())

    def test_cannot_delete_task_with_description(self):
        task = make_task(
            self.project,
            title="С описанием",
            description="текст",
            created_by=self.agency_user,
        )
        res = self.agency_api.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Task.objects.filter(pk=task.id).exists())

    def test_cannot_delete_task_with_comment(self):
        task = make_task(self.project, title="С чатом", created_by=self.agency_user)
        Comment.objects.create(task=task, text="привет", is_system=False)
        res = self.agency_api.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(res.status_code, 400)

    def test_cannot_delete_done_task(self):
        task = make_task(
            self.project,
            title="Готово",
            status=Task.Status.DONE,
            outcome="ок",
            created_by=self.agency_user,
        )
        res = self.agency_api.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(res.status_code, 400)

    def test_client_cannot_delete_task(self):
        task = make_task(self.project, title="Чужая", created_by=self.agency_user)
        res = self.client_api.delete(f"/api/tasks/{task.id}/")
        self.assertEqual(res.status_code, 403)
