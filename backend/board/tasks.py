from celery import shared_task
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

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


def _configured_default_responsible_id() -> str:
    """Stable agency user for client-originated tasks (not whoever last opened the app)."""
    return (
        (getattr(settings, "BITRIX_DEFAULT_RESPONSIBLE_ID", "") or "").strip()
        or (settings.BITRIX_CLIENT_TASK_AUTHOR_ID or "").strip()
    )


def _resolve_responsible_id(client: BitrixClient, task, portal) -> str:
    """
    Bitrix requires RESPONSIBLE_ID to be a user of the SAME portal the task is
    created on. A client-portal user id is meaningless on the agency portal (and
    vice-versa), so it must never leak across portals.

    We resolve, in order:
      1) the task author, only if they belong to *this* portal;
      2) BITRIX_DEFAULT_RESPONSIBLE_ID or BITRIX_CLIENT_TASK_AUTHOR_ID
         (stable pin for cross-portal / client-submitted tasks so RESPONSIBLE_ID
         does not follow whoever last opened the app);
      3) the acting OAuth user of *this* portal (installer token).
    Never pick a random stored admin by id.
    """
    if (
        task.created_by_id
        and task.created_by
        and task.created_by.portal_id == portal.id
        and task.created_by.bitrix_id
    ):
        return str(task.created_by.bitrix_id)

    configured = _configured_default_responsible_id()
    if configured:
        return configured

    current = client.get_current_user()
    uid = _bitrix_user_id(current)
    if uid:
        return uid
    return ""


def _oauth_user_label(client: BitrixClient) -> str:
    """Short label for the Bitrix user behind the app token (not the Nextgen clicker)."""
    try:
        me = client.get_current_user() or {}
    except BitrixAPIError:
        return "id=?"
    uid = _bitrix_user_id(me) or "?"
    name = " ".join(
        str(me.get(k) or "").strip()
        for k in ("NAME", "name", "LAST_NAME", "lastName")
        if me.get(k)
    ).strip()
    email = str(me.get("EMAIL") or me.get("email") or "").strip()
    if name and email:
        return f"id={uid} ({name}, {email})"
    if name:
        return f"id={uid} ({name})"
    if email:
        return f"id={uid} ({email})"
    return f"id={uid}"


def _task_invisible_hint(client: BitrixClient, bitrix_task_id: str, exc: BaseException) -> str:
    if "недоступна токену" not in str(exc).lower() and "пустой" not in str(exc).lower():
        return str(exc)
    who = _oauth_user_label(client)
    return (
        f"задача {bitrix_task_id} не видна пользователю токена приложения ({who}). "
        f"Это НЕ тот, кто нажал кнопку в Nextgen — все вызовы Bitrix идут от "
        f"пользователя, под которым установлено/авторизовано приложение. "
        f"Создайте задачу заново после обновления (постановщик в Bitrix будет он)"
    )


def _bitrix_access_denied(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "не разрешено",
            "не доступно",
            "недоступно",
            "нет доступа",
            "действие не",
            "недоступна токену",
            "access denied",
            "access_denied",
            "permission",
            "forbidden",
        )
    )


def _bitrix_action_unavailable(exc: BaseException) -> bool:
    """Start/complete rejected as N/A — often already in that state."""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "не разрешено",
            "не доступно",
            "недоступно",
            "действие не",
            "action is not available",
            "action not allowed",
        )
    )


def _claim_bitrix_task_for_oauth(client: BitrixClient, bitrix_task_id: str) -> str:
    """Make the OAuth app user responsible/accomplice so status actions work."""
    uid = _bitrix_user_id(client.get_current_user())
    if not uid:
        return ""
    try:
        client.update_task(
            bitrix_task_id,
            {"RESPONSIBLE_ID": uid, "ACCOMPLICES": [uid]},
        )
    except BitrixAPIError:
        try:
            client.update_task(bitrix_task_id, {"ACCOMPLICES": [uid]})
        except BitrixAPIError as exc:
            logger.info(
                "claim task %s for oauth %s failed: %s", bitrix_task_id, uid, exc
            )
    return uid


