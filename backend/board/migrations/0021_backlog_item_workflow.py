from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0020_backlog_item"),
        ("portals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="backlogitem",
            name="assignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_backlog_items",
                to="portals.bitrixuser",
            ),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="converted_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="from_backlog_items",
                to="board.project",
            ),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="converted_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="from_backlog_items",
                to="board.task",
            ),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="is_pinned",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="priority",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "Низкий"), (1, "Обычный"), (2, "Высокий")],
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="sort_order",
            field=models.IntegerField(db_index=True, default=0),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("idea", "Идея"),
                    ("in_progress", "В работе"),
                    ("deferred", "Отложено"),
                    ("done", "Сделано"),
                    ("converted", "В проект/задачу"),
                ],
                db_index=True,
                default="idea",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="backlogitem",
            name="tags",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterModelOptions(
            name="backlogitem",
            options={"ordering": ["-is_pinned", "sort_order", "-priority", "-updated_at", "-id"]},
        ),
    ]
