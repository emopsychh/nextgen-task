from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("board", "0028_backlog_item_source")]

    operations = [
        migrations.CreateModel(
            name="ProjectMeeting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("scheduled_at", models.DateTimeField(db_index=True)),
                ("duration_minutes", models.PositiveSmallIntegerField(default=30)),
                ("format", models.CharField(choices=[("video", "Видеовстреча"), ("phone", "Звонок"), ("office", "Офлайн")], default="video", max_length=16)),
                ("location", models.CharField(blank=True, max_length=500)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_project_meetings", to="portals.bitrixuser")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meetings", to="board.project")),
            ],
            options={"ordering": ["scheduled_at", "id"]},
        ),
    ]
