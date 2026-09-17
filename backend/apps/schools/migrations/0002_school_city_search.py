"""Kolumna porównawcza miejscowości + indeks pod krok „Miejscowość” w wyszukiwarce szkół.

Backfill jest **w migracji**, a nie tylko w ``seed_schools``, i to jest celowe: słownik na
produkcji jest już wgrany, a wdrożenie uruchamia migracje przed komendą seedującą. Gdyby kolumnę
wypełniała wyłącznie komenda, między jednym a drugim krokiem wdrożenia podpowiedź miast nie
zwracałaby ani jednego wiersza – a dokładnie w tym okienku formularz rejestracji jest już nowy
i tej podpowiedzi oczekuje.

Składanie znaków liczy Python (``apps.core.text.fold``), a nie SQL: to ta sama funkcja, którą
liczy ``School.save`` i ``seed_schools``, więc kolumna nie ma jak rozjechać się z zapytaniem.
Rozszerzenia ``unaccent`` świadomie **nie** zakładamy – wymaga uprawnień, których rola aplikacyjna
na produkcji nie ma, a złożony raz napis jest dla planisty tańszy od funkcji w warunku.
"""

from django.db import migrations, models

#: Ile wierszy na jedno ``bulk_update``. Słownik ma ponad osiem tysięcy pozycji, a jedno zapytanie
#: na całość zbudowałoby ``CASE`` większy, niż sterownik musi udźwignąć.
BATCH_SIZE = 500


def fill_city_search(apps, schema_editor):
    """Wypełnia ``city_search`` dla wierszy wgranych przed tą migracją."""
    from apps.core.text import fold

    School = apps.get_model("schools", "School")
    rows = []
    for school in School.objects.all().only("id", "city").iterator(chunk_size=BATCH_SIZE):
        school.city_search = fold(school.city.strip())[:120]
        rows.append(school)
    School.objects.bulk_update(rows, ["city_search"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0001_schools_directory"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="city_search",
            field=models.CharField(
                db_index=True,
                default="",
                editable=False,
                max_length=120,
                verbose_name="miejscowość (postać porównawcza)",
            ),
        ),
        # Wsteczna droga jest pusta (``noop``): cofnięcie migracji zdejmuje kolumnę razem
        # z zawartością, więc nie ma czego przywracać.
        migrations.RunPython(fill_city_search, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="school",
            index=models.Index(fields=["city_search", "kind", "name"], name="schools_city_kind_idx"),
        ),
    ]
