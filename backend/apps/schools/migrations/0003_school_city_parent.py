"""Gmina przy każdej szkole: „Wrocław-Krzyki” → „Wrocław”, „Śródmieście” → „Warszawa”.

Wykaz SIO zapisuje pięć największych miast dzielnicami (a Warszawę **wyłącznie** dzielnicami),
przez co krok „Miejscowość” w rejestracji nie znajdował stolicy w ogóle, a Wrocław pokazywał jako
pięć osobnych miejscowości. Kolumna ``city_parent`` jest odpowiedzią; regułę – razem
z uzasadnieniem każdego warunku – trzyma ``apps.schools.normalise``.

Backfill jest **w migracji**, a nie tylko w ``seed_schools``, dokładnie z tego samego powodu, co
przy ``0002``: na produkcji słownik jest już wgrany, a wdrożenie uruchamia migracje **przed**
komendą seedującą. Gdyby kolumnę wypełniała wyłącznie komenda, między jednym a drugim krokiem
wdrożenia formularz rejestracji pytałby o miejscowość, której w bazie nie ma ani jednej.

Przeliczamy przy okazji obie kolumny porównawcze: ``city_search`` zyskuje gminę na początku,
a ``search_text`` – gminę obok oryginalnej miejscowości (inaczej „warszawa” nadal nie trafiałoby
w ani jedną stołeczną szkołę). Wszystkie trzy liczy jedno wywołanie ``derived_fields``, więc nie
mają jak rozjechać się między sobą ani z tym, co policzy ``School.save()``.
"""

from django.db import migrations, models

#: Ile wierszy na jedno ``bulk_update``. Słownik ma ponad osiem tysięcy pozycji, a jedno zapytanie
#: na całość zbudowałoby ``CASE`` większy, niż sterownik musi udźwignąć. Przy tej wielkości całość
#: to kilkanaście zapytań – migracja kończy się w ułamku sekundy i nie trzyma blokady na tabeli.
BATCH_SIZE = 1000

#: Kolumny wyliczane przez ``derived_fields`` – dokładnie te, które backfill zapisuje.
DERIVED = ("city_parent", "city_search", "search_text")


def fill_city_parent(apps, schema_editor):
    """Wylicza gminę i obie kolumny porównawcze dla wierszy wgranych przed tą migracją."""
    # Import w środku funkcji, bo migracja ma go wykonać przy uruchomieniu, a nie przy wczytaniu
    # grafu migracji. Moduł jest czysto tekstowy (bez modeli), więc jest to bezpieczny import
    # kodu aplikacji w migracji – nie zależy od kształtu tabeli w tym miejscu historii.
    from apps.schools.normalise import derived_fields

    School = apps.get_model("schools", "School")
    batch = []
    queryset = School.objects.all().only("id", "name", "city", "voivodeship", "postal_code")
    for school in queryset.iterator(chunk_size=BATCH_SIZE):
        for field, value in derived_fields(
            school.name, school.city, school.voivodeship, school.postal_code
        ).items():
            setattr(school, field, value)
        batch.append(school)
        if len(batch) >= BATCH_SIZE:
            School.objects.bulk_update(batch, list(DERIVED))
            batch = []
    if batch:
        School.objects.bulk_update(batch, list(DERIVED))


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0002_school_city_search"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="city_parent",
            field=models.CharField(
                db_index=True,
                default="",
                editable=False,
                max_length=120,
                verbose_name="gmina (miejscowość nadrzędna)",
            ),
        ),
        # Wsteczna droga jest pusta (``noop``): cofnięcie migracji zdejmuje kolumnę razem
        # z zawartością, a kolumny porównawcze przeliczy najbliższy ``seed_schools``.
        migrations.RunPython(fill_city_parent, migrations.RunPython.noop),
    ]
