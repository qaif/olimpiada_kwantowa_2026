"""Komponenty etapu: kilka form w jednym etapie (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3).

Sama tabela, **pusta i bez ani jednego wiersza dla Konkursu #1**. Migracja nie tworzy komponentów
dla istniejących etapów i to jest cała jej ostrożność: etap bez komponentów czyta ``Stage.format``
dokładnie tak, jak czytał przed etapem 2, więc dopóki nikt nie doda wiersza z panelu, tabela
wyników liczy się tą samą drogą i daje te same liczby (§ 0.1, § 5.2).

Odwracalna z definicji – cofnięcie usuwa pustą tabelę i nie ma czego stracić. Nie zmienia ani
jednego pola ``Stage``, ``ScoringScale``, ``Problem`` ani ``StageEntry`` i nie włącza żadnej flagi.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0028_scoring_weights_and_offset"),
    ]

    operations = [
        migrations.CreateModel(
            name="StageComponent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("SUBMISSIONS", "rozwiązania pisemne"),
                            ("QUIZ", "test online"),
                            ("INTERVIEW", "rozmowa"),
                            ("ONSITE", "zawody na miejscu"),
                            ("TEAM", "praca drużynowa"),
                        ],
                        max_length=16,
                        verbose_name="forma",
                    ),
                ),
                ("name", models.CharField(blank=True, max_length=80, verbose_name="nazwa")),
                ("position", models.PositiveSmallIntegerField(default=1, verbose_name="kolejność")),
                (
                    "weight_numerator",
                    models.PositiveSmallIntegerField(default=1, verbose_name="licznik wagi"),
                ),
                (
                    "weight_denominator",
                    models.PositiveSmallIntegerField(default=1, verbose_name="mianownik wagi"),
                ),
                ("required", models.BooleanField(default=True, verbose_name="wymagany")),
                (
                    "stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="components",
                        to="competitions.stage",
                        verbose_name="etap",
                    ),
                ),
            ],
            options={
                "verbose_name": "komponent etapu",
                "verbose_name_plural": "komponenty etapu",
                "ordering": ("stage", "position", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("stage", "kind", "position"), name="competitions_stagecomponent_unique"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("weight_denominator__gte", 1)),
                        name="competitions_stagecomponent_denominator_positive",
                    ),
                ],
            },
        ),
    ]