def _set_bitrix_status_field(
    client: BitrixClient, bitrix_task_id: str, status_code: int
) -> bool:
    """Fallback when start/complete are forbidden. Returns True if update stuck."""
    try:
        client.update_task(bitrix_task_id, {"STATUS": status_code})
        return True
    except BitrixAPIError:
        _claim_bitrix_task_for_oauth(client, bitrix_task_id)
        try:
            client.update_task(bitrix_task_id, {"STATUS": status_code})
            return True
        except BitrixAPIError as exc:
            logger.info(
                "STATUS=%s update failed for %s: %s",
                status_code,
                bitrix_task_id,
                exc,
            )
            return False


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
    creator_id: str | None = None,
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
    if creator_id:
        fields["CREATED_BY"] = creator_id
    if group_id:
        fields["GROUP_ID"] = group_id
    if parent_id:
        fields["PARENT_ID"] = parent_id
    # Enable «Учёт времени» so task.elapseditem.add works when the user enters
    # time in the app. We never start the Bitrix live stopwatch from Nextgen.
    fields["ALLOW_TIME_TRACKING"] = "Y"
    # Bitrix PRIORITY: 2 = High («важная»), 1 = Normal. Mirror the local flag.
    fields["PRIORITY"] = "2" if getattr(task, "is_important", False) else "1"
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


def _stop_bitrix_timer_quiet(client: BitrixClient, bitrix_task_id: str) -> None:
    """Best-effort stop of the Bitrix «Учёт времени» timer.

    Pausing/completing a task whose day-plan timer is still running does not
    stick in Bitrix — the live timer keeps the task "in progress". Stop it as
    part of the status transition so pause/complete are not reverted.
    """
    try:
        client.pause_task_timer(bitrix_task_id)
    except BitrixAPIError as exc:
        logger.info("pauseTimer during status change failed task=%s: %s", bitrix_task_id, exc)
    except Exception as exc:  # never block a status change on timer noise
        logger.info("pauseTimer during status change error task=%s: %s", bitrix_task_id, exc)


