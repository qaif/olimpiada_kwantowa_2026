"""Migracja ``tenancy.0002``: Konkurs #1 powstaje z danych, które w bazie już stoją.

To jest test ograniczenia z § 0 dokumentu („zachowaj działającą i skonfigurowaną obecną Olimpiadę
Kwantową”) sprowadzonego do jednego pytania: czy po wdrożeniu konkurs ma **te** dane, które miała
produkcja, a nie dane wpisane w kodzie. Dlatego sprawdzamy dwa kształty bazy:

- **produkcyjny** – witryna z domeną organizatora i wypełnione ``cms.SiteSettings``,
- **pusty** – witryna bez ustawień serwisu (świeża instalacja, baza testowa).

Test wygląda inaczej niż reszta pakietu z tych samych powodów, co ``apps/cms/tests/test_migrations.py``:
przewijanie migracji to DDL po DML, więc potrzebny jest ``transaction=True``, testy transakcyjne
czyszczą bazę po sobie (nie zakładamy więc niczego o jej zawartości i budujemy wiersze sami),
a fikstura przywraca czoło migracji także wtedy, gdy test przerwie się w połowie.

Modele bierzemy **zwykłe**, a nie historyczne: przewijana jest wyłącznie migracja danych, więc
schemat ``tenancy``, ``cms`` i ``wagtailcore`` jest w obu punktach ten sam. Stan historyczny
z ``project_state`` zawierałby zresztą tylko przodków ``tenancy.0001`` – a więc ani ``cms``, ani
tego, co ta migracja czyta.
"""

import importlib

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from wagtail.images.models import Image
from wagtail.models import Collection, Locale, Page, Site

from apps.cms.models import SiteSettings
from apps.tenancy.models import Competition

BEFORE = ("tenancy", "0001_initial")


def _last_competition_table_migration() -> tuple[str, str]:
    """„Po” to ostatnia migracja ``tenancy`` zmieniająca **tabelę konkursu**, nie samo ``0002``.

    Odczyty poniżej idą przez żywy model ``Competition``, a ten zna też kolumny dołożone później
    (``0003_prefixes``). Zatrzymanie na ``0002`` dawałoby ``UndefinedColumn`` w każdym ``SELECT``.
    Nie celujemy jednak w czoło aplikacji: jego przodkami są backfille wydania B z innych aplikacji,
    które przy cofaniu odmawiają pracy na bazie z dwoma konkursami – a dokładnie taki stan buduje
    ``test_reverse_removes_only_what_the_migration_created``. ``MigrationLoader(None)`` czyta
    wyłącznie pliki, bez połączenia z bazą, więc wolno to zrobić przy imporcie modułu.
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


def migrate_to(target) -> None:
    """Przewija bazę do wskazanej migracji."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej.

    ``migrate_to(AFTER)`` nie wystarcza: cofnięcie jednej aplikacji zdejmuje po drodze każdą
    migrację z innych aplikacji, która od niej zależy, a powrót do konkretnego celu przywraca
    wyłącznie jego przodków.
    """
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def rewound(transactional_db):  # noqa: ARG001 - fixture bazy, używana przez efekt uboczny
    """Baza cofnięta do stanu sprzed powstania Konkursu #1 i bez ani jednej witryny.

    Czyścimy witryny i ustawienia serwisu, żeby to **test** opisywał kształt bazy, a nie
    kolejność uruchomienia pakietu: po cofnięciu ``0002`` konkurs znika, ale witryna postawiona
    przy zakładaniu bazy testowej zostaje.
    """
    migrate_to(BEFORE)
    # Surowy SQL, nie ORM: kolektor ``Site.delete()`` zagląda do każdej tabeli z kluczem do witryny,
    # także tych z późniejszych migracji ``tenancy`` (aliasy witryn), których po cofnięciu do
    # ``0001`` w bazie nie ma. Kolejność: najpierw ustawienia (klucz do witryny), potem witryny.
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{SiteSettings._meta.db_table}"')
        cursor.execute(f'DELETE FROM "{Site._meta.db_table}"')
    yield
    migrate_to_head()


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


@pytest.mark.django_db(transaction=True)
def test_competition_is_built_from_the_existing_site_and_settings(rewound):
    """Kształt produkcji: konkurs dostaje dane organizatora z ``SiteSettings``, nie z kodu."""
    site = make_site(PRODUCTION_HOST)
    SiteSettings.objects.create(site=site, **SITE_SETTINGS)

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


@pytest.mark.django_db(transaction=True)
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


@pytest.mark.django_db(transaction=True)
def test_database_without_a_site_gets_no_competition(rewound):
    """Konkurs bez witryny nie miałby ani strony głównej, ani domeny – więc nie powstaje."""
    migrate_to(AFTER)

    assert not Competition.objects.exists()


@pytest.mark.django_db(transaction=True)
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
    SiteSettings.objects.create(site=site, organizer_logo=image, **SITE_SETTINGS)

    migrate_to(AFTER)

    assert Competition.objects.get().logo_id == image.pk


@pytest.mark.django_db(transaction=True)
def test_reverse_removes_only_what_the_migration_created(rewound):
    """Cofnięcie jest cofnięciem jednego kroku wdrożenia, a nie czyszczeniem instalacji."""
    site = make_site(PRODUCTION_HOST)
    SiteSettings.objects.create(site=site, **SITE_SETTINGS)
    migrate_to(AFTER)
    other = Competition.objects.create(
        site=Site.objects.create(
            hostname="fizyczna.invalid",
            port=80,
            site_name="Fizyczna",
            root_page_id=site.root_page_id,
        ),
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        organizer_name="Inny organizator",
        primary_domain="fizyczna.invalid",
    )

    migrate_to(BEFORE)

    assert list(Competition.objects.values_list("slug", flat=True)) == [other.slug]

    # Sprzątanie przed powrotem do czoła (fixture ``rewound``): backfille wydania B odmawiają pracy
    # na bazie z dwoma konkursami, a ``0002`` założy Konkurs #1 od nowa obok ``other``. Surowy
    # SQL, bo po cofnięciu do ``0001`` tabel późniejszych relacji jeszcze nie ma.
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{Competition._meta.db_table}" WHERE id = %s', [other.pk])
        cursor.execute(f'DELETE FROM "{Site._meta.db_table}" WHERE id = %s', [other.site_id])


@pytest.mark.django_db(transaction=True)
def test_running_the_migration_twice_does_not_duplicate_the_competition(rewound):
    """Idempotencja: powtórzony przebieg nie dokłada drugiego właściciela tych samych danych.

    Funkcję wołamy wprost (``importlib``, bo nazwa modułu zaczyna się cyfrą), a nie przez
    ``migrate``: executor drugi raz jej nie uruchomi, więc sprawdzałby wtedy swoją własną
    ewidencję, a nie zachowanie migracji na bazie, po której ktoś chodził ręcznie.
    """
    site = make_site(PRODUCTION_HOST)
    SiteSettings.objects.create(site=site, **SITE_SETTINGS)
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
