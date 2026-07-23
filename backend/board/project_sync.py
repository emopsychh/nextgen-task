"""Bitrix workgroup tasks → app Projects (parent) and Tasks (subtasks)."""

from __future__ import annotations

import logging

from portals.bitrix import BitrixAPIError, BitrixClient

logger = logging.getLogger(__name__)


def _parent_id(task_data: dict) -> str:
    raw = (
        task_data.get("parentId")
        or task_data.get("PARENT_ID")
        or task_data.get("parent_id")
        or ""
    )
    text = str(raw).strip()
    if text in ("", "0", "None", "null"):
        return ""
    return text


def _group_id(task_data: dict) -> str:
    raw = (
        task_data.get("groupId")
        or task_data.get("GROUP_ID")
        or task_data.get("group_id")
        or ""
    )
    text = str(raw).strip()
    return "" if text in ("", "0") else text


def _task_title(task_data: dict, portal=None) -> str:
    from board.titles import strip_portal_title_prefix

    raw = str(task_data.get("title") or task_data.get("TITLE") or "").strip() or "Без названия"
    return strip_portal_title_prefix(raw, portal)


def _task_description(task_data: dict) -> str:
    return str(task_data.get("description") or task_data.get("DESCRIPTION") or "").strip()


def _bitrix_id(task_data: dict) -> str:
    return str(task_data.get("id") or task_data.get("ID") or "").strip()


def find_client_portal_for_group(*, agency_portal, group_id: str):
    """Match PortalLink.bitrix_group_id → client portal."""
    from portals.models import PortalLink

    group_id = str(group_id or "").strip()
    if not group_id:
        return None
    link = (
        PortalLink.objects.filter(
            agency_portal=agency_portal,
            bitrix_group_id=group_id,
        )
        .select_related("client_portal")
        .first()
    )
    return link.client_portal if link else None


def resolve_client_and_group_for_agency_task(*, agency_portal, task_data: dict):
    """
    From an agency Bitrix task payload, find client portal + group id.
    May refresh PortalLink cache when group is known on company but not yet cached.
    """
    from portals.deal_resolve import resolve_bitrix_group_id
    from portals.models import PortalLink

    gid = _group_id(task_data)
    if gid:
        client = find_client_portal_for_group(agency_portal=agency_portal, group_id=gid)
        if client:
            return client, gid

    # Refresh group ids for all linked clients and retry
    links = PortalLink.objects.filter(agency_portal=agency_portal).select_related(
        "client_portal"
    )
    for link in links:
        try:
            resolved = resolve_bitrix_group_id(
                agency_portal=agency_portal,
                client_portal=link.client_portal,
                force_refresh=True,
            )
        except BitrixAPIError:
            continue
        if gid and resolved == gid:
            return link.client_portal, gid
        if not gid and resolved:
            # Task without GROUP in payload — only usable if single client
            pass

    if gid:
        # Still unknown company for this group
        return None, gid
    return None, ""


def upsert_project_from_bitrix(*, client_portal, task_data: dict, group_id: str = ""):
    """Create/update app Project from a top-level Bitrix task. Returns (project, created)."""
    from board.models import Project

    bitrix_id = _bitrix_id(task_data)
    if not bitrix_id:
        return None, False

    gid = group_id or _group_id(task_data)
    # Projects keep their Bitrix title as-is (no client portal tag stripping needed for parents)
    title = str(task_data.get("title") or task_data.get("TITLE") or "").strip() or "Без названия"
    description = _task_description(task_data)

    project = Project.objects.filter(
        portal=client_portal, bitrix_task_id=bitrix_id
    ).first()
    if project:
        changed = False
        if project.name != title:
            project.name = title
            changed = True
        if description and project.description != description:
            project.description = description
            changed = True
        if gid and project.bitrix_group_id != gid:
            project.bitrix_group_id = gid
            changed = True
        if changed:
            project.save()
        return project, False

    project = Project.objects.create(
        portal=client_portal,
        name=title,
        description=description,
        bitrix_task_id=bitrix_id,
        bitrix_group_id=gid,
        is_active=True,
    )
    return project, True


def upsert_task_from_bitrix_subtask(*, project, task_data: dict, agency: bool = True):
    """Create/update app Task from a Bitrix subtask under a Project parent."""
    from board.models import Task
    from board.status_sync import (
        bitrix_task_is_important,
        local_status_from_bitrix_task,
        parse_bitrix_deadline,
    )

    bitrix_id = _bitrix_id(task_data)
    if not bitrix_id:
        return None, False

    title = _task_title(task_data, project.portal)
    description = _task_description(task_data)
    status = local_status_from_bitrix_task(task_data) or Task.Status.TODO
    due = parse_bitrix_deadline(task_data)
    important = bitrix_task_is_important(task_data)

    qs = Task.objects.filter(project=project)
    if agency:
        task = qs.filter(agency_bitrix_task_id=bitrix_id).first()
    else:
        task = qs.filter(bitrix_task_id=bitrix_id).first()

    if task:
        changed = False
        if task.title != title:
            task.title = title
            changed = True
        if description and task.description != description:
            task.description = description
            changed = True
        if task.status != status:
            task.status = status
            changed = True
        if task.due_date != due:
            task.due_date = due
            changed = True
        if important is not None and task.is_important != important:
            task.is_important = important
            changed = True
        if agency and task.agency_bitrix_task_id != bitrix_id:
            task.agency_bitrix_task_id = bitrix_id
            changed = True
        if changed:
            task.sync_status = Task.SyncStatus.SYNCED
            task.sync_error = ""
            task.save()
        return task, False

    kwargs = {
        "project": project,
        "title": title,
        "description": description,
        "status": status,
        "due_date": due,
        "is_important": bool(important),
        "sync_status": Task.SyncStatus.SYNCED,
    }
    if agency:
        kwargs["agency_bitrix_task_id"] = bitrix_id
    else:
        kwargs["bitrix_task_id"] = bitrix_id
    task = Task.objects.create(**kwargs)
    return task, True