def _read_bitrix_status(
    client: BitrixClient, bitrix_task_id: str, *payloads
) -> int | None:
    """Read status from action payloads and/or a fresh tasks.task.get."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        code = bitrix_status_code(payload)
        if code is not None:
            return code
    try:
        data = client.get_task(bitrix_task_id) or {}
    except BitrixAPIError as exc:
        # Access denied / empty get must surface — not look like status=None.
        if _bitrix_access_denied(exc) or "недоступна токену" in str(exc).lower():
            raise
        logger.info("get_task after status action failed id=%s: %s", bitrix_task_id, exc)
        return None
    code = bitrix_status_code(data)
    if code is None and data:
        logger.warning(
            "Bitrix task %s has no parseable status; keys=%s",
            bitrix_task_id,
            sorted(str(k) for k in data.keys())[:40],
        )
    return code


def apply_bitrix_status(client: BitrixClient, bitrix_task_id: str, target_local: str) -> None:
    """Push start/complete to Bitrix; pause remains local to each system."""
    target = _normalize_local(target_local)

    # App pause must not pause Bitrix.
    if target == "todo":
        return

    task_data = client.get_task(bitrix_task_id)
    current = bitrix_status_code(task_data)
    status_known = current is not None
    if current is None:
        # Do not invent PENDING forever — if get omits status we still try start,
        # but failures are handled differently below.
        current = BITRIX_STATUS_PENDING
        logger.warning(
            "Bitrix task %s status unknown before action; keys=%s",
            bitrix_task_id,
            sorted(str(k) for k in (task_data or {}).keys())[:40],
        )

    if target == "done":
        if current in (BITRIX_STATUS_COMPLETED, BITRIX_STATUS_SUPPOSEDLY_COMPLETED):
            return
        # Stop live «Учёт времени» — otherwise complete() often does not stick
        # and Bitrix stays in status 3 while the app marks sync as SYNCED.
        _stop_bitrix_timer_quiet(client, bitrix_task_id)
        if current in (BITRIX_STATUS_PENDING, BITRIX_STATUS_DEFERRED):
            try:
                client.start_task(bitrix_task_id)
            except BitrixAPIError:
                pass
        elif current == BITRIX_STATUS_IN_PROGRESS:
            # Some portals keep the task "running" until an explicit pause.
            try:
                client.pause_task(bitrix_task_id)
            except BitrixAPIError:
                pass
            _stop_bitrix_timer_quiet(client, bitrix_task_id)

        complete_ok = False
        complete_result = None
        try:
            complete_result = client.complete_task(bitrix_task_id)
            complete_ok = True
        except BitrixAPIError as exc:
            logger.info(
                "complete failed for %s (%s) — claim+STATUS fallback",
                bitrix_task_id,
                exc,
            )
            _claim_bitrix_task_for_oauth(client, bitrix_task_id)
            try:
                complete_result = client.complete_task(bitrix_task_id)
                complete_ok = True
            except BitrixAPIError:
                complete_ok = _set_bitrix_status_field(
                    client, bitrix_task_id, BITRIX_STATUS_COMPLETED
                )

        after = _read_bitrix_status(client, bitrix_task_id, complete_result)
        if after in (BITRIX_STATUS_COMPLETED, BITRIX_STATUS_SUPPOSEDLY_COMPLETED):
            return
        if after is None and complete_ok:
            # Bitrix accepted complete/STATUS but get_task omitted status fields.
            logger.warning(
                "complete accepted for %s but status unreadable — treating as done",
                bitrix_task_id,
            )
            return
        if after not in (BITRIX_STATUS_COMPLETED, BITRIX_STATUS_SUPPOSEDLY_COMPLETED):
            _stop_bitrix_timer_quiet(client, bitrix_task_id)
            try:
                client.pause_task(bitrix_task_id)
            except BitrixAPIError:
                pass
            try:
                complete_result = client.complete_task(bitrix_task_id)
                complete_ok = True
            except BitrixAPIError:
                _claim_bitrix_task_for_oauth(client, bitrix_task_id)
                complete_ok = _set_bitrix_status_field(
                    client, bitrix_task_id, BITRIX_STATUS_COMPLETED
                )
            after = _read_bitrix_status(client, bitrix_task_id, complete_result)
        if after in (BITRIX_STATUS_COMPLETED, BITRIX_STATUS_SUPPOSEDLY_COMPLETED):
            return
        if after is None and complete_ok:
            logger.warning(
                "complete accepted for %s but status unreadable — treating as done",
                bitrix_task_id,
            )
            return
        raise BitrixAPIError(
            f"Bitrix задача {bitrix_task_id} не завершилась (status={after})"
        )

    # target == in_progress — start once; noop if already running.
    if current == BITRIX_STATUS_IN_PROGRESS:
        return
    if current in (BITRIX_STATUS_COMPLETED, BITRIX_STATUS_SUPPOSEDLY_COMPLETED):
        raise BitrixAPIError("Завершённую задачу нельзя возобновить")

    start_ok = False
    start_result = None
    last_err: BitrixAPIError | None = None
    try:
        start_result = client.start_task(bitrix_task_id)
        start_ok = True
    except BitrixAPIError as exc:
        last_err = exc
        logger.info(
            "start failed for %s (%s) — claim+STATUS fallback",
            bitrix_task_id,
            exc,
        )
        _claim_bitrix_task_for_oauth(client, bitrix_task_id)
        try:
            start_result = client.start_task(bitrix_task_id)
            start_ok = True
            last_err = None
        except BitrixAPIError as exc2:
            last_err = exc2
            start_ok = _set_bitrix_status_field(
                client, bitrix_task_id, BITRIX_STATUS_IN_PROGRESS
            )
            if start_ok:
                last_err = None

    after = _read_bitrix_status(client, bitrix_task_id, start_result)
    if after == BITRIX_STATUS_IN_PROGRESS:
        return
    if after is None and start_ok:
        logger.warning(
            "start accepted for %s but status unreadable — treating as in_progress",
            bitrix_task_id,
        )
        return
    if after != BITRIX_STATUS_IN_PROGRESS:
        try:
            start_result = client.start_task(bitrix_task_id)
            start_ok = True
            last_err = None
        except BitrixAPIError as exc:
            last_err = exc
            _claim_bitrix_task_for_oauth(client, bitrix_task_id)
            start_ok = _set_bitrix_status_field(
                client, bitrix_task_id, BITRIX_STATUS_IN_PROGRESS
            )
            if start_ok:
                last_err = None
        after = _read_bitrix_status(client, bitrix_task_id, start_result)
    if after == BITRIX_STATUS_IN_PROGRESS:
        return
    if after is None and start_ok:
        logger.warning(
            "start accepted for %s but status unreadable — treating as in_progress",
            bitrix_task_id,
        )
        return
    # After all fallbacks: unreadable status + "action N/A" ≈ already running.
    if (
        after is None
        and not status_known
        and last_err is not None
        and _bitrix_action_unavailable(last_err)
    ):
        logger.warning(
            "start unavailable for %s while status unknown (%s) — accepting",
            bitrix_task_id,
            last_err,
        )
        return
    detail = f"status={after}"
    if last_err:
        detail = f"{detail}; bitrix: {last_err}"
    raise BitrixAPIError(f"Bitrix задача {bitrix_task_id} не началась ({detail})")


def _ensure_project_agency_parent(project) -> tuple[str, str]:
    """
    Ensure the app Project has an agency Bitrix parent task in the company GROUP.
    Returns (bitrix_task_id, group_id).
    """
    from portals.deal_resolve import resolve_bitrix_group_id

    agency = _agency_portal_for_client(project.portal)
    if not agency:
        raise BitrixAPIError("Клиент не привязан к агентству")

    # Revalidate against CRM company (deal when bound, else portal-link) on every
    # outbound sync. A persisted Project.group_id may belong to a previous binding.
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
    create_only: bool = False,
) -> str:
    """Create/update Bitrix task on a portal; return bitrix task id.

    ``create_only=True`` stops right after tasks.task.add so the caller can
    commit ``agency_bitrix_task_id`` before RESPONSIBLE/status updates (those
    fire OnTaskUpdate and must not race an unbound local row).
    """
    if not portal.access_token:
        raise BitrixAPIError(f"Нет токена Bitrix у портала {portal.domain or portal.id}")

    client = BitrixClient(portal)
    responsible_id = _resolve_responsible_id(client, task, portal)
    if not responsible_id and not existing_id:
        raise BitrixAPIError(
            f"Не указан исполнитель на {portal.domain}: задайте "
            "BITRIX_DEFAULT_RESPONSIBLE_ID или BITRIX_CLIENT_TASK_AUTHOR_ID, "
            "либо откройте приложение на этом портале и сохраните задачу снова"
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
        logger.info(
            "sync→bitrix update task=%s portal=%s bitrix_id=%s priority=%s important=%s",
            task.id,
            portal.id,
            existing_id,
            fields.get("PRIORITY"),
            getattr(task, "is_important", None),
        )
        try:
            client.update_task(existing_id, fields)
        except BitrixAPIError as exc:
            # Still try status push — update may fail on legacy tasks with a
            # foreign CREATED_BY while STATUS/start can succeed (or vice versa).
            if not _bitrix_access_denied(exc):
                raise
            logger.info(
                "update forbidden task=%s bitrix=%s (%s) — status-only",
                task.id,
                existing_id,
                exc,
            )
        # After create_only phase, reassign away from OAuth user if needed.
        oauth_uid = _bitrix_user_id(client.get_current_user())
        if (
            responsible_id
            and oauth_uid
            and responsible_id != oauth_uid
        ):
            try:
                client.update_task(existing_id, {"RESPONSIBLE_ID": responsible_id})
            except BitrixAPIError as exc:
                logger.info(
                    "reassign RESPONSIBLE to %s failed bitrix=%s (%s) — keep oauth=%s",
                    responsible_id,
                    existing_id,
                    exc,
                    oauth_uid,
                )
        try:
            apply_bitrix_status(client, existing_id, task.status)
        except BitrixAPIError as exc:
            hint = _task_invisible_hint(client, existing_id, exc)
            raise BitrixAPIError(f"не удалось сменить статус в Bitrix: {hint}") from exc
        return existing_id

    # Create under the OAuth token user so the app can always get/start/complete.
    # Then try to reassign RESPONSIBLE to the human author (Nextgen clicker).
    # Sending a foreign CREATED_BY / creating only for another responsible often
    # yields empty tasks.task.get for the token («задача не видна»).
    oauth_uid = _bitrix_user_id(client.get_current_user())
    desired_responsible = responsible_id
    create_responsible = oauth_uid or desired_responsible
    fields = _task_fields(
        task,
        responsible_id=create_responsible,
        creator_id=None,
        group_id=group_id,
        parent_id=parent_id,
        include_deadline=True,
        crm_bindings=crm_bindings,
    )
    fields["TITLE"] = title
    result = client.create_task(fields)
    bitrix_id = _extract_bitrix_id(result)
    if not bitrix_id:
        raise BitrixAPIError("Bitrix не вернул id созданной задачи")

    if create_only:
        return bitrix_id

    try:
        client.get_task(bitrix_id)
    except BitrixAPIError as exc:
        raise BitrixAPIError(_task_invisible_hint(client, bitrix_id, exc)) from exc

    if (
        desired_responsible
        and oauth_uid
        and desired_responsible != oauth_uid
    ):
        try:
            client.update_task(bitrix_id, {"RESPONSIBLE_ID": desired_responsible})
        except BitrixAPIError as exc:
            logger.info(
                "reassign RESPONSIBLE to %s failed bitrix=%s (%s) — keep oauth=%s",
                desired_responsible,
                bitrix_id,
                exc,
                oauth_uid,
            )

    if task.status != "todo":
        try:
            apply_bitrix_status(client, bitrix_id, task.status)
        except BitrixAPIError as exc:
            hint = _task_invisible_hint(client, bitrix_id, exc)
            raise BitrixAPIError(f"задача создана, но статус не применён: {hint}") from exc
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
    responsible = _configured_default_responsible_id()
    if not responsible:
        responsible = _bitrix_user_id(client.get_current_user())
    if not responsible and not project.bitrix_task_id:
        raise BitrixAPIError(
            f"Не указан исполнитель на {agency.domain}: задайте "
            "BITRIX_DEFAULT_RESPONSIBLE_ID или BITRIX_CLIENT_TASK_AUTHOR_ID, "
            "либо откройте приложение на портале агентства"
        )

    fields = {
        "TITLE": project.name,
        "DESCRIPTION": project.description or "",
        "GROUP_ID": group_id,
        "ALLOW_TIME_TRACKING": "N",
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


def _sync_task_locked(task) -> dict:
    """Create/update the agency Bitrix subtask for a task.

    Client Bitrix «Задачи» are intentionally NOT created/updated — clients see
    work only in the Nextgen app. Agency portal gets the subtask under the
    company project (PARENT_ID + GROUP_ID + CRM deal binding).

    Assumes the caller holds a row lock (select_for_update) on this Task.
    """
    from board.models import Task

    client_portal = task.project.portal
    errors: list[str] = []
    update_fields = ["sync_status", "sync_error", "updated_at"]

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
    elif not agency:
        # No PortalLink — nothing to push; keep pending reason visible.
        errors.append("агентство: клиент не привязан к агентству")

    if errors:
        task.sync_status = Task.SyncStatus.ERROR
        task.sync_error = "; ".join(errors)
        task.save(update_fields=list(set(update_fields)))
        return {
            "errors": True,
            "error": task.sync_error,
            "partial_ids": {
                "bitrix_task_id": task.bitrix_task_id,
                "agency_bitrix_task_id": task.agency_bitrix_task_id,
            },
        }

    task.sync_status = Task.SyncStatus.SYNCED
    task.sync_error = ""
    task.save(update_fields=list(set(update_fields)))
    return {"errors": False}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_task_to_bitrix(self, task_id: int):
    """
    Sync task to agency Bitrix as a subtask under the Project parent.

    Two-phase so OnTaskAdd/OnTaskUpdate cannot invent duplicate locals:
      1) create Bitrix row (if needed) and *commit* agency_bitrix_task_id
      2) push fields / RESPONSIBLE / status (webhooks now find the same row)
    """
    from django.db import transaction

    from board.models import Task

    def _locked_task():
        return (
            Task.objects.select_for_update(of=("self",))
            .select_related("project", "project__portal", "created_by")
            .get(pk=task_id)
        )

    try:
        # Phase 1 — create + commit id before webhook-triggering updates.
        with transaction.atomic():
            try:
                task = _locked_task()
            except Task.DoesNotExist:
                return {"ok": False, "reason": "missing"}
            client_portal = task.project.portal
            agency = _agency_portal_for_client(client_portal)
            if (
                agency
                and agency.id != client_portal.id
                and not (task.agency_bitrix_task_id or "").strip()
            ):
                parent_id, group_id = _ensure_project_agency_parent(task.project)
                crm_bindings = _crm_deal_uf_bindings(client_portal)
                new_id = _sync_one_portal(
                    task,
                    agency,
                    existing_id="",
                    group_id=group_id,
                    parent_id=parent_id,
                    crm_bindings=crm_bindings or None,
                    create_only=True,
                )
                task.agency_bitrix_task_id = new_id
                task.save(update_fields=["agency_bitrix_task_id", "updated_at"])

        # Phase 2 — full field/status sync (id already visible to other txs).
        with transaction.atomic():
            try:
                task = _locked_task()
            except Task.DoesNotExist:
                return {"ok": False, "reason": "missing"}
            outcome = _sync_task_locked(task)
    except BitrixAPIError as exc:
        logger.info("sync_task_to_bitrix bitrix error task=%s: %s", task_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}
    except Exception as exc:
        # Unexpected failure around the locked section — let Celery retry.
        logger.exception("sync_task_to_bitrix crashed task=%s", task_id)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}

    # Ids are committed at this point; a retry will UPDATE, never re-create.
    if outcome.get("errors"):
        # Direct calls (manage.py shell) must not explode on Celery Retry.
        called_directly = bool(getattr(self.request, "called_directly", False))
        if called_directly:
            return {
                "ok": False,
                "error": outcome["error"],
                "partial_ids": outcome.get("partial_ids"),
            }
        try:
            raise self.retry(exc=BitrixAPIError(outcome["error"]))
        except self.MaxRetriesExceededError:
            return {
                "ok": False,
                "error": outcome["error"],
                "partial_ids": outcome.get("partial_ids"),
            }

    try:
        from board.realtime import publish_task_event

        publish_task_event(task, kind="task_synced")
    except Exception:
        pass

    # Catch up elapsed rows that raced create / failed earlier.
    try:
        from board.timeutils import enqueue_unsynced_elapsed_for_task

        enqueue_unsynced_elapsed_for_task(task)
    except Exception:
        logger.info("enqueue pending elapsed after task sync failed task=%s", task_id)

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

    # System lines stay app-local, except the completion-duration announcement
    # which must also appear in the Bitrix task chat.
    if comment.is_system:
        from board.completion import is_completion_time_message

        if not is_completion_time_message(comment.text or ""):
            return {"ok": True, "skipped": "system"}

    author_name = comment.author_name or (
        comment.author.display_name if comment.author else "Участник"
    )
    body = (comment.text or "").strip()
    # File-only comments: Bitrix chat message is created by sync_attachment_to_bitrix
    if not body:
        return {"ok": True, "skipped": "empty_text"}
    # Bitrix already renders the author profile next to the message.
    message = body
    if not message:
        return {"ok": False, "reason": "empty"}

    task = comment.task
    # Comments go only to the agency Bitrix subtask — we do not create/mirror
    # tasks on the client Bitrix portal.
    targets: list[tuple] = []
    client_portal = task.project.portal
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
            author_id = None
            if (
                comment.author_id
                and comment.author
                and comment.author.portal_id != portal.id
            ):
                # A client-portal user cannot be an author on the agency
                # portal. Post through the dedicated agency employee instead.
                author_id = (settings.BITRIX_CLIENT_TASK_AUTHOR_ID or "").strip() or None
            result = BitrixClient(portal).add_task_comment(
                bitrix_task_id,
                message,
                author_id=author_id,
            )
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
                saved_ids["agency_bitrix_comment_id"] = cid
        except BitrixAPIError as exc:
            errors.append(f"{portal.domain}: {exc}")

    if saved_ids:
        update_fields = []
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

    # Client Bitrix tasks are not created/updated — files go only to agency.

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


@shared_task(bind=True, max_retries=1, default_retry_delay=15)
def pull_task_from_bitrix(
    self,
    task_id: int,
    *,
    include_status: bool = True,
    include_comments: bool = True,
    include_files: bool = False,
):
    """Background Bitrix catch-up; never hold an interactive HTTP request."""
    from board.models import Task
    from board.realtime import publish_task_event

    task = Task.objects.select_related("project").filter(pk=task_id).first()
    if not task:
        return {"ok": False, "reason": "task_not_found"}

    changed = False
    try:
        if include_status:
            from board.status_sync import pull_task_status_from_bitrix

            changed = bool(pull_task_status_from_bitrix(task)) or changed
        if include_comments:
            from board.comment_sync import pull_comments_from_bitrix

            changed = bool(pull_comments_from_bitrix(task)) or changed
        if include_files:
            from board.file_sync import pull_attachments_from_bitrix

            changed = bool(pull_attachments_from_bitrix(task)) or changed
    except Exception as exc:
        raise self.retry(exc=exc)

    publish_task_event(task, kind="task_pull_complete")
    return {"ok": True, "changed": changed, "task_id": task_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def post_time_entry_to_deal(self, entry_id: int):
    """
    Deduct a closed time session from the accompaniment deal remaining hours.
    Idempotent via atomic claim on TimeEntry.billed_to_deal_at.
    """
    from django.db import transaction
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
            # Serialize the remaining-hours read-modify-write for this client.
            # Without this, two sessions on the same deal (or a concurrent credit
            # transfer) both read the same remaining value and the second write
            # clobbers the first — silently losing billed hours (double-spend).
            # We lock the same PortalLink row the credit logic locks, so ALL
            # remaining-hours mutations for a client↔agency pair are ordered.
            with transaction.atomic():
                PortalLink.objects.select_for_update().filter(pk=link.pk).first()
                deal = client.get_deal(binding.deal_id)
                new_remaining, spent = compute_remaining_after_spend(deal, seconds)
                if new_remaining is not None:
                    client.update_deal(
                        binding.deal_id, remaining_update_fields(new_remaining)
                    )
                    deal_updated = True
                    hours = read_deal_hours(deal)
                    paid = hours.paid
                    binding.paid_hours = paid
                    binding.remaining_hours = new_remaining
                    binding.save(
                        update_fields=["paid_hours", "remaining_hours", "updated_at"]
                    )
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


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def move_deal_stage_task(self, portal_id: int, stage_key: str):
    """Background: move accompaniment deal stage after report send/accept."""
    from portals.deal_stage_move import move_client_deal_stage

    try:
        return move_client_deal_stage(int(portal_id), str(stage_key))
    except Exception as exc:
        logger = __import__("logging").getLogger(__name__)
        logger.exception(
            "move_deal_stage_task failed portal=%s key=%s", portal_id, stage_key
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}


def _extract_elapsed_id(result) -> str:
    if isinstance(result, (int, float)):
        return str(int(result))
    if isinstance(result, str):
        return result if result.isdigit() else ""
    if isinstance(result, dict):
        for key in ("id", "ID", "result"):
            value = result.get(key)
            if isinstance(value, (int, float)):
                return str(int(value))
            if isinstance(value, str) and value.isdigit():
                return value
    return ""


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_completion_time_to_bitrix(self, task_id: int):
    """Deprecated no-op: each manual TimeEntry is pushed via sync_timer_to_bitrix."""
    return {"ok": True, "skipped": "per_entry_sync", "task_id": task_id}


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def cleanup_bitrix_elapsed_items(self, task_id: int, elapsed_ids: list):
    """Delete stale Bitrix elapsed rows after absolute time was rewritten."""
    from board.models import Task

    try:
        task = Task.objects.select_related("project", "project__portal").get(pk=task_id)
    except Task.DoesNotExist:
        return {"ok": False, "reason": "missing"}
    agency = _agency_portal_for_client(task.project.portal)
    bitrix_id = str(task.agency_bitrix_task_id or "")
    if not agency or not agency.access_token or not bitrix_id:
        return {"ok": False, "reason": "no_bitrix"}
    client = BitrixClient(agency)
    deleted = 0
    for elapsed_id in elapsed_ids or []:
        if not elapsed_id:
            continue
        try:
            client.delete_elapsed_item(bitrix_id, elapsed_id)
            deleted += 1
        except BitrixAPIError as exc:
            logger.info(
                "elapseditem.delete failed task=%s bitrix=%s item=%s: %s",
                task_id,
                bitrix_id,
                elapsed_id,
                exc,
            )
    return {"ok": True, "deleted": deleted}


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def sync_timer_to_bitrix(self, entry_id: int, action: str = "set"):
    """Push absolute TimeEntry duration into Bitrix «Учёт времени»."""
    from django.db import transaction

    from board.models import TimeEntry

    _ = action
    try:
        with transaction.atomic():
            entry = (
                TimeEntry.objects.select_for_update()
                .select_related(
                    "task",
                    "task__project",
                    "task__project__portal",
                    "author",
                )
                .get(pk=entry_id)
            )

            seconds = int(entry.duration_seconds or 0)
            if seconds <= 0 or entry.ended_at is None:
                return {"ok": True, "skipped": "not_closed", "seconds": seconds}

            task = entry.task
            agency = _agency_portal_for_client(task.project.portal)
            bitrix_id = str(task.agency_bitrix_task_id or "")
            if not agency or not agency.access_token:
                raise BitrixAPIError("Нет связанной задачи на портале агентства")
            if not bitrix_id:
                raise BitrixAPIError("Нет agency_bitrix_task_id — задача ещё не в Bitrix")

            client = BitrixClient(agency)
            try:
                client.update_task(bitrix_id, {"ALLOW_TIME_TRACKING": "Y"})
            except BitrixAPIError as exc:
                logger.info(
                    "ALLOW_TIME_TRACKING=Y failed task=%s bitrix=%s: %s",
                    task.id,
                    bitrix_id,
                    exc,
                )

            # Absolute set: update existing Bitrix row when we already posted one.
            if entry.bitrix_elapsed_id:
                try:
                    client.update_elapsed_item(
                        bitrix_id,
                        entry.bitrix_elapsed_id,
                        seconds,
                        comment=entry.note or "",
                    )
                    return {
                        "ok": True,
                        "updated": True,
                        "seconds": seconds,
                        "elapsed_id": entry.bitrix_elapsed_id,
                        "bitrix_task_id": bitrix_id,
                    }
                except BitrixAPIError as exc:
                    logger.info(
                        "elapseditem.update failed task=%s item=%s: %s — re-add",
                        task.id,
                        entry.bitrix_elapsed_id,
                        exc,
                    )
                    try:
                        client.delete_elapsed_item(bitrix_id, entry.bitrix_elapsed_id)
                    except BitrixAPIError:
                        pass
                    entry.bitrix_elapsed_id = ""
                    entry.save(update_fields=["bitrix_elapsed_id", "updated_at"])

            candidates: list[str | None] = []
            # Prefer the pinned service/installer user — matches Portal OAuth
            # and avoids "Действие не разрешено" when a random employee last logged in.
            configured = _configured_default_responsible_id()
            if configured:
                candidates.append(configured)
            oauth_uid = _bitrix_user_id(client.get_current_user()) or None
            if oauth_uid and oauth_uid not in candidates:
                candidates.append(oauth_uid)
            candidates.append(None)
            if entry.author_id and getattr(entry.author, "bitrix_id", None):
                if entry.author.portal_id == agency.id:
                    author_uid = str(entry.author.bitrix_id)
                    if author_uid not in candidates:
                        candidates.append(author_uid)

            result = None
            last_exc: BitrixAPIError | None = None
            for user_id in candidates:
                try:
                    result = client.add_elapsed_item(
                        bitrix_id,
                        seconds,
                        comment=entry.note or "",
                        user_id=user_id,
                    )
                    last_exc = None
                    break
                except BitrixAPIError as exc:
                    last_exc = exc
                    logger.info(
                        "elapseditem.add failed task=%s bitrix=%s user=%s: %s",
                        task.id,
                        bitrix_id,
                        user_id,
                        exc,
                    )
            if result is None:
                raise last_exc or BitrixAPIError("Не удалось добавить учёт времени")
            elapsed_id = _extract_elapsed_id(result)
            if not elapsed_id and result not in (None, "", {}, []):
                elapsed_id = str(result)[:64] if not isinstance(result, dict) else "ok"
            if elapsed_id:
                entry.bitrix_elapsed_id = elapsed_id
                entry.save(update_fields=["bitrix_elapsed_id", "updated_at"])
            return {
                "ok": True,
                "seconds": seconds,
                "elapsed_id": elapsed_id,
                "bitrix_task_id": bitrix_id,
            }
    except TimeEntry.DoesNotExist:
        return {"ok": False, "reason": "missing"}
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


@shared_task(bind=True, max_retries=2, default_retry_delay=20)
def ensure_portal_event_bindings(self, portal_id: int):
    """
    Subscribe portal to Bitrix task events in the background.

    Must NOT run inside /api/bitrix/auth/ — event.get/bind can take minutes and
    blocks Bitrix iframe boot («Идет загрузка приложения»).
    """
    from portals.models import Portal
    from board.status_sync import ensure_task_event_bindings

    try:
        portal = Portal.objects.get(pk=portal_id)
    except Portal.DoesNotExist:
        return {"ok": False, "reason": "missing"}
    try:
        ok = ensure_task_event_bindings(portal)
        return {"ok": bool(ok), "portal_id": portal_id}
    except BitrixAPIError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc), "portal_id": portal_id}
