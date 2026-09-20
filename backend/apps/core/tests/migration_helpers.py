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


def applied_state_model(app_label: str, model_name: str):
    """Model w kształcie, jaki tabela ma **w tej chwili w bazie** – z migracji faktycznie zastosowanych.

    Różnica wobec modelu ze stanu ``migrate_to(BEFORE)`` jest istotna przy **zapisie**: stan „przed”
    zna kolumny swoich przodków, a w bazie stoją też kolumny dołożone przez migracje spoza tamtej
    gałęzi (np. ``tenancy.0003_prefixes``). Django zdejmuje z takich kolumn domyślną wartość zaraz
    po ``AddField``, więc ``INSERT`` pomijający je kończy się naruszeniem ``NOT NULL``. Model
    złożony z migracji zastosowanych wymienia dokładnie te kolumny, które tabela naprawdę ma.
    """
    executor = MigrationExecutor(connection)
    graph = executor.loader.graph
    # Tylko węzły obecne w grafie: ewidencja pamięta też migracje zastąpione przez ``squash``.
    applied = {node for node in executor.loader.applied_migrations if node in graph.nodes}
    leaves = [
        node for node in applied if not any(child in applied for child in graph.node_map[node].children)
    ]
    return executor.loader.project_state(leaves).apps.get_model(app_label, model_name)


def ensure_competition(apps):
    """Konkurs #1 w stanie ``apps`` – zakłada go, gdy poprzedni test transakcyjny wyczyścił bazę.

    Zwraca **model historyczny** ze stanu ``apps``, bo to on obowiązuje w teście migracji.

    Zakładanie szło do 20.09.2026 modelem bieżącym (``conftest.make_competition``), bo tabela
    ``tenancy`` stała w tych oknach na czole migracji. Od ``tenancy.0008`` (adresy przekazywania
    rozwiązań) tak już nie jest: cele przewinięcia tych testów sięgają ``competitions.0020``
    i ``accounts.0020``, a ``tenancy.0006`` od tamtych zależy – więc razem z nią schodzi też
    ``0007`` i jej kolumna. Żywy model wstawiałby wtedy kolumnę, której w bazie nie ma, i to
    zależnie od kolejności pakietu. Stąd :func:`applied_state_model`: wiersz zakładamy modelem
    o kształcie tabeli, która w tej chwili stoi. Witryna idzie modelem bieżącym – przewinięcia
    tych testów nie dotykają ``wagtailcore``.
    """
    Competition = apps.get_model("tenancy", "Competition")
    row = Competition.objects.order_by("pk").first()
    if row is not None:
        return row
    from conftest import HOST_COMPETITION, make_site

    # ``own_root=True`` – dokładnie to, co robiła tu ``conftest.make_competition``: własne
    # poddrzewo stron, żeby zmiana drogi zakładania nie zmieniła świata, który test zastaje.
    site = make_site(HOST_COMPETITION, default=True, own_root=True)
    applied_state_model("tenancy", "Competition").objects.create(
        site_id=site.pk,
        slug="kwantowa",
        name="Olimpiada Kwantowa",
        organizer_name="Organizator testowy",
        primary_domain=HOST_COMPETITION,
    )
    return Competition.objects.order_by("pk").first()
