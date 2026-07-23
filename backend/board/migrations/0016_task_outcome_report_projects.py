# Task.outcome + WorkReport multi-project (portal + M2M)

import django.db.models.deletion
from django.db import migrations, models


def forwards_portal_and_m2m(apps, schema_editor):
    WorkReport = apps.get_model("board", "WorkReport")
    for report in WorkReport.objects.all().iterator():
        if report.project_id:
            project = report.project
            if not report.portal_id:
                report.portal_id = project.portal_id
                report.save(update_fields=["portal_id"])
            report.projects.add(project)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("board", "0015_work_report_line"),
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="outcome",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="workreport",
            name="portal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_reports",
                to="portals.portal",
            ),
        ),
        migrations.AddField(
            model_name="workreport",
            name="projects",
            field=models.ManyToManyField(
                blank=True, related_name="work_reports_m2m", to="board.project"
            ),
        ),
        migrations.AlterField(
            model_name="workreport",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="work_reports",
                to="board.project",
            ),
        ),
        migrations.RunPython(forwards_portal_and_m2m, noop_reverse),
    ]
