from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portals", "0012_bitrixuser_username_password"),
    ]

    operations = [
        migrations.AddField(
            model_name="portaldealbinding",
            name="hourly_rate_rub",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Стоимость часа в рублях",
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="package_rub",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Размер пакета в рублях",
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="portaldealbinding",
            name="balance_rub",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Остаток баланса в рублях",
                max_digits=12,
                null=True,
            ),
        ),
    ]
