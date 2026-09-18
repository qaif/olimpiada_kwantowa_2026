"""Migracja danych ``accounts.0020``: zastana baza dostaje właściciela, grupy stają się rolami.

To nie jest kosmetyka. Po tej migracji każdy profil uczestnika, członka komitetu i opiekuna ma
wskazanego administratora danych, a każda rola nadana przez ostatnie lata istnieje jako fakt
o **konkursie**, a nie o instalacji. Wiersz pominięty przez backfill to albo konto bez panelu, albo
– po wydaniu D, które domyka kolumnę na ``NOT NULL`` – wdrożenie zatrzymane w połowie.

Test wygląda inaczej niż reszta pakietu z tych samych powodów, co ``test_migrations.py``:
przewijanie migracji wymaga ``transaction=True``, a fikstura przywraca czoło migracji także wtedy,
gdy test przerwie się w połowie.

**Uczestnika i konkurs bierzemy modelami historycznymi** (``Participant``, ``Competition``), a resztę
– prawdziwymi klasami. Do etapu 2 historycznych modeli nie było tu wcale i było to uzasadnione:
na stanie ``0019`` prawdziwe klasy opisywały bazę tak samo jak historyczne. Etap 2 dokłada
``accounts_participant.region_id`` (``accounts.0025``) i tabelę ``accounts_consentdefinition``
(``accounts.0023``), których na stanie ``0019`` **nie ma** – a prawdziwa klasa wymienia je w każdym
``SELECT``, ``INSERT`` i w zbieraniu obiektów przy kasowaniu. Granica jest więc dokładnie tam, gdzie
przebiega różnica schematów, i nigdzie indziej: ``User``, ``Group`` i ``Membership`` zostają
prawdziwe, bo ich tabele na stanie ``0019`` wyglądają tak samo jak dziś.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    CompetitionRole,
    Membership,
    Voivodeship,
    generate_public_code,
)

from .factories import UserFactory

BEFORE = ("accounts", "0019_memberships_and_competition_fks")
AFTER = ("accounts", "0020_backfill_competition_and_memberships")

#: Nazwa modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
backfill = importlib.import_module(f"apps.accounts.migrations.{AFTER[1]}")


def migrate_to(target):
    """Przewija bazę do wskazanej migracji i zwraca stan aplikacji z tamtego momentu."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


def participants(historical):
    """Manager uczestnika **z tamtego stanu** – bez kolumn dołożonych przez etap 2."""
    return historical.get_model("accounts", "Participant").objects


def make_participant(historical, competition):
    """Uczestnik wpisany wprost, modelem historycznym. ``competition=None`` – wiersz bez właściciela."""
    user = UserFactory()
    participant = participants(historical).create(
        user_id=user.pk,
        competition_id=competition.pk if competition is not None else None,
        public_code=generate_public_code(competition),
        school="LO nr 1",
        grade=3,
        district=Voivodeship.MAZOWIECKIE,
        birth_year=2008,
    )
    # Konto zostaje prawdziwe: to po nim test dodaje grupy i to jego szuka backfill ról.
    participant.account = user
    return participant


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej.

    Cofnięcie jednej aplikacji zdejmuje po drodze każdą migrację z innych aplikacji, która od niej
    zależy, a powrót do konkretnego celu przywraca wyłącznie jego przodków. Reszta pakietu
    zastawałaby wtedy bazę bez tamtych tabel – i wywracałaby się w innym miejscu.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def before_backfill(transactional_db, competition):  # noqa: ARG001 - baza, używana przez efekt uboczny
    """Baza cofnięta do stanu sprzed backfillu: kolumny są, właścicieli nie ma.

    Fikstura ``competition`` idzie **przed** przewinięciem, bo Konkurs #1 zakłada migracja
    ``tenancy.0002``, a test transakcyjny bywa uruchomiony na bazie już raz wyczyszczonej.
    Cofnięcie ``0020`` zdejmuje wtedy właścicieli i członkostwa – czyli robi dokładnie to, co
    ma zrobić ``backwards``.
    """
    historical = migrate_to(BEFORE)
    yield competition, historical
    migrate_to_head()


