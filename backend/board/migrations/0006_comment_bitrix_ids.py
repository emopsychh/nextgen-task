# Generated manually for Comment Bitrix ids

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0005_timeentry_billed_to_deal_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="comment",
            name="bitrix_comment_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="comment",
            name="agency_bitrix_comment_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
    ]
