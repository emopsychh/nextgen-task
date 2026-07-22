# Generated manually: due_date DateField → DateTimeField + attachment Bitrix ids

import datetime

from django.db import migrations, models
from django.utils import timezone


def forwards_due_to_datetime(apps, schema_editor):
    Task = apps.get_model("board", "Task")
    for task in Task.objects.exclude(due_date__isnull=True).iterator():
        raw = task.due_date
        if raw is None:
            continue
        if isinstance(raw, datetime.datetime):
            continue
        # Date → end of local day (legacy behaviour)
        dt = datetime.datetime.combine(raw, datetime.time(23, 59, 59))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, datetime.timezone.utc)
        Task.objects.filter(pk=task.pk).update(due_date=dt)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0008_timeentry_bitrix_elapsed_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="due_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(forwards_due_to_datetime, noop_reverse),
        migrations.AddField(
            model_name="attachment",
            name="bitrix_file_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="attachment",
            name="agency_bitrix_file_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
    ]
