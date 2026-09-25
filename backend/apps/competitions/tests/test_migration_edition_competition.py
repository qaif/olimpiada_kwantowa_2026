"""Migracja danych ``competitions.0020``: zastana domena zawodów dostaje właściciela.

Jedna kolumna przypisuje właściciela **całej** domenie zawodów: etap, zadanie, wpis do etapu,
termin rozmowy i wydarzenie dochodzą do konkursu przez edycję (§ 3.4). Edycja pominięta przez
backfill to więc nie jeden wiersz bez etykiety, tylko cały rocznik niewidoczny dla
``current_edition()`` – a po wydaniu D wdrożenie zatrzymane na kontroli przed ``NOT NULL``.

Kształt testu jest ten sam, co w ``accounts/tests/test_migration_memberships_backfill.py``: bazę
przewija raz na moduł fikstura w transakcji wycofywanej na końcu modułu
(``apps/core/tests/migration_helpers.py``). Historycznych modeli nie używamy świadomie –
``0019`` dokłada wyłącznie kolumnę, więc na tym stanie prawdziwe klasy opisują bazę tak samo,
a czytają się lepiej.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.db import connection

from apps.competitions.models import Edition
from apps.core.tests.migration_helpers import applied_state_model, migrate_to, rewound_database
from apps.tenancy.models import Competition

from .factories import EditionFactory, StageFactory

BEFORE = ("competitions", "0019_edition_competition")
AFTER = ("competitions", "0020_backfill_edition_competition")

#: Nazwa modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
backfill = importlib.import_module(f"apps.competitions.migrations.{AFTER[1]}")


@pytest.fixture(scope="module")
def before_backfill(django_db_setup, django_db_blocker):
    """Baza cofnięta do stanu sprzed backfillu: kolumna jest, właścicieli nie ma.

    Konkurs #1 bierzemy **przed** przewinięciem (``rewound_database``).
    """
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.competition


@pytest.mark.django_db
@pytest.mark.migrations
def test_the_backfill_assigns_every_edition_to_competition_one(before_backfill):
    """Baza jednokonkursowa: wszystko, co w niej stoi, należy do jedynego konkursu."""
    competition = before_backfill
    stage = StageFactory(competition=None)
    Edition.objects.filter(pk=stage.edition_id).update(competition=None)

    migrate_to(AFTER)

    assert Edition.objects.get(pk=stage.edition_id).competition_id == competition.pk
    # Etap nie ma własnej kolumny i mieć nie będzie – i właśnie dlatego jest już zakresowany.
    assert list(stage.__class__.objects.for_competition(competition)) == [stage]


@pytest.mark.django_db
@pytest.mark.migrations
def test_the_backfill_does_not_touch_rows_that_already_have_an_owner(before_backfill):
    """Wydania B i C stoją obok siebie: wiersz zapisany przez nowy kod ma zostać nietknięty."""
    competition = before_backfill
    edition = EditionFactory(competition=competition)

    migrate_to(AFTER)

    assert Edition.objects.get(pk=edition.pk).competition_id == competition.pk


@pytest.mark.django_db
@pytest.mark.migrations
def test_running_the_backfill_twice_changes_nothing(before_backfill):
    """Idempotencja: powtórzone wywołanie na tej samej bazie niczego nie przestawia."""
    competition = before_backfill
    edition = EditionFactory(competition=None)
    Edition.objects.filter(pk=edition.pk).update(competition=None)

    migrate_to(AFTER)
    backfill.forwards(django_apps, connection.schema_editor())

    assert Edition.objects.filter(competition=competition).count() == 1


@pytest.mark.django_db
@pytest.mark.migrations
def test_the_backfill_is_reversible(before_backfill):
    """Cofnięcie zdejmuje właścicieli – a nie jest ``noop``, bo kolumna zostaje na miejscu."""
    competition = before_backfill
    edition = EditionFactory(competition=None)
    Edition.objects.filter(pk=edition.pk).update(competition=None)

    migrate_to(AFTER)
    assert Edition.objects.get(pk=edition.pk).competition_id == competition.pk

    migrate_to(BEFORE)

    assert Edition.objects.get(pk=edition.pk).competition_id is None
    assert Competition.objects.filter(pk=competition.pk).exists()


@pytest.mark.django_db
@pytest.mark.migrations
def test_the_backfill_on_an_empty_database_does_nothing(before_backfill):  # noqa: ARG001
    """Świeża instalacja bez drzewa stron nie ma konkursu – i to jest odpowiedź poprawna.

    Migracja ma wtedy przejść bez wyjątku, bo inaczej ``migrate`` na pustej bazie (pierwsze
    wdrożenie, baza testowa) zatrzymałby się na pierwszym uruchomieniu.

    Kasujemy modelami w kształcie **zastosowanych** migracji (``applied_state_model``), a nie
    dzisiejszymi: baza stoi na ``0019``, więc nie ma jeszcze kolumn dołożonych w wydaniu D (m.in.
    ``grading_commentsnippet.competition_id``), a kolektor kasowania Django buduje zapytania
    z modeli i pytałby o kolumnę, której w tej chwili nie ma. Do 25.09.2026 stał tu surowy
    ``DELETE`` – przechodził tylko dlatego, że wcześniejszy test transakcyjny zdążył wyczyścić
    ``flush``-em tabele wskazujące konkurs (szablony dokumentów), których ``DELETE`` nie znał.
    """
    applied_state_model("competitions", "Edition").objects.all().delete()
    applied_state_model("tenancy", "Competition").objects.all().delete()

    migrate_to(AFTER)

    assert Edition.objects.count() == 0


@pytest.mark.django_db
def test_the_backfill_refuses_to_run_on_a_multi_competition_database(competition, other_competition):  # noqa: ARG001
    """Dwa konkursy znaczą, że założenie „wszystko ma jednego właściciela” jest fałszywe.

    Cichy skutek tego założenia to przepisanie edycji organizatora A na organizatora B, więc
    migracja ma przerwać wdrożenie, a nie zgadywać. Ta sama reguła i to samo zdanie stoją
    w ``accounts.0020``.
    """
    with pytest.raises(RuntimeError):
        backfill.sole_competition(django_apps)
