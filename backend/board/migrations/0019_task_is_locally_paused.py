from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0018_uniq_deal_and_workreport_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="is_locally_paused",
            field=models.BooleanField(default=False),
        ),
    ]
