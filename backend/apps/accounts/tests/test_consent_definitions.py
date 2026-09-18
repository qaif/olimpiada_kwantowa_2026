"""Definicje zgód per konkurs: wiersz **równy** stałej, jedno wejście, żadnej zmiany bez flagi.

Cztery rzeczy, których pilnują te testy (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.2, § 5.3):

- **wiersz w bazie jest równy stałej, pole po polu.** Migracja przepisująca zgody byłaby bez tego
  jedynym miejscem w systemie, w którym treść oświadczenia mogłaby się zmienić bez żadnego śladu,
- **włączenie flagi nie zmienia dla Konkursu #1 ani jednego znaku** – bo jedno i drugie źródło
  niesie ten sam napis. To jest warunek, pod którym decyzja D8 („Konkurs #1 zostaje na stałej”)
  jest odwracalna w dowolnej chwili,
- **przy wyłączonej fladze nie pada ani jedno zapytanie.** Formularz rejestracji jest publiczny
  i chodzi po nim każdy uczestnik; próg zapytań (§ 5.6) jest ustawiony na dzisiejszy stan,
- **konkurs widzi wyłącznie swoje definicje.** Cudza treść oświadczenia w formularzu byłaby
  zebraniem zgody, której nikt nie napisał.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts import consents
from apps.accounts.consents import (
    CONSENT_FEATURE_FLAG,
    CONSENTS,
    DEFAULT_CONSENTS,
    ConsentKind,
)
from apps.accounts.models import ConsentDefinition
from apps.core.models import AuditLog

from .factories import ParticipantFactory

pytestmark = pytest.mark.django_db

#: Pola porównywane „wiersz kontra stała”. Wszystkie, które niosą treść – reszta (``id``,
#: ``competition``) opisuje miejsce wiersza, a nie oświadczenie.
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


def as_rows(definitions) -> list[tuple]:
    return [
        (definition.kind, *(getattr(definition, name) for name in CONTENT_FIELDS))
        for definition in definitions
    ]


def as_constants(consent_tuple) -> list[tuple]:
    return [
        (str(consent.kind), *(getattr(consent, name) for name in CONTENT_FIELDS)) for consent in consent_tuple
    ]


def enable_flag(competition):
    """Włącza ``per_competition_consents`` zapisem do ``feature_flags``, a nie podmianą metody.

    Przełącznik ma być sprawdzony tak, jak działa na produkcji – razem z odczytem
    ``Competition.has_feature`` i razem z tym, że wartość stoi w wierszu konkursu.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), CONSENT_FEATURE_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def seed_definitions(competition):
    """Zestaw startowy dla konkursu założonego **po** migracji ``accounts.0024`` (fikstura testu)."""
    return [
        ConsentDefinition.objects.create(
            competition=competition,
            kind=str(consent.kind),
            ordering=order,
            **{name: getattr(consent, name) for name in CONTENT_FIELDS},
        )
        for order, consent in enumerate(DEFAULT_CONSENTS)
    ]


# --- migracja: wiersz kontra stała ---------------------------------------------------------------


def test_consent_definitions_match_the_constant(competition):
    """Wiersze w bazie są **równe** ``DEFAULT_CONSENTS``, pole po polu (§ 5.3).

    Test sprawdza migrację, zanim ktokolwiek włączy flagę.
    """
    definitions = ConsentDefinition.objects.for_competition(competition).order_by("ordering")

    assert as_rows(definitions) == as_constants(DEFAULT_CONSENTS)


def test_definitions_keep_the_order_of_the_form(competition):
    """Kolejność jest treścią: regulamin i RODO stoją przed zgodami dobrowolnymi."""
    definitions = ConsentDefinition.objects.for_competition(competition)

    assert [definition.field_name for definition in definitions] == [
        consent.field_name for consent in DEFAULT_CONSENTS
    ]
    assert [definition.ordering for definition in definitions] == [0, 1, 2, 3]


def test_the_alias_still_points_at_the_starting_set():
    """``CONSENTS`` zostaje aliasem na sezon – po to, żeby testy niezmienności porównywały oba."""
    assert CONSENTS is DEFAULT_CONSENTS


# --- consent_set: jedno wejście ------------------------------------------------------------------


def test_consent_set_returns_the_constant_without_the_flag(competition):
    assert consents.consent_set(competition) is DEFAULT_CONSENTS


def test_consent_set_does_not_query_the_database_without_the_flag(competition, django_assert_num_queries):
    """Ani jedno zapytanie – próg zapytań ``/register/`` jest ustawiony na dzisiejszy stan (§ 5.6)."""
    with django_assert_num_queries(0):
        consents.consent_set(competition)


def test_consent_set_is_identical_with_and_without_the_flag(competition):
    """Włączenie flagi nie zmienia dla Konkursu #1 ani jednego znaku."""
    without = consents.consent_set(competition)

    enable_flag(competition)

    assert consents.consent_set(competition) == without


