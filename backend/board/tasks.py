from celery import shared_task
import logging

from portals.bitrix import (
    BITRIX_STATUS_COMPLETED,
    BITRIX_STATUS_DEFERRED,
    BITRIX_STATUS_IN_PROGRESS,
    BITRIX_STATUS_PENDING,
    BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
    BitrixAPIError,
    BitrixClient,
    bitrix_status_code,
)

from board.titles import strip_portal_title_prefix


def _extract_bitrix_id(result) -> str:
    if not isinstance(result, dict):
        return ""
    if "task" in result and isinstance(result["task"], dict):
        return str(result["task"].get("id") or "")
    return str(result.get("id") or result.get("taskId") or "")


def _bitrix_user_id(user_data: dict) -> str:
    return str(user_data.get("ID") or user_data.get("id") or "")


def _resolve_responsible_id(client: BitrixClient, task) -> str:
    """
    Bitrix requires RESPONSIBLE_ID on the same portal as the task.
    Agency users creating work on a client portal must not use their agency user id.
    """
    portal = task.project.portal
    if (
        task.created_by_id
        and task.created_by
        and task.created_by.portal_id == portal.id
        and task.created_by.bitrix_id
    ):
        return str(task.created_by.bitrix_id)
    current = client.get_current_user()
    uid = _bitrix_user_id(current)
    if uid:
        return uid
    # Fallback: any user stored for this portal
    from portals.models import BitrixUser

    local = portal.users.order_by("-is_admin", "id").first()
    return str(local.bitrix_id) if local else ""


def _agency_portal_for_client(client_portal):
    from portals.models import PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    return link.agency_portal if link else None


def _crm_deal_uf_bindings(client_portal) -> list[str]:
    """
    Bitrix task field UF_CRM_TASK values for the client's active accompaniment deal.
    Format: D_<dealId> (deal), C_ / CO_ / L_ for other CRM types.
    """
    from portals.models import PortalDealBinding, PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    if not link or not link.agency_portal_id:
        return []
    binding = (
        PortalDealBinding.objects.filter(
            agency_portal_id=link.agency_portal_id,
            client_portal_id=client_portal.id,
            is_active=True,
        )
        .exclude(deal_id="")
        .order_by("-updated_at")
        .first()
    )
    if not binding or not binding.deal_id:
        return []
    deal_id = str(binding.deal_id).strip()
    if not deal_id:
        return []
    return [f"D_{deal_id}"]


def _task_fields(
    task,
    *,
    responsible_id: str | None = None,
    group_id: str | None = None,
    parent_id: str | None = None,
    include_deadline: bool = True,
    crm_bindings: list[str] | None = None,
) -> dict:
    from board.status_sync import format_bitrix_deadline

    fields = {
        "TITLE": task.title,
        "DESCRIPTION": task.description or "",
    }
    if include_deadline:
        fields["DEADLINE"] = format_bitrix_deadline(task.due_date)
    if responsible_id:
        fields["RESPONSIBLE_ID"] = responsible_id
        fields["CREATED_BY"] = responsible_id
    if group_id:
        fields["GROUP_ID"] = group_id
    if parent_id:
        fields["PARENT_ID"] = parent_id
    # Enable Bitrix «Учёт времени» so startTimer/pauseTimer work in the UI
    fields["ALLOW_TIME_TRACKING"] = "Y"
    if crm_bindings:
        fields["UF_CRM_TASK"] = list(crm_bindings)
    return fields


def _deadline_needs_push(client: BitrixClient, bitrix_task_id: str, due) -> bool:
    """Skip DEADLINE in updates when Bitrix already has the same due (minute precision)."""
    from board.status_sync import deadlines_equal, parse_bitrix_deadline

    try:
        current = parse_bitrix_deadline(client.get_task(bitrix_task_id) or {})
    except BitrixAPIError:
        return True
    return not deadlines_equal(current, due)


def _normalize_local(status: str) -> str:
    return status if status in ("todo", "in_progress", "done") else "todo"


