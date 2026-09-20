from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0011_hours_overage"),
    ]

    operations = [
        migrations.AddField(
            model_name="bitrixuser",
            name="password",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="bitrixuser",
            name="username",
            field=models.CharField(blank=True, max_length=150, null=True, unique=True),
        ),
    ]
