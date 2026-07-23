"""Diagnose / retry attachment sync to Bitrix agency subtasks."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from board.models import Attachment
from board.tasks import sync_attachment_to_bitrix
from portals.bitrix import BitrixAPIError, BitrixClient
from portals.models import Portal


class Command(BaseCommand):
    help = "Check disk/task scopes and optionally re-sync attachments missing Bitrix ids"

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry",
            action="store_true",
            help="Re-queue sync for attachments without agency_bitrix_file_id",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Max attachments to retry",
        )
        parser.add_argument(
            "--attachment-id",
            type=int,
            default=0,
            help="Retry a single attachment id (clears Bitrix file ids first)",
        )

    def handle(self, *args, **options):
        self.stdout.write("=== Portals / disk probe ===")
        for portal in Portal.objects.filter(is_active=True).order_by("id"):
            role = portal.role
            domain = portal.domain
            has_token = bool(portal.access_token)
            self.stdout.write(f"\n[{portal.id}] {role} {domain} token={has_token}")
            if not has_token:
                continue
            client = BitrixClient(portal)
            try:
                storage = client.get_app_storage()
                self.stdout.write(f"  disk.storage.getforapp: {storage}")
            except BitrixAPIError as exc:
                self.stdout.write(self.style.ERROR(f"  disk.storage.getforapp FAIL: {exc}"))
            try:
                rights = client.call("disk.rights.getTasks")
                self.stdout.write(f"  disk.rights.getTasks: ok ({len(rights) if isinstance(rights, list) else type(rights)})")
            except BitrixAPIError as exc:
                self.stdout.write(self.style.ERROR(f"  disk.rights.getTasks FAIL: {exc}"))

        qs = Attachment.objects.select_related(
            "task", "task__project", "comment", "comment__task"
        ).order_by("-id")
        missing = qs.filter(agency_bitrix_file_id="")[:50]
        self.stdout.write("\n=== Recent attachments without agency_bitrix_file_id ===")
        for att in missing:
            task = att.task or (att.comment.task if att.comment_id else None)
            self.stdout.write(
                f"  att={att.id} name={att.original_name!r} "
                f"task={getattr(task, 'id', None)} "
                f"agency_task={getattr(task, 'agency_bitrix_task_id', '')!r} "
                f"client_file={att.bitrix_file_id!r}"
            )

        if options["attachment_id"]:
            att = Attachment.objects.filter(pk=options["attachment_id"]).first()
            if not att:
                self.stdout.write(self.style.ERROR("attachment not found"))
                return
            att.agency_bitrix_file_id = ""
            att.bitrix_file_id = ""
            att.save(update_fields=["agency_bitrix_file_id", "bitrix_file_id"])
            result = sync_attachment_to_bitrix(att.id)
            self.stdout.write(self.style.SUCCESS(f"sync result: {result}"))
            return

        if options["retry"]:
            to_retry = list(
                Attachment.objects.filter(agency_bitrix_file_id="")
                .order_by("-id")[: options["limit"]]
            )
            self.stdout.write(f"\n=== Retrying {len(to_retry)} attachments ===")
            for att in to_retry:
                result = sync_attachment_to_bitrix(att.id)
                self.stdout.write(f"  att={att.id} → {result}")
