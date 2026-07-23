from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0012_task_is_important"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="client_bitrix_elapsed_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
