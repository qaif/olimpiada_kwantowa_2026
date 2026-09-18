"""Kategorie uczestników: nowa tabela i nullowalna kolumna przy wpisie do etapu (§ 1.2.4).

**Migracja nie zmienia ani jednego wiersza.** Konkurs #1 kategorii nie ma: tabela ``competitions_category``
powstaje pusta, ``competitions_stageentry.category_id`` dostaje ``NULL`` wszędzie i tak zostaje,
a flaga ``categories`` jest domyślnie wyłączona (``apps/tenancy/models.py``, ``FEATURE_DEFAULTS``).
Ranking, próg kwalifikacji i snapshot tabeli wyników liczą się dokładnie tak, jak przed etapem 2 –
to jest warunek z ``docs/UNIWERSALNY-ETAP-2.md`` § 0.1.

Dlatego też nie ma tu ani ``RunPython``, ani backfillu, ani wartości domyślnej: ``ADD COLUMN ... NULL``
bez ``DEFAULT`` w PostgreSQL 16 nie przepisuje pliku tabeli, więc wdrożenie nie blokuje tabeli wpisów
nawet przy komplecie uczestników. Domknięcia na ``NOT NULL`` nie będzie nigdy – „bez kategorii” jest
poprawnym i docelowym stanem konkursu, a nie brakiem do uzupełnienia (§ 0.7).

Obie operacje są odwracalne z definicji (``CreateModel`` → ``DROP TABLE``, ``AddField`` → ``DROP COLUMN``),
więc ``reverse_code`` nie ma czego opisywać.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0021_edition_competition_not_null"),
        # Kategoria ma **własną** kolumnę konkursu (jest konfiguracją konkursu, nie rocznika),
        # więc tabela konkursów musi już stać.
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("code", models.SlugField(max_length=32, verbose_name="kod")),
                ("name", models.CharField(max_length=120, verbose_name="nazwa")),
                ("position", models.PositiveSmallIntegerField(default=0, verbose_name="kolejność")),
                (
                    "grade_min",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="klasa od"),
                ),
                (
                    "grade_max",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="klasa do"),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="aktywna")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="categories",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
            ],
            options={
                "verbose_name": "kategoria",
                "verbose_name_plural": "kategorie",
                "ordering": ("competition", "position", "id"),
            },
        ),
        migrations.AddField(
            model_name="stageentry",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="stage_entries",
                to="competitions.category",
                verbose_name="kategoria",
            ),
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(
                fields=("competition", "code"), name="competitions_category_unique_code"
            ),
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.CheckConstraint(
                condition=Q(grade_min__isnull=True)
                | Q(grade_max__isnull=True)
                | Q(grade_min__lte=F("grade_max")),
                name="competitions_category_grades_ordered",
            ),
        ),
    ]