@pytest.mark.django_db(transaction=True)
def test_the_backfill_assigns_the_existing_data_to_competition_one(before_backfill):
    """Profile bez właściciela dostają Konkurs #1, a grupy zamieniają się w członkostwa."""
    competition, historical = before_backfill
    participant = make_participant(historical, None)
    coordinator = UserFactory(groups=[GROUP_COORDINATOR])
    participant.account.groups.add(Group.objects.get_or_create(name=GROUP_PARTICIPANT)[0])
    assert Membership.objects.count() == 0

    migrate_to(AFTER)

    assert participants(historical).get(pk=participant.pk).competition_id == competition.pk
    assert Membership.objects.filter(
        user=coordinator, competition=competition, role=CompetitionRole.COORDINATOR
    ).exists()
    assert Membership.objects.filter(
        user=participant.account, competition=competition, role=CompetitionRole.PARTICIPANT
    ).exists()
    # Rola nadana przez migrację nie ma autora i to jest odpowiedź poprawna, a nie brak danych.
    assert Membership.objects.filter(granted_by__isnull=False).count() == 0


@pytest.mark.django_db(transaction=True)
def test_the_backfill_does_not_touch_rows_that_already_have_an_owner(before_backfill):
    """Wydania B i C stoją obok siebie: wiersz zapisany przez nowy kod ma zostać nietknięty."""
    competition, historical = before_backfill
    mine = make_participant(historical, competition)

    migrate_to(AFTER)

    assert participants(historical).get(pk=mine.pk).competition_id == competition.pk


@pytest.mark.django_db(transaction=True)
def test_running_the_backfill_twice_changes_nothing(before_backfill):
    """Idempotencja: powtórzone ``migrate`` na tej samej bazie nie dokłada drugiego członkostwa."""
    competition, _ = before_backfill
    UserFactory(groups=[GROUP_COORDINATOR])

    migrate_to(AFTER)
    first = Membership.objects.count()
    # Drugi przebieg funkcji wprost – wynik ma być identyczny, a nie ``IntegrityError`` na więzie.
    backfill.forwards(django_apps, connection.schema_editor())

    assert Membership.objects.count() == first == 1
    assert Membership.objects.filter(competition=competition).count() == first


@pytest.mark.django_db(transaction=True)
def test_the_backfill_is_reversible(before_backfill):
    """Cofnięcie wraca do stanu, w którym rolę niosą wyłącznie grupy – a nie do noopa."""
    competition, historical = before_backfill
    participant = make_participant(historical, None)
    UserFactory(groups=[GROUP_COORDINATOR])

    migrate_to(AFTER)
    assert Membership.objects.exists()

    migrate_to(BEFORE)

    assert Membership.objects.count() == 0
    assert participants(historical).get(pk=participant.pk).competition_id is None
    # Grupy zostają nietknięte: to one niosą uprawnienia do ``/cms/`` (migracja ``cms.0003``).
    assert Group.objects.filter(name=GROUP_COORDINATOR).exists()
    assert competition.pk is not None


@pytest.mark.django_db(transaction=True)
def test_the_backfill_on_an_empty_database_does_nothing(before_backfill):
    """Świeża instalacja bez drzewa stron nie ma konkursu – i to jest odpowiedź poprawna.

    Migracja ma wtedy przejść bez wyjątku, bo inaczej ``migrate`` na pustej bazie (pierwsze
    wdrożenie, baza testowa) zatrzymałby się na pierwszym uruchomieniu.

    Kasujemy modelami historycznymi także konkurs: prawdziwa klasa zebrałaby przy kasowaniu również
    definicje zgód (``accounts.0023``), a tej tabeli na stanie ``0019`` jeszcze nie ma.
    """
    _, historical = before_backfill
    participants(historical).all().delete()
    historical.get_model("tenancy", "Competition").objects.all().delete()
    UserFactory(groups=[GROUP_COORDINATOR])

    migrate_to(AFTER)

    assert Membership.objects.count() == 0


@pytest.mark.django_db
def test_the_backfill_refuses_to_run_on_a_multi_competition_database(competition, other_competition):  # noqa: ARG001
    """Dwa konkursy znaczą, że założenie „wszystko ma jednego właściciela” jest fałszywe.

    Cichy skutek tego założenia to przepisanie danych organizatora A na organizatora B, więc
    migracja ma przerwać wdrożenie, a nie zgadywać.
    """
    with pytest.raises(RuntimeError):
        backfill.sole_competition(django_apps)
