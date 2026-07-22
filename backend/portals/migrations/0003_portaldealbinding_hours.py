from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0002_portaldealbinding"),
    ]

    operations = [
        migrations.AddField(
            model_name="portaldealbinding",
            name="paid_hours",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="remaining_hours",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
