"""Wspólna mechanika testów migracji: przewijanie w transakcji i zastany Konkurs #1.

Urosło z wydania D – cztery aplikacje tamtego zadania (``core``, ``support``, ``integrations``, ``results``) sprawdzają
swoje backfille tak samo: przewiń bazę tuż przed migracją danych, wstaw wiersze „sprzed
wdrożenia”, przewiń o jedną migrację dalej, przeczytaj wynik. Cztery kopie tej mechaniki różniłyby
się po pierwszej poprawce, a poprawka w teście migracji jest rzadka i trudna do zauważenia.

Dwie rzeczy są tu mniej oczywiste, niż wyglądają, i obie wynikają z tego, jak działa
``MigrationExecutor``:

- **cel przewinięcia bywa listą.** ``executor.migrate([(app, name)])`` cofa wszystko, co nie jest
  przodkiem podanego węzła – łącznie z kolumnami konkursu w **cudzych** aplikacjach, czyli razem
  ze światem, o którego przepisanie w teście chodzi. Test podaje więc jako cel komplet zależności
  swojej migracji, a nie sam jej poprzednik,
- **Konkursu #1 może w przewiniętej bazie nie być** (przewinięcie ``tenancy`` zdejmuje go razem
  z migracją, która go założyła). :func:`ensure_competition` zakłada go wtedy modelem w kształcie
  tabeli, która w tej chwili stoi, zamiast pomijać test albo sprawdzać pustkę.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor

# =================================================================================================
# Przewinięta baza w **jednej transakcji** (od 25.09.2026, ``docs/TESTY.md`` § testy migracji)
# =================================================================================================
#
# Do 25.09.2026 każdy test migracji był transakcyjny (``transaction=True``): przewijał bazę,
# sprawdzał, a na koniec wracał do czoła migracji (``migrate_to_head``) i czyścił wszystkie tabele
# (``flush``). Trzy kroki, z których pierwszy i drugi kosztują po kilkanaście–kilkadziesiąt sekund –
# i to razy liczba testów w module: przewinięcie tej samej bazy do tego samego punktu powtarzało
# się tyle razy, ile testów miał plik.
#
# Postgres ma DDL transakcyjny, więc przewinięcie **da się wycofać**. Stąd kształt obecny:
#
# - fikstura modułu (:func:`rewound_database`) otwiera transakcję, przewija bazę **raz** i trzyma
#   ją przez wszystkie testy modułu; na końcu modułu transakcja jest wycofywana, a baza wraca do
#   czoła migracji sama – razem z tabelą ``django_migrations`` i bez ``flush``,
# - każdy test biegnie w punkcie zapisu (zwykłe ``django_db``), więc to, co dołożył (wiersze,
#   migracje przewinięte dalej albo z powrotem), znika po nim, a następny test zastaje bazę
#   w punkcie, w który przewinęła ją fikstura modułu.
#
# Jedna rzecz nie działa w transakcji sama z siebie: klucze obce Django na Postgresie są
# ``DEFERRABLE INITIALLY DEFERRED``, a ``ALTER TABLE`` na tabeli z odroczonymi sprawdzeniami
# kończy się „cannot ALTER TABLE … because it has pending trigger events”. To był powód, dla którego
# te testy były transakcyjne. :func:`immediate_constraints` przełącza więzy na natychmiastowe na
# czas transakcji – sprawdzenie idzie przy każdej instrukcji, więc nic nie czeka na ``ALTER``.
#
# Test oznaczony ``@pytest.mark.migrations`` dostaje od ``backend/conftest.py`` trzy rzeczy:
# więzy natychmiastowe w swoim punkcie zapisu, ``slow`` (szybka pętla ``-m "not slow"`` go pomija)
# i ``xdist_group`` swojego modułu (przewinięcie liczy się raz na moduł, a nie raz na worker).


def immediate_constraints() -> None:
    """``SET CONSTRAINTS ALL IMMEDIATE`` – żeby DDL po DML w jednej transakcji był dozwolony."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


