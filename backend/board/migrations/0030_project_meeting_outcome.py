from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("board", "0029_project_meeting")]

    operations = [
        migrations.AddField(
            model_name="projectmeeting",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="projectmeeting",
            name="outcome",
            field=models.TextField(blank=True),
        ),
    ]
