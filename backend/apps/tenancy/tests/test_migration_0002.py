"""Migracja ``tenancy.0002``: Konkurs #1 powstaje z danych, które w bazie już stoją.

To jest test ograniczenia z § 0 dokumentu („zachowaj działającą i skonfigurowaną obecną Olimpiadę
Kwantową”) sprowadzonego do jednego pytania: czy po wdrożeniu konkurs ma **te** dane, które miała
produkcja, a nie dane wpisane w kodzie. Dlatego sprawdzamy dwa kształty bazy:

- **produkcyjny** – witryna z domeną organizatora i wypełnione ``cms.SiteSettings``,
- **pusty** – witryna bez ustawień serwisu (świeża instalacja, baza testowa).

Bazę przewija **raz na moduł** fikstura :func:`rewound` – w transakcji, którą na końcu modułu
wycofuje (``apps/core/tests/migration_helpers.py``: DDL w Postgresie jest transakcyjny, więc
wycofanie przywraca czoło migracji bez ``migrate`` i bez ``flush``). Każdy test biegnie w swoim
punkcie zapisu, więc zastaje bazę dokładnie w punkcie :data:`BEFORE`, bez witryn i ustawień
serwisu, niezależnie od tego, co zrobił test przed nim.

Do **odczytu** bierzemy modele zwykłe, a nie historyczne: w punkcie ``AFTER`` schemat tabeli
konkursu jest dokładnie taki, jaki zna żywy model. Stan historyczny z ``project_state`` zawierałby
zresztą tylko przodków ``tenancy.0001`` – a więc ani ``cms``, ani tego, co ta migracja czyta.
Do **zapisu** jest odwrotnie i dlatego istnieje :func:`historical_model`: wiersz wstawiany na
schemacie starszym niż żywy model (ustawienia serwisu, drugi konkurs w teście odwrotności) musi
mieć dokładnie te kolumny, które w bazie w tej chwili są.
"""

import importlib

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from wagtail.images.models import Image
from wagtail.models import Collection, Locale, Page, Site

from apps.cms.models import SiteSettings
from apps.core.tests.migration_helpers import (
    MIGRATION_TESTS,
    applied_state_model,
    migrate_to,
    rewound_database,
)
from apps.tenancy.models import Competition

pytestmark = MIGRATION_TESTS

BEFORE = ("tenancy", "0001_initial")

#: Stan tuż po migracji, której ten plik dotyczy. Używa go **wyłącznie** test odwrotności – patrz
#: jego docstring: od ``tenancy.0008`` plan dojścia do :data:`AFTER` ciągnie za sobą backfille
#: wydania B, a te odmawiają cofnięcia na bazie z dwoma konkursami.
BASE = ("tenancy", "0002_competition_from_site")


def _last_competition_table_migration() -> tuple[str, str]:
    """„Po” to ostatnia migracja ``tenancy`` zmieniająca **tabelę konkursu**, nie samo ``0002``.

    Odczyty poniżej idą przez żywy model ``Competition``, a ten zna też kolumny dołożone później
    (``0003_prefixes``, ``0008_competition_submission_forward_emails``). Zatrzymanie na ``0002``
    dawałoby ``UndefinedColumn`` w każdym ``SELECT``.

    Do 20.09.2026 wybór ten omijał czoło aplikacji, bo przodkami czoła są backfille wydania B
    z innych aplikacji, które przy cofaniu odmawiają pracy na bazie z dwoma konkursami. Od
    ``tenancy.0008`` (adresy przekazywania rozwiązań) ostatnia migracja tabeli konkursu **jest**
    czołem, więc plan ``AFTER`` te backfille zawiera – i tak ma być, bo kolumna jest w żywym
    modelu. Jedyny test, któremu to przeszkadzało, dostał własny punkt docelowy (:data:`BASE`);
    pozostałe idą do przodu na bazie jednokonkursowej, gdzie backfille pracują normalnie.

    ``MigrationLoader(None)`` czyta wyłącznie pliki, bez połączenia z bazą, więc wolno to zrobić
    przy imporcie modułu.
    """
    loader = MigrationLoader(None, ignore_no_migrations=True)
    (head,) = [node for node in loader.graph.leaf_nodes() if node[0] == "tenancy"]
    chosen = ("tenancy", "0002_competition_from_site")
    for node in loader.graph.forwards_plan(head):
        if node[0] != "tenancy":
            continue
        for operation in loader.disk_migrations[node].operations:
            if getattr(operation, "model_name", "").lower() == "competition":
                chosen = node
    return chosen


AFTER = _last_competition_table_migration()