def apply_bitrix_status(client: BitrixClient, bitrix_task_id: str, target_local: str) -> None:
    """Bring Bitrix task to the local status using official action methods."""
    target = _normalize_local(target_local)
    task_data = client.get_task(bitrix_task_id)
    current = bitrix_status_code(task_data)
    if current is None:
        current = BITRIX_STATUS_PENDING

    if target == "done" and current in (
        BITRIX_STATUS_COMPLETED,
        BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
    ):
        return
    if target == "in_progress" and current == BITRIX_STATUS_IN_PROGRESS:
        return
    if target == "todo" and current in (BITRIX_STATUS_PENDING, BITRIX_STATUS_DEFERRED):
        return

    # Leave completed states before moving elsewhere.
    if current in (
        BITRIX_STATUS_COMPLETED,
        BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
    ) and target != "done":
        client.renew_task(bitrix_task_id)
        refreshed = bitrix_status_code(client.get_task(bitrix_task_id))
        current = refreshed if refreshed is not None else BITRIX_STATUS_PENDING

    if target == "todo":
        if current == BITRIX_STATUS_IN_PROGRESS:
            client.pause_task(bitrix_task_id)
        return

    if target == "in_progress":
        if current in (BITRIX_STATUS_PENDING, BITRIX_STATUS_DEFERRED):
            client.start_task(bitrix_task_id)
        elif current != BITRIX_STATUS_IN_PROGRESS:
            # e.g. after renew already pending — start; otherwise no-op
            try:
                client.start_task(bitrix_task_id)
            except BitrixAPIError:
                pass
        return

    # target == done
    if current in (BITRIX_STATUS_PENDING, BITRIX_STATUS_DEFERRED):
        try:
            client.start_task(bitrix_task_id)
        except BitrixAPIError:
            pass
    client.complete_task(bitrix_task_id)


def _ensure_project_agency_parent(project) -> tuple[str, str]:
    """
    Ensure the app Project has an agency Bitrix parent task in the company GROUP.
    Returns (bitrix_task_id, group_id).
    """
    from portals.deal_resolve import resolve_bitrix_group_id

    agency = _agency_portal_for_client(project.portal)
    if not agency:
        raise BitrixAPIError("Клиент не привязан к агентству")

    group_id = project.bitrix_group_id or ""
    if not group_id:
        group_id = resolve_bitrix_group_id(
            agency_portal=agency, client_portal=project.portal
        )

    if project.bitrix_task_id and project.bitrix_group_id == group_id:
        return project.bitrix_task_id, group_id

    result = _do_sync_project_to_bitrix(project.id)
    if not result.get("ok"):
        raise BitrixAPIError(
            result.get("error")
            or result.get("reason")
            or "Не удалось создать родительскую задачу проекта в Bitrix"
        )
    project.refresh_from_db()
    if not project.bitrix_task_id:
        raise BitrixAPIError("Не удалось создать родительскую задачу проекта в Bitrix")
    return project.bitrix_task_id, project.bitrix_group_id or group_id


