"""Ekran „Szablony dokumentów” ``/coordinator/documents/`` (etap 2, T13).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest **wyłączony** w konkursie z domyślnymi przełącznikami, a adres daje wtedy 404, a nie
  403: Olimpiada Kwantowa po wdrożeniu ma mieć adresy dokładnie takie, jak przed nim (§ 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu flagi – odpowiedź nie zdradza konfiguracji,
- podgląd składa się po stronie serwera, z podstawieniami, i **niczego nie zapisuje**,
- publikacja ma drugi krok, który nazywa wersję („od tej chwili dokumenty będą wystawiane
  w wersji X”), a dopiero potwierdzenie woła ``set_current_template``,
- poprzednia wersja **zostaje** jako historia, a wpis audytowy pisze serwis,
- szablonów sąsiada nie widać ani na liście, ani w historii rodzaju (§ 3.6).

Adresy są w ``apps/web/urls.py`` od montażu wydania E (T15): dwa wzorce dopisane na końcu listy
przez ``urls_documents``. Testy chodzą więc po **produkcyjnej** mapie adresów, a nie po urlconfie
testowym – to jest ta sama mapa, którą zobaczy koordynator.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.documents import (
    DOCUMENTS_FLAG,
    INITIAL_VERSION,
    DocumentKind,
    DocumentTemplate,
    current_template,
    templates_of,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/documents/"
LAUREAT_URL = f"/coordinator/documents/{DocumentKind.LAUREAT}/"


def enable(competition):
    """Włącza ekran tak, jak zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), DOCUMENTS_FLAG: True}
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


def payload(**overrides) -> dict:
    """Komplet pól formularza nowej wersji, plus to, co test podmienia."""
    data = {
        "version": "2.0 z 1 marca 2027",
        "title": "Dyplom laureata {competition_genitive}",
        "statement": "{recipient} uzyskał(a) tytuł laureata w edycji {edition}",
        "signature_line": "Przewodniczący komitetu",
        "footer_note": "",
    }
    data.update(overrides)
    return data


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    """Konkurs #1 po wdrożeniu: adresu nie ma, dopóki nikt świadomie nie włączy własnych tekstów."""
    client, _user = coordinator_for(client_for, competition)

    assert not competition.has_feature(DOCUMENTS_FLAG)
    assert client.get(LIST_URL).status_code == 404
    assert client.get(LAUREAT_URL).status_code == 404
    assert client.post(LAUREAT_URL, payload(confirm="1")).status_code == 404
    assert templates_of(competition).filter(version="2.0 z 1 marca 2027").count() == 0


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


# --- lista rodzajów -------------------------------------------------------------------------------


def test_list_shows_every_kind_with_the_version_in_force(coordinator_client):
    """Rodzaje są zamkniętą listą w kodzie – ekran wymienia wszystkie, także te bez szablonu."""
    content = coordinator_client.get(LIST_URL).content.decode()

    for kind in DocumentKind:
        assert kind.label in content
    assert INITIAL_VERSION in content
    # Faktura i lista obecności nie mają jeszcze wiersza: ekran ma to powiedzieć, a nie przemilczeć.
    assert "tekst wbudowany" in content


def test_unknown_kind_is_a_404(coordinator_client):
    assert coordinator_client.get("/coordinator/documents/DYPLOMIK/").status_code == 404


# --- ekran rodzaju --------------------------------------------------------------------------------


def test_the_kind_screen_shows_the_current_text_and_the_allowed_placeholders(coordinator_client, competition):
    current = current_template(competition, DocumentKind.LAUREAT)

    content = coordinator_client.get(LAUREAT_URL).content.decode()

    assert current.version in content
    assert current.title in content
    assert "{participant_code}" in content
    # Znacznik własny faktury nie ma czego znaczyć na dyplomie i nie wolno go tu podpowiadać.
    assert "{amount}" not in content


def test_the_invoice_screen_lists_its_own_placeholders(coordinator_client):
    content = coordinator_client.get(f"/coordinator/documents/{DocumentKind.INVOICE}/").content.decode()

    assert "{amount}" in content
    assert "{due_date}" in content


def test_the_form_starts_from_the_current_text_but_without_a_version(coordinator_client, competition):
    """Treść przepisana, numer pusty: podpowiedziany numer kusiłby, żeby go zatwierdzić bez czytania."""
    current = current_template(competition, DocumentKind.LAUREAT)

    response = coordinator_client.get(LAUREAT_URL)

    form = response.context["form"]
    assert form.initial["statement"] == current.statement
    assert not form.initial.get("version")


# --- podgląd --------------------------------------------------------------------------------------


def test_the_preview_substitutes_the_values_and_saves_nothing(coordinator_client, competition):
    before = templates_of(competition).count()

    response = coordinator_client.post(LAUREAT_URL, payload(preview="1"))

    assert response.status_code == 200
    content = response.content.decode()
    assert competition.genitive in content
    # Złożone zdanie, a nie wzorzec: to jest cała różnica między podglądem a przepisaniem pola.
    assert "Anna Przykładowska uzyskał(a) tytuł laureata w edycji 2026/2027" in content
    assert templates_of(competition).count() == before


