"""Fill local DB with a rich demo so client/agency cabinets look alive."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

MSK = ZoneInfo("Europe/Moscow")

from board.models import (
    BacklogItem,
    Comment,
    Project,
    SupportTicket,
    SupportTicketMessage,
    Task,
    TimeEntry,
    WorkReport,
)
from portals.models import BitrixUser, Portal, PortalDealBinding, PortalLink

DEMO_AGENCY = "dev-agency"
DEMO_CLIENT = "dev-client"
EXTRA_CLIENTS = (
    ("dev-client-sever", "Северсталь Digital", "sever.local"),
    ("dev-client-lamoda", "Lamoda Tech", "lamoda.local"),
)


def _at(days=0, hours=0, minutes=0):
    return timezone.now() + timedelta(days=days, hours=hours, minutes=minutes)


def _msk(month: int, day: int, hour: int = 12, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MSK)


def _touch(model, pk, **fields):
    model.objects.filter(pk=pk).update(**fields)


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
        defaults={"domain": domain, "role": role, "name": name, "timezone": "Europe/Moscow"},
    )
    if not created:
        portal.role = role
        portal.domain = domain
        portal.name = name
        portal.timezone = portal.timezone or "Europe/Moscow"
        portal.is_active = True
        portal.save(update_fields=["role", "domain", "name", "timezone", "is_active", "updated_at"])
    return portal


def _task(project: Project, *, title: str, status: str, **kwargs) -> Task:
    created_at = kwargs.pop("created_at", timezone.now())
    updated_at = kwargs.pop("updated_at", created_at)
    completed_at = kwargs.pop("completed_at", None)
    if completed_at is None and status == Task.Status.DONE:
        completed_at = updated_at
    if completed_at is not None:
        kwargs["completed_at"] = completed_at
    task = Task.objects.create(
        project=project,
        title=title,
        status=status,
        sync_status=Task.SyncStatus.SKIPPED,
        **kwargs,
    )
    stamp = {"created_at": created_at, "updated_at": updated_at}
    if completed_at is not None:
        stamp["completed_at"] = completed_at
    _touch(Task, task.pk, **stamp)
    return task


def _comment(task: Task, author: BitrixUser, text: str, *, days: int) -> Comment:
    comment = Comment.objects.create(
        task=task,
        author=author,
        author_name=author.display_name,
        text=text,
    )
    when = _at(days=days, hours=-2)
    _touch(Comment, comment.pk, created_at=when, updated_at=when)
    return comment


class Command(BaseCommand):
    help = "Create demo portals, hours package, projects, tasks, reports and tickets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Wipe previous demo board data on these portals before seeding.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            agency, client, extras = self._portals()
            if options["reset"]:
                self._wipe([client, *extras])
            self._seed_main(agency, client)
            self._seed_extra(agency, extras)
        self.stdout.write(self.style.SUCCESS("Demo data is ready. Relogin as client / agency."))

    def _portals(self):
        agency = _portal(
            DEMO_AGENCY,
            role=Portal.Role.AGENCY,
            domain="agency.local",
            name="Nextgen",
        )
        client = _portal(
            DEMO_CLIENT,
            role=Portal.Role.CLIENT,
            domain="client.local",
            name="Альфа Логистик",
        )
        extras = [
            _portal(mid, role=Portal.Role.CLIENT, domain=domain, name=name)
            for mid, name, domain in EXTRA_CLIENTS
        ]
        _user(agency, "dev-agency-user", "Мария", "Соколова", "agency@example.com")
        _user(agency, "dev-agency-lead", "Илья", "Орлов", "ilya@example.com")
        _user(client, "dev-client-user", "Анна", "Волкова", "client@example.com")
        PortalLink.objects.get_or_create(agency_portal=agency, client_portal=client)
        for extra in extras:
            PortalLink.objects.get_or_create(agency_portal=agency, client_portal=extra)
            _user(extra, f"user-{extra.member_id}", "Клиент", extra.name.split()[0], f"{extra.member_id}@example.com")
        return agency, client, extras

    def _wipe(self, clients: list[Portal]):
        ids = [p.id for p in clients]
        WorkReport.objects.filter(portal_id__in=ids).delete()
        SupportTicket.objects.filter(portal_id__in=ids).delete()
        BacklogItem.objects.filter(portal_id__in=ids).delete()
        Project.objects.filter(portal_id__in=ids).delete()
        PortalDealBinding.objects.filter(client_portal_id__in=ids).delete()

    def _seed_main(self, agency: Portal, client: Portal):
        maria = agency.users.get(bitrix_id="dev-agency-user")
        ilya = agency.users.get(bitrix_id="dev-agency-lead")
        anna = client.users.get(bitrix_id="dev-client-user")

        binding, _ = PortalDealBinding.objects.update_or_create(
            agency_portal=agency,
            client_portal=client,
            defaults={
                "deal_id": "130",
                "deal_title": "Сопровождение Альфа Логистик — август",
                "paid_hours": Decimal("10.00"),
                "remaining_hours": Decimal("7.83"),
                "is_active": True,
                "stage_semantic": "",
            },
        )
        _touch(PortalDealBinding, binding.pk, updated_at=_msk(8, 22, 11, 40))

        decomp = Project.objects.create(
            portal=client,
            name="Отчёт «Декомпозиция»",
            description="Разработка отчёта и настройка источников данных",
        )
        age = Project.objects.create(
            portal=client,
            name="Настройка процесса «Возраст и возрастная категория»",
            description="Настройка бизнес-процесса и правил в CRM",
        )
        phones = Project.objects.create(
            portal=client,
            name="Передачи номеров в Битрикс всегда с одним префиксом +7",
            description="Разработка и внедрение интеграции",
        )
        charts = Project.objects.create(
            portal=client,
            name="Приложение «График счетов»",
            description="Внутренний график оплат — закрыт в этом спринте.",
        )

        due_decomp = _msk(8, 28, 18)
        due_age = _msk(8, 30, 18)
        due_phones = _msk(9, 2, 18)

        # --- decomposition: 0/2 ---
        t_struct = _task(
            decomp,
            title="Собрать структуру отчёта по лидам",
            status=Task.Status.TODO,
            description="Блоки: источники, воронка, стоимость лида, рекомендации.",
            due_date=due_decomp,
            created_by=anna,
            created_at=_msk(8, 10, 11),
        )
        t_mock = _task(
            decomp,
            title="Согласовать макет отчёта по лидам",
            status=Task.Status.TODO,
            description="Нужно ваше ОК по сетке разделов и акцентам на обложке.",
            due_date=due_decomp,
            is_important=True,
            created_by=maria,
            created_at=_msk(8, 14, 12),
            updated_at=_msk(8, 23, 10, 20),
        )
        _comment(
            t_mock,
            maria,
            "Макет собрали в Figma. Посмотрите порядок блоков и подпись на обложке — без вашего ОК дальше не двигаем.",
            days=-2,
        )

        # --- age process: 2/6 ---
        t_age_done_1 = _task(
            age,
            title="Карта статусов процесса",
            status=Task.Status.DONE,
            outcome="Статусы и переходы согласованы с CRM.",
            due_date=_msk(8, 10, 18),
            created_by=maria,
            created_at=_msk(8, 2, 10),
            updated_at=_msk(8, 8, 16),
        )
        t_age_done_2 = _task(
            age,
            title="Правила возрастной категории",
            status=Task.Status.DONE,
            outcome="Диапазоны возрастов записаны в смарт-процесс.",
            due_date=_msk(8, 10, 18),
            created_by=ilya,
            created_at=_msk(8, 3, 11),
            updated_at=_msk(8, 9, 15),
        )
        t_confirm = _task(
            age,
            title="Подтвердить логику поля оплаты Power Split",
            status=Task.Status.IN_PROGRESS,
            description="Нужно ваше подтверждение логики поля оплаты перед выкладкой.",
            due_date=due_age,
            is_important=True,
            created_by=maria,
            created_at=_msk(8, 16, 10),
            updated_at=_msk(8, 22, 12, 10),
        )
        _task(
            age,
            title="Робот смены категории при дне рождения",
            status=Task.Status.TODO,
            due_date=due_age,
            created_by=maria,
            created_at=_msk(8, 17, 11),
        )
        _task(
            age,
            title="Права менеджеров на процессе",
            status=Task.Status.TODO,
            due_date=due_age,
            created_by=ilya,
            created_at=_msk(8, 18, 9),
        )
        _task(
            age,
            title="Тестовые сделки по возрастным веткам",
            status=Task.Status.TODO,
            due_date=due_age,
            created_by=anna,
            created_at=_msk(8, 19, 14),
        )
        _comment(
            t_confirm,
            maria,
            "Логику Power Split собрали. Если ок — закрепляем поле и закрываем задачу.",
            days=-1,
        )

        # --- phones: 3/5 = 60% ---
        t_ph1 = _task(
            phones,
            title="Нормализация входящих номеров",
            status=Task.Status.DONE,
            outcome="Все номера приводятся к +7.",
            due_date=_msk(8, 12, 18),
            created_by=maria,
            created_at=_msk(8, 4, 10),
            updated_at=_msk(8, 10, 12),
        )
        t_ph2 = _task(
            phones,
            title="Обработка 8-ки и без кода страны",
            status=Task.Status.DONE,
            outcome="8XXXXXXXXXX и 9XXXXXXXXX получают префикс +7.",
            due_date=_msk(8, 12, 18),
            created_by=ilya,
            created_at=_msk(8, 5, 11),
            updated_at=_msk(8, 11, 13),
        )
        t_ph3 = _task(
            phones,
            title="Запись в карточку лида",
            status=Task.Status.DONE,
            outcome="В CRM уходит уже нормализованный номер.",
            due_date=_msk(8, 12, 18),
            created_by=maria,
            created_at=_msk(8, 6, 12),
            updated_at=_msk(8, 12, 14),
        )
        _task(
            phones,
            title="Проверка дублей после нормализации",
            status=Task.Status.IN_PROGRESS,
            due_date=due_phones,
            created_by=ilya,
            created_at=_msk(8, 18, 12),
        )
        _task(
            phones,
            title="Логи ошибок на вебхуке",
            status=Task.Status.TODO,
            due_date=due_phones,
            created_by=maria,
            created_at=_msk(8, 19, 10),
        )

        # recently completed — separate 100% project so in-progress % stays intact
        t_chart = _task(
            charts,
            title="Корректировка приложения «График счетов»",
            status=Task.Status.DONE,
            outcome="Поправили ось сумм и подписи месяцев.",
            due_date=_msk(8, 22, 18),
            created_by=maria,
            created_at=_msk(8, 7, 10),
            updated_at=_msk(8, 22, 16, 20),
        )
        t_split = _task(
            charts,
            title="Добавить в приложение графика счетов поле вид оплаты Повер сплит",
            status=Task.Status.DONE,
            outcome="Поле вида оплаты Power Split добавлено в график.",
            due_date=_msk(8, 22, 18),
            created_by=ilya,
            created_at=_msk(8, 8, 11),
            updated_at=_msk(8, 21, 17, 40),
        )
        t_conv = _task(
            charts,
            title="Доработка процесса расчета отчета (конверсии и показатели по лидам)",
            status=Task.Status.DONE,
            outcome="Конверсия и показатели по лидам совпадают с витриной.",
            due_date=_msk(8, 22, 18),
            created_by=maria,
            created_at=_msk(8, 9, 12),
            updated_at=_msk(8, 20, 15, 10),
        )

        for task, seconds, started in (
            (t_age_done_1, 50 * 60, _msk(8, 8, 11)),
            (t_age_done_2, 40 * 60, _msk(8, 9, 10)),
            (t_ph1, 40 * 60, _msk(8, 10, 11)),
        ):
            ended = started + timedelta(seconds=seconds)
            entry = TimeEntry.objects.create(
                task=task,
                author=maria,
                started_at=started,
                ended_at=ended,
                duration_seconds=seconds,
                note="Демо-сессия",
            )
            _touch(TimeEntry, entry.pk, created_at=started, updated_at=ended)

        ticket = SupportTicket.objects.create(
            portal=client,
            subject="Ответить на вопрос по доступам к платформе",
            body="Нужны доступы в кабинет платформы для проверки отчёта. Подскажите, кому выдать.",
            project=phones,
            status=SupportTicket.Status.OPEN,
            created_by=anna,
        )
        SupportTicketMessage.objects.create(
            ticket=ticket,
            author=anna,
            text="Нужны доступы в кабинет платформы для проверки отчёта. Подскажите, кому выдать.",
        )
        SupportTicketMessage.objects.create(
            ticket=ticket,
            author=maria,
            text="Список ролей собрали. Ответьте, пожалуйста, кому из команды выдаём вход.",
        )
        _touch(
            SupportTicket,
            ticket.pk,
            created_at=_msk(8, 20, 11),
            updated_at=_msk(8, 21, 15, 30),
        )

        BacklogItem.objects.create(
            portal=client,
            title="Мобильное приложение для кладовщиков",
            notes="Идея на Q4: сканер + статусы отгрузки.",
            status=BacklogItem.Status.IDEA,
            priority=BacklogItem.Priority.NORMAL,
            created_by=maria,
            tags=["q4", "mobile"],
        )
        BacklogItem.objects.create(
            portal=client,
            title="Автозакрытие просроченных лидов",
            notes="Робот через 14 дней без касания.",
            status=BacklogItem.Status.IN_PROGRESS,
            priority=BacklogItem.Priority.HIGH,
            is_pinned=True,
            created_by=ilya,
            assignee=ilya,
            tags=["crm"],
        )

        _ = (t_struct, t_ph2, t_ph3, t_chart, t_split, t_conv)

    def _seed_extra(self, agency: Portal, extras: list[Portal]):
        maria = agency.users.get(bitrix_id="dev-agency-user")
        if len(extras) >= 1:
            sever = extras[0]
            PortalDealBinding.objects.update_or_create(
                agency_portal=agency,
                client_portal=sever,
                defaults={
                    "deal_id": "204",
                    "deal_title": "Сопровождение Северсталь — сентябрь",
                    "paid_hours": Decimal("40.00"),
                    "remaining_hours": Decimal("18.50"),
                    "is_active": True,
                },
            )
            portal = Project.objects.create(
                portal=sever,
                name="Портал закупок",
                description="Кабинет поставщика и статусы заявок.",
            )
            _task(
                portal,
                title="Карточка заявки поставщика",
                status=Task.Status.IN_PROGRESS,
                due_date=_at(days=4),
                is_important=True,
                created_by=maria,
                created_at=_at(days=-6),
            )
            _task(
                portal,
                title="Фильтр по ИНН",
                status=Task.Status.TODO,
                created_by=maria,
                created_at=_at(days=-2),
            )
        if len(extras) >= 2:
            lamoda = extras[1]
            PortalDealBinding.objects.update_or_create(
                agency_portal=agency,
                client_portal=lamoda,
                defaults={
                    "deal_id": "311",
                    "deal_title": "Ретейн Lamoda Tech",
                    "paid_hours": Decimal("20.00"),
                    "remaining_hours": Decimal("20.00"),
                    "is_active": True,
                },
            )
            shop = Project.objects.create(
                portal=lamoda,
                name="Виджет размеров",
                description="Подсказки размера на карточке товара.",
            )
            _task(
                shop,
                title="Собрать таблицу размеров",
                status=Task.Status.TODO,
                due_date=_at(days=20),
                created_by=maria,
                created_at=_at(days=-1),
            )