def test_consent_set_reads_the_database_with_the_flag(competition):
    enable_flag(competition)
    definition = ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)
    definition.text = "Akceptuję {link} konkursu."
    definition.save(update_fields=["text"])

    terms = consents.consent_set(competition)[0]

    assert terms.text == "Akceptuję {link} konkursu."
    # Stała zostaje stałą: źródło zmienia się dla konkursu z flagą, a nie dla całej instalacji.
    assert DEFAULT_CONSENTS[0].text != terms.text


def test_consent_set_skips_definitions_withdrawn_from_the_set(competition):
    """Wycofanie zgody jest przestawieniem ``is_active``, a nie skasowaniem wiersza z dowodami."""
    enable_flag(competition)
    ConsentDefinition.objects.for_competition(competition).filter(kind=ConsentKind.PUBLISH_NAME).update(
        is_active=False
    )

    kinds = [consent.kind for consent in consents.consent_set(competition)]

    assert ConsentKind.PUBLISH_NAME not in kinds
    assert len(kinds) == 3


def test_consent_set_falls_back_to_the_constant_when_the_competition_has_no_definitions(competition, caplog):
    """Pusta tabela przy włączonej fladze to błąd konfiguracji – a nie formularz bez regulaminu."""
    enable_flag(competition)
    ConsentDefinition.objects.for_competition(competition).delete()

    with caplog.at_level("WARNING"):
        assert consents.consent_set(competition) is DEFAULT_CONSENTS

    assert CONSENT_FEATURE_FLAG in caplog.text


def test_consent_set_without_a_competition_returns_the_constant(unbound_competition):  # noqa: ARG001
    """„Nie wiadomo, o który konkurs chodzi” zwraca zestaw domyślny, a nie pustkę ani błąd.

    Odwrót jest miękki świadomie: formularz rejestracji nie ma prawa przestać się otwierać dlatego,
    że hosta nie dało się przypisać do konkursu (§ 1.0 (b), docstring ``_competition_or_none``).
    """
    assert consents.consent_set() is DEFAULT_CONSENTS


def test_required_kinds_follows_the_definitions_of_the_competition(competition):
    """Reguła wieku zostaje kodem; danymi jest to, **które** zgody konkurs zbiera."""
    before = consents.required_kinds(2010, competition=competition)
    enable_flag(competition)
    ConsentDefinition.objects.for_competition(competition).filter(kind=ConsentKind.PUBLISH_NAME).update(
        required=True
    )

    kinds = consents.required_kinds(2010, competition=competition)

    assert ConsentKind.PUBLISH_NAME not in before
    assert ConsentKind.PUBLISH_NAME in kinds
    assert ConsentKind.GUARDIAN in kinds  # rocznik niepełnoletni – reguła z ``is_minor``


# --- powierzchnie: API, formularz, panel ---------------------------------------------------------


def test_the_public_api_serves_the_definitions_with_the_flag(client_for, competition):
    """``GET /api/auth/consents/`` czyta ``consent_set``, a nie stałą – dowód na jedno wejście.

    Bez flagi ten sam endpoint wydaje zestaw zamrożony w ``apps/tenancy/tests/test_branding.py``
    i to tamten test jest bramką „nic się nie zmieniło”.
    """
    enable_flag(competition)
    ConsentDefinition.objects.for_competition(competition).filter(kind=ConsentKind.TERMS).update(
        version="2.0 z 1 stycznia 2027"
    )

    payload = client_for(competition).get("/api/auth/consents/").json()

    assert [row["version"] for row in payload][0] == "2.0 z 1 stycznia 2027"
    assert [row["field"] for row in payload] == [c.field_name for c in DEFAULT_CONSENTS]


def test_the_registration_form_shows_the_text_from_the_definitions(client_for, competition):
    """Etykieta przy checkboksie bierze się z ``consent_set`` – tą samą drogą, co API."""
    enable_flag(competition)
    ConsentDefinition.objects.for_competition(competition).filter(kind=ConsentKind.TERMS).update(
        text="Akceptuję {link} tego konkursu."
    )

    form = client_for(competition).get("/register/").context["form"]

    assert "Akceptuję" in str(form.fields["terms_consent"].label)


# --- izolacja -------------------------------------------------------------------------------------


def test_definitions_of_one_competition_are_invisible_to_the_other(competition, other_competition):
    seed_definitions(other_competition)
    ConsentDefinition.objects.for_competition(other_competition).filter(kind=ConsentKind.TERMS).update(
        text="Treść drugiego organizatora."
    )

    enable_flag(competition)
    mine = consents.consent_set(competition)

    assert all("drugiego organizatora" not in consent.text for consent in mine)
    assert ConsentDefinition.objects.for_competition(competition).count() == len(DEFAULT_CONSENTS)
    assert as_rows(ConsentDefinition.objects.for_competition(competition)) == as_constants(DEFAULT_CONSENTS)


