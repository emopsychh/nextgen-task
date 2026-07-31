from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0019_task_is_locally_paused"),
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BacklogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_backlog_items",
                        to="portals.bitrixuser",
                    ),
                ),
                (
                    "portal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="backlog_items",
                        to="portals.portal",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
            },
        ),
    ]
