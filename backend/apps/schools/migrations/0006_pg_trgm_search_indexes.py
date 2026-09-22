"""Wyszukiwarka szkół po ``pg_trgm``, nie po B-tree – i trzy indeksy, które nigdy nie były użyte.

**Skąd wzięła się ta migracja.** Statystyki produkcji (22.09.2026, 15 dni okna): tabela
``schools_school`` (8118 wierszy) ma 864 sekwencyjne przejścia po całej tabeli (7,0 mln
przeczytanych wierszy) wobec 247 tys. skanów indeksowych, a jedno zapytanie wyszukiwarki kosztuje
130–185 ms – prawie w całości ten seq scan. Przyczyna jest prosta: ``search_schools``
(``apps.schools.api``) filtruje ``search_text__contains=token`` dla każdego wyrazu zapytania,
a ``search_cities`` dobiera dzielnicę przez ``city_search__contains="|dzielnica"`` – zwykły B-tree
(nawet z automatycznym indeksem ``varchar_pattern_ops`` pod ``LIKE 'prefiks%'``) nie umie obsłużyć
dopasowania **w środku** napisu, więc planista i tak sięga po pełne przejście.

**Co migracja robi – dwie rzeczy, w tej kolejności:**

1. Włącza rozszerzenie ``pg_trgm`` (``TrigramExtension``). Rola aplikacyjna nie potrzebuje do tego
   uprawnień superużytkownika – ``pg_trgm`` jest rozszerzeniem zaufanym (*trusted*) od
   PostgreSQL 13, a obraz ``postgres:16-alpine`` (patrz ``docs/OPERACJE.md``) je zawiera. Ta sama
   operacja jest bezpieczna na bazie testowej: ``CREATE EXTENSION IF NOT EXISTS`` jest
   idempotentne i ``pytest-django`` tworzy testową bazę tym samym mechanizmem migracji.
2. Zakłada dwa indeksy GIN z klasą operatorów ``gin_trgm_ops`` – po jednym na kolumnę, którą
   wyszukiwarka pyta ``contains``: ``search_text`` (koniunkcja tokenów) i ``city_search``
   (dopasowanie dzielnicy po separatorze). Prefiks (``city_search__startswith``) zostaje przy
   automatycznym indeksie ``varchar_pattern_ops`` – ten wariant B-tree obsługuje bez zmian, więc
   nie ma powodu go dublować w GIN.

**Co zdejmuje – trzy indeksy z zerem skanów na produkcji w tym samym oknie:**

- ``schools_city_kind_idx`` (``city_search``, ``kind``, ``name``) – miał obsłużyć porządek listy
  wewnątrz miasta, ale porządek liczy ``_kind_rank()`` w ``apps.schools.api``: wyrażenie ``Case``,
  nie kolumna ``kind``. Indeks nigdy nie mógł posłużyć sortowaniu, a sam filtr ``city_search`` ma
  własny, tańszy indeks jednokolumnowy,
- ``schools_school_search_text_d46ad0ab`` i jego bliźniak ``_like`` – automatyczna para spod
  ``db_index=True`` na ``search_text`` (``AlterField`` niżej). Żadne zapytanie w kodzie nie pyta
  tej kolumny ``=`` ani ``startswith``, więc B-tree nie miał czego obsłużyć; zastępuje go GIN
  trigramowy założony wyżej.

Kolejność operacji ma znaczenie: rozszerzenie musi istnieć, zanim ``CREATE INDEX ... USING gin
(... gin_trgm_ops)`` w ogóle się skompiluje, więc ``TrigramExtension`` stoi jako pierwsza operacja.
Ośmiuset kilkunastu wierszy ``CREATE INDEX`` na tabeli tej wielkości nie da się odczuć – blokada
trwa ułamek sekundy, więc migracja jest bezpieczna do puszczenia na produkcji bez okna serwisowego.
"""

import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("schools", "0005_custom_institution"),
    ]

    operations = [
        TrigramExtension(),
        migrations.RemoveIndex(
            model_name="school",
            name="schools_city_kind_idx",
        ),
        migrations.AlterField(
            model_name="school",
            name="search_text",
            field=models.CharField(editable=False, max_length=400, verbose_name="tekst wyszukiwania"),
        ),
        migrations.AddIndex(
            model_name="school",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_text"], name="schools_search_trgm_idx", opclasses=["gin_trgm_ops"]
            ),
        ),
        migrations.AddIndex(
            model_name="school",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["city_search"], name="schools_city_trgm_idx", opclasses=["gin_trgm_ops"]
            ),
        ),
    ]
