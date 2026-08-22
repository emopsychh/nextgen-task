from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0009_agencyuserpreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="portal",
            name="timezone",
            field=models.CharField(blank=True, default="Europe/Moscow", max_length=64),
        ),
    ]
