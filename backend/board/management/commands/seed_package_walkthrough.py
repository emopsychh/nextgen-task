"""Seed a hands-on deal: close in-progress tasks, pack the report, send, accept."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from board.models import Project, Task, TimeEntry, WorkReport, WorkReportLine
from board.reports import ensure_report_for_binding
from portals.models import BitrixUser, Portal, PortalDealBinding, PortalLink

DEAL_ID = "practice-close"
PROJECT_NAME = "Закрытие пакета — практика"


def _user(portal: Portal, bitrix_id: str, name: str, last_name: str, email: str) -> BitrixUser:
    user, _ = BitrixUser.objects.update_or_create(
        portal=portal,
        bitrix_id=bitrix_id,
        defaults={"name": name, "last_name": last_name, "email": email, "is_admin": True},
    )
    return user


def _portal(member_id: str, *, role: str, domain: str, name: str) -> Portal:
    portal, created = Portal.objects.get_or_create(
        member_id=member_id,
        defaults={"domain": domain, "role": role, "name": name, "timezone": "Asia/Novosibirsk"},
    )
    if not created:
        portal.role = role
        portal.domain = domain
        portal.name = name
        portal.is_active = True
        portal.save(update_fields=["role", "domain", "name", "is_active", "updated_at"])
    return portal


class Command(BaseCommand):
    help = (
        "Create an active deal with in-progress tasks and a spent hour package "
        "so you can close tasks, assemble the report, send it, and accept as client."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        with transaction.atomic():
            agency = _portal(
                "dev-agency",
                role=Portal.Role.AGENCY,
                domain="agency.local",
                name="Nextgen",
            )
            client = _portal(
                "dev-client",
                role=Portal.Role.CLIENT,
                domain="client.local",
                name="Альфа Логистик",
            )
            PortalLink.objects.get_or_create(agency_portal=agency, client_portal=client)
            maria = _user(agency, "dev-agency-user", "Мария", "Соколова", "agency@example.com")
            _user(agency, "dev-agency-lead", "Илья", "Орлов", "ilya@example.com")
            _user(client, "dev-client-user", "Анна", "Волкова", "client@example.com")

            PortalDealBinding.objects.filter(client_portal=client, is_active=True).exclude(
                deal_id=DEAL_ID
            ).update(is_active=False)
            PortalDealBinding.objects.filter(agency_portal=agency, deal_id=DEAL_ID, is_active=True).exclude(
                client_portal=client
            ).update(is_active=False)

            binding, _ = PortalDealBinding.objects.update_or_create(
                agency_portal=agency,
                client_portal=client,
                deal_id=DEAL_ID,
                defaults={
                    "deal_title": "Пакет часов — закрытие августа",
                    "paid_hours": Decimal("6.00"),
                    "remaining_hours": Decimal("0.00"),
                    "is_active": True,
                    "stage_semantic": "",
                },
            )

            project, _ = Project.objects.update_or_create(
                portal=client,
                name=PROJECT_NAME,
                defaults={
                    "description": "Ручной сценарий: закрыть задачи в работе, собрать отчёт по сделке и согласовать с клиентом.",
                    "is_active": True,
                },
            )

            specs = (
                {
                    "title": "Настроить выгрузку оплат за август",
                    "description": "Выгрузка из банка и сверка с актами. Закройте задачу, когда сверка готова.",
                    "hours": Decimal("2.50"),
                    "working": True,
                },
                {
                    "title": "Проверить акты и закрывающие документы",
                    "description": "Пройти акты, отметить расхождения. После проверки закройте задачу.",
                    "hours": Decimal("2.00"),
                    "working": True,
                },
                {
                    "title": "Свести часы по проекту в пакет",
                    "description": "Итоговая сверка списанных часов. Закройте задачу — затем соберите отчёт.",
                    "hours": Decimal("1.50"),
                    "working": False,
                },
            )

            tasks = []
            for spec in specs:
                task, _ = Task.objects.update_or_create(
                    project=project,
                    title=spec["title"],
                    defaults={
                        "description": spec["description"],
                        "status": Task.Status.IN_PROGRESS,
                        "outcome": "",
                        "sync_status": Task.SyncStatus.SKIPPED,
                        "created_by": maria,
                        "is_locally_paused": False,
                        "working_by": maria if spec["working"] else None,
                        "working_started_at": now - timedelta(hours=1) if spec["working"] else None,
                        "completed_at": None,
                        "due_date": now + timedelta(days=2),
                    },
                )
                tasks.append((task, spec["hours"]))

            TimeEntry.objects.filter(task__project=project).delete()
            WorkReportLine.objects.filter(task__project=project).delete()

            cursor = now - timedelta(days=3)
            for task, hours in tasks:
                seconds = int(hours * 60 * 60)
                started = cursor
                ended = started + timedelta(seconds=seconds)
                TimeEntry.objects.create(
                    task=task,
                    author=maria,
                    started_at=started,
                    ended_at=ended,
                    duration_seconds=seconds,
                    note="Списание в пакет часов",
                    billed_to_deal_at=ended,
                    billed_deal_binding=binding,
                )
                cursor = ended + timedelta(hours=2)

            report = ensure_report_for_binding(binding)
            if report.status != WorkReport.Status.DRAFT or report.sent_at:
                report.status = WorkReport.Status.DRAFT
                report.sent_at = None
                report.accepted_at = None
                report.client_comment = ""
                report.save(
                    update_fields=[
                        "status",
                        "sent_at",
                        "accepted_at",
                        "client_comment",
                        "updated_at",
                    ]
                )
            report.lines.update(is_reserved=False)
            report.projects.clear()

        self.stdout.write(self.style.SUCCESS("Готово. Сценарий для ручной проверки:"))
        self.stdout.write("1. Войти как агентство - клиент «Альфа Логистик» - проект «Закрытие пакета — практика».")
        self.stdout.write("2. Закрыть три задачи в работе (нужен итог/результат).")
        self.stdout.write("3. Отчёты - сделка «Пакет часов — закрытие августа»: закрытые задачи уже в отчёте - отправить клиенту.")
        self.stdout.write("4. Выйти и войти как клиент - Отчёты - согласовать пакет.")
        self.stdout.write(f"   Сделка {DEAL_ID}: пакет 6 ч, остаток 0 ч - отправка не блокируется.")