def _sync_one_portal(
    task,
    portal,
    *,
    existing_id: str,
    group_id: str | None = None,
    parent_id: str | None = None,
    crm_bindings: list[str] | None = None,
) -> str:
    """Create/update Bitrix task on a portal; return bitrix task id."""
    if not portal.access_token:
        raise BitrixAPIError(f"Нет токена Bitrix у портала {portal.domain or portal.id}")

    client = BitrixClient(portal)
    responsible_id = _resolve_responsible_id(client, task)
    if not responsible_id and not existing_id:
        raise BitrixAPIError(
            f"Не указан исполнитель на {portal.domain}: откройте приложение на этом портале "
            "и сохраните задачу снова"
        )

    # Never prefix with client portal name — context is the project/workgroup.
    client_portal = task.project.portal
    title = strip_portal_title_prefix(task.title, client_portal)
    if title != task.title:
        task.title = title
        task.save(update_fields=["title", "updated_at"])

    if existing_id:
        push_deadline = _deadline_needs_push(client, existing_id, task.due_date)
        fields = _task_fields(
            task,
            group_id=group_id,
            parent_id=parent_id,
            include_deadline=push_deadline,
            crm_bindings=crm_bindings,
        )
        fields["TITLE"] = title
        client.update_task(existing_id, fields)
        # Only push status on explicit local→Bitrix sync (PENDING).
        # Title/deadline cleanup must not call start() and undo a Bitrix pause.
        if task.sync_status == task.SyncStatus.PENDING:
            try:
                apply_bitrix_status(client, existing_id, task.status)
            except BitrixAPIError as exc:
                raise BitrixAPIError(f"не удалось сменить статус в Bitrix: {exc}") from exc
        return existing_id

    fields = _task_fields(
        task,
        responsible_id=responsible_id,
        group_id=group_id,
        parent_id=parent_id,
        include_deadline=True,
        crm_bindings=crm_bindings,
    )
    fields["TITLE"] = title
    result = client.create_task(fields)
    bitrix_id = _extract_bitrix_id(result)
    if bitrix_id and task.status != "todo":
        try:
            apply_bitrix_status(client, bitrix_id, task.status)
        except BitrixAPIError as exc:
            raise BitrixAPIError(f"задача создана, но статус не применён: {exc}") from exc
    return bitrix_id


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_project_to_bitrix(self, project_id: int):
    """
    App Project → agency Bitrix parent task inside company workgroup (GROUP_ID).
    Not duplicated to the client Bitrix portal.
    """
    try:
        return _do_sync_project_to_bitrix(project_id)
    except BitrixAPIError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}


