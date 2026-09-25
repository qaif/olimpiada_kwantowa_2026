"""Migracja ``accounts.0026``: województwa jako regiony i backfill profili – dla każdego konkursu.

Test przewija bazę do stanu sprzed migracji danych i puszcza ją jeszcze raz, na wierszach, które
opisuje sam. Sprawdza cztery rzeczy, których nie widzi ``test_regions.py`` (ten patrzy na wynik
migracji uruchomionej przy zakładaniu bazy testowej):

- **drugi konkurs też dostaje zestaw startowy.** Migracja chodzi po wszystkich konkursach, bo do
  etapu 2 podział terytorialny był stałą wspólną dla całej instalacji,
- **backfill nie zmienia wyniku reguły konfliktu interesów.** Profil z województwem dostaje region
  o tym samym znaczeniu – także wtedy, gdy ``district`` stoi w bazie w zapisie niekanonicznym
  (dane sprzed ``accounts.0007``). Bez tego dzień włączenia flagi ``custom_regions`` byłby dniem,
  w którym reguła przestaje działać dla profili założonych wcześniej,
- **jest idempotentna.** Wdrożenie wolno powtórzyć po przerwanej migracji,
- **odwrót nie traci informacji.** ``district`` zostaje nietknięte, więc cofnięcie wdrożenia
  oddaje bazę w stanie, z którego da się ruszyć drugi raz.

Bazę przewija raz na moduł fikstura :func:`rewound`, w transakcji wycofywanej na końcu modułu
(``apps/core/tests/migration_helpers.py``); każdy test zastaje ją w punkcie :data:`BEFORE`.

Modele bierzemy **zwykłe** wszędzie tam, gdzie tabela nie zmieniła się po ``0026``. ``Participant``
jest wyjątkiem i musi być brany **historycznie**: przewinięcie do ``0025`` zdejmuje wszystkie
migracje stojące po nim, a od ``0028`` są wśród nich migracje **schematu** (``Participant.country``
i ``institution_name``, § 1.3.2). Model na żywo wstawiałby wtedy do ``INSERT``-a kolumny, których
w przewiniętej bazie nie ma – i test przewracałby się na cudzej zmianie zamiast sprawdzać swoją.
"""

import functools

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from wagtail.models import Locale, Page, Site

from apps.accounts.models import CommitteeMember, Region, Voivodeship
from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database
from apps.tenancy.models import Competition

pytestmark = MIGRATION_TESTS

BEFORE = ("accounts", "0025_region")
AFTER = ("accounts", "0026_regions_from_voivodeships")

COUNTRY_CODE = "pl"
ABROAD_CODE = "poza-polska"


@pytest.fixture(scope="module")
def rewound(django_db_setup, django_db_blocker):
    """Baza cofnięta do stanu sprzed wpisania regionów – tabela stoi, wierszy nie ma."""
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db


