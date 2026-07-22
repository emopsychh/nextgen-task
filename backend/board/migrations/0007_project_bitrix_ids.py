# Generated manually for Project Bitrix parent task / group ids

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0006_comment_bitrix_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="bitrix_task_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="project",
            name="bitrix_group_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
