"""Wydanie D dla prac: kolumna denormalizacyjna przestaje dopuszczać „pracę niczyją”.

``Submission.competition`` jest jedyną kolumną denormalizacyjną etapu 1
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.4): powtarza wynik drogi ``entry → stage → edition``, żeby
panel koordynatora i kolejka recenzenta nie ciągnęły trzech złączeń kilkanaście razy na żądanie.
Kolumna, po której się **filtruje**, z wartością ``NULL`` nie jest kolumną z luką – jest pracą
niewidoczną dla własnego koordynatora, i to niewidoczną cicho.

Zmiana ``on_delete`` z ``SET_NULL`` na ``PROTECT`` nie jest osobną decyzją, tylko konsekwencją
``NOT NULL``: reguła „wyzeruj przy skasowaniu” i zakaz wartości pustej wykluczają się wzajemnie
(Django odmawia takiego modelu kontrolą ``fields.E011``). Praktycznie nic to nie zmienia –
skasowanie konkursu zatrzymuje się wcześniej, na ``PROTECT`` przy ``Edition``.

Przed ``ALTER TABLE`` idzie zapytanie kontrolne (§ 4.4), tak samo jak przy edycji: niezerowy wynik
przerywa wdrożenie z nazwą tabeli, zanim schemat zostanie ruszony.
"""

import django.db.models.deletion
from django.db import migrations, models

TABLE = "submissions_submission"


def check_no_submission_without_competition(apps, schema_editor):
    """Zapytanie kontrolne przed ``NOT NULL`` (§ 4.4). Modele historyczne, jedno zapytanie liczące.

    Praca bez konkursu znaczy tu coś innego niż edycja bez konkursu: nie „nie wiadomo, czyja”,
    tylko „backfill jej nie zobaczył”, bo jej etap nie miał wtedy właściciela. Dlatego komunikat
    wskazuje **obie** migracje backfillu – naprawa zaczyna się od edycji.
    """
    Submission = apps.get_model("submissions", "Submission")
    orphans = Submission.objects.filter(competition__isnull=True).count()
    if orphans:
        raise RuntimeError(
            f"{TABLE}: {orphans} prac bez konkursu. Uruchom backfill edycji "
            "(competitions.0020_backfill_edition_competition), a po nim backfill prac "
            "(submissions.0006_backfill_submission_competition), zanim powtórzysz migrację."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0006_backfill_submission_competition"),
        # Praca dochodzi do konkursu przez edycję; jej ``NOT NULL`` ma stać wcześniej, bo to on
        # gwarantuje, że backfill prac miał skąd wziąć właściciela.
        ("competitions", "0021_edition_competition_not_null"),
    ]

    operations = [
        migrations.RunPython(
            check_no_submission_without_competition, migrations.RunPython.noop, elidable=False
        ),
        migrations.AlterField(
            model_name="submission",
            name="competition",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