def _do_sync_project_to_bitrix(project_id: int) -> dict:
    from board.models import Project
    from portals.deal_resolve import resolve_bitrix_group_id

    try:
        project = Project.objects.select_related("portal").get(pk=project_id)
    except Project.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    client_portal = project.portal
    agency = _agency_portal_for_client(client_portal)
    if not agency:
        return {"ok": False, "reason": "no_agency_link"}
    if not agency.access_token:
        return {"ok": False, "reason": "no_agency_token"}

    group_id = resolve_bitrix_group_id(
        agency_portal=agency, client_portal=client_portal
    )

    client = BitrixClient(agency)
    responsible = _bitrix_user_id(client.get_current_user())
    if not responsible:
        local = agency.users.order_by("-is_admin", "id").first()
        responsible = str(local.bitrix_id) if local else ""
    if not responsible and not project.bitrix_task_id:
        raise BitrixAPIError(
            f"Не указан исполнитель на {agency.domain}: откройте приложение на портале агентства"
        )

    fields = {
        "TITLE": project.name,
        "DESCRIPTION": project.description or "",
        "GROUP_ID": group_id,
        "ALLOW_TIME_TRACKING": "Y",
    }
    crm_bindings = _crm_deal_uf_bindings(client_portal)
    if crm_bindings:
        fields["UF_CRM_TASK"] = crm_bindings
    if project.bitrix_task_id:
        client.update_task(project.bitrix_task_id, fields)
        bitrix_id = project.bitrix_task_id
    else:
        if responsible:
            fields["RESPONSIBLE_ID"] = responsible
            fields["CREATED_BY"] = responsible
        result = client.create_task(fields)
        bitrix_id = _extract_bitrix_id(result)
        if not bitrix_id:
            raise BitrixAPIError("Bitrix не вернул ID задачи проекта")

    update_fields = ["updated_at"]
    if bitrix_id != project.bitrix_task_id:
        project.bitrix_task_id = bitrix_id
        update_fields.append("bitrix_task_id")
    if group_id != project.bitrix_group_id:
        project.bitrix_group_id = group_id
        update_fields.append("bitrix_group_id")
    project.save(update_fields=update_fields)
    return {"ok": True, "bitrix_task_id": bitrix_id, "group_id": group_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_task_to_bitrix(self, task_id: int):
    """
    Sync task:
    - client Bitrix: flat task (no GROUP/PARENT)
    - agency Bitrix: subtask under Project parent (PARENT_ID + GROUP_ID)
    """
    from board.models import Task

    try:
        task = Task.objects.select_related(
            "project", "project__portal", "created_by"
        ).get(pk=task_id)
    except Task.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    client_portal = task.project.portal
    errors: list[str] = []
    update_fields = ["sync_status", "sync_error", "updated_at"]

    # 1) Client portal — flat task
    try:
        client_id = _sync_one_portal(
            task, client_portal, existing_id=task.bitrix_task_id or ""
        )
        if client_id and client_id != task.bitrix_task_id:
            task.bitrix_task_id = client_id
            update_fields.append("bitrix_task_id")
    except BitrixAPIError as exc:
        errors.append(f"клиент: {exc}")
    except Exception as exc:
        errors.append(f"клиент: {exc}")

    # 2) Agency portal — subtask in company project (+ CRM deal binding)
    agency = _agency_portal_for_client(client_portal)
    if agency and agency.id != client_portal.id:
        try:
            parent_id, group_id = _ensure_project_agency_parent(task.project)
            crm_bindings = _crm_deal_uf_bindings(client_portal)
            agency_id = _sync_one_portal(
                task,
                agency,
                existing_id=task.agency_bitrix_task_id or "",
                group_id=group_id,
                parent_id=parent_id,
                crm_bindings=crm_bindings or None,
            )
            if agency_id and agency_id != task.agency_bitrix_task_id:
                task.agency_bitrix_task_id = agency_id
                update_fields.append("agency_bitrix_task_id")
        except BitrixAPIError as exc:
            errors.append(f"агентство: {exc}")
        except Exception as exc:
            errors.append(f"агентство: {exc}")

    if errors:
        task.sync_status = Task.SyncStatus.ERROR
        task.sync_error = "; ".join(errors)
        task.save(update_fields=list(set(update_fields)))
        try:
            raise self.retry(exc=BitrixAPIError(task.sync_error))
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": task.sync_error, "partial_ids": {
                "bitrix_task_id": task.bitrix_task_id,
                "agency_bitrix_task_id": task.agency_bitrix_task_id,
            }}

    task.sync_status = Task.SyncStatus.SYNCED
    task.sync_error = ""
    task.save(update_fields=list(set(update_fields)))
    try:
        from board.realtime import publish_task_event

        publish_task_event(task, kind="task_synced")
    except Exception:
        pass
    return {
        "ok": True,
        "bitrix_task_id": task.bitrix_task_id,
        "agency_bitrix_task_id": task.agency_bitrix_task_id,
        "errors": [],
    }


