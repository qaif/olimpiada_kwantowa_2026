"""Przebieg zawodów jako dane: dwie nowe tabele i warunek przy więzi rodzaju etapu (§ 1.2.2, § 1.2.5).

**Migracja nie zmienia ani jednego istniejącego wiersza.** ``competitions_pipelinestep``
i ``competitions_transitionrule`` powstają puste (wypełnia je dopiero ``0024``), a ``STAGE_ORDER``
i ``QualificationRule`` zostają jedynym czytanym źródłem kolejności i progu, dopóki konkurs ma
flagę ``process_editor`` wyłączoną – czyli dla Olimpiady Kwantowej zawsze na tym wydaniu.

Dwie operacje dotykają zastanej tabeli i obie są bez skutku dla jej zawartości:

- ``AlterField`` na ``stage.kind`` dokłada wartość ``ROUND`` do listy wyboru. ``choices`` żyje
  wyłącznie w Pythonie – PostgreSQL nie dostaje tu żadnego DDL-a, bo kolumna była i zostaje
  ``varchar(16)``;
- ``RemoveConstraint`` + ``AddConstraint`` **pod tą samą nazwą** zamienia unikalne (edycja, rodzaj)
  na warunkowe: reguła obowiązuje każdy rodzaj poza ``ROUND``. Kolejność operacji jest istotna
  (najpierw zdjęcie, potem założenie), a nazwa nietknięta celowo – ``competitions_stage_unique_kind``
  ma zostać tym samym bytem w logach wdrożenia i w ``\\d+``, a nie nowym więzem do wytłumaczenia
  (ta sama reguła, co w etapie 1 § 1.5). W bazie Olimpiady Kwantowej nie ma ani jednego etapu
  rodzaju ``ROUND``, więc indeks częściowy obejmuje dokładnie te same wiersze, co pełny.

Wszystkie operacje są odwracalne z definicji: ``CreateModel`` → ``DROP TABLE``, a para
``RemoveConstraint``/``AddConstraint`` cofa się do pełnego unikalnego (edycja, rodzaj) – co jest
możliwe dopóty, dopóki nikt nie założył dwóch rund w jednej edycji, czyli dopóki flaga jest
wyłączona.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0022_category"),
    ]

    operations = [
        migrations.CreateModel(
            name="PipelineStep",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(verbose_name="miejsce w kolejce")),
                (
                    "off_pipeline",
                    models.BooleanField(default=False, verbose_name="poza torem zawodów"),
                ),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipeline_steps",
                        to="competitions.edition",
                        verbose_name="edycja",
                    ),
                ),
                (
                    "stage",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipeline_step",
                        to="competitions.stage",
                        verbose_name="etap",
                    ),
                ),
            ],
            options={
                "verbose_name": "krok przebiegu",
                "verbose_name_plural": "kroki przebiegu",
                "ordering": ("edition", "position", "id"),
            },
        ),
        migrations.CreateModel(
            name="TransitionRule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("MIN_POINTS", "minimum punktów"),
                            ("TOP_N", "najlepszych N"),
                            ("TOP_N_PER_GROUP", "N w grupie"),
                            ("HYBRID", "minimum punktów ORAZ top N"),
                            ("PERCENTILE", "najlepsze P procent"),
                            ("MANUAL", "wyłącznie decyzja komitetu"),
                        ],
                        max_length=24,
                        verbose_name="tryb",
                    ),
                ),
                (
                    "group_by",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", "bez podziału"),
                            ("REGION", "region"),
                            ("CATEGORY", "kategoria"),
                        ],
                        max_length=16,
                        verbose_name="podział",
                    ),
                ),
                (
                    "min_points",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="minimum punktów"),
                ),
                (
                    "top_n",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="liczba kwalifikowanych"),
                ),
                (
                    "percentile",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="procent"),
                ),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transition_rules",
                        to="competitions.category",
                        verbose_name="kategoria",
                    ),
                ),
                (
                    "step",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transition_rules",
                        to="competitions.pipelinestep",
                        verbose_name="krok",
                    ),
                ),
            ],
            options={
                "verbose_name": "reguła przejścia",
                "verbose_name_plural": "reguły przejścia",
                "ordering": ("step", "position", "id"),
            },
        ),
        migrations.RemoveConstraint(
            model_name="stage",
            name="competitions_stage_unique_kind",
        ),
        migrations.AlterField(
            model_name="stage",
            name="kind",
            field=models.CharField(
                choices=[
                    ("ELIM", "Eliminacje"),
                    ("DISTRICT", "Wojewódzki"),
                    ("FINAL", "Finał"),
                    ("TRAINING", "Trening"),
                    ("ROUND", "Runda"),
                ],
                max_length=16,
                verbose_name="rodzaj",
            ),
        ),
        migrations.AddConstraint(
            model_name="stage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "ROUND"), _negated=True),
                fields=("edition", "kind"),
                name="competitions_stage_unique_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="pipelinestep",
            constraint=models.UniqueConstraint(
                condition=models.Q(("off_pipeline", False)),
                fields=("edition", "position"),
                name="competitions_pipelinestep_unique_position",
            ),
        ),
        migrations.AddConstraint(
            model_name="transitionrule",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("percentile__isnull", True),
                    models.Q(("percentile__gte", 1), ("percentile__lte", 100)),
                    _connector="OR",
                ),
                name="competitions_transitionrule_percentile_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="transitionrule",
            constraint=models.CheckConstraint(
                condition=models.Q(("top_n__isnull", True), ("top_n__gte", 1), _connector="OR"),
                name="competitions_transitionrule_top_n_positive",
            ),
        ),
    ]
