"""Backfill wydania D: każde zastane zgłoszenie należy do organizatora Konkursu #1.

Zastana baza jest bazą jednokonkursową, więc każda sprawa, która w niej leży, została napisana do
**tego** organizatora i przez niego przeczytana (``docs/UNIWERSALNY-ETAP-1.md`` § 0.1, § 4.2).
Zostawienie tych wierszy z pustym konkursem znaczyłoby po wdrożeniu „sprawa do operatora
platformy”, czyli wyprowadziłoby całą dotychczasową kolejkę koordynatora z jego panelu – regresja
widoczna dla Olimpiady Kwantowej, czyli dokładnie to, czego zabrania § 0.

Warunek jest wyłącznie na ``NULL``, żeby powtórzony przebieg nie nadpisał wartości wpisanej
w międzyczasie przez kod (``open_ticket`` właściciela już wypełnia).

Migracja jest odwracalna wprost, a nie przez ``noop``: kolumna po cofnięciu zostaje na miejscu
(zdejmuje ją dopiero odwrotność ``0002``), więc bez jawnej odwrotności cofnięta migracja
zostawiłaby dane, których ponowny przebieg by nie ruszył.
"""

from django.db import migrations


def sole_competition_id(apps):
    """Identyfikator jedynego konkursu instalacji albo ``None``, gdy nie ma go z czego wziąć.

    Ta sama reguła i to samo zdanie, co w ``cms.0022``, ``competitions.0020`` i ``accounts.0020``:
    więcej niż jeden konkurs przerywa wdrożenie, bo założenie „wszystko tu ma jednego właściciela”
    jest wtedy fałszywe, a jego cichym skutkiem byłaby cudza korespondencja w cudzej kolejce.

    Sam **identyfikator**, a nie wiersz, i to nie jest oszczędność: ``values_list("pk")`` wybiera
    jedną kolumnę, a pobranie obiektu wybrałoby wszystkie – łącznie z tymi, których w bazie
    w danej chwili nie ma. Przy cofaniu migracji kolumny dokładane przez ``tenancy`` bywają już
    zdjęte, choć model historyczny wciąż je zna, i wtedy ``SELECT *`` kończy się błędem bazy
    w środku migracji odwrotnej.
    """
    Competition = apps.get_model("tenancy", "Competition")
    rows = list(Competition.objects.order_by("pk").values_list("pk", flat=True)[:2])
    if len(rows) > 1:
        raise RuntimeError(
            "Backfill konkursu działa wyłącznie na bazie jednokonkursowej "
            "(znaleziono więcej niż jeden Competition)."
        )
    return rows[0] if rows else None


def forwards(apps, schema_editor):
    competition_id = sole_competition_id(apps)
    if competition_id is None:
        return
    apps.get_model("support", "SupportTicket").objects.filter(competition__isnull=True).update(
        competition_id=competition_id
    )


def backwards(apps, schema_editor):
    competition_id = sole_competition_id(apps)
    if competition_id is None:
        return
    apps.get_model("support", "SupportTicket").objects.filter(competition_id=competition_id).update(
        competition=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0002_supportticket_competition"),
    ]

    operations = [
        # ``elidable=False``: jednorazowe przepisanie produkcyjnych danych, a nie krok budowy
        # schematu – ``squashmigrations`` nie ma prawa go zwinąć.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