@functools.cache
def participants_at(target):
    """Model ``Participant`` **ze stanu** wskazanej migracji – bez kolumn dołożonych później.

    Kolumny ``country`` i ``institution_name`` dokłada ``accounts.0028`` (§ 1.3.2), a przewinięcie
    do ``0025`` zdejmuje je razem z całą resztą. Historia w tym miejscu nie jest ostrożnością na
    zapas: ten test wpisuje wiersze **przed** migracją i czyta je **po** niej, więc w obu punktach
    musi patrzeć na tabelę taką, jaka wtedy jest. Stan zależy wyłącznie od plików migracji, więc
    składamy go raz na punkt, a nie przy każdym odczycie.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor.loader.project_state(target).apps.get_model("accounts", "Participant")


def reloaded(participant, target=AFTER):
    """Ten sam wiersz, przeczytany modelem ze stanu ``target`` – odpowiednik ``refresh_from_db``."""
    return participants_at(target).objects.get(pk=participant.pk)


def make_competition(slug: str) -> Competition:
    """Konkurs z witryną. Witryna potrzebuje korzenia drzewa stron, a ten po ``flush`` nie istnieje."""
    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is None:
        locale = Locale.objects.order_by("pk").first() or Locale.objects.create(
            language_code=settings.LANGUAGE_CODE
        )
        root = Page.add_root(title="Root", slug="root", locale=locale)
    site = Site.objects.create(hostname=f"{slug}.test", port=80, root_page=root)
    return Competition.objects.create(
        site=site,
        slug=slug,
        name=f"Olimpiada {slug}",
        organizer_name="Organizator testowy",
        primary_domain=f"{slug}.test",
    )


def make_participant(competition, district: str):
    """Uczestnik wpisany wprost, bez fabryki: fabryka zakłada świat, którego ten test nie zamawia."""
    from apps.accounts.models import User, generate_public_code

    user = User.objects.create(email=f"{district or 'brak'}-{competition.slug}@example.test")
    return participants_at(BEFORE).objects.create(
        # Klucze obce podajemy identyfikatorem: model historyczny jest **inną klasą** niż model na
        # żywo, więc przypisanie mu żywego ``User`` albo ``Competition`` skończyłoby się
        # ``ValueError`` o niezgodnym typie.
        user_id=user.pk,
        competition_id=competition.pk,
        public_code=generate_public_code(competition),
        school="LO nr 1",
        grade=3,
        district=district,
        birth_year=2008,
    )


def make_member(competition, district: str | None) -> CommitteeMember:
    from apps.accounts.models import User

    user = User.objects.create(email=f"komitet-{district or 'brak'}-{competition.slug}@example.test")
    return CommitteeMember.objects.create(user=user, competition=competition, district=district)


def codes_of(competition) -> list[str]:
    return list(
        Region.objects.filter(competition=competition, level="REGION")
        .order_by("position", "id")
        .values_list("code", flat=True)
    )


def test_every_competition_gets_the_set_mirroring_the_voivodeships(rewound):  # noqa: ARG001 - jw.
    first = make_competition("pierwsza")
    second = make_competition("druga")

    migrate_to(AFTER)

    for competition in (first, second):
        assert codes_of(competition) == list(Voivodeship.values)
        names = dict(Region.objects.filter(competition=competition).values_list("code", "name"))
        assert [names[code] for code in Voivodeship.values] == [str(label) for label in Voivodeship.labels]
        assert names[COUNTRY_CODE] == "Polska"
        abroad = Region.objects.get(competition=competition, code=ABROAD_CODE)
        assert (abroad.is_active, abroad.counts_for_conflict) == (False, False)


def test_profiles_keep_their_district_and_gain_the_matching_region(rewound):  # noqa: ARG001 - jw.
    """Backfill jest złączeniem po ``district`` – i ``district`` po nim zostaje bez zmiany."""
    competition = make_competition("pierwsza")
    participant = make_participant(competition, Voivodeship.MAZOWIECKIE)
    member = make_member(competition, Voivodeship.MAZOWIECKIE)
    other = make_participant(competition, Voivodeship.LODZKIE)

    migrate_to(AFTER)

    participant = reloaded(participant)
    member.refresh_from_db()
    other = reloaded(other)
    assert participant.district == Voivodeship.MAZOWIECKIE
    assert participant.region.code == Voivodeship.MAZOWIECKIE
    # Ta sama para co przed migracją: uczestnik i członek komitetu z tego samego województwa mają
    # teraz **ten sam** region, więc reguła konfliktu daje ten sam wynik, co dziś.
    assert member.region_id == participant.region_id
    assert other.region.code == Voivodeship.LODZKIE
    assert other.region_id != participant.region_id


def test_a_member_without_a_district_stays_without_a_region(rewound):  # noqa: ARG001 - jw.
    """Brak okręgu u członka komitetu nie wyklucza go z niczego – i nie jest zgadywany."""
    competition = make_competition("pierwsza")
    member = make_member(competition, None)

    migrate_to(AFTER)

    member.refresh_from_db()
    assert member.region_id is None


def test_a_non_canonical_district_still_finds_its_region(rewound):  # noqa: ARG001 - jw.
    """Zapisy sprzed ``accounts.0007`` („woj. Mazowieckie”) trafiają tam, gdzie trafiają dziś."""
    competition = make_competition("pierwsza")
    legacy = make_participant(competition, "woj. Mazowieckie")
    canonical = make_participant(competition, Voivodeship.MAZOWIECKIE)

    migrate_to(AFTER)

    legacy = reloaded(legacy)
    canonical = reloaded(canonical)
    assert legacy.region_id == canonical.region_id
    # Sama kolumna zostaje nietknięta: migracja dokłada region, a nie poprawia dane.
    assert legacy.district == "woj. Mazowieckie"


def test_the_migration_repeated_does_not_multiply_regions(rewound):  # noqa: ARG001 - jw.
    competition = make_competition("pierwsza")
    migrate_to(AFTER)
    expected = Region.objects.filter(competition=competition).count()

    migrate_to(BEFORE)
    migrate_to(AFTER)

    assert Region.objects.filter(competition=competition).count() == expected
    assert codes_of(competition) == list(Voivodeship.values)


def test_running_the_forwards_step_again_does_not_overwrite_a_region_chosen_by_hand(rewound):  # noqa: ARG001 - jw.
    """Backfill dotyka wyłącznie wierszy **bez** regionu – wybór człowieka nie jest nadpisywany.

    Krok „w przód” wołamy tu wprost, a nie przez przewinięcie bazy: przewinięcie przeszłoby przez
    odwrót, który regiony kasuje, więc sprawdzałoby zupełnie inną rzecz. Rejestrem modeli jest
    rejestr na żywo – migracja bierze modele przez ``apps.get_model``, więc i tu, i w migracji
    chodzi o te same tabele.
    """
    from importlib import import_module

    from django.apps import apps as live_apps

    create_regions = import_module("apps.accounts.migrations.0026_regions_from_voivodeships").create_regions

    competition = make_competition("pierwsza")
    participant = make_participant(competition, Voivodeship.MAZOWIECKIE)
    migrate_to(AFTER)
    elsewhere = Region.objects.get(competition=competition, code=Voivodeship.LODZKIE)
    participants_at(AFTER).objects.filter(pk=participant.pk).update(region_id=elsewhere.pk)
    before = Region.objects.filter(competition=competition).count()

    create_regions(live_apps, None)

    participant = reloaded(participant)
    assert participant.region_id == elsewhere.pk
    assert Region.objects.filter(competition=competition).count() == before


def test_reversing_the_migration_keeps_every_district(rewound):  # noqa: ARG001 - jw.
    """Odwrót zabiera regiony, a ``district`` zostawia – dlatego cofnięcie nic nie traci."""
    competition = make_competition("pierwsza")
    participant = make_participant(competition, Voivodeship.MAZOWIECKIE)
    member = make_member(competition, Voivodeship.LODZKIE)
    migrate_to(AFTER)

    migrate_to(BEFORE)

    participant = reloaded(participant, BEFORE)
    member.refresh_from_db()
    assert not Region.objects.filter(competition=competition).exists()
    assert participant.region_id is None
    assert member.region_id is None
    assert participant.district == Voivodeship.MAZOWIECKIE
    assert member.district == Voivodeship.LODZKIE
