"""Wagi zadań i przesunięcie skali punktacji (§ 1.2.6 a–b, decyzja organizatora D10).

**Migracja nie zmienia ani jednego wiersza i ani jednej sumy punktów.** Trzy nowe kolumny wchodzą
z wartościami domyślnymi, które odtwarzają dzisiejszą arytmetykę co do liczby:

- ``competitions_problem.weight_numerator = 1`` i ``weight_denominator = 1`` – waga ``1/1``, czyli
  suma etapu policzona przez ``fractions.Fraction`` daje dokładnie tę samą liczbę, co dzisiejsze
  sumowanie ``int`` (``apps/tenancy/tests/test_results_snapshot.py``, § 5.2);
- ``competitions_scoringscale.offset = 0`` – ocena leży w bazie tam, gdzie leżała, a odczyt
  „wartość z bazy minus przesunięcie” jest przy zerze tożsamością.

Żadna kolumna wyników nie zmienia typu: ``Review.score`` i ``FinalGrade.score`` zostają
``PositiveSmallIntegerField``, a ``StageEntry.total_points`` – ``PositiveIntegerField``. To jest
cała treść decyzji D10: punkt ujemny wchodzi przesunięciem skali, a nie zdjęciem bazodanowej
gwarancji „punkt nie bywa ujemny”, która dziś łapie błąd serwisu, zanim dojdzie do tabeli wyników.

``ADD COLUMN ... DEFAULT <stała>`` w PostgreSQL 16 nie przepisuje pliku tabeli (domyślna wartość
nie jest volatile), więc wdrożenie nie blokuje ani tabeli zadań, ani tabeli skal. Więz na mianowniku
wagi jest sprawdzany przy dołożeniu, a wszystkie wiersze mają w tej chwili ``1``, więc skanowanie
tabeli nie ma czego odrzucić.

Wszystkie operacje są odwracalne z definicji (``AddField`` → ``DROP COLUMN``, ``AddConstraint`` →
``DROP CONSTRAINT``), więc ``reverse_code`` nie ma czego opisywać.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0027_stageentry_fee"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoringscale",
            name="offset",
            field=models.SmallIntegerField(default=0, verbose_name="przesunięcie skali"),
        ),
        migrations.AddField(
            model_name="problem",
            name="weight_numerator",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="waga – licznik"),
        ),
        migrations.AddField(
            model_name="problem",
            name="weight_denominator",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="waga – mianownik"),
        ),
        migrations.AddConstraint(
            model_name="problem",
            constraint=models.CheckConstraint(
                condition=models.Q(weight_denominator__gte=1),
                name="competitions_problem_weight_denominator_positive",
            ),
        ),
    ]
