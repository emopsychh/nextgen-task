from datetime import datetime, time

from celery import shared_task

from portals.bitrix import (
    BITRIX_STATUS_COMPLETED,
    BITRIX_STATUS_DEFERRED,
    BITRIX_STATUS_IN_PROGRESS,
    BITRIX_STATUS_PENDING,
    BITRIX_STATUS_SUPPOSEDLY_COMPLETED,
    BitrixAPIError,
    BitrixClient,
    parse_bitrix_status,
)


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


def _task_fields(task, *, responsible_id: str | None = None) -> dict:
    fields = {
        "TITLE": task.title,
        "DESCRIPTION": task.description or "",
    }
    if due := task.due_date:
        fields["DEADLINE"] = datetime.combine(due, time(23, 59, 59)).strftime(
            "%Y-%m-%dT23:59:59+00:00"
        )
    if responsible_id:
        fields["RESPONSIBLE_ID"] = responsible_id
        fields["CREATED_BY"] = responsible_id
    return fields


def _normalize_local(status: str) -> str:
    return status if status in ("todo", "in_progress", "done") else "todo"


def apply_bitrix_status(client: BitrixClient, bitrix_task_id: str, target_local: str) -> None:
    """Bring Bitrix task to the local status using official action methods."""
    target = _normalize_local(target_local)
    task_data = client.get_task(bitrix_task_id)
    current = parse_bitrix_status(task_data.get("status") or task_data.get("STATUS"))
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
        current = BITRIX_STATUS_PENDING

    if target == "todo":
        if current == BITRIX_STATUS_IN_PROGRESS:
            client.pause_task(bitrix_task_id)
        return

    if target == "in_progress":
        client.start_task(bitrix_task_id)
        return

    # target == done
    if current in (BITRIX_STATUS_PENDING, BITRIX_STATUS_DEFERRED):
        try:
            client.start_task(bitrix_task_id)
        except BitrixAPIError:
            pass
    client.complete_task(bitrix_task_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_task_to_bitrix(self, task_id: int):
    from board.models import Task

    try:
        task = Task.objects.select_related(
            "project", "project__portal", "created_by"
        ).get(pk=task_id)
    except Task.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    portal = task.project.portal
    if not portal.access_token:
        task.sync_status = Task.SyncStatus.SKIPPED
        task.sync_error = "Portal has no Bitrix access token"
        task.save(update_fields=["sync_status", "sync_error", "updated_at"])
        return {"ok": False, "reason": "no_token"}

    client = BitrixClient(portal)
    try:
        responsible_id = _resolve_responsible_id(client, task)
        if not responsible_id and not task.bitrix_task_id:
            raise BitrixAPIError(
                "Не указан исполнитель: откройте приложение на портале клиента, "
                "чтобы обновить токен, и повторите сохранение задачи"
            )

        if task.bitrix_task_id:
            fields = _task_fields(task)
            client.update_task(task.bitrix_task_id, fields)
            apply_bitrix_status(client, task.bitrix_task_id, task.status)
        else:
            fields = _task_fields(task, responsible_id=responsible_id)
            result = client.create_task(fields)
            bitrix_id = _extract_bitrix_id(result)
            task.bitrix_task_id = bitrix_id
            if bitrix_id and task.status != Task.Status.TODO:
                apply_bitrix_status(client, bitrix_id, task.status)

        task.sync_status = Task.SyncStatus.SYNCED
        task.sync_error = ""
        task.save(
            update_fields=["bitrix_task_id", "sync_status", "sync_error", "updated_at"]
        )
        return {"ok": True, "bitrix_task_id": task.bitrix_task_id}
    except BitrixAPIError as exc:
        task.sync_status = Task.SyncStatus.ERROR
        task.sync_error = str(exc)
        task.save(update_fields=["sync_status", "sync_error", "updated_at"])
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}
    except Exception as exc:
        task.sync_status = Task.SyncStatus.ERROR
        task.sync_error = str(exc)
        task.save(update_fields=["sync_status", "sync_error", "updated_at"])
        raise


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def post_task_complete_to_deal(self, task_id: int):
    """Post timeline comment and decrement remaining hours on the linked CRM deal."""
    from board.models import Task
    from board.timeutils import format_duration_ru, task_tracked_seconds
    from portals.deal_hours import (
        compute_remaining_after_spend,
        hours_fields_configured,
        read_deal_hours,
        remaining_update_fields,
    )
    from portals.models import PortalDealBinding, PortalLink

    try:
        task = Task.objects.select_related("project", "project__portal").get(pk=task_id)
    except Task.DoesNotExist:
        return {"ok": False, "reason": "missing"}

    client_portal = task.project.portal
    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    if not link:
        return {"ok": False, "reason": "no_agency_link"}

    agency = link.agency_portal
    binding = (
        PortalDealBinding.objects.filter(
            agency_portal=agency,
            client_portal=client_portal,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
    if not binding:
        return {"ok": False, "reason": "no_deal_binding"}

    if not agency.access_token:
        return {"ok": False, "reason": "no_agency_token"}

    seconds = task_tracked_seconds(task)
    duration = format_duration_ru(seconds) if seconds > 0 else "не указано"
    comment = f"Закрыта задача «{task.title}»: затрачено {duration}"

    client = BitrixClient(agency)
    try:
        hours_result = None
        if hours_fields_configured() and seconds > 0:
            deal = client.get_deal(binding.deal_id)
            new_remaining, spent = compute_remaining_after_spend(deal, seconds)
            if new_remaining is not None:
                client.update_deal(binding.deal_id, remaining_update_fields(new_remaining))
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
            "result": result,
            "hours": hours_result,
        }
    except BitrixAPIError as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"ok": False, "error": str(exc)}
