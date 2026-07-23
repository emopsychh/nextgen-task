# Generated manually for WorkReportLine + attachments

import django.db.models.deletion
from django.db import migrations, models

import board.models


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0014_work_report"),
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkReportLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("work_done", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="board.workreport",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_report_lines",
                        to="board.task",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
                "unique_together": {("report", "task")},
            },
        ),
        migrations.CreateModel(
            name="WorkReportLineAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=board.models.attachment_upload_to)),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="board.workreportline",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="work_report_attachments",
                        to="portals.bitrixuser",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
