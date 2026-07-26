from django.db import migrations, models


def dedupe_active_deal_bindings(apps, schema_editor):
    """Keep newest active binding per (agency, deal_id); deactivate the rest."""
    PortalDealBinding = apps.get_model("portals", "PortalDealBinding")
    seen: set[tuple[int, str]] = set()
    qs = (
        PortalDealBinding.objects.filter(is_active=True)
        .order_by("-updated_at", "-id")
        .only("id", "agency_portal_id", "deal_id")
    )
    for row in qs.iterator():
        key = (row.agency_portal_id, str(row.deal_id))
        if key in seen:
            PortalDealBinding.objects.filter(pk=row.id).update(is_active=False)
        else:
            seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ("portals", "0007_hours_credit_idempotency"),
    ]

    operations = [
        migrations.RunPython(dedupe_active_deal_bindings, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="portaldealbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("agency_portal", "deal_id"),
                name="uniq_active_deal_binding_per_deal",
            ),
        ),
    ]
