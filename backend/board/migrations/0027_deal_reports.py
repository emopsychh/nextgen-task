from django.db import migrations, models
import django.db.models.deletion


def backfill_deal_reports(apps, schema_editor):
    WorkReport = apps.get_model("board", "WorkReport")
    WorkReportLine = apps.get_model("board", "WorkReportLine")
    PortalDealBinding = apps.get_model("portals", "PortalDealBinding")

    used_binding_ids = set(
        WorkReport.objects.exclude(deal_binding_id=None).values_list(
            "deal_binding_id", flat=True
        )
    )
    for report in WorkReport.objects.filter(deal_binding_id=None).order_by("id"):
        portal_id = report.portal_id
        if not portal_id and report.project_id:
            project = apps.get_model("board", "Project").objects.filter(
                pk=report.project_id
            ).first()
            portal_id = project.portal_id if project else None
        if not portal_id:
            continue
        binding = (
            PortalDealBinding.objects.filter(client_portal_id=portal_id)
            .exclude(id__in=used_binding_ids)
            .order_by("-is_active", "-updated_at", "-id")
            .first()
        )
        if binding:
            report.deal_binding_id = binding.id
            report.portal_id = portal_id
            report.save(update_fields=["deal_binding", "portal"])
            used_binding_ids.add(binding.id)

    for binding in PortalDealBinding.objects.exclude(id__in=used_binding_ids).order_by("id"):
        WorkReport.objects.create(
            portal_id=binding.client_portal_id,
            deal_binding_id=binding.id,
            status="draft",
        )

    reserved_tasks = set()
    for line in WorkReportLine.objects.order_by("report_id", "id"):
        if line.task_id in reserved_tasks:
            continue
        line.is_reserved = True
        line.save(update_fields=["is_reserved"])
        reserved_tasks.add(line.task_id)


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0010_portal_timezone"),
        ("board", "0026_task_attention_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="timeentry",
            name="billed_deal_binding",
            field=models.ForeignKey(
                blank=True,
                help_text="CRM deal binding against which this entry was billed",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="billed_time_entries",
                to="portals.portaldealbinding",
            ),
        ),
        migrations.AddField(
            model_name="workreport",
            name="deal_binding",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_report",
                to="portals.portaldealbinding",
            ),
        ),
        migrations.AddField(
            model_name="workreportline",
            name="is_reserved",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_deal_reports, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="workreportline",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="workreportline",
            name="is_reserved",
            field=models.BooleanField(default=True),
        ),
        migrations.AddConstraint(
            model_name="workreportline",
            constraint=models.UniqueConstraint(
                fields=("report", "task"),
                name="uniq_work_report_line_task",
            ),
        ),
        migrations.AddConstraint(
            model_name="workreportline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_reserved", True)),
                fields=("task",),
                name="uniq_reserved_report_task",
            ),
        ),
    ]
