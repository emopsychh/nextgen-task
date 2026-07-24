"""Support ticket helpers — create, reply, close/reopen + portal events."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from board.models import Project, SupportTicket, SupportTicketMessage, Task
from board.realtime import publish_portal_event
from portals.models import BitrixUser, Portal


def require_agency(user) -> None:
    if not getattr(user, "is_agency", False):
        raise PermissionDenied("Только для агентства")


def require_client(user) -> None:
    if getattr(user, "is_agency", False):
        raise PermissionDenied("Только для клиента")


def _author_name(user: BitrixUser | None) -> str:
    if not user:
        return ""
    return user.display_name or f"#{user.bitrix_id}"


def ticket_payload(ticket: SupportTicket, **extra) -> dict:
    return {
        "kind": extra.pop("kind", "ticket"),
        "ticket_id": ticket.pk,
        "portal_id": ticket.portal_id,
        "status": ticket.status,
        "subject": ticket.subject,
        **extra,
    }


def publish_ticket_event(ticket: SupportTicket, kind: str, **extra) -> None:
    publish_portal_event(ticket.portal_id, ticket_payload(ticket, kind=kind, **extra))


@transaction.atomic
def create_ticket(
    portal: Portal,
    *,
    subject: str,
    body: str,
    actor: BitrixUser | None,
    project: Project | None = None,
    task: Task | None = None,
) -> SupportTicket:
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise ValidationError({"subject": "Укажите тему"})
    if not body:
        raise ValidationError({"body": "Опишите проблему"})
    if project is not None and project.portal_id != portal.id:
        raise ValidationError({"project": "Проект не из этого портала"})
    if task is not None:
        if project is None:
            project = task.project
        if task.project.portal_id != portal.id:
            raise ValidationError({"task": "Задача не из этого портала"})
        if project is not None and task.project_id != project.id:
            raise ValidationError({"task": "Задача не из выбранного проекта"})

    ticket = SupportTicket.objects.create(
        portal=portal,
        subject=subject[:500],
        body=body,
        project=project,
        task=task,
        status=SupportTicket.Status.OPEN,
        created_by=actor,
    )
    publish_ticket_event(ticket, "ticket_created", actor_name=_author_name(actor))
    return ticket


@transaction.atomic
def add_message(
    ticket: SupportTicket,
    *,
    text: str,
    actor: BitrixUser | None,
) -> SupportTicketMessage:
    text = (text or "").strip()
    if not text:
        raise ValidationError({"text": "Пустое сообщение"})
    if ticket.status == SupportTicket.Status.CLOSED:
        raise ValidationError({"detail": "Тикет закрыт — сначала откройте снова"})

    msg = SupportTicketMessage.objects.create(ticket=ticket, author=actor, text=text)
    SupportTicket.objects.filter(pk=ticket.pk).update(updated_at=timezone.now())
    ticket.refresh_from_db()
    publish_ticket_event(
        ticket,
        "ticket_message",
        message_id=msg.pk,
        actor_name=_author_name(actor),
    )
    return msg


@transaction.atomic
def close_ticket(ticket: SupportTicket, actor: BitrixUser | None) -> SupportTicket:
    if ticket.status == SupportTicket.Status.CLOSED:
        return ticket
    ticket.status = SupportTicket.Status.CLOSED
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=["status", "closed_at", "updated_at"])
    publish_ticket_event(ticket, "ticket_closed", actor_name=_author_name(actor))
    return ticket


@transaction.atomic
def reopen_ticket(ticket: SupportTicket, actor: BitrixUser | None) -> SupportTicket:
    if ticket.status == SupportTicket.Status.OPEN:
        return ticket
    ticket.status = SupportTicket.Status.OPEN
    ticket.closed_at = None
    ticket.save(update_fields=["status", "closed_at", "updated_at"])
    publish_ticket_event(ticket, "ticket_reopened", actor_name=_author_name(actor))
    return ticket
