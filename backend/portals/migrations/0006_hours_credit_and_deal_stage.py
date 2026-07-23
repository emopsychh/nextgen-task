from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0005_portallink_bitrix_group_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="portallink",
            name="hours_credit",
            field=models.DecimalField(
                blank=True, decimal_places=2, default=0, max_digits=10
            ),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_credit_source_deal_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portallink",
            name="hours_credit_source_title",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="stage_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="stage_semantic",
            field=models.CharField(
                blank=True,
                help_text="Bitrix stage SEMANTICS: S=success, F=failure, empty=process",
                max_length=8,
            ),
        ),
    ]
