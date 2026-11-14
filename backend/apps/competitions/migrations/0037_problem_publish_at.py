# PUB-01: zaplanowana publikacja treści zadania (``Problem.publish_at``). Pisana ręcznie (bez bazy
# przy tworzeniu). Kolumna z NULL-em bez wartości domyślnej – PostgreSQL dopisuje ją bez
# przepisywania tabeli, a NULL znaczy „razem z otwarciem etapu”, czyli dokładnie dotychczasowe
# zachowanie każdego istniejącego zadania.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0036_stage_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="problem",
            name="publish_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="publikacja treści"),
        ),
    ]
