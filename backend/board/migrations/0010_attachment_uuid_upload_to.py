# Generated manually for UUID-based attachment storage paths

from django.db import migrations, models

import board.models


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0009_due_datetime_attachment_bitrix"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attachment",
            name="file",
            field=models.FileField(upload_to=board.models.attachment_upload_to),
        ),
    ]