@dataclass(frozen=True)
class RewoundDatabase:
    """To, co zastaje test modułu: stan aplikacji z punktu przewinięcia i Konkurs #1 sprzed niego."""

    apps: Any
    competition: Any


@contextmanager
def rewound_database(django_db_blocker, targets, *, prepare=None) -> Iterator[RewoundDatabase]:
    """Baza przewinięta do ``targets`` w transakcji trzymanej do końca bloku (fikstura modułu).

    Kolejność kroków ma znaczenie:

    1. **Konkurs #1 przed przewinięciem** – tak, jak robiła to dotąd fikstura ``competition``
       w teście transakcyjnym: na czole migracji żywy model zgadza się ze schematem. Po
       przewinięciu już nie musi. Konkurs trafia do kontekstu na cały moduł (``_bind_competition``
       testu migracji niczego nie wiąże – nie może zapytać żywym modelem o przewiniętą tabelę).
    2. przewinięcie,
    3. ``prepare(state)`` – przygotowanie wspólne dla wszystkich testów modułu (np. skasowanie
       witryn w teście migracji tworzącej konkurs); też jest w transakcji, więc też zniknie.

    Użycie::

        @pytest.fixture(scope="module")
        def rewound(django_db_setup, django_db_blocker):
            with rewound_database(django_db_blocker, BEFORE) as db:
                yield db
    """
    import conftest
    from apps.tenancy.context import competition_context

    with django_db_blocker.unblock():
        outer = transaction.atomic()
        # Jak atomiki ``TestCase``: blok ``durable=True`` w badanym kodzie ma się zachować tak,
        # jak w teście, a nie wywracać na „zagnieżdżonym” bloku fikstury.
        outer._from_testcase = True
        outer.__enter__()
        try:
            immediate_constraints()
            # Konkurs z przypiętym hostem, jak z fikstury ``competition``; po ``flush`` wcześniejszego
            # testu transakcyjnego – odtworzony (``restored_competition`` przypina ten sam host).
            current = (
                conftest.pinned_competition()
                if conftest.existing_competition() is not None
                else conftest.restored_competition()
            )
            state = migrate_to(_as_targets(targets))
            if prepare is not None:
                prepare(state)
            conftest.REWOUND_DATABASE["module"] = str(targets)
            with competition_context(current):
                yield RewoundDatabase(apps=state, competition=current)
        finally:
            conftest.REWOUND_DATABASE["module"] = None
            transaction.set_rollback(True)
            outer.__exit__(None, None, None)


#: ``pytestmark`` modułu testów migracji: zwykłe ``django_db`` (punkt zapisu w transakcji fikstury
#: modułu) i marker ``migrations``.
MIGRATION_TESTS = [pytest.mark.django_db, pytest.mark.migrations]


def _as_targets(targets) -> list[tuple[str, str]]:
    """Jeden węzeł ``("app", "0001_x")`` albo lista węzłów – ``MigrationExecutor`` chce listy."""
    if isinstance(targets, tuple) and len(targets) == 2 and all(isinstance(part, str) for part in targets):
        return [targets]
    return list(targets)


class _LazyApps:
    """Rejestr modeli historycznych składany dopiero przy pierwszym ``get_model``.

    Złożenie stanu wszystkich aplikacji kosztuje kilka sekund, a większość wywołań
    ``migrate_to(AFTER)`` wyniku w ogóle nie czyta – przewija bazę i sprawdza ją żywymi modelami.
    """

    def __init__(self, loader, targets):
        self._loader = loader
        self._targets = targets
        self._apps = None

    def __getattr__(self, name):
        if self._apps is None:
            self._apps = self._loader.project_state(self._targets).apps
        return getattr(self._apps, name)


def migrate_to(targets):
    """Przewija bazę do wskazanych migracji i zwraca stan aplikacji z tamtego momentu."""
    targets = _as_targets(targets)
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    executor.loader.build_graph()
    return _LazyApps(executor.loader, targets)


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
