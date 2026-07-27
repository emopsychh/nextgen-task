from django.core.management.base import BaseCommand
from django.db import transaction

from board.models import Attachment, Comment, Project, Task, TimeEntry
from portals.bitrix import BitrixAPIError, BitrixClient
from portals.models import PortalDealBinding, PortalLink


class Command(BaseCommand):
    help = (
        "Clear app board data (projects, tasks, comments, attachments, time entries) "
        "and optional deal bindings. Keeps portals, users, and portal links. "
        "With --with-bitrix also deletes linked agency Bitrix tasks/projects."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-deals",
            action="store_true",
            help="Also delete PortalDealBinding rows (company/group cache on PortalLink stays).",
        )
        parser.add_argument(
            "--with-bitrix",
            action="store_true",
            help="Also delete linked agency Bitrix tasks and project parent tasks.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Abort local cleanup if any Bitrix delete fails (requires --with-bitrix).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(self.style.WARNING("Pass --yes to confirm deletion."))
            return

        bitrix_stats = {"deleted": 0, "skipped": 0, "errors": 0}
        if options["with_bitrix"]:
            bitrix_stats = self._delete_bitrix_board_tasks(strict=options["strict"])

        with transaction.atomic():
            counts = {
                "attachments": Attachment.objects.count(),
                "comments": Comment.objects.count(),
                "time_entries": TimeEntry.objects.count(),
                "tasks": Task.objects.count(),
                "projects": Project.objects.count(),
            }
            Attachment.objects.all().delete()
            Comment.objects.all().delete()
            TimeEntry.objects.all().delete()
            Task.objects.all().delete()
            Project.objects.all().delete()

            if options["with_deals"]:
                counts["deal_bindings"] = PortalDealBinding.objects.count()
                PortalDealBinding.objects.all().delete()
                # Clear cached Bitrix ids so next resolve is fresh
                PortalLink.objects.update(bitrix_company_id="", bitrix_group_id="")

        if options["with_bitrix"]:
            counts["bitrix"] = bitrix_stats
        self.stdout.write(self.style.SUCCESS(f"Cleared: {counts}"))

    def _delete_bitrix_board_tasks(self, *, strict: bool) -> dict:
        """Delete agency Bitrix copies before wiping local rows."""
        stats = {"deleted": 0, "skipped": 0, "errors": 0}
        clients: dict[int, BitrixClient] = {}

        def client_for(agency_portal) -> BitrixClient | None:
            if not agency_portal or not agency_portal.access_token:
                return None
            cached = clients.get(agency_portal.id)
            if cached is not None:
                return cached
            client = BitrixClient(agency_portal)
            clients[agency_portal.id] = client
            return client

        def agency_for_client(client_portal):
            link = (
                PortalLink.objects.filter(client_portal=client_portal)
                .select_related("agency_portal")
                .first()
            )
            return link.agency_portal if link else None

        # Subtasks first, then parent project tasks.
        for task in Task.objects.select_related("project", "project__portal").iterator():
            bitrix_id = (task.agency_bitrix_task_id or "").strip()
            if not bitrix_id:
                stats["skipped"] += 1
                continue
            agency = agency_for_client(task.project.portal)
            client = client_for(agency)
            if not client:
                stats["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"skip task local={task.id} bitrix={bitrix_id}: no agency token"
                    )
                )
                continue
            try:
                client.delete_task(bitrix_id)
                stats["deleted"] += 1
                self.stdout.write(f"bitrix task deleted id={bitrix_id} (local task {task.id})")
            except BitrixAPIError as exc:
                stats["errors"] += 1
                msg = f"bitrix task delete failed id={bitrix_id} (local task {task.id}): {exc}"
                if strict:
                    raise SystemExit(msg)
                self.stdout.write(self.style.WARNING(msg))

        for project in Project.objects.select_related("portal").iterator():
            bitrix_id = (project.bitrix_task_id or "").strip()
            if not bitrix_id:
                stats["skipped"] += 1
                continue
            agency = agency_for_client(project.portal)
            client = client_for(agency)
            if not client:
                stats["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"skip project local={project.id} bitrix={bitrix_id}: no agency token"
                    )
                )
                continue
            try:
                client.delete_task(bitrix_id)
                stats["deleted"] += 1
                self.stdout.write(
                    f"bitrix project task deleted id={bitrix_id} (local project {project.id})"
                )
            except BitrixAPIError as exc:
                stats["errors"] += 1
                msg = (
                    f"bitrix project delete failed id={bitrix_id} "
                    f"(local project {project.id}): {exc}"
                )
                if strict:
                    raise SystemExit(msg)
                self.stdout.write(self.style.WARNING(msg))

        return stats