@shared_task(bind=True, max_retries=5, default_retry_delay=5)
def sync_comment_to_bitrix(self, comment_id: int):
    """Post a chat message into linked Bitrix task(s)."""
    from board.models import Comment

    try:
        comment = Comment.objects.select_related(
            "task",
            "task__project",
            "task__project__portal",
            "author",
        ).get(pk=comment_id)
    except Comment.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    # System lines are app-local only — posting them to Bitrix creates
    # duplicate activity and can feed comment/deadline loops.
    if comment.is_system:
        return {"ok": True, "skipped": "system"}

    author_name = comment.author_name or (
        comment.author.display_name if comment.author else "Участник"
    )
    body = (comment.text or "").strip()
    # File-only comments: Bitrix chat message is created by sync_attachment_to_bitrix
    if not body:
        return {"ok": True, "skipped": "empty_text"}
    message = f"{author_name}: {body}".strip()
    if not message:
        return {"ok": False, "reason": "empty"}

    task = comment.task
    targets: list[tuple] = []
    client_portal = task.project.portal
    if task.bitrix_task_id and client_portal.access_token:
        targets.append((client_portal, task.bitrix_task_id))

    agency = _agency_portal_for_client(client_portal)
    if agency and task.agency_bitrix_task_id and agency.access_token:
        targets.append((agency, task.agency_bitrix_task_id))

    if not targets:
        # Task may still be syncing to Bitrix — retry shortly
        try:
            raise self.retry(countdown=5)
        except self.MaxRetriesExceededError:
            return {"ok": False, "reason": "no_bitrix_task"}

    errors = []
    saved_ids: dict[str, str] = {}
    for portal, bitrix_task_id in targets:
        try:
            result = BitrixClient(portal).add_task_comment(bitrix_task_id, message)
            cid = ""
            if isinstance(result, (int, float)):
                cid = str(int(result))
            elif isinstance(result, str) and result.isdigit():
                cid = result
            elif isinstance(result, dict):
                for key in ("id", "ID", "result"):
                    val = result.get(key)
                    if isinstance(val, (int, float)):
                        cid = str(int(val))
                        break
                    if isinstance(val, str) and val.isdigit():
                        cid = val
                        break
            if cid:
                if portal.id == client_portal.id:
                    saved_ids["bitrix_comment_id"] = cid
                else:
                    saved_ids["agency_bitrix_comment_id"] = cid
        except BitrixAPIError as exc:
            errors.append(f"{portal.domain}: {exc}")

    if saved_ids:
        update_fields = []
        if "bitrix_comment_id" in saved_ids and not comment.bitrix_comment_id:
            comment.bitrix_comment_id = saved_ids["bitrix_comment_id"]
            update_fields.append("bitrix_comment_id")
        if "agency_bitrix_comment_id" in saved_ids and not comment.agency_bitrix_comment_id:
            comment.agency_bitrix_comment_id = saved_ids["agency_bitrix_comment_id"]
            update_fields.append("agency_bitrix_comment_id")
        if update_fields:
            update_fields.append("updated_at")
            comment.save(update_fields=update_fields)

    if errors:
        try:
            raise self.retry(exc=BitrixAPIError("; ".join(errors)))
        except self.MaxRetriesExceededError:
            return {"ok": False, "errors": errors}

    # Notify Bitrix users on the agency task (best-effort)
    try:
        _notify_comment_participants(comment, agency, task)
    except Exception:
        pass

    from board.realtime import publish_task_event

    publish_task_event(task, kind="comment_synced")
    return {"ok": True, "posted": len(targets) - len(errors), "ids": saved_ids}


