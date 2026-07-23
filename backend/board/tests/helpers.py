"""Small factory helpers for board/portals tests (no external deps)."""

from __future__ import annotations

import uuid

from board.models import Project, Task
from portals.models import BitrixUser, Portal, PortalLink


def make_portal(
    role: str = Portal.Role.CLIENT,
    *,
    member_id: str | None = None,
    domain: str | None = None,
    token: str = "tok",
    name: str = "",
) -> Portal:
    mid = member_id or f"m-{uuid.uuid4().hex[:10]}"
    return Portal.objects.create(
        member_id=mid,
        domain=domain or f"{mid}.bitrix24.ru",
        role=role,
        name=name,
        access_token=token,
    )


def make_link(agency: Portal, client: Portal, **kwargs) -> PortalLink:
    return PortalLink.objects.create(
        agency_portal=agency, client_portal=client, **kwargs
    )


def make_user(portal: Portal, bitrix_id: str = "1", **kwargs) -> BitrixUser:
    return BitrixUser.objects.create(portal=portal, bitrix_id=bitrix_id, **kwargs)


def make_project(portal: Portal, *, name: str = "Проект", **kwargs) -> Project:
    return Project.objects.create(portal=portal, name=name, **kwargs)


def make_task(project: Project, *, title: str = "Задача", **kwargs) -> Task:
    return Task.objects.create(project=project, title=title, **kwargs)
