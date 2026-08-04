# Generated manually for WorkReport.Status.DISMISSED

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0021_backlog_item_workflow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workreport",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Manager review"),
                    ("pending_client", "Pending client"),
                    ("disputed", "Disputed"),
                    ("accepted", "Accepted"),
                    ("paid", "Paid"),
                    ("dismissed", "Dismissed"),
                ],
                db_index=True,
                default="draft",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="workreportevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("sent", "Sent"),
                    ("accepted", "Accepted"),
                    ("disputed", "Disputed"),
                    ("paid", "Paid"),
                    ("reopened", "Reopened"),
                    ("dismissed", "Dismissed"),
                ],
                max_length=32,
            ),
        ),
    ]
