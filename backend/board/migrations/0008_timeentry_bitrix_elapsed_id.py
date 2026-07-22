# Generated manually for TimeEntry.bitrix_elapsed_id

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0007_project_bitrix_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="bitrix_elapsed_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
