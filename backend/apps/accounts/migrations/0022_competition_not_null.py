"""Domknięcie wydania D: właściciel konkursu przestaje być opcjonalny w tabelach kont.

Pięć kolumn dołożonych w wydaniu B jako nullowalne (``0019``) i wypełnionych backfillem
(``0020``) dostaje ``NOT NULL``. Od tej chwili wiersz bez właściciela nie ma prawa powstać, więc
znika ostatnia droga, którą profil, kod zaproszenia albo rejestr wysyłek mógłby wyjść poza swój
konkurs (``docs/UNIWERSALNY-ETAP-1.md`` § 4.1, wydanie D).

**Zapytanie kontrolne idzie pierwsze i przerywa wdrożenie.** Gdyby ``AlterField`` trafił na wiersz
z ``NULL``, PostgreSQL przerwałby migrację komunikatem o kolumnie – prawdziwym, ale nie mówiącym
ani ile tych wierszy jest, ani czy problem dotyczy jednej tabeli, czy pięciu. ``RunPython``
sprawdza wszystkie pięć tabel **przed** pierwszą zmianą schematu, wymienia je z nazwy razem
z liczbą wierszy i podnosi ``RuntimeError``; baza zostaje wtedy dokładnie taka, jaka była, a
operator wie, co dosypać (§ 4.4). Kopia z § 0.2 i tak leży obok.

Kontrola **niczego nie naprawia** i to jest decyzja: wiersz bez właściciela na bazie, która miała
jeden konkurs, znaczy, że backfill nie przeszedł albo że coś zapisało profil z pominięciem
serwisu. Dopisanie mu właściciela „z rozsądku” w migracji schematu przypisałoby czyjeś dane
organizatorowi wybranemu przez maszynę.

Dla Konkursu #1 jest to zmiana wyłącznie schematu: po ``0020`` żaden z tych wierszy nie ma
``NULL``, a zachowanie serwisu i panelu nie zmienia się o jedną odpowiedź.
"""

import django.db.models.deletion
from django.db import migrations, models

#: Modele domykane tą migracją, w kolejności z § 3.2 dokumentu. Tabela zamiast pięciu wywołań, bo
#: to jest **lista**, a nie logika – i ma się dać porównać wzrokiem z tabelą w dokumencie.
SCOPED_MODELS = (
    "Participant",
    "CommitteeMember",
    "SchoolSupervisor",
    "InvitationCode",
    "MessageBroadcast",
)


def check_no_orphans(apps, schema_editor):
    """Przerywa wdrożenie, gdy choć jeden wiersz nie ma konkursu. Nic nie zapisuje.

    Liczymy **wszystkie** tabele, a nie przerywamy na pierwszej: operator ma zobaczyć pełen
    rachunek za jednym przebiegiem, a nie odkrywać kolejną tabelę po każdej poprawce.
    """
    broken = []
    for name in SCOPED_MODELS:
        model = apps.get_model("accounts", name)
        count = model.objects.filter(competition__isnull=True).count()
        if count:
            broken.append(f"{model._meta.db_table}: {count}")
    if broken:
        raise RuntimeError(
            "Wiersze bez konkursu – wdrożenie przerwane przed zmianą schematu ("
            + "; ".join(broken)
            + "). Uzupełnij właściciela (backfill accounts.0020) i powtórz migrację."
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0021_participant_per_competition")]

    operations = [
        # ``elidable=False``: kontrola przed ``NOT NULL`` jest częścią procedury wdrożenia, a nie
        # krokiem budowy schematu, który wolno zwinąć przy ``squashmigrations``.
        # Odwrotność jest pusta, bo sprawdzenie niczego nie zmieniło – cofnięcie ma tylko oddać
        # kolumnom ``NULL`` (robią to ``AlterField`` poniżej).
        migrations.RunPython(check_no_orphans, migrations.RunPython.noop, elidable=False),
        migrations.AlterField(
            model_name="participant",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="participants",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AlterField(
            model_name="committeemember",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="committee_members",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AlterField(
            model_name="schoolsupervisor",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="school_supervisors",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AlterField(
            model_name="invitationcode",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invitation_codes",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AlterField(
            model_name="messagebroadcast",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="broadcasts",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
