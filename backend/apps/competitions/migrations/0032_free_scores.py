"""Dowolne wartości ocen (wydanie 0.35.0, prośba organizatora z 2026-09-24).

Dwie rzeczy naraz, bo obie są jednym wydaniem i żadna nie ma sensu bez drugiej:

- ``ScoringScale.free_values`` – przełącznik trybu oceniania etapu. ``AddField`` z domyślnym
  ``False``, czyli **każdy istniejący etap zostaje w trybie „tylko wartości ze skali”** – dokładnie
  w tym, w którym był. Nowe etapy też startują z ``False`` (``create_stage`` nie ustawia pola);
  dowolne wartości włącza organizator na ekranie skali,
- kolumny punktów stają się dziesiętne (``numeric(p, 2)``): ``StageEntry.total_points``,
  ``QualificationRule.min_points``, ``TransitionRule.min_points``, ``InterviewScore.points``
  i ``Problem.max_points`` (samo maksimum zadania bywa od tego wydania ułamkowe, np. 12,5).

``AlterField`` z liczby całkowitej na ``numeric`` jest w PostgreSQL rzutowaniem **bezstratnym**
(``ALTER COLUMN … TYPE numeric(10, 2) USING …::numeric``): 7 staje się 7.00, a porównania, sumy
i sortowanie dają te same wyniki. Kolumny ``Positive*`` niosły w bazie więz ``>= 0`` nakładany
przez Django; po zmianie typu Django go zdejmuje, więc odtwarzamy go jawnie ``CheckConstraint``-ami
– ta sama gwarancja pod nową nazwą. Walidacja więzów przechodzi, bo stare kolumny i tak nie
dopuszczały liczb ujemnych.

Czas i blokady: zmiana typu przepisuje tabelę pod ``ACCESS EXCLUSIVE``. Dla ``StageEntry`` (rząd
dziesiątek tysięcy wierszy na edycję) to ułamek sekundy do kilku sekund; pozostałe tabele są
małe. Szacunek i zalecenie (wdrożenie poza godzinami oceniania) – ``docs/OPERACJE.md``.

Cofnięcie migracji zamienia kolumny z powrotem na całkowite; ocena ułamkowa zapisana po wdrożeniu
zostałaby wtedy **zaokrąglona przez bazę** – cofać wolno wyłącznie przed włączeniem trybu
dowolnego w którymkolwiek etapie.

Zależność wyłącznie od poprzedniej migracji tej aplikacji: operacje dotykają tylko jej własnych
tabel, a zależność od najnowszych migracji innych aplikacji (tak ją wpisał ``makemigrations``)
cofałaby tę migrację razem z każdym testem, który przewija cudzą aplikację – ta sama lekcja, co
przy ``ai_grading.0001`` w wydaniu 0.34.0.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0031_interview_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoringscale",
            name="free_values",
            field=models.BooleanField(default=False, verbose_name="dowolne wartości"),
        ),
        migrations.AlterField(
            model_name="interviewscore",
            name="points",
            field=models.DecimalField(decimal_places=2, max_digits=7, verbose_name="punkty"),
        ),
        migrations.AlterField(
            model_name="problem",
            name="max_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=7, null=True, verbose_name="maksimum punktów"
            ),
        ),
        migrations.AlterField(
            model_name="qualificationrule",
            name="min_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="minimum punktów"
            ),
        ),
        migrations.AlterField(
            model_name="stageentry",
            name="total_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="suma punktów"
            ),
        ),
        migrations.AlterField(
            model_name="transitionrule",
            name="min_points",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="minimum punktów"
            ),
        ),
        migrations.AddConstraint(
            model_name="interviewscore",
            constraint=models.CheckConstraint(
                condition=models.Q(("points__gte", 0)),
                name="competitions_interviewscore_points_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="problem",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_points__isnull", True), ("max_points__gte", 0), _connector="OR"),
                name="competitions_problem_max_points_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="qualificationrule",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_points__isnull", True), ("min_points__gte", 0), _connector="OR"),
                name="competitions_qualificationrule_min_points_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="stageentry",
            constraint=models.CheckConstraint(
                condition=models.Q(("total_points__isnull", True), ("total_points__gte", 0), _connector="OR"),
                name="competitions_stageentry_total_points_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="transitionrule",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_points__isnull", True), ("min_points__gte", 0), _connector="OR"),
                name="competitions_transitionrule_min_points_non_negative",
            ),
        ),
    ]
