"""Punkty sugestii AI jako ``numeric(7, 2)`` – ta sama precyzja, co maksimum zadania (wydanie 0.35.0).

Wydanie 0.35.0 podniosło kolumny punktów zadania i ocen do ``numeric(7, 2)``, a ``proposed_points``
i ``max_points`` sugestii AI zostały przy ``numeric(6, 2)`` (uwaga integratora 0.35.0). Zadanie
o maksimum powyżej 9 999,99 dałoby wtedy sugestię, której kolumna nie mieści. Poszerzenie typu
``numeric`` w PostgreSQL jest bezstratne: każda zapisana wartość zostaje tą samą liczbą.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_grading", "0002_providers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="aiassessment",
            name="proposed_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="proponowane punkty"
            ),
        ),
        migrations.AlterField(
            model_name="aiassessment",
            name="max_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="maksimum"
            ),
        ),
    ]
