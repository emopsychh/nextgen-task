from django.core.management.base import BaseCommand
from django.db import transaction

from board.models import Attachment, Comment, Project, Task, TimeEntry
from portals.models import PortalDealBinding


class Command(BaseCommand):
    help = (
        "Clear app board data (projects, tasks, comments, attachments, time entries) "
        "and optional deal bindings. Keeps portals, users, and portal links."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-deals",
            action="store_true",
            help="Also delete PortalDealBinding rows (company/group cache on PortalLink stays).",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["yes"]:
            self.stdout.write(self.style.WARNING("Pass --yes to confirm deletion."))
            return

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
            from portals.models import PortalLink

            PortalLink.objects.update(bitrix_company_id="", bitrix_group_id="")

        self.stdout.write(self.style.SUCCESS(f"Cleared: {counts}"))