def pull_projects_from_bitrix(client_portal) -> dict:
    """
    Import top-level tasks from the company Bitrix workgroup as app Projects.
    Also imports their subtasks as app Tasks.
    """
    from portals.deal_resolve import resolve_bitrix_group_id
    from portals.models import PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    if not link:
        return {"ok": False, "reason": "no_agency_link", "projects": 0, "tasks": 0}

    agency = link.agency_portal
    if not agency.access_token:
        return {"ok": False, "reason": "no_agency_token", "projects": 0, "tasks": 0}

    try:
        group_id = resolve_bitrix_group_id(
            agency_portal=agency, client_portal=client_portal, force_refresh=True
        )
    except BitrixAPIError as exc:
        logger.info("pull projects: no group for portal %s: %s", client_portal.id, exc)
        return {"ok": False, "reason": str(exc), "projects": 0, "tasks": 0}

    client = BitrixClient(agency)
    try:
        parents = client.list_tasks(group_id=group_id, parent_id=0)
        if not parents:
            # Some portals ignore PARENT_ID=0 — list all and filter locally
            all_rows = client.list_tasks(group_id=group_id)
            parents = [r for r in all_rows if not _parent_id(r)]
    except BitrixAPIError as exc:
        logger.info("list parent tasks failed group=%s: %s", group_id, exc)
        return {"ok": False, "reason": str(exc), "projects": 0, "tasks": 0}

    created_projects = 0
    created_tasks = 0
    for row in parents:
        # Skip rows that somehow have a parent
        if _parent_id(row):
            continue
        project, created = upsert_project_from_bitrix(
            client_portal=client_portal, task_data=row, group_id=group_id
        )
        if created:
            created_projects += 1
        if not project:
            continue
        parent_bx = project.bitrix_task_id
        try:
            children = client.list_tasks(group_id=group_id, parent_id=parent_bx)
        except BitrixAPIError:
            children = []
        for child in children:
            _, t_created = upsert_task_from_bitrix_subtask(
                project=project, task_data=child, agency=True
            )
            if t_created:
                created_tasks += 1

    return {
        "ok": True,
        "group_id": group_id,
        "projects": created_projects,
        "tasks": created_tasks,
        "seen_parents": len(parents),
    }


def ingest_agency_bitrix_task(*, agency_portal, bitrix_task_id: str) -> dict:
    """
    OnTaskAdd / OnTaskUpdate on agency portal:
    - top-level task in company GROUP → app Project
    - subtask under known Project parent → app Task
    """
    if not agency_portal.access_token:
        return {"ok": False, "reason": "no_token"}

    client = BitrixClient(agency_portal)
    try:
        task_data = client.get_task(bitrix_task_id)
    except BitrixAPIError as exc:
        return {"ok": False, "reason": str(exc)}

    if not task_data:
        return {"ok": False, "reason": "empty_task"}

    parent = _parent_id(task_data)
    client_portal, group_id = resolve_client_and_group_for_agency_task(
        agency_portal=agency_portal, task_data=task_data
    )

    if not parent:
        # Parent / project-level task
        if not client_portal:
            return {"ok": False, "reason": "unknown_group", "group_id": group_id or None}
        project, created = upsert_project_from_bitrix(
            client_portal=client_portal, task_data=task_data, group_id=group_id
        )
        return {
            "ok": True,
            "kind": "project",
            "created": created,
            "project_id": project.id if project else None,
            "client_portal_id": client_portal.id,
        }

    # Subtask → find Project by parent Bitrix id
    from board.models import Project

    project = Project.objects.filter(bitrix_task_id=parent).select_related("portal").first()
    if not project and client_portal:
        # Parent not pulled yet — try pull once
        pull_projects_from_bitrix(client_portal)
        project = Project.objects.filter(bitrix_task_id=parent).first()
    if not project:
        return {"ok": False, "reason": "unknown_parent", "parent_id": parent}

    task, created = upsert_task_from_bitrix_subtask(
        project=project, task_data=task_data, agency=True
    )
    return {
        "ok": True,
        "kind": "task",
        "created": created,
        "task_id": task.id if task else None,
        "project_id": project.id,
    }
