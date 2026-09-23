from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("board", "0030_project_meeting_outcome")]

    operations = [
        migrations.AlterField(
            model_name="projectmeeting",
            name="duration_minutes",
            field=models.PositiveSmallIntegerField(default=60),
        ),
    ]
