from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0006_hours_credit_and_deal_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="portallink",
            name="hours_credit_applied_to_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_credit_last_amount",
            field=models.DecimalField(
                blank=True, decimal_places=2, default=0, max_digits=10
            ),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_credit_last_source_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
