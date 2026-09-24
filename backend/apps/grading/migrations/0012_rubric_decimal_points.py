"""Maksimum kryterium rubryki jako ``numeric(7, 2)`` – ułamki w rubrykach (po wydaniu 0.35.0).

W etapie z dowolnymi wartościami ocen (``ScoringScale.free_values``) kryterium bywa warte 2,5 pkt.
Rzutowanie ``smallint`` → ``numeric`` jest w PostgreSQL bezstratne: każde dotychczasowe maksimum
zostaje tą samą liczbą (4 → 4.00), a punkty cząstkowe w ``Review.rubric`` (JSON) nie zmieniają się
wcale – dalej są liczbami całkowitymi, dopóki recenzent nie wpisze ułamka.

Więz „maksimum dodatnie” zmienia brzmienie z ``>= 1`` na ``> 0`` (pod tą samą nazwą): 0,5 pkt jest
w etapie dowolnym poprawnym maksimum. Każdy istniejący wiersz spełnia oba warunki, więc założenie
więzu niczego nie odrzuci.

Blokada ``ACCESS EXCLUSIVE`` na tabeli kryteriów (dziesiątki wierszy na zadanie) trwa ułamek sekundy.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grading", "0011_decimal_scores"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="rubriccriterion",
            name="grading_rubric_criterion_max_points_positive",
        ),
        migrations.AlterField(
            model_name="rubriccriterion",
            name="max_points",
            field=models.DecimalField(decimal_places=2, max_digits=7, verbose_name="maksimum punktów"),
        ),
        migrations.AddConstraint(
            model_name="rubriccriterion",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_points__gt", 0)),
                name="grading_rubric_criterion_max_points_positive",
            ),
        ),
    ]
