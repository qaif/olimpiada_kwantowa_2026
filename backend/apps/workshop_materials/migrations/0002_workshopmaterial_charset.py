# Napisane ręcznie (WM-FMT-01, 9.10.2026) – odpowiada ``makemigrations``; Docker był wyłączony.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workshop_materials', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='workshopmaterial',
            name='charset',
            field=models.CharField(blank=True, max_length=20, verbose_name='kodowanie'),
        ),
    ]
