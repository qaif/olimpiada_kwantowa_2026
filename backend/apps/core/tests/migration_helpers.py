"""Wspólna mechanika testów migracji wydania D: przewijanie i zastany Konkurs #1.

Cztery aplikacje tego zadania (``core``, ``support``, ``integrations``, ``results``) sprawdzają
swoje backfille tak samo: przewiń bazę tuż przed migracją danych, wstaw wiersze „sprzed
wdrożenia”, przewiń o jedną migrację dalej, przeczytaj wynik. Cztery kopie tej mechaniki różniłyby
się po pierwszej poprawce, a poprawka w teście migracji jest rzadka i trudna do zauważenia.

Dwie rzeczy są tu mniej oczywiste, niż wyglądają, i obie wynikają z tego, jak działa
``MigrationExecutor``:

- **cel przewinięcia bywa listą.** ``executor.migrate([(app, name)])`` cofa wszystko, co nie jest
  przodkiem podanego węzła – łącznie z kolumnami konkursu w **cudzych** aplikacjach, czyli razem
  ze światem, o którego przepisanie w teście chodzi. Test podaje więc jako cel komplet zależności
  swojej migracji, a nie sam jej poprzednik,
- **baza po teście transakcyjnym jest pusta.** ``transactional_db`` kończy się ``flush``-em, który
  odtwarza typy treści i uprawnienia, ale nie wiersze wpisane przez ``RunPython`` – więc drugi
  taki test w przebiegu nie zastaje już Konkursu #1 założonego przez ``tenancy.0002``.
  :func:`ensure_competition` odtwarza go tą samą drogą, co fikstura ``competition``
  (``backend/conftest.py``), zamiast pomijać test albo sprawdzać pustkę.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def migrate_to(targets):
    """Przewija bazę do wskazanych migracji i zwraca stan aplikacji z tamtego momentu."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    executor.loader.build_graph()
    return executor.loader.project_state(targets).apps


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej.

    Cofnięcie jednej aplikacji zdejmuje po drodze każdą migrację z innych, która od niej zależy,
    a powrót do konkretnego celu przywraca wyłącznie jego przodków. Reszta pakietu zastawałaby
    wtedy bazę bez tamtych tabel – i wywracała się w zupełnie innym miejscu, kilka minut później.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


def ensure_competition(apps):
    """Konkurs #1 w stanie ``apps`` – zakłada go, gdy poprzedni test transakcyjny wyczyścił bazę.

    Zwraca **model historyczny**, bo to on obowiązuje w teście migracji. Zakładanie idzie za to
    modelami bieżącymi (``conftest.make_competition``): tabela ``tenancy`` stoi w tych testach na
    czole migracji, a kopia jej wypełniania byłaby drugą definicją tego, czym jest Konkurs #1.
    """
    Competition = apps.get_model("tenancy", "Competition")
    row = Competition.objects.order_by("pk").first()
    if row is not None:
        return row
    from conftest import HOST_COMPETITION, make_competition

    make_competition(HOST_COMPETITION, "kwantowa", default_site=True)
    return Competition.objects.order_by("pk").first()