PRODUCTION_HOST = "olimpiadakwantowa.pl"
SITE_SETTINGS = {
    "site_name": "Olimpiada Kwantowa",
    "tagline": "Przyszłość ma naturę kwantową.",
    "organizer_name": "Fundacja Quantum AI",
    "organizer_address": "ul. Sanocka 9/103, 02-110 Warszawa",
    "organizer_registry": "KRS 0000808359",
    "contact_email": "contact@qaif.org",
    "contact_phone": "+48 507 982 292",
    "contact_url": "https://www.qaif.org/",
}


#: Model w kształcie, jaki baza ma **teraz** – złożony z migracji faktycznie zastosowanych. Potrzebny
#: wszędzie tam, gdzie test wstawia wiersz do tabeli, której schemat jest w tej chwili starszy niż
#: żywy model: ustawienia serwisu i drugi konkurs.
historical_model = applied_state_model


def create_site_settings(site, **fields) -> None:
    """Wiersz ``cms.SiteSettings`` w kształcie, jaki baza ma **teraz**, a nie jaki ma żywy model.

    Cofnięcie ``tenancy`` zdejmuje po drodze późniejsze migracje ``cms`` (np. pole języka interfejsu
    z ``cms.0024``), a powrót do ``AFTER`` ich nie przywraca.
    """
    model = historical_model("cms", "SiteSettings")
    values = {key: (value.pk if hasattr(value, "pk") else value) for key, value in fields.items()}
    # Klucze obce podajemy identyfikatorami: obiekty pochodzą z żywych modeli, a model historyczny
    # przyjmuje tylko instancje własnego rejestru.
    renamed = {(f"{key}_id" if hasattr(fields[key], "pk") else key): value for key, value in values.items()}
    model.objects.create(site_id=site.pk, **renamed)


def _without_sites(_state) -> None:
    """Czyścimy witryny i ustawienia serwisu, żeby to **test** opisywał kształt bazy.

    Po cofnięciu ``0002`` konkurs znika, ale witryna postawiona przy zakładaniu bazy testowej
    zostaje. Surowy SQL, nie ORM: kolektor ``Site.delete()`` zagląda do każdej tabeli z kluczem do
    witryny, także tych z późniejszych migracji ``tenancy`` (aliasy witryn), których po cofnięciu
    do ``0001`` w bazie nie ma. Kolejność: najpierw ustawienia (klucz do witryny), potem witryny.
    """
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{SiteSettings._meta.db_table}"')
        cursor.execute(f'DELETE FROM "{Site._meta.db_table}"')


@pytest.fixture(scope="module")
def rewound(django_db_setup, django_db_blocker):
    """Baza cofnięta do stanu sprzed powstania Konkursu #1 i bez ani jednej witryny."""
    with rewound_database(django_db_blocker, BEFORE, prepare=_without_sites) as db:
        yield db


def make_site(hostname: str) -> Site:
    """Witryna z korzeniem drzewa – ``Site.root_page`` jest polem obowiązkowym."""
    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is None:
        locale = Locale.objects.order_by("pk").first() or Locale.objects.create(
            language_code=settings.LANGUAGE_CODE
        )
        root = Page.add_root(title="Root", slug="root", locale=locale)
    return Site.objects.create(
        hostname=hostname,
        port=80,
        site_name="Olimpiada Kwantowa",
        root_page=root,
        is_default_site=True,
    )


def test_competition_is_built_from_the_existing_site_and_settings(rewound):
    """Kształt produkcji: konkurs dostaje dane organizatora z ``SiteSettings``, nie z kodu."""
    site = make_site(PRODUCTION_HOST)
    create_site_settings(site, **SITE_SETTINGS)

    migrate_to(AFTER)

    competition = Competition.objects.get()
    assert competition.slug == "kwantowa"
    assert competition.site_id == site.pk
    assert competition.name == SITE_SETTINGS["site_name"]
    assert competition.short_name == SITE_SETTINGS["site_name"]
    assert competition.tagline == SITE_SETTINGS["tagline"]
    assert competition.organizer_name == SITE_SETTINGS["organizer_name"]
    assert competition.organizer_address == SITE_SETTINGS["organizer_address"]
    assert competition.organizer_registry == SITE_SETTINGS["organizer_registry"]
    assert competition.contact_email == SITE_SETTINGS["contact_email"]
    assert competition.contact_phone == SITE_SETTINGS["contact_phone"]
    assert competition.organizer_url == SITE_SETTINGS["contact_url"]
    # Domena z witryny, nadawca z ustawień instalacji – dwa źródła, oba już w bazie/konfiguracji.
    assert competition.primary_domain == PRODUCTION_HOST
    assert competition.from_email == settings.DEFAULT_FROM_EMAIL
    assert competition.email_subject_prefix == settings.EMAIL_SUBJECT_PREFIX
    # „Jak dziś”: własna domena, brak prefiksu, zero flag.
    assert (competition.routing_mode, competition.path_prefix) == ("DOMAIN", "")
    assert competition.feature_flags == {}
    assert competition.is_active is True
    assert competition.default_language == "pl"


