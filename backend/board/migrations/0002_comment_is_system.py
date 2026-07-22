from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="comment",
            name="is_system",
            field=models.BooleanField(default=False),
        ),
    ]
