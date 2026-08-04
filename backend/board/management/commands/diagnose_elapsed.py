"""Diagnose Bitrix «Учёт времени» sync (task.elapseditem.add)."""

from __future__ import annotations

import json
import traceback

from django.core.management.base import BaseCommand
from django.utils import timezone

from board.models import Task, TimeEntry
from board.tasks import (
    _agency_portal_for_client,
    _bitrix_elapsed_dt,
    _bitrix_user_id,
    _ensure_elapsed_access,
    _extract_elapsed_id,
    sync_timer_to_bitrix,
)
from portals.bitrix import BitrixAPIError, BitrixClient


class Command(BaseCommand):
    help = (
        "Inspect TimeEntry → Bitrix elapsed sync. "
        "Use --list, --entry-id, --task-id, and optionally --sync / --probe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="Recent time entries")
        parser.add_argument("--entry-id", type=int, default=0)
        parser.add_argument("--task-id", type=int, default=0, help="Local task id")
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run sync_timer_to_bitrix for the entry/task",
        )
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Call task.elapseditem.add directly and print raw Bitrix result/error",
        )
        parser.add_argument(
            "--seconds",
            type=int,
            default=60,
            help="Seconds to probe-add (default 60); only with --probe",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._list()
            return

        entry = self._resolve_entry(options)
        task = None
        if entry:
            task = entry.task
        elif options["task_id"]:
            task = (
                Task.objects.select_related("project", "project__portal")
                .filter(pk=options["task_id"])
                .first()
            )
            if not task:
                self.stderr.write(self.style.ERROR(f"task {options['task_id']} not found"))
                return

        if not entry and not task:
            self.stderr.write(
                self.style.ERROR("Pass --list, --entry-id, or --task-id")
            )
            return

        if task:
            self._print_task(task)
        if entry:
            self._print_entry(entry)

        agency = _agency_portal_for_client(task.project.portal)
        if not agency:
            self.stderr.write(self.style.ERROR("No agency PortalLink for this client"))
            return
        self.stdout.write(
            f"agency portal={agency.id} domain={agency.domain} "
            f"token={'yes' if agency.access_token else 'NO'} "
            f"expires={agency.expires_at}"
        )

        if not agency.access_token:
            return

        client = BitrixClient(agency)
        try:
            me = client.get_current_user()
            oauth_uid = _bitrix_user_id(me)
            self.stdout.write(f"oauth user.current ID={oauth_uid} raw={me!r}")
        except BitrixAPIError as exc:
            self.stderr.write(self.style.ERROR(f"user.current failed: {exc}"))
            oauth_uid = None

        bitrix_id = str(task.agency_bitrix_task_id or "")
        if not bitrix_id:
            self.stderr.write(self.style.ERROR("task has no agency_bitrix_task_id"))
            return

        try:
            bx = client.get_task(bitrix_id)
            self.stdout.write(f"bitrix task.get: {json.dumps(bx, ensure_ascii=False, default=str)[:1200]}")
        except BitrixAPIError as exc:
            self.stderr.write(self.style.ERROR(f"tasks.task.get failed: {exc}"))

        try:
            rows = client.call(
                "task.elapseditem.getlist",
                {"TASKID": int(bitrix_id), "ORDER": {"ID": "DESC"}},
            )
            self.stdout.write(
                f"elapseditem.getlist: {json.dumps(rows, ensure_ascii=False, default=str)[:2000]}"
            )
        except BitrixAPIError as exc:
            self.stderr.write(self.style.ERROR(f"elapseditem.getlist failed: {exc} resp={exc.response}"))

        try:
            allowed = client.call(
                "task.elapseditem.isactionallowed",
                {"TASKID": int(bitrix_id), "ITEMID": 0, "ACTIONID": 1},
            )
            self.stdout.write(f"isactionallowed(ADD)= {allowed!r}")
        except BitrixAPIError as exc:
            self.stderr.write(
                self.style.WARNING(f"isactionallowed failed: {exc} resp={exc.response}")
            )

        if options["probe"]:
            self._probe(client, bitrix_id, oauth_uid, options["seconds"], entry)

        if options["sync"]:
            target = entry
            if not target:
                target = (
                    TimeEntry.objects.filter(
                        task=task, ended_at__isnull=False, duration_seconds__gt=0
                    )
                    .order_by("-id")
                    .first()
                )
            if not target:
                self.stderr.write(self.style.ERROR("No closed TimeEntry to sync"))
                return
            self.stdout.write(f"Running sync_timer_to_bitrix({target.id}) …")
            try:
                result = sync_timer_to_bitrix(target.id, "set")
                self.stdout.write(self.style.SUCCESS(f"sync result: {result!r}"))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"sync raised: {exc}"))
                self.stderr.write(traceback.format_exc())
            target.refresh_from_db()
            self._print_entry(target)

    def _list(self):
        qs = (
            TimeEntry.objects.select_related("task")
            .filter(ended_at__isnull=False, duration_seconds__gt=0)
            .order_by("-id")[:20]
        )
        if not qs:
            self.stdout.write("No closed TimeEntry rows")
            return
        for e in qs:
            t = e.task
            self.stdout.write(
                f"entry={e.id} task={t.id} sec={e.duration_seconds} "
                f"bx_elapsed={e.bitrix_elapsed_id!r} "
                f"agency_task={t.agency_bitrix_task_id!r} "
                f"ended={e.ended_at}"
            )

    def _resolve_entry(self, options) -> TimeEntry | None:
        if options["entry_id"]:
            entry = (
                TimeEntry.objects.select_related(
                    "task", "task__project", "task__project__portal", "author"
                )
                .filter(pk=options["entry_id"])
                .first()
            )
            if not entry:
                self.stderr.write(self.style.ERROR(f"entry {options['entry_id']} not found"))
            return entry
        if options["task_id"]:
            return (
                TimeEntry.objects.select_related(
                    "task", "task__project", "task__project__portal", "author"
                )
                .filter(
                    task_id=options["task_id"],
                    ended_at__isnull=False,
                    duration_seconds__gt=0,
                )
                .order_by("-id")
                .first()
            )
        return None

    def _print_task(self, task: Task) -> None:
        self.stdout.write(
            f"task id={task.id} title={task.title!r} status={task.status} "
            f"agency_bitrix_task_id={task.agency_bitrix_task_id!r} "
            f"portal={task.project.portal_id}"
        )

    def _print_entry(self, entry: TimeEntry) -> None:
        self.stdout.write(
            f"entry id={entry.id} sec={entry.duration_seconds} "
            f"bitrix_elapsed_id={entry.bitrix_elapsed_id!r} "
            f"started={entry.started_at} ended={entry.ended_at} "
            f"note={entry.note!r} author={entry.author_id}"
        )

    def _probe(self, client, bitrix_id, oauth_uid, seconds, entry):
        self.stdout.write("--- PROBE task.elapseditem.add ---")
        try:
            _ensure_elapsed_access(client, str(bitrix_id), oauth_uid)
            self.stdout.write("ensure_elapsed_access: ok")
        except BitrixAPIError as exc:
            self.stderr.write(
                self.style.ERROR(f"ensure_elapsed_access: {exc} resp={exc.response}")
            )

        date_start = _bitrix_elapsed_dt(
            entry.started_at if entry else timezone.now()
        )
        date_stop = _bitrix_elapsed_dt(entry.ended_at if entry else timezone.now())
        variants = [
            {"label": "docs-minimal", "user_id": None, "source": None, "dates": False},
            {"label": "no-user+dates+source2", "user_id": None, "source": 2, "dates": True},
            {
                "label": "oauth-user+dates",
                "user_id": oauth_uid,
                "source": 2,
                "dates": True,
            },
        ]
        for v in variants:
            fields = {
                "SECONDS": int(seconds),
                "COMMENT_TEXT": f"diagnose_elapsed probe ({v['label']})",
            }
            if v["source"] is not None:
                fields["SOURCE"] = v["source"]
            if v["user_id"]:
                try:
                    fields["USER_ID"] = int(v["user_id"])
                except (TypeError, ValueError):
                    fields["USER_ID"] = v["user_id"]
            if v["dates"]:
                if date_start:
                    fields["DATE_START"] = date_start
                if date_stop:
                    fields["DATE_STOP"] = date_stop
            payload = {"TASKID": int(bitrix_id), "ARFIELDS": fields}
            self.stdout.write(f"try {v['label']}: {payload}")
            try:
                result = client.call("task.elapseditem.add", payload)
                eid = _extract_elapsed_id(result)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK {v['label']}: result={result!r} elapsed_id={eid}"
                    )
                )
                # Clean up probe row so we do not litter Bitrix.
                if eid:
                    try:
                        client.delete_elapsed_item(bitrix_id, eid)
                        self.stdout.write(f"deleted probe item {eid}")
                    except BitrixAPIError as exc:
                        self.stderr.write(self.style.WARNING(f"cleanup delete failed: {exc}"))
                return
            except BitrixAPIError as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"FAIL {v['label']}: {exc} resp={exc.response}"
                    )
                )
        self.stderr.write(self.style.ERROR("All probe variants failed"))
