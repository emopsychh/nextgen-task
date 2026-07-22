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
    bitrix_status_code,
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


def _agency_portal_for_client(client_portal):
    from portals.models import PortalLink

    link = (
        PortalLink.objects.filter(client_portal=client_portal)
        .select_related("agency_portal")
        .first()
    )
    return link.agency_portal if link else None


def _sync_one_portal(task, portal, *, existing_id: str, title_prefix: str = "") -> str:
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

    title = task.title
    if title_prefix:
        title = f"{title_prefix}{task.title}"

    if existing_id:
        fields = _task_fields(task)
        fields["TITLE"] = title
        client.update_task(existing_id, fields)
        try:
            apply_bitrix_status(client, existing_id, task.status)
        except BitrixAPIError as exc:
            raise BitrixAPIError(f"не удалось сменить статус в Bitrix: {exc}") from exc
        return existing_id

    fields = _task_fields(task, responsible_id=responsible_id)
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
def sync_task_to_bitrix(self, task_id: int):
    """Sync task into client Bitrix Tasks and (if linked) agency Bitrix Tasks."""
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

    # 1) Client portal (project owner)
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

    # 2) Agency portal copy (native Tasks on agency Bitrix)
    agency = _agency_portal_for_client(client_portal)
    if agency and agency.id != client_portal.id:
        try:
            prefix = f"[{client_portal.name or client_portal.domain}] "
            agency_id = _sync_one_portal(
                task,
                agency,
                existing_id=task.agency_bitrix_task_id or "",
                title_prefix=prefix,
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

    if comment.is_system:
        author_name = comment.author_name or (
            comment.author.display_name if comment.author else "Система"
        )
        message = f"{author_name} {comment.text}".strip()
    else:
        author_name = comment.author_name or (
            comment.author.display_name if comment.author else "Участник"
        )
        message = f"{author_name}: {comment.text}".strip()
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
    return {"ok": True, "posted": len(targets) - len(errors), "ids": saved_ids}


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
    if not binding and link.bitrix_company_id:
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


# Backwards-compatible alias (no longer used for hour deduction)
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def post_task_complete_to_deal(self, task_id: int):
    """Deprecated: hours are billed per TimeEntry. Kept as no-op for old queue messages."""
    return {"ok": True, "skipped": "deprecated_use_post_time_entry_to_deal", "task_id": task_id}
