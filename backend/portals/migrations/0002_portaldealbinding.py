import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalDealBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("deal_id", models.CharField(max_length=64)),
                ("deal_title", models.CharField(blank=True, max_length=500)),
                ("category_id", models.CharField(blank=True, max_length=64)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agency_portal",
                    models.ForeignKey(
                        limit_choices_to={"role": "agency"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deal_bindings",
                        to="portals.portal",
                    ),
                ),
                (
                    "client_portal",
                    models.ForeignKey(
                        limit_choices_to={"role": "client"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="accompaniment_deals",
                        to="portals.portal",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="portaldealbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("agency_portal", "client_portal"),
                name="uniq_active_deal_binding_per_client",
            ),
        ),
    ]
