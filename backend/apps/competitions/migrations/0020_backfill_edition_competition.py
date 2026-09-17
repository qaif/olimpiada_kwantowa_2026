"""Backfill wydania B: każda edycja w tej bazie należy do Konkursu #1.

Jeden ``UPDATE`` bez warunku po stronie własności – w bazie jednokonkursowej nie ma innego
właściciela (``docs/UNIWERSALNY-ETAP-1.md`` § 0.1, § 4.2). Warunek jest wyłącznie na ``NULL``, żeby
powtórzony przebieg nie nadpisał wartości wpisanej w międzyczasie przez kod: wydania B i C stoją
obok siebie, a ``Edition.save()`` właściciela już wypełnia.

Ta jedna kolumna przypisuje właściciela **całej domenie zawodów**: etapy, zadania, wpisy, terminy
rozmów i wydarzenia dochodzą do konkursu przez edycję, więc nie mają czego backfillować.

Migracja jest odwracalna wprost, a nie przez ``noop``: kolumna po cofnięciu zostaje na miejscu
(zdejmuje ją dopiero odwrotność ``0019``), więc bez jawnej odwrotności cofnięta migracja
zostawiłaby dane, których ponowny przebieg by nie ruszył.
"""

from django.db import migrations


def sole_competition(apps):
    """Identyfikator konkursu, do którego należy cała zastana baza – albo ``None``.

    ``None`` znaczy „świeża instalacja bez drzewa stron” (``tenancy.0002`` nie miała z czego
    utworzyć konkursu) i jest poprawną odpowiedzią: pusta baza nie ma czego backfillować.

    Więcej niż jeden konkurs **przerywa wdrożenie**. Ta migracja opiera się w całości na założeniu
    „wszystko, co tu stoi, ma jednego właściciela”; przy dwóch konkursach założenie jest fałszywe,
    a jego cichy skutek to przepisanie edycji organizatora A na organizatora B. Ten sam warunek
    i to samo zdanie stoją w ``accounts.0020`` – celowo, bo to ta sama reguła.

    Czytamy **sam klucz**, a nie cały wiersz, i to nie jest oszczędność bajtów: migracja danych
    bywa odgrywana na stanie historycznym, w którym tabela konkursów ma mniej kolumn niż model
    (``apps/tenancy/tests/test_migration_0002.py`` przewija ``tenancy`` do ``0002``, czyli przed
    ``0003_prefixes``). ``SELECT *`` pytałby wtedy o kolumnę, której jeszcze nie ma. Ten sam wzorzec
    stoi w ``accounts.0020``.
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
    competition_id = sole_competition(apps)
    if competition_id is None:
        return
    apps.get_model("competitions", "Edition").objects.filter(competition__isnull=True).update(
        competition_id=competition_id
    )


def backwards(apps, schema_editor):
    competition_id = sole_competition(apps)
    if competition_id is None:
        return
    apps.get_model("competitions", "Edition").objects.filter(competition_id=competition_id).update(
        competition=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0019_edition_competition"),
    ]

    operations = [
        # ``elidable=False``: tej migracji nie wolno zwinąć przy ``squashmigrations``. Jest
        # jednorazowym przepisaniem produkcyjnych danych, a nie krokiem budowy schematu.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
