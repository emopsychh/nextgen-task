# Generated manually for PortalLink.bitrix_company_id

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0003_portaldealbinding_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="portallink",
            name="bitrix_company_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
