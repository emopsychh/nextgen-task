from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0010_portal_timezone"),
    ]

    operations = [
        migrations.AddField(
            model_name="portallink",
            name="hours_overage",
            field=models.DecimalField(
                blank=True, decimal_places=2, default=0, max_digits=10
            ),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_overage_source_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_overage_source_title",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_overage_applied_to_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_overage_last_amount",
            field=models.DecimalField(
                blank=True, decimal_places=2, default=0, max_digits=10
            ),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_overage_last_source_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="hours_overage_applied",
            field=models.DecimalField(
                blank=True, decimal_places=2, default=0, max_digits=10
            ),
        ),
    ]
