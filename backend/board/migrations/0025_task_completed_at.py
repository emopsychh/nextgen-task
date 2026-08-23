from django.db import migrations, models
from django.db.models import F


def backfill_completed_at(apps, schema_editor):
    Task = apps.get_model("board", "Task")
    Task.objects.filter(status="done", completed_at__isnull=True).update(
        completed_at=F("updated_at")
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0024_task_working_presence"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_completed_at, noop),
    ]