def _notify_comment_participants(comment, agency, task) -> None:
    """Send im.notify to responsible / creator / accomplices on agency Bitrix task."""
    if not agency or not agency.access_token or not task.agency_bitrix_task_id:
        return
    client = BitrixClient(agency)
    data = client.get_task(task.agency_bitrix_task_id) or {}
    user_ids: set[str] = set()
    for key in (
        "responsibleId",
        "RESPONSIBLE_ID",
        "createdBy",
        "CREATED_BY",
    ):
        val = data.get(key)
        if val not in (None, "", "0", 0):
            user_ids.add(str(val))
    for key in ("accomplices", "ACCOMPLICES", "auditors", "AUDITORS"):
        val = data.get(key)
        if isinstance(val, list):
            for item in val:
                if item not in (None, "", "0", 0):
                    user_ids.add(str(item))
        elif isinstance(val, dict):
            for item in val.values():
                if item not in (None, "", "0", 0):
                    user_ids.add(str(item))
    author_bx = ""
    if comment.author and comment.author.portal_id == agency.id:
        author_bx = str(comment.author.bitrix_id or "")
    preview = (comment.text or "").strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "…"
    author_name = comment.author_name or (
        comment.author.display_name if comment.author else "Участник"
    )
    message = f"[Nextgen] {author_name} в задаче «{task.title}»: {preview}"
    for uid in user_ids:
        if author_bx and uid == author_bx:
            continue
        try:
            client.notify_user(uid, message)
        except BitrixAPIError:
            pass


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def sync_attachment_to_bitrix(self, attachment_id: int):
    """Upload a local attachment to Bitrix and attach to linked task(s). Prefer agency subtask."""
    from board.file_sync import upload_and_attach
    from board.models import Attachment
    from board.realtime import publish_task_event

    logger = logging.getLogger(__name__)

    try:
        attachment = Attachment.objects.select_related(
            "task", "task__project", "task__project__portal", "comment", "comment__task"
        ).get(pk=attachment_id)
    except Attachment.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    task = attachment.task
    if not task and attachment.comment_id:
        task = attachment.comment.task
        if not attachment.task_id:
            attachment.task = task
            attachment.save(update_fields=["task"])
    if not task:
        return {"ok": False, "reason": "no_task"}

    client_portal = task.project.portal
    agency = _agency_portal_for_client(client_portal)
    errors = []
    update_fields = []

    logger.info(
        "sync_attachment start id=%s task=%s agency_task=%s client_task=%s name=%s",
        attachment_id,
        task.id,
        task.agency_bitrix_task_id,
        task.bitrix_task_id,
        attachment.original_name,
    )

    # Ensure agency subtask exists — that's where managers look for files
    if agency and agency.access_token and not task.agency_bitrix_task_id:
        try:
            parent_id, group_id = _ensure_project_agency_parent(task.project)
            bx = _sync_one_portal(
                task,
                agency,
                existing_id="",
                group_id=group_id,
                parent_id=parent_id,
                crm_bindings=_crm_deal_uf_bindings(task.project.portal) or None,
            )
            if bx:
                task.agency_bitrix_task_id = bx
                task.save(update_fields=["agency_bitrix_task_id", "updated_at"])
        except Exception as exc:
            errors.append(f"ensure agency task: {exc}")
            logger.exception("ensure agency task failed attachment=%s", attachment_id)

    # Agency first (Проекты → задача → подзадача)
    if (
        agency
        and task.agency_bitrix_task_id
        and agency.access_token
        and not attachment.agency_bitrix_file_id
    ):
        try:
            fid = upload_and_attach(
                client=BitrixClient(agency),
                bitrix_task_id=task.agency_bitrix_task_id,
                attachment=attachment,
            )
            attachment.agency_bitrix_file_id = fid
            update_fields.append("agency_bitrix_file_id")
        except BitrixAPIError as exc:
            errors.append(f"agency: {exc}")
            logger.warning(
                "agency attach failed attachment=%s task=%s: %s",
                attachment_id,
                task.agency_bitrix_task_id,
                exc,
            )
    elif agency and not task.agency_bitrix_task_id:
        errors.append("agency: no agency_bitrix_task_id")
    elif not agency:
        errors.append("agency: portal not linked")

    if task.bitrix_task_id and client_portal.access_token and not attachment.bitrix_file_id:
        try:
            fid = upload_and_attach(
                client=BitrixClient(client_portal),
                bitrix_task_id=task.bitrix_task_id,
                attachment=attachment,
            )
            attachment.bitrix_file_id = fid
            update_fields.append("bitrix_file_id")
        except BitrixAPIError as exc:
            errors.append(f"client: {exc}")
            logger.warning(
                "client attach failed attachment=%s task=%s: %s",
                attachment_id,
                task.bitrix_task_id,
                exc,
            )

    if update_fields:
        attachment.save(update_fields=update_fields)
        publish_task_event(task, kind="attachment_synced")

    if errors and not update_fields:
        logger.error(
            "sync_attachment failed id=%s errors=%s", attachment_id, errors
        )
        try:
            raise self.retry(exc=BitrixAPIError("; ".join(errors)))
        except self.MaxRetriesExceededError:
            return {"ok": False, "errors": errors}
    return {
        "ok": True,
        "bitrix_file_id": attachment.bitrix_file_id,
        "agency_bitrix_file_id": attachment.agency_bitrix_file_id,
        "errors": errors,
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def post_time_entry_to_deal(self, entry_id: int):
    """
    Deduct a closed time session from the accompaniment deal remaining hours.
    Idempotent via atomic claim on TimeEntry.billed_to_deal_at.
    """
    from django.utils import timezone

    from board.models import TimeEntry
    from board.timeutils import format_duration_ru
    from portals.deal_hours import (
        compute_remaining_after_spend,
        hours_fields_configured,
        read_deal_hours,
        remaining_update_fields,
    )
    from portals.deal_resolve import get_active_binding, resolve_or_refresh_binding
    from portals.models import PortalLink

    try:
        entry = TimeEntry.objects.select_related(
            "task",
            "task__project",
            "task__project__portal",
        ).get(pk=entry_id)
    except TimeEntry.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    if entry.billed_to_deal_at is not None:
        return {"ok": True, "skipped": "already_billed"}
    if entry.ended_at is None or entry.duration_seconds <= 0:
        return {"ok": True, "skipped": "no_duration"}

    task = entry.task
    client_portal = task.project.portal
    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    if not link:
        return {"ok": False, "reason": "no_agency_link"}

    agency = link.agency_portal
    if not agency.access_token:
        return {"ok": False, "reason": "no_agency_token"}

    binding = get_active_binding(agency_portal=agency, client_portal=client_portal)
    if not binding:
        try:
            binding = resolve_or_refresh_binding(
                agency_portal=agency,
                client_portal=client_portal,
            )
        except BitrixAPIError as exc:
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(exc)}

    if not binding:
        return {"ok": False, "reason": "no_deal_binding"}

    # Claim before Bitrix writes so retries cannot double-spend.
    claimed_at = timezone.now()
    claimed = TimeEntry.objects.filter(pk=entry.id, billed_to_deal_at__isnull=True).update(
        billed_to_deal_at=claimed_at
    )
    if not claimed:
        return {"ok": True, "skipped": "already_billed"}

    seconds = int(entry.duration_seconds)
    duration_label = format_duration_ru(seconds)
    comment = f"Задача «{task.title}»: учтено {duration_label}"
    deal_updated = False

    client = BitrixClient(agency)
    try:
        hours_result = None
        if hours_fields_configured():
            deal = client.get_deal(binding.deal_id)
            new_remaining, spent = compute_remaining_after_spend(deal, seconds)
            if new_remaining is not None:
                client.update_deal(binding.deal_id, remaining_update_fields(new_remaining))
                deal_updated = True
                hours = read_deal_hours(deal)
                paid = hours.paid
                binding.paid_hours = paid
                binding.remaining_hours = new_remaining
                binding.save(update_fields=["paid_hours", "remaining_hours", "updated_at"])
                comment += f". Остаток часов: {new_remaining}"
                hours_result = {
                    "spent_hours": float(spent),
                    "remaining_hours": float(new_remaining),
                    "paid_hours": float(paid) if paid is not None else None,
                }

        result = client.add_deal_timeline_comment(binding.deal_id, comment)
        return {
            "ok": True,
            "deal_id": binding.deal_id,
            "entry_id": entry.id,
            "result": result,
            "hours": hours_result,
        }
    except BitrixAPIError as exc:
        # Allow retry only if the deal was not modified yet.
        if not deal_updated:
            TimeEntry.objects.filter(pk=entry.id, billed_to_deal_at=claimed_at).update(
                billed_to_deal_at=None
            )
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                return {"ok": False, "error": str(exc)}
        # Hours already deducted — keep claim; comment may be missing.
        return {"ok": True, "partial": True, "error": str(exc), "deal_id": binding.deal_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def sync_timer_to_bitrix(self, entry_id: int, action: str):
    """
    Mirror app timer onto Bitrix task «Учёт времени» (agency subtask).
    action: start | stop
    """
    from board.models import TimeEntry

    try:
        entry = TimeEntry.objects.select_related(
            "task",
            "task__project",
            "task__project__portal",
            "author",
        ).get(pk=entry_id)
    except TimeEntry.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    task = entry.task
    agency = _agency_portal_for_client(task.project.portal)
    bitrix_id = task.agency_bitrix_task_id or ""
    if not agency or not agency.access_token or not bitrix_id:
        # Ensure agency Bitrix task exists, then retry
        if agency and agency.access_token:
            try:
                parent_id, group_id = _ensure_project_agency_parent(task.project)
                bx = _sync_one_portal(
                    task,
                    agency,
                    existing_id=task.agency_bitrix_task_id or "",
                    group_id=group_id,
                    parent_id=parent_id,
                    crm_bindings=_crm_deal_uf_bindings(task.project.portal) or None,
                )
                if bx and bx != task.agency_bitrix_task_id:
                    task.agency_bitrix_task_id = bx
                    task.save(update_fields=["agency_bitrix_task_id", "updated_at"])
                    bitrix_id = bx
            except Exception as exc:
                try:
                    raise self.retry(exc=exc)
                except self.MaxRetriesExceededError:
                    return {"ok": False, "reason": "no_agency_task", "error": str(exc)}
        if not bitrix_id:
            return {"ok": False, "reason": "no_agency_task"}

    client = BitrixClient(agency)
    try:
        # Keep time tracking enabled on the Bitrix task
        try:
            client.update_task(bitrix_id, {"ALLOW_TIME_TRACKING": "Y"})
        except BitrixAPIError:
            pass

        if action == "start":
            # Status is applied by sync_task_to_bitrix — only drive Учёт времени here.
            client.start_task_timer(bitrix_id)
            return {"ok": True, "action": "start", "bitrix_task_id": bitrix_id}

        if action == "stop":
            paused_ok = False
            try:
                client.pause_task_timer(bitrix_id)
                paused_ok = True
            except BitrixAPIError as exc:
                logger = __import__("logging").getLogger(__name__)
                logger.info(
                    "pauseTimer failed task=%s: %s — will post elapseditem", bitrix_id, exc
                )

            seconds = int(entry.duration_seconds or 0)
            if seconds <= 0 and entry.ended_at and entry.started_at:
                seconds = max(0, int((entry.ended_at - entry.started_at).total_seconds()))
            # If live Bitrix timer couldn't be paused, post a closed elapsed record
            if (not paused_ok) and seconds > 0 and not entry.bitrix_elapsed_id:
                user_id = ""
                if entry.author and entry.author.portal_id == agency.id:
                    user_id = str(entry.author.bitrix_id or "")
                if not user_id:
                    user_id = _bitrix_user_id(client.get_current_user())
                result = client.add_elapsed_item(
                    bitrix_id,
                    seconds,
                    comment=f"Nextgen: {task.title}",
                    user_id=user_id or None,
                )
                eid = ""
                if isinstance(result, (int, float)):
                    eid = str(int(result))
                elif isinstance(result, str) and result.isdigit():
                    eid = result
                elif isinstance(result, dict):
                    for key in ("id", "ID", "result"):
                        val = result.get(key)
                        if isinstance(val, (int, float)):
                            eid = str(int(val))
                            break
                        if isinstance(val, str) and val.isdigit():
                            eid = val
                            break
                if eid:
                    entry.bitrix_elapsed_id = eid
                    entry.save(update_fields=["bitrix_elapsed_id", "updated_at"])
            # Do not pause_task here — status sync owns start/pause/complete.
            return {
                "ok": True,
                "action": "stop",
                "bitrix_task_id": bitrix_id,
                "seconds": seconds,
                "timer_paused": paused_ok,
                "elapsed_id": entry.bitrix_elapsed_id,
            }

        return {"ok": False, "reason": "unknown_action", "action": action}
    except BitrixAPIError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}


# Backwards-compatible alias (no longer used for hour deduction)
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def post_task_complete_to_deal(self, task_id: int):
    """Deprecated: hours are billed per TimeEntry. Kept as no-op for old queue messages."""
    return {"ok": True, "skipped": "deprecated_use_post_time_entry_to_deal", "task_id": task_id}