def test_a_preview_of_a_broken_placeholder_is_a_400(coordinator_client, competition):
    """Literówka ma się wywrócić w panelu, a nie w dokumencie wydanym tysiącu osób."""
    before = templates_of(competition).count()

    response = coordinator_client.post(LAUREAT_URL, payload(preview="1", title="Dyplom {partcipant_code}"))

    assert response.status_code == 400
    assert "Nieznane znaczniki" in response.content.decode()
    assert templates_of(competition).count() == before


# --- publikacja -----------------------------------------------------------------------------------


def test_publishing_asks_for_confirmation_first(coordinator_client, competition):
    before = current_template(competition, DocumentKind.LAUREAT).version

    response = coordinator_client.post(LAUREAT_URL, payload())

    assert response.status_code == 200
    content = response.content.decode()
    assert "Od tej chwili dokumenty będą wystawiane w wersji" in content
    assert "2.0 z 1 marca 2027" in content
    assert current_template(competition, DocumentKind.LAUREAT).version == before
    assert not AuditLog.objects.filter(action="document_template.changed").exists()


def test_the_confirmed_version_becomes_the_current_one_and_is_audited(coordinator_client, competition):
    previous = current_template(competition, DocumentKind.LAUREAT)

    response = coordinator_client.post(LAUREAT_URL, payload(confirm="1"))

    assert response.status_code == 302
    assert response["Location"] == LAUREAT_URL
    current = current_template(competition, DocumentKind.LAUREAT)
    assert current.version == "2.0 z 1 marca 2027"
    assert current.signature_line == "Przewodniczący komitetu"
    entry = AuditLog.objects.get(action="document_template.changed")
    assert entry.diff["version"] == "2.0 z 1 marca 2027"
    assert entry.diff["previous_version"] == previous.version


def test_the_previous_version_stays_as_history(coordinator_client, competition):
    """Dokument wydany w zeszłym roku wskazuje swój tekst numerem wersji – wiersz musi zostać."""
    previous = current_template(competition, DocumentKind.LAUREAT)

    coordinator_client.post(LAUREAT_URL, payload(confirm="1"))

    previous.refresh_from_db()
    assert previous.is_current is False
    assert previous.title  # treść nietknięta
    content = coordinator_client.get(LAUREAT_URL).content.decode()
    assert previous.version in content
    assert "historia" in content


def test_a_repeated_version_is_rejected_without_changing_anything(coordinator_client, competition):
    """Dwie wersje o tej samej nazwie znaczyłyby, że nie wiadomo, którą z nich ktoś trzyma w ręku."""
    before = current_template(competition, DocumentKind.LAUREAT)

    response = coordinator_client.post(LAUREAT_URL, payload(version=INITIAL_VERSION, confirm="1"))

    assert response.status_code == 400
    assert templates_of(competition).filter(kind=DocumentKind.LAUREAT, version=INITIAL_VERSION).count() == 1
    assert current_template(competition, DocumentKind.LAUREAT).pk == before.pk


def test_a_broken_placeholder_is_rejected_with_400(coordinator_client, competition):
    response = coordinator_client.post(
        LAUREAT_URL, payload(confirm="1", statement="{recipient} z {nieznany}")
    )

    assert response.status_code == 400
    assert not templates_of(competition).filter(version="2.0 z 1 marca 2027").exists()


def test_an_empty_version_is_rejected_with_400(coordinator_client, competition):
    before = templates_of(competition).count()

    response = coordinator_client.post(LAUREAT_URL, payload(version="   ", confirm="1"))

    assert response.status_code == 400
    assert templates_of(competition).count() == before


def test_the_first_version_of_a_kind_without_a_template_can_be_published(coordinator_client, competition):
    """Faktura i lista obecności nie mają wiersza z migracji – ekran ma je dać założyć."""
    url = f"/coordinator/documents/{DocumentKind.ATTENDANCE_LIST}/"

    response = coordinator_client.post(
        url,
        payload(version="1.0", title="Lista obecności – {stage}", statement="{date}", confirm="1"),
    )

    assert response.status_code == 302
    assert current_template(competition, DocumentKind.ATTENDANCE_LIST).version == "1.0"


# --- izolacja -------------------------------------------------------------------------------------


def test_the_history_of_another_competition_is_invisible(client_for, competition, other_competition):
    enable(competition)
    enable(other_competition)
    DocumentTemplate.objects.create(
        competition=other_competition,
        kind=DocumentKind.LAUREAT,
        version="wersja-sasiada",
        title="Dyplom drugiego organizatora",
        statement="cudze zdanie",
    )
    client, _user = coordinator_for(client_for, competition)

    content = client.get(LAUREAT_URL).content.decode()

    assert "wersja-sasiada" not in content
    assert "cudze zdanie" not in content


def test_publishing_does_not_reach_the_neighbour(client_for, competition, other_competition):
    """Konkurs bierze się z żądania, a nie z formularza – cudzego tekstu nie da się stąd ruszyć."""
    enable(competition)
    enable(other_competition)
    client, _user = coordinator_for(client_for, competition)

    client.post(LAUREAT_URL, payload(confirm="1"))

    assert templates_of(other_competition).filter(version="2.0 z 1 marca 2027").count() == 0
    assert templates_of(competition).filter(version="2.0 z 1 marca 2027").count() == 1
