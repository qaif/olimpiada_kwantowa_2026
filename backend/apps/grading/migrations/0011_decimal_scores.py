"""Oceny dziesiętne (wydanie 0.35.0): ``Review.score`` i ``FinalGrade.score`` jako ``numeric(7, 2)``.

W etapie z dowolnymi wartościami (``competitions.ScoringScale.free_values``) recenzent wpisuje np.
4,25. Rzutowanie ``smallint`` → ``numeric`` jest w PostgreSQL bezstratne: każda wystawiona dotąd
ocena zostaje tą samą liczbą (5 → 5.00), więc konsensus, moderacja i tabele wyników liczą się po
migracji dokładnie tak, jak przed nią.

Więz „ocena nie jest ujemna” dawał dotąd typ ``PositiveSmallIntegerField``; po zmianie typu stoi
jawnie jako ``CheckConstraint`` – oceny ujemnych skal leżą w bazie przesunięte o
``ScoringScale.offset``, więc liczba ujemna w tej kolumnie nadal jest błędem serwisu.

Blokada ``ACCESS EXCLUSIVE`` na czas przepisania tabeli recenzji – szacunek w ``docs/OPERACJE.md``.
Zależność wyłącznie od poprzedniej migracji tej aplikacji (patrz ``competitions.0032``).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grading", "0010_reviewer_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="finalgrade",
            name="score",
            field=models.DecimalField(decimal_places=2, max_digits=7, verbose_name="punkty"),
        ),
        migrations.AlterField(
            model_name="review",
            name="score",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="punkty"),
        ),
        migrations.AddConstraint(
            model_name="finalgrade",
            constraint=models.CheckConstraint(
                condition=models.Q(("score__gte", 0)), name="grading_finalgrade_score_non_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="review",
            constraint=models.CheckConstraint(
                condition=models.Q(("score__isnull", True), ("score__gte", 0), _connector="OR"),
                name="grading_review_score_non_negative",
            ),
        ),
    ]
