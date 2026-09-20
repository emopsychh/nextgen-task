from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0013_deal_binding_money"),
    ]

    operations = [
        migrations.AddField(
            model_name="portal",
            name="organization",
            field=models.CharField(
                blank=True,
                help_text='Юридическое название организации, например АНО "ЧИКАГА"',
                max_length=255,
            ),
        ),
    ]
