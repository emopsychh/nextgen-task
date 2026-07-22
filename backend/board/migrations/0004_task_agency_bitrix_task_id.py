from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0003_timeentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="agency_bitrix_task_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
