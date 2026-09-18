"""Ekran „Zgody konkursu” ``/coordinator/consents/`` (etap 2, T11).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest **wyłączony** w konkursie z domyślnymi przełącznikami, a adres daje wtedy 404, a nie
  403: Olimpiada Kwantowa po wdrożeniu ma mieć adresy dokładnie takie, jak przed nim (§ 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu flagi – odpowiedź nie zdradza konfiguracji,
- zapis treści nie rusza wersji, a zmiana wersji nie rusza treści i wymaga **drugiego** kroku
  z jednym zdaniem o skutku,
- zgody sąsiada nie widać nawet po identyfikatorze (404 z zawężonego querysetu, § 3.6),
- wpis audytowy po zmianie wersji pisze serwis, po zmianie treści – widok, a zapis bez zmiany nie
  zostawia śladu w ogóle.

Adresy są w ``apps/web/urls.py`` od montażu wydania E (T15): trzy wzorce dopisane na końcu listy
przez ``urls_consents``. Testy chodzą więc po **produkcyjnej** mapie adresów, a nie po urlconfie
testowym – to jest ta sama mapa, którą zobaczy koordynator.
"""

from __future__ import annotations

import pytest

from apps.accounts.consents import CONSENT_FEATURE_FLAG, ConsentKind, ConsentSource
from apps.accounts.models import CompetitionRole, ConsentDefinition
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/consents/"


def edit_url(definition) -> str:
    return f"/coordinator/consents/{definition.pk}/"


def version_url(definition) -> str:
    return f"/coordinator/consents/{definition.pk}/version/"


def enable(competition):
    """Włącza ekran tak, jak zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), CONSENT_FEATURE_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client, user


@pytest.fixture
def coordinator_client(client_for, competition):
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** ekranem."""
    enable(competition)
    client, _user = coordinator_for(client_for, competition)
    return client


@pytest.fixture
def terms(competition) -> ConsentDefinition:
    """Definicja zgody na regulamin – wiersz z migracji ``accounts.0024``."""
    return ConsentDefinition.objects.for_competition(competition).get(kind=ConsentKind.TERMS)


def form_payload(definition, **overrides) -> dict:
    """Komplet pól formularza treści w stanie „bez zmian”, plus to, co test podmienia."""
    payload = {
        "text": definition.text,
        "link_text": definition.link_text,
        "document_slug": definition.document_slug,
        "help_text": definition.help_text,
        "missing_message": definition.missing_message,
        "ordering": definition.ordering,
    }
    if definition.is_active:
        payload["is_active"] = "on"
    payload.update(overrides)
    return payload


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition, terms):
    """Konkurs #1 po wdrożeniu: adresu nie ma, dopóki nikt świadomie nie włączy zgód własnych."""
    client, _user = coordinator_for(client_for, competition)

    assert not competition.has_feature(CONSENT_FEATURE_FLAG)
    assert client.get(LIST_URL).status_code == 404
    assert client.get(edit_url(terms)).status_code == 404
    assert client.post(version_url(terms), {"version": "2.0"}).status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    """Rola sprawdza się przed flagą: odpowiedź nie mówi uczestnikowi, jak skonfigurowano konkurs."""
    user = ParticipantFactory().user
    client = client_for(competition)
    client.force_login(user)

    assert client.get(LIST_URL).status_code == 403

    enable(competition)

    assert client.get(LIST_URL).status_code == 403


