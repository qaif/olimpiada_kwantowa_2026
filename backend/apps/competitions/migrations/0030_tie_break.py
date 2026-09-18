"""Porządek rozstrzygania remisów jako dane (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.6 c).

Sama tabela, **pusta i bez ani jednego wiersza dla Konkursu #1**. Migracja niczego nie wpisuje
i to jest cała jej ostrożność: regulamin Olimpiady Kwantowej remisów nie rozstrzyga, więc etap bez
wierszy ``TieBreak`` ma układać tabelę dokładnie tak, jak układał przed etapem 2 – remis to samo
miejsce, porządek wydruku po ``public_code`` (§ 0.1, § 5.2).

Odwracalna z definicji – cofnięcie usuwa pustą tabelę i nie ma czego stracić. Nie zmienia ani
jednego pola ``Stage``, ``Problem``, ``StageComponent`` ani ``StageEntry`` i nie włącza żadnej flagi;
czytelnikiem tabeli jest ``results.services.tie_break_keys``, a ten pyta o nią dopiero przy
włączonej fladze ``weighted_scoring``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0029_stage_component"),
    ]

    operations = [
        migrations.CreateModel(
            name="TieBreak",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "key",
                    models.CharField(
                        choices=[
                            ("HIGHEST_SINGLE", "najwyższy wynik w jednym zadaniu"),
                            ("PROBLEM_SCORE", "wynik we wskazanym zadaniu"),
                            ("COMPONENT_SCORE", "wynik we wskazanym komponencie"),
                            ("SOLVED_COUNT", "liczba zadań z pełnym wynikiem"),
                            ("SUBMITTED_AT", "wcześniejsze oddanie ostatniej pracy"),
                            ("NONE", "bez rozstrzygania (wspólne miejsce)"),
                        ],
                        max_length=24,
                        verbose_name="kryterium",
                    ),
                ),
                ("descending", models.BooleanField(default=True, verbose_name="malejąco")),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                (
                    "component",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tie_breaks",
                        to="competitions.stagecomponent",
                        verbose_name="komponent",
                    ),
                ),
                (
                    "problem",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tie_breaks",
                        to="competitions.problem",
                        verbose_name="zadanie",
                    ),
                ),
                (
                    "stage",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tie_breaks",
                        to="competitions.stage",
                        verbose_name="etap",
                    ),
                ),
            ],
            options={
                "verbose_name": "rozstrzyganie remisu",
                "verbose_name_plural": "rozstrzyganie remisów",
                "ordering": ("stage", "position", "id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("stage", "position"),
                        name="competitions_tiebreak_unique_position",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("key", "PROBLEM_SCORE"), _negated=True),
                            ("problem__isnull", False),
                            _connector="OR",
                        ),
                        name="competitions_tiebreak_problem_required",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("key", "COMPONENT_SCORE"), _negated=True),
                            ("component__isnull", False),
                            _connector="OR",
                        ),
                        name="competitions_tiebreak_component_required",
                    ),
                ],
            },
        ),
    ]
