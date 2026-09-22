"""Migracja ``accounts.0024``: zgody ze stałej jako wiersze – dla **każdego** konkursu w bazie.

Test przewija bazę do stanu sprzed migracji danych i puszcza ją jeszcze raz, na wierszach, które
opisuje sam. Sprawdza trzy rzeczy, których nie widzi ``test_consent_definitions.py`` (ten patrzy na
wynik migracji uruchomionej przy zakładaniu bazy testowej):

- **drugi konkurs też dostaje zestaw startowy.** Migracja chodzi po wszystkich konkursach, bo do
  etapu 2 zgody były stałą wspólną dla całej instalacji – innego zestawu, który mógłby się komuś
  należeć, po prostu nie ma,
- **jest idempotentna.** ``update_or_create`` znaczy, że wykonanie jej po raz drugi przywraca
  wartość ze stałej i nie mnoży wierszy,
- **odwrót kasuje definicje, a dowodów nie rusza.** To jest cała różnica między definicją
  a dowodem: ``ConsentRecord`` mówi, na co ktoś zgodził się **wtedy**.

Kształt testu (``transaction=True``, przywracanie czoła migracji w fiksturze) jest przepisany
z ``apps/accounts/tests/test_migrations.py`` i z tych samych powodów: przewijanie migracji to DDL
po DML, a czoło trzeba przywrócić także wtedy, gdy test przerwie się w połowie.

Modele bierzemy **zwykłe**, a nie historyczne – tak samo jak ``apps/tenancy/tests/test_migration_0002.py``
i z tego samego powodu: przewijana jest wyłącznie migracja **danych**, więc schemat w obu punktach
jest ten sam, a stan historyczny nie zna modeli spoza przodków migracji.
"""

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from wagtail.models import Locale, Page, Site

from apps.accounts.consents import DEFAULT_CONSENTS
from apps.accounts.models import ConsentDefinition
from apps.tenancy.models import Competition

from .factories import UserFactory

BEFORE = ("accounts", "0023_consent_definitions")
AFTER = ("accounts", "0024_consent_definitions_from_the_constant")

CONTENT_FIELDS = (
    "field_name",
    "text",
    "link_text",
    "document_slug",
    "version",
    "required",
    "required_for_minor",
    "help_text",
    "missing_message",
)


def migrate_to(target):
    """Przewija bazę do wskazanej migracji i zwraca stan aplikacji z tamtego momentu."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


def migrate_to_head() -> None:
    """Przywraca czoło migracji **wszystkich** aplikacji, nie tylko przewijanej."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()


@pytest.fixture
def rewound(transactional_db):  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    """Baza cofnięta do stanu sprzed wpisania definicji zgód – tabela stoi, wierszy nie ma."""
    yield migrate_to(BEFORE)
    migrate_to_head()


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


def make_participant(historical, competition):
    """Uczestnik wpisany wprost, modelem historycznym – bez kolumn dołożonych po ``0023``."""
    from apps.accounts.models import Voivodeship, generate_public_code

    user = UserFactory()
    return historical.get_model("accounts", "Participant").objects.create(
        user_id=user.pk,
        competition_id=competition.pk,
        public_code=generate_public_code(competition),
        school="LO nr 1",
        grade=3,
        district=Voivodeship.MAZOWIECKIE,
        birth_year=2008,
    )


def make_consent_record(historical, *, participant_id: int, kind: str, version: str):
    """Dowód zgody wpisany **modelem historycznym** – z tego samego powodu, co ``make_participant``.

    Od ``accounts.0032`` prawdziwa klasa ``ConsentRecord`` ma kolumnę ``supervisor_id``, której na
    stanie ``0023`` jeszcze nie ma. Model historyczny widzi wyłącznie kolumny swojego punktu
    w czasie, więc ten sam ``INSERT``/``SELECT`` działa zarówno przed ``migrate_to(AFTER)``, jak
    i po powrocie ``migrate_to(BEFORE)``.
    """
    return historical.get_model("accounts", "ConsentRecord").objects.create(
        participant_id=participant_id,
        kind=kind,
        document_version=version,
        source="web",
    )


def rows_of(competition) -> list[tuple]:
    definitions = ConsentDefinition.objects.filter(competition=competition).order_by("ordering")
    return [
        (definition.kind, *(getattr(definition, name) for name in CONTENT_FIELDS))
        for definition in definitions
    ]


def constant_rows() -> list[tuple]:
    return [
        (str(consent.kind), *(getattr(consent, name) for name in CONTENT_FIELDS))
        for consent in DEFAULT_CONSENTS
    ]


@pytest.mark.django_db(transaction=True)
def test_every_competition_gets_the_set_from_the_constant(rewound):  # noqa: ARG001 - jw.
    first = make_competition("pierwsza")
    second = make_competition("druga")

    migrate_to(AFTER)

    assert rows_of(first) == constant_rows()
    assert rows_of(second) == constant_rows()


@pytest.mark.django_db(transaction=True)
def test_the_migration_repeated_restores_the_text_from_the_constant(rewound):  # noqa: ARG001 - jw.
    """Idempotencja ma tu znaczenie praktyczne: wdrożenie wolno powtórzyć po przerwanej migracji."""
    competition = make_competition("pierwsza")
    migrate_to(AFTER)
    ConsentDefinition.objects.filter(competition=competition).update(text="podmienione")

    migrate_to(BEFORE)
    migrate_to(AFTER)

    assert ConsentDefinition.objects.filter(competition=competition).count() == len(DEFAULT_CONSENTS)
    assert rows_of(competition) == constant_rows()


@pytest.mark.django_db(transaction=True)
def test_reversing_the_migration_keeps_the_proofs(rewound):
    """Odwrót zabiera definicje, a dowody zostawia – to jest reguła, a nie szczegół wykonania.

    Uczestnika i dowód zakłada **model historyczny** (``make_participant``, ``make_consent_record``):
    na stanie ``0023`` tabela uczestnika nie ma jeszcze kolumny ``region_id`` (``accounts.0025``),
    a tabela dowodu – kolumny ``supervisor_id`` (``accounts.0032``); prawdziwa klasa wymieniłaby
    obie w ``INSERT``/``SELECT``. Wiersz, który opisują, zostaje prawdziwy niezależnie od tego,
    którym modelem go czytamy – to on jest przedmiotem testu, nie kolumny dołożone później.
    """
    competition = make_competition("pierwsza")
    participant = make_participant(rewound, competition)
    record = make_consent_record(
        rewound,
        participant_id=participant.pk,
        kind="TERMS",
        version=DEFAULT_CONSENTS[0].version,
    )

    migrate_to(AFTER)
    migrate_to(BEFORE)

    assert not ConsentDefinition.objects.filter(competition=competition).exists()
    kept = rewound.get_model("accounts", "ConsentRecord").objects.get(pk=record.pk)
    assert kept.document_version == DEFAULT_CONSENTS[0].version
