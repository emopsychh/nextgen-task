# Generated manually for PortalLink.bitrix_group_id

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0004_portallink_bitrix_company_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="portallink",
            name="bitrix_group_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
