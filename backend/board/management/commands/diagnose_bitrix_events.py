"""Diagnose Bitrix event bindings and inbound status pull."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from board.models import Task
from board.status_sync import (
    ensure_task_event_bindings,
    event_handler_url,
    local_status_from_bitrix_task,
    pull_task_status_from_bitrix,
    resolve_all_bitrix_task_sources,
)
from portals.bitrix import BitrixAPIError, BitrixClient
from portals.models import Portal


class Command(BaseCommand):
    help = "Check OnTaskUpdate bindings and optionally pull status for a task"

    def add_arguments(self, parser):
        parser.add_argument(
            "--rebind",
            action="store_true",
            help="Re-bind OnTaskUpdate / comment / add handlers",
        )
        parser.add_argument(
            "--task-id",
            type=int,
            default=0,
            help="Local task id to compare / pull from Bitrix",
        )
        parser.add_argument(
            "--pull",
            action="store_true",
            help="Apply inbound status/deadline for --task-id",
        )

    def handle(self, *args, **options):
        handler = event_handler_url()
        self.stdout.write(f"PUBLIC handler URL: {handler}")
        self.stdout.write(
            f"BITRIX_APPLICATION_TOKEN set: {bool((settings.BITRIX_APPLICATION_TOKEN or '').strip())}"
        )

        for portal in Portal.objects.filter(is_active=True).order_by("id"):
            self.stdout.write(
                f"\n[{portal.id}] {portal.role} {portal.domain} "
                f"token={bool(portal.access_token)} "
                f"app_token={bool((portal.application_token or '').strip())}"
            )
            if not portal.access_token:
                continue
            client = BitrixClient(portal)
            try:
                existing = client.call("event.get") or []
                if isinstance(existing, dict):
                    existing = existing.get("result") or existing.get("events") or []
                if not isinstance(existing, list):
                    existing = []
                if not existing:
                    self.stdout.write(self.style.WARNING("  event.get: (empty)"))
                for row in existing:
                    if not isinstance(row, dict):
                        continue
                    ev = row.get("event") or row.get("EVENT") or ""
                    h = row.get("handler") or row.get("HANDLER") or ""
                    mark = "OK" if str(h).rstrip("/") == handler.rstrip("/") else "STALE?"
                    self.stdout.write(f"  {mark} {ev} → {h}")
            except BitrixAPIError as exc:
                self.stdout.write(self.style.ERROR(f"  event.get FAIL: {exc}"))

            if options["rebind"]:
                ok = ensure_task_event_bindings(portal)
                self.stdout.write(
                    self.style.SUCCESS(f"  rebind: {ok}")
                    if ok
                    else self.style.WARNING(f"  rebind: {ok}")
                )

        task_id = options["task_id"]
        if not task_id:
            return

        task = (
            Task.objects.select_related("project", "project__portal")
            .filter(pk=task_id)
            .first()
        )
        if not task:
            self.stdout.write(self.style.ERROR(f"task {task_id} not found"))
            return

        self.stdout.write(
            f"\n=== Task {task.id} local status={task.status} "
            f"sync={task.sync_status} agency={task.agency_bitrix_task_id!r} "
            f"client={task.bitrix_task_id!r}"
        )
        for portal, bitrix_id in resolve_all_bitrix_task_sources(task):
            try:
                data = BitrixClient(portal).get_task(bitrix_id) or {}
            except BitrixAPIError as exc:
                self.stdout.write(
                    self.style.ERROR(f"  get_task {portal.domain}#{bitrix_id}: {exc}")
                )
                continue
            remote = local_status_from_bitrix_task(data)
            raw = (
                data.get("realStatus")
                or data.get("REAL_STATUS")
                or data.get("status")
                or data.get("STATUS")
            )
            action = data.get("action") or data.get("ACTION") or {}
            self.stdout.write(
                f"  {portal.role} {portal.domain}#{bitrix_id}: "
                f"raw={raw!r} action.start={action.get('start')!r} "
                f"action.pause={action.get('pause')!r} → local={remote!r}"
            )

        if options["pull"]:
            changed = pull_task_status_from_bitrix(task)
            task.refresh_from_db()
            self.stdout.write(
                self.style.SUCCESS(
                    f"  pull changed={changed} now status={task.status} sync={task.sync_status}"
                )
            )