def test_the_other_competition_reads_its_own_set(competition, other_competition):
    seed_definitions(other_competition)
    ConsentDefinition.objects.for_competition(other_competition).filter(kind=ConsentKind.TERMS).update(
        text="Treść drugiego organizatora."
    )
    enable_flag(other_competition)

    assert consents.consent_set(other_competition)[0].text == "Treść drugiego organizatora."
    assert consents.consent_set(competition) is DEFAULT_CONSENTS


# --- więzy i walidacja ----------------------------------------------------------------------------


def test_one_kind_per_competition(competition):
    with pytest.raises(IntegrityError), transaction.atomic():
        ConsentDefinition.objects.create(
            competition=competition,
            kind=ConsentKind.TERMS,
            field_name="inne_pole",
            text="Druga zgoda tego samego rodzaju.",
            version="1.0",
        )


def test_one_field_name_per_competition(competition):
    """Nazwa pola jest kluczem w formularzu i w serializerze – zdublowana daje zgodę pod cudzym
    rodzajem. Sprawdzamy ją na **innym** rodzaju, żeby odbił właśnie ten więz, a nie unikalność
    rodzaju."""
    ConsentDefinition.objects.for_competition(competition).filter(kind=ConsentKind.PUBLISH_NAME).delete()

    with pytest.raises(IntegrityError), transaction.atomic():
        ConsentDefinition.objects.create(
            competition=competition,
            kind=ConsentKind.PUBLISH_NAME,
            field_name="terms_consent",
            text="Zgoda pod zajętą nazwą pola.",
            version="1.0",
        )


def test_a_definition_without_a_version_is_refused_by_the_database(other_competition):
    """Więz bazodanowy, a nie sam ``full_clean``: zgoda bez wersji nie jest dowodem."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ConsentDefinition.objects.create(
            competition=other_competition,
            kind=ConsentKind.TERMS,
            field_name="terms_consent",
            text="Zgoda bez wersji.",
            version="",
        )


def test_clean_refuses_a_text_with_an_unknown_placeholder(other_competition):
    definition = ConsentDefinition(
        competition=other_competition,
        kind=ConsentKind.TERMS,
        field_name="terms_consent",
        text="Zgadzam się na {cokolwiek}.",
        version="1.0",
    )

    with pytest.raises(ValidationError) as error:
        definition.full_clean()

    assert "text" in error.value.message_dict


def test_clean_accepts_the_two_known_placeholders(other_competition):
    definition = ConsentDefinition(
        competition=other_competition,
        kind=ConsentKind.TERMS,
        field_name="terms_consent",
        text="Zapoznałem/-am się z {link} organizatora {organizer}.",
        version="1.0",
    )

    definition.full_clean()  # nie podnosi


# --- zmiana wersji: ekran koordynatora i audyt ---------------------------------------------------


def test_change_version_writes_the_new_version_and_an_audit_entry(competition):
    definition = ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)
    previous = definition.version
    actor = ParticipantFactory(competition=competition).user

    consents.change_version(definition, "2.0 z 1 stycznia 2027", actor=actor)

    definition.refresh_from_db()
    assert definition.version == "2.0 z 1 stycznia 2027"
    entry = AuditLog.objects.get(action="consent_definition.version_changed")
    assert entry.diff["version"] == {"from": previous, "to": "2.0 z 1 stycznia 2027"}
    assert entry.diff["kind"] == ConsentKind.TERMS
    assert entry.target_type == "accounts.consentdefinition"


def test_change_version_leaves_the_proofs_untouched(competition):
    """Dowody są kopią napisu z chwili złożenia oświadczenia – zmiana wersji ich nie dotyka."""
    from apps.accounts.models import ConsentRecord

    participant = ParticipantFactory(competition=competition)
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.TERMS,
        document_version=consents.TERMS_VERSION,
        source="web",
    )
    definition = ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)

    consents.change_version(definition, "2.0 z 1 stycznia 2027")

    record.refresh_from_db()
    assert record.document_version == consents.TERMS_VERSION


def test_change_version_to_the_same_value_leaves_no_audit_entry(competition):
    """Wpis audytowy bez różnicy jest szumem w dzienniku, który ktoś czyta po latach."""
    definition = ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)

    consents.change_version(definition, definition.version)

    assert not AuditLog.objects.filter(action="consent_definition.version_changed").exists()


def test_change_version_refuses_an_empty_version(competition):
    definition = ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)

    with pytest.raises(ValidationError):
        consents.change_version(definition, "   ")

    definition.refresh_from_db()
    assert definition.version == consents.TERMS_VERSION