def test_competition_is_created_on_a_database_without_site_settings(rewound):
    """Świeża instalacja: nazwa bierze się z witryny, reszta zostaje pusta – nic nie zgadujemy."""
    make_site("localhost")

    migrate_to(AFTER)

    competition = Competition.objects.get()
    assert competition.slug == "kwantowa"
    assert competition.name == "Olimpiada Kwantowa"
    assert competition.primary_domain == "localhost"
    assert competition.organizer_name == ""
    assert competition.logo_id is None


def test_database_without_a_site_gets_no_competition(rewound):
    """Konkurs bez witryny nie miałby ani strony głównej, ani domeny – więc nie powstaje."""
    migrate_to(AFTER)

    assert not Competition.objects.exists()


def test_the_logo_points_at_the_same_image_as_the_footer(rewound):
    """Logo jest wskazaniem obrazu z biblioteki, a nie kopią pliku."""
    site = make_site(PRODUCTION_HOST)
    # Kolekcję zakładamy, gdy jej nie ma: testy transakcyjne czyszczą bazę po sobie, a korzeń
    # kolekcji jest wierszem z migracji Wagtaila, nie z ``flush``.
    collection = Collection.objects.filter(depth=1).order_by("path").first() or Collection.add_root(
        name="Root"
    )
    image = Image.objects.create(
        title="Znak", file="images/znak.png", width=10, height=10, collection=collection
    )
    create_site_settings(site, organizer_logo=image, **SITE_SETTINGS)

    migrate_to(AFTER)

    assert Competition.objects.get().logo_id == image.pk


def test_reverse_removes_only_what_the_migration_created(rewound):
    """Cofnięcie jest cofnięciem jednego kroku wdrożenia, a nie czyszczeniem instalacji.

    Ten jeden test celuje w :data:`BASE`, a nie w :data:`AFTER`, i od 20.09.2026 jest to warunek
    jego działania. Odkąd ``tenancy.0008`` dokłada kolumnę tabeli konkursu, plan dojścia do
    ``AFTER`` ciągnie za sobą backfille wydania B (``accounts.0020``, ``competitions.0020``),
    a te **odmawiają cofnięcia** na bazie z dwoma konkursami – czyli dokładnie na tej, którą ten
    test buduje, i słusznie: ich założeniem jest „wszystko tu ma jednego właściciela”.

    Przedmiotem testu jest odwrotność ``0002``, więc stan tuż po niej w zupełności wystarcza.
    Drugi konkurs zakładamy modelem historycznym i z tego samego powodu, co ustawienia serwisu
    w :func:`create_site_settings`: żywy model zna kolumny, których w tej chwili w bazie nie ma.
    """
    site = make_site(PRODUCTION_HOST)
    create_site_settings(site, **SITE_SETTINGS)
    migrate_to(BASE)
    other_site = Site.objects.create(
        hostname="fizyczna.invalid",
        port=80,
        site_name="Fizyczna",
        root_page_id=site.root_page_id,
    )
    historical_model("tenancy", "Competition").objects.create(
        site_id=other_site.pk,
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        organizer_name="Inny organizator",
        primary_domain="fizyczna.invalid",
    )

    migrate_to(BEFORE)

    # ``values_list`` czyta **jedną** kolumnę, więc działa także na schemacie sprzed późniejszych
    # migracji tabeli konkursu – w przeciwieństwie do ``get()``, który pyta o komplet kolumn.
    assert list(Competition.objects.values_list("slug", flat=True)) == ["fizyczna"]


def test_running_the_migration_twice_does_not_duplicate_the_competition(rewound):
    """Idempotencja: powtórzony przebieg nie dokłada drugiego właściciela tych samych danych.

    Funkcję wołamy wprost (``importlib``, bo nazwa modułu zaczyna się cyfrą), a nie przez
    ``migrate``: executor drugi raz jej nie uruchomi, więc sprawdzałby wtedy swoją własną
    ewidencję, a nie zachowanie migracji na bazie, po której ktoś chodził ręcznie.
    """
    site = make_site(PRODUCTION_HOST)
    create_site_settings(site, **SITE_SETTINGS)
    migrate_to(AFTER)

    module = importlib.import_module("apps.tenancy.migrations.0002_competition_from_site")
    module.create_competition(_LiveApps(), None)

    assert Competition.objects.count() == 1


class _LiveApps:
    """Rejestr modeli w kształcie, jakiego oczekuje funkcja migracji (``apps.get_model``).

    Podstawiamy modele zwykłe – patrz docstring modułu: schemat jest w obu punktach ten sam,
    a test ma sprawdzać zachowanie funkcji, nie mechanikę ``project_state``.
    """

    @staticmethod
    def get_model(app_label: str, model_name: str):
        from django.apps import apps as django_apps

        return django_apps.get_model(app_label, model_name)
