"""Schemat wydania B dla rozwiązań: jedyna kolumna denormalizacyjna całego etapu 1.

``Submission.competition`` powtarza to, co da się odczytać przez ``entry → stage → edition``
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.4). Denormalizacja jest tu dopuszczona jako **wyjątek** i ma
mierzalny powód: panel koordynatora i lista przydziałów recenzenta filtrują po pracach kilkanaście
razy na żądanie, a złączenie trzech tabel przy każdym takim zapytaniu jest kosztem bez pożytku.

``SET_NULL``, a nie ``PROTECT`` jak przy edycji: to jest **kopia**, a nie źródło własności.
Skasowanie konkursu ma zatrzymać się na ``Edition`` – tam stoi klucz, który tego broni – a nie
odbijać się drugi raz o tabelę z kilkudziesięcioma tysiącami wierszy.

Kolumna jest nullowalna przez całe wydanie B (§ 4.1); ``NOT NULL`` wchodzi w wydaniu D, po
zapytaniu kontrolnym (§ 4.4). Spójności z drogą przez rodzica nie da się wyrazić
``CheckConstraint``-em (warunek sięga innej tabeli), więc pilnują jej ``Submission.save()``
i test ``test_submission_competition_matches_entry``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0004_submissionfile_page_count"),
        ("tenancy", "0002_competition_from_site"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submissions",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