def test_anonymous_is_redirected_to_login(client_for, competition):
    enable(competition)
    response = client_for(competition).get(LIST_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- lista ----------------------------------------------------------------------------------------


def test_list_shows_every_definition_with_version_and_requirement(coordinator_client, competition):
    content = coordinator_client.get(LIST_URL).content.decode()

    for definition in ConsentDefinition.objects.for_competition(competition):
        assert definition.field_name in content
        assert definition.version in content
    assert "wymagana dla niepełnoletnich" in content
    assert "dobrowolna" in content


def test_list_keeps_a_definition_switched_off(coordinator_client, competition, terms):
    """Wyłączona zgoda zostaje na liście – inaczej jedyną drogą do jej przywrócenia jest /admin/."""
    terms.is_active = False
    terms.save(update_fields=["is_active"])

    content = coordinator_client.get(LIST_URL).content.decode()

    assert terms.field_name in content
    assert "wyłączona" in content


def test_list_warns_when_nothing_is_active(coordinator_client, competition):
    """Zero aktywnych definicji znaczy zestaw domyślny w formularzu – i ekran musi to powiedzieć."""
    ConsentDefinition.objects.for_competition(competition).update(is_active=False)

    content = coordinator_client.get(LIST_URL).content.decode()

    assert "zestaw domyślny" in content


# --- edycja treści --------------------------------------------------------------------------------


def test_editing_the_text_saves_it_and_leaves_an_audit_entry(coordinator_client, terms):
    response = coordinator_client.post(
        edit_url(terms), form_payload(terms, text="Akceptuję {link}.", help_text="nowa podpowiedź")
    )

    assert response.status_code == 302
    assert response["Location"] == LIST_URL
    terms.refresh_from_db()
    assert terms.text == "Akceptuję {link}."
    assert terms.help_text == "nowa podpowiedź"
    entry = AuditLog.objects.get(action="consent_definition.updated")
    assert set(entry.diff["fields"]) == {"text", "help_text"}
    assert entry.diff["kind"] == ConsentKind.TERMS


def test_saving_without_a_change_leaves_no_audit_entry(coordinator_client, terms):
    coordinator_client.post(edit_url(terms), form_payload(terms))

    assert not AuditLog.objects.filter(action="consent_definition.updated").exists()


def test_a_broken_placeholder_is_rejected_with_400(coordinator_client, terms):
    """Niepodstawione ``{cokolwiek}`` wywróciłoby formularz rejestracji u uczestnika, nie tutaj."""
    response = coordinator_client.post(edit_url(terms), form_payload(terms, text="Zgoda {cokolwiek}."))

    assert response.status_code == 400
    terms.refresh_from_db()
    assert "{cokolwiek}" not in terms.text


def test_the_form_does_not_change_the_version(coordinator_client, terms):
    """Wersja jest w formularzu wyłączona: wartość z POST-a nie ma prawa jej ruszyć."""
    before = terms.version

    coordinator_client.post(edit_url(terms), form_payload(terms, version="9.9 podstawione"))

    terms.refresh_from_db()
    assert terms.version == before


def test_the_form_does_not_change_the_requirement(coordinator_client, terms):
    """Wymagalności nie ma w formularzu – podstawienie jej w POST nie zmienia podstawy przetwarzania."""
    response = coordinator_client.post(
        edit_url(terms), form_payload(terms, required="", required_for_minor="on")
    )

    assert response.status_code == 302
    terms.refresh_from_db()
    assert terms.required is True
    assert terms.required_for_minor is False


def test_switching_a_consent_off_takes_it_out_of_the_registration_set(coordinator_client, competition, terms):
    from apps.accounts.consents import consent_set

    coordinator_client.post(edit_url(terms), form_payload(terms, is_active=""))

    competition.refresh_from_db()
    assert ConsentKind.TERMS not in [consent.kind for consent in consent_set(competition)]


# --- zmiana wersji --------------------------------------------------------------------------------


def test_the_version_change_asks_for_confirmation_first(coordinator_client, terms):
    before = terms.version

    response = coordinator_client.post(version_url(terms), {"version": "2.0 z 1 marca 2027"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "Od tej chwili nowe zgody będą zapisywane pod wersją" in content
    assert "2.0 z 1 marca 2027" in content
    terms.refresh_from_db()
    assert terms.version == before
    assert not AuditLog.objects.filter(action="consent_definition.version_changed").exists()


def test_the_confirmed_version_change_is_saved_and_audited(coordinator_client, terms):
    before = terms.version

    response = coordinator_client.post(version_url(terms), {"version": "2.0 z 1 marca 2027", "confirm": "1"})

    assert response.status_code == 302
    terms.refresh_from_db()
    assert terms.version == "2.0 z 1 marca 2027"
    entry = AuditLog.objects.get(action="consent_definition.version_changed")
    assert entry.diff["version"] == {"from": before, "to": "2.0 z 1 marca 2027"}


def test_an_empty_version_is_rejected_with_400(coordinator_client, terms):
    before = terms.version

    response = coordinator_client.post(version_url(terms), {"version": "   ", "confirm": "1"})

    assert response.status_code == 400
    terms.refresh_from_db()
    assert terms.version == before


def test_the_same_version_changes_nothing(coordinator_client, terms):
    response = coordinator_client.post(version_url(terms), {"version": terms.version, "confirm": "1"})

    assert response.status_code == 302
    assert not AuditLog.objects.filter(action="consent_definition.version_changed").exists()


def test_get_on_the_version_address_goes_back_to_the_consent(coordinator_client, terms):
    response = coordinator_client.get(version_url(terms))

    assert response.status_code == 302
    assert response["Location"] == edit_url(terms)


def test_the_version_change_leaves_the_proof_untouched(coordinator_client, competition, terms):
    """Dowód jest kopią napisu z chwili zgody – zmiana wersji opisuje przyszłość, nie przeszłość."""
    from apps.accounts.models import ConsentRecord

    participant = ParticipantFactory()
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.TERMS,
        document_version=terms.version,
        source=ConsentSource.WEB,
    )
    version_before = terms.version

    coordinator_client.post(version_url(terms), {"version": "3.0", "confirm": "1"})

    record.refresh_from_db()
    terms.refresh_from_db()
    assert record.document_version == version_before
    assert terms.version == "3.0"


# --- izolacja -------------------------------------------------------------------------------------


def test_a_definition_of_another_competition_is_not_found(client_for, competition, other_competition):
    """Cudza zgoda daje 404, a nie 403: istnienie cudzego wiersza nie jest niczyją informacją."""
    enable(competition)
    enable(other_competition)
    client, _user = coordinator_for(client_for, competition)
    foreign = ConsentDefinition.objects.create(
        competition=other_competition,
        kind=ConsentKind.TERMS,
        field_name="terms_consent",
        text="Treść drugiego organizatora.",
        version="1.0",
        required=True,
    )

    assert client.get(edit_url(foreign)).status_code == 404
    assert client.post(version_url(foreign), {"version": "2.0", "confirm": "1"}).status_code == 404
    foreign.refresh_from_db()
    assert foreign.version == "1.0"


def test_the_list_shows_only_the_consents_of_this_competition(client_for, competition, other_competition):
    enable(competition)
    ConsentDefinition.objects.create(
        competition=other_competition,
        kind=ConsentKind.TERMS,
        field_name="cudze_pole",
        text="Treść drugiego organizatora.",
        version="1.0",
    )
    client, _user = coordinator_for(client_for, competition)

    content = client.get(LIST_URL).content.decode()

    assert "cudze_pole" not in content
