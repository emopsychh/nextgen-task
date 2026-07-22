# Generated manually for MVP bootstrap

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Portal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("member_id", models.CharField(db_index=True, max_length=64, unique=True)),
                ("domain", models.CharField(max_length=255)),
                (
                    "role",
                    models.CharField(
                        choices=[("agency", "Agency"), ("client", "Client"), ("unknown", "Unknown")],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("name", models.CharField(blank=True, max_length=255)),
                ("access_token", models.TextField(blank=True)),
                ("refresh_token", models.TextField(blank=True)),
                ("application_token", models.CharField(blank=True, max_length=255)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "domain"]},
        ),
        migrations.CreateModel(
            name="BitrixUser",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bitrix_id", models.CharField(max_length=64)),
                ("name", models.CharField(blank=True, max_length=255)),
                ("last_name", models.CharField(blank=True, max_length=255)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("avatar_url", models.URLField(blank=True)),
                ("is_admin", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "portal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="users",
                        to="portals.portal",
                    ),
                ),
            ],
            options={"ordering": ["name", "last_name"]},
        ),
        migrations.CreateModel(
            name="PortalLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agency_portal",
                    models.ForeignKey(
                        limit_choices_to={"role": "agency"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="client_links",
                        to="portals.portal",
                    ),
                ),
                (
                    "client_portal",
                    models.ForeignKey(
                        limit_choices_to={"role": "client"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agency_links",
                        to="portals.portal",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AlterUniqueTogether(
            name="bitrixuser",
            unique_together={("portal", "bitrix_id")},
        ),
        migrations.AlterUniqueTogether(
            name="portallink",
            unique_together={("agency_portal", "client_portal")},
        ),
    ]
