"""Backfill wydania C: każdy komunikat w tej bazie należy do Konkursu #1.

Jeden ``UPDATE`` bez warunku po stronie własności – w bazie jednokonkursowej nie ma innego
właściciela (``docs/UNIWERSALNY-ETAP-1.md`` § 0.1, § 4.2). Warunek jest wyłącznie na ``NULL``, żeby
powtórzony przebieg nie nadpisał wartości wpisanej w międzyczasie przez kod: wydania C i D stoją
obok siebie, a ``Announcement.save()`` właściciela już wypełnia.

Bez tego kroku produkcja straciłaby baner: po wydaniu C zapytanie baneru zawęża się do konkursu
żądania, a komunikat bez konkursu nie należy do żadnego – czyli komunikat, który wisiał na stronie
przed wdrożeniem, zniknąłby z niej po wdrożeniu. To jest dokładnie ta klasa regresji, której
zabrania § 0, i jedyny powód, dla którego ta migracja jest osobnym, nieusuwalnym krokiem.

Migracja jest odwracalna wprost, a nie przez ``noop``: kolumna po cofnięciu zostaje na miejscu
(zdejmuje ją dopiero odwrotność ``0021``), więc bez jawnej odwrotności cofnięta migracja
zostawiłaby dane, których ponowny przebieg by nie ruszył.
"""

from django.db import migrations


def sole_competition(apps):
    """Konkurs, do którego należy cała zastana baza – albo ``None``, gdy nie ma go z czego wziąć.

    ``None`` znaczy „świeża instalacja bez drzewa stron” (``tenancy.0002`` nie miała z czego
    utworzyć konkursu) i jest poprawną odpowiedzią: pusta baza nie ma czego backfillować.

    Więcej niż jeden konkurs **przerywa wdrożenie**. Ta migracja opiera się w całości na założeniu
    „wszystko, co tu stoi, ma jednego właściciela”; przy dwóch konkursach założenie jest fałszywe,
    a jego cichy skutek to ogłoszenie organizatora A na stronie organizatora B. Ten sam warunek
    i to samo zdanie stoją w ``competitions.0020`` i ``accounts.0020`` – celowo, bo to ta sama reguła.

    Zwracamy **klucz główny**, nie obiekt, i pytamy wyłącznie o kolumnę ``id``. Model historyczny
    ``Competition`` ma tyle kolumn, ile miał w swoim miejscu planu migracji, a późniejsze migracje
    ``tenancy`` (``0003_prefixes``) bywają zdejmowane **przed** tą przy cofaniu bazy – kolejności nie
    da się wymusić zależnością w żadną stronę, bo ta migracja jest na produkcji już wykonana.
    ``SELECT id`` jest odpowiedzią odporną na to z definicji: kolumna klucza głównego istnieje
    w każdym stanie schematu, w którym tabela w ogóle jest.
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
    apps.get_model("cms", "Announcement").objects.filter(competition__isnull=True).update(
        competition_id=competition_id
    )


def backwards(apps, schema_editor):
    competition_id = sole_competition(apps)
    if competition_id is None:
        return
    apps.get_model("cms", "Announcement").objects.filter(competition_id=competition_id).update(
        competition=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0021_announcement_competition"),
    ]

    operations = [
        # ``elidable=False``: tej migracji nie wolno zwinąć przy ``squashmigrations``. Jest
        # jednorazowym przepisaniem produkcyjnych danych, a nie krokiem budowy schematu.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
