import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0023_uniq_agency_bitrix_task_id"),
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="working_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="working_tasks",
                to="portals.bitrixuser",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="working_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
