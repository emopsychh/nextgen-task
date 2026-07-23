"""Diagnose Bitrix event bindings and inbound status pull."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from board.comment_sync import (
    latest_bitrix_work_activity,
    resolve_status_with_timer_activity,
)
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
    help = (
        "Check OnTaskUpdate bindings and pull status. "
        "Find a task with --task-id (local), --bitrix-id, --title, or --list."
    )

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
            help="Local Nextgen task id (from app URL /api/tasks/<id>/)",
        )
        parser.add_argument(
            "--bitrix-id",
            type=str,
            default="",
            help="Agency or client Bitrix task id (подзадача)",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="",
            help="Search local tasks by title substring, e.g. TESTZZZTT",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List recent local tasks (id, status, title, bitrix ids)",
        )
        parser.add_argument(
            "--pull",
            action="store_true",
            help="Apply inbound status/deadline for the resolved task",
        )

    def _resolve_task(self, options) -> Task | None:
        task_id = options["task_id"]
        bitrix_id = (options["bitrix_id"] or "").strip()
        title = (options["title"] or "").strip()

        if task_id:
            task = (
                Task.objects.select_related("project", "project__portal")
                .filter(pk=task_id)
                .first()
            )
            if not task:
                self.stdout.write(self.style.ERROR(f"local task id={task_id} not found"))
            return task

        if bitrix_id:
            task = (
                Task.objects.select_related("project", "project__portal")
                .filter(
                    Q(agency_bitrix_task_id=bitrix_id) | Q(bitrix_task_id=bitrix_id)
                )
                .first()
            )
            if not task:
                self.stdout.write(
                    self.style.ERROR(f"no local task with Bitrix id={bitrix_id!r}")
                )
            return task

        if title:
            qs = (
                Task.objects.select_related("project", "project__portal")
                .filter(title__icontains=title)
                .order_by("-id")[:20]
            )
            rows = list(qs)
            if not rows:
                self.stdout.write(self.style.ERROR(f"no tasks matching title={title!r}"))
                return None
            if len(rows) > 1:
                self.stdout.write(self.style.WARNING(f"multiple matches for {title!r}:"))
                for t in rows:
                    self.stdout.write(
                        f"  id={t.id} status={t.status} "
                        f"agency={t.agency_bitrix_task_id!r} title={t.title!r}"
                    )
                self.stdout.write("Re-run with --task-id <id>")
                return None
            return rows[0]

        return None

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

        if options["list"]:
            self.stdout.write("\n=== Recent local tasks ===")
            for t in (
                Task.objects.select_related("project")
                .order_by("-id")[:30]
            ):
                self.stdout.write(
                    f"  id={t.id} status={t.status} "
                    f"agency={t.agency_bitrix_task_id or '-'} "
                    f"client={t.bitrix_task_id or '-'} "
                    f"title={t.title!r}"
                )

        task = self._resolve_task(options)
        if task is None:
            if not options["list"] and not (
                options["task_id"] or options["bitrix_id"] or options["title"]
            ):
                self.stdout.write(
                    "\nTip: find a task with --list / --title TESTZZZTT / "
                    "--bitrix-id 123 / --task-id 45"
                )
            return

        self.stdout.write(
            f"\n=== Task {task.id} local status={task.status} "
            f"sync={task.sync_status} agency={task.agency_bitrix_task_id!r} "
            f"client={task.bitrix_task_id!r} title={task.title!r}"
        )
        sources = resolve_all_bitrix_task_sources(task)
        if not sources:
            self.stdout.write(
                self.style.ERROR(
                    "  no Bitrix sources (missing agency_bitrix_task_id / tokens)"
                )
            )
        for portal, bitrix_id in sources:
            try:
                data = BitrixClient(portal).get_task(bitrix_id) or {}
            except BitrixAPIError as exc:
                self.stdout.write(
                    self.style.ERROR(f"  get_task {portal.domain}#{bitrix_id}: {exc}")
                )
                continue
            remote = local_status_from_bitrix_task(data)
            activity = latest_bitrix_work_activity(portal, bitrix_id, data)
            resolved = resolve_status_with_timer_activity(remote, activity)
            raw = (
                data.get("realStatus")
                or data.get("REAL_STATUS")
                or data.get("status")
                or data.get("STATUS")
            )
            action = data.get("action") or data.get("ACTION") or {}
            chat = data.get("chatId") or data.get("CHAT_ID") or ""
            changed = (
                data.get("statusChangedDate")
                or data.get("STATUS_CHANGED_DATE")
                or data.get("changedDate")
                or data.get("CHANGED_DATE")
                or ""
            )
            self.stdout.write(
                f"  {portal.role} {portal.domain}#{bitrix_id}:\n"
                f"    raw_status={raw!r} chatId={chat!r} changed={changed!r}\n"
                f"    action.start={action.get('start')!r} "
                f"action.pause={action.get('pause')!r}\n"
                f"    mapped_from_task={remote!r} activity={activity!r} "
                f"resolved={resolved!r}"
            )
            # Probe history + chat for debugging pause detection
            try:
                from board.comment_sync import latest_activity_from_bitrix_history
                from portals.bitrix import BitrixAPIError as BxErr

                hist = latest_activity_from_bitrix_history(portal, bitrix_id)
                self.stdout.write(f"    history_activity={hist!r}")
                try:
                    chat_probe = BitrixClient(portal).call(
                        "im.dialog.messages.get",
                        {
                            "DIALOG_ID": f"chat{chat}" if chat and not str(chat).startswith("chat") else str(chat or ""),
                            "LIMIT": 5,
                        },
                    )
                    msgs = []
                    if isinstance(chat_probe, dict):
                        msgs = chat_probe.get("messages") or []
                    self.stdout.write(
                        f"    chat_probe ok msgs={len(msgs) if isinstance(msgs, list) else type(msgs)}"
                    )
                    if isinstance(msgs, list):
                        for m in msgs[:3]:
                            if isinstance(m, dict):
                                self.stdout.write(
                                    f"      chat_msg: {(m.get('text') or m.get('TEXT') or '')[:120]!r}"
                                )
                except BxErr as exc:
                    self.stdout.write(self.style.WARNING(f"    chat_probe FAIL: {exc}"))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"    history/chat probe FAIL: {exc}"))

        if options["pull"]:
            changed = pull_task_status_from_bitrix(task)
            task.refresh_from_db()
            running = task.time_entries.filter(ended_at__isnull=True).exists()
            self.stdout.write(
                self.style.SUCCESS(
                    f"  pull changed={changed} now status={task.status} "
                    f"sync={task.sync_status} timer_running={running}"
                )
            )
