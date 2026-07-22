# Generated manually for TimeEntry.billed_to_deal_at

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0004_task_agency_bitrix_task_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="billed_to_deal_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When this session was deducted from the CRM deal remaining hours",
                null=True,
            ),
        ),
    ]
