"""Fix Task.due_date values stored as UTC wall-clock (pre-timezone fix).

Before the timezone fix, setting «18:00» often stored 18:00 UTC (wall-as-UTC).
Correct Moscow semantics: 18:00 Europe/Moscow → 15:00 UTC.

If Bitrix already rewrote the value to true UTC (15:00Z for 18 MSK), the new
UI already shows 18:00 — do NOT run this command on those rows.

Usage (on the server, inside web container):

  python manage.py fix_due_timezones --dry-run
  python manage.py fix_due_timezones
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from board.due_dates import AGENCY_DISPLAY_TZ, resolve_zone, wall_to_utc
from board.models import Task


class Command(BaseCommand):
    help = (
        "Reinterpret due_date UTC wall components as Europe/Moscow "
        "and store the correct UTC instant."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only print what would change",
        )
        parser.add_argument(
            "--tz",
            default=AGENCY_DISPLAY_TZ,
            help=f"IANA zone for wall interpretation (default {AGENCY_DISPLAY_TZ})",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        tz = resolve_zone(options["tz"])
        qs = Task.objects.filter(due_date__isnull=False).order_by("id")
        changed = 0
        skipped = 0
        for task in qs.iterator():
            due = task.due_date
            if timezone.is_naive(due):
                due = timezone.make_aware(due, timezone.utc)
            # Current UTC clock face → treat as wall in tz
            wall = due.astimezone(timezone.utc).replace(tzinfo=None)
            new_due = wall_to_utc(wall, tz)
            if new_due == due.astimezone(timezone.utc):
                skipped += 1
                continue
            self.stdout.write(
                f"task={task.id} {due.isoformat()} → {new_due.isoformat()} "
                f"({task.title[:40]!r})"
            )
            if not dry:
                task.due_date = new_due
                task.save(update_fields=["due_date", "updated_at"])
            changed += 1
        prefix = "DRY-RUN " if dry else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}updated={changed} unchanged={skipped} tz={tz}"
            )
        )
