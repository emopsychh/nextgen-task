from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Coalesce


def backfill_seen_outcomes(apps, schema_editor):
    Task = apps.get_model("board", "Task")
    Task.objects.filter(status="done", outcome_seen_at__isnull=True).update(
        outcome_seen_at=Coalesce(F("completed_at"), F("updated_at"))
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0025_task_completed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="awaiting_client_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="outcome_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_seen_outcomes, noop),
    ]
