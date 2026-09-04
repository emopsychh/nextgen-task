from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("board", "0027_deal_reports"),
    ]

    operations = [
        migrations.AddField(
            model_name="backlogitem",
            name="source",
            field=models.CharField(
                choices=[("agency", "Агентство"), ("client", "Клиент")],
                db_index=True,
                default="agency",
                max_length=16,
            ),
        ),
    ]
