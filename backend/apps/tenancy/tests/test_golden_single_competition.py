"""Testy złote: przy **jednym** konkursie serwis odpowiada tak, jak odpowiadał przed zmianą.

Kryterium przyjęcia całego etapu 1 brzmi „nic nie ruszyliśmy” (``docs/UNIWERSALNY-ETAP-1.md``
§ 0.4) i te testy są jego zapisem. Różnica wobec reszty pakietu: świat nie jest tu zbudowany pod
jedno pytanie, tylko odtwarza **kształt produkcji** (``golden.py``) – edycję z czterema etapami,
komplet stron, konta wszystkich ról, prace w różnych stanach, recenzje, wyniki i reklamację.
Dopiero na takim świecie „strona główna się renderuje” znaczy to samo, co na produkcji.

Testy chodzą przez klienta HTTP pod domeną Konkursu #1 (``client_for``), bo od wprowadzenia
wielokonkursowości **host jest częścią wejścia**: żądanie bez hosta konkursu przechodziłoby przez
odwrót do witryny domyślnej i sprawdzałoby inną ścieżkę niż ta, którą chodzi produkcja.
"""

from __future__ import annotations

import inspect

import pytest

from apps.accounts.consents import CONSENT_FIELD_NAMES
from apps.submissions.models import Submission
from apps.tenancy.tests.golden import build_golden, file_appeal, publish_results

pytestmark = pytest.mark.django_db


@pytest.fixture
def golden(competition):
    """Świat w kształcie produkcji. Jedna fikstura, bo świat ma sens wyłącznie w całości."""
    return build_golden(competition)


@pytest.fixture
def anon(client_for, competition):
    return client_for(competition)


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- strony publiczne -------------------------------------------------------------------------


def test_home_page_renders_with_the_current_edition_label(anon, golden):
    """Punkt 1 listy kontrolnej produkcji (§ 0.3): strona główna z etykietą bieżącej edycji."""
    response = anon.get("/")

    assert response.status_code == 200
    assert response.context["site_edition_label"] == golden.edition.year_label


def test_timeline_strip_shows_the_stages_of_the_current_edition(anon, golden):
    """Punkt 2 listy kontrolnej: pasek osi czasu z etapami bieżącej edycji i wydarzeniami."""
    content = anon.get("/").content.decode()

    assert "timeline-strip" in content
    assert golden.elim.display_name in content
    assert "Gala finałowa" in content


def test_registration_page_asks_for_exactly_the_documented_consents(anon, golden):
    """Punkt 4 listy kontrolnej: komplet zgód ze stałej ``CONSENTS``, w tej samej kolejności.

    Porównujemy z listą pól, a nie z liczbą: zgoda, która zniknie z formularza, ma zatrzymać
    wdrożenie, bo brak pola to brak oświadczenia, a nie kosmetyka układu.
    """
    response = anon.get("/register/")
    fields = [name for name in response.context["form"].fields if name in CONSENT_FIELD_NAMES]

    assert response.status_code == 200
    assert tuple(fields) == CONSENT_FIELD_NAMES


def test_published_results_render_for_anonymous_readers(anon, golden):
    """Punkt 9 listy kontrolnej: ogłoszona tabela wyników, z tą samą anonimizacją."""
    publication = publish_results(golden)

    response = anon.get(f"/results/{publication.stage_id}/")
    content = response.content.decode()

    assert response.status_code == 200
    for participant in golden.participants:
        assert participant.public_code in content
    # Anonimizacja kodem znaczy: kod **zamiast** nazwiska, a nie kod obok nazwiska.
    assert golden.participants[0].user.last_name not in content


def test_status_json_keeps_its_contract(anon, golden):
    """Punkt 10 listy kontrolnej: kształt ``/status.json`` jest kontraktem dla monitoringu."""
    payload = anon.get("/status.json").json()

    assert set(payload) == {
        "status",
        "time",
        "version",
        "services",
        "backup_last_ok",
        "backup_last_verified",
        "registration_open",
        "edition",
        "stage",
        "stage_deadline",
        "announcements",
    }
    assert payload["edition"] == golden.edition.year_label


def test_documents_section_and_partners_page_answer(anon, golden):
    """Punkt 3 listy kontrolnej: sekcja dokumentów i jej podstrony pod produkcyjnymi adresami."""
    assert anon.get("/dokumenty/").status_code == 200
    assert anon.get("/dokumenty/regulamin/").status_code == 200
    assert anon.get("/dokumenty/rodo/").status_code == 200
    assert anon.get("/partnerzy/").status_code == 200


# --- panele -----------------------------------------------------------------------------------


def test_participant_panel_renders_for_a_participant(client_for, competition, golden):
    """Punkt 6 listy kontrolnej: uczestnik wchodzi na swój panel – bez 403 i bez 404."""
    participant = golden.participants[0]
    client = logged_in(client_for, competition, participant.user)

    response = client.get("/me/")

    assert response.status_code == 200
    assert golden.elim.display_name in response.content.decode()


def test_reviewer_queue_renders_for_a_reviewer(client_for, competition, golden):
    client = logged_in(client_for, competition, golden.reviewer.user)

    response = client.get("/review/")

    assert response.status_code == 200


def test_coordinator_dashboard_renders_with_attention_counters(client_for, competition, golden):
    """Punkt 7 listy kontrolnej: pulpit koordynatora razem z licznikami „co wymaga uwagi”."""
    client = logged_in(client_for, competition, golden.coordinator)

    response = client.get("/coordinator/")

    assert response.status_code == 200
    assert response.context["edition"] == golden.edition
    assert response.context["attention"]


def test_coordinator_dashboard_lists_every_stage_of_the_edition(client_for, competition, golden):
    """Punkt 8 listy kontrolnej: komplet etapów edycji – razem z treningowym.

    Karty etapów stoją na pulpicie, a nie pod osobnym adresem ``/coordinator/stages/``: ten
    ostatni jest w serwisie wyłącznie prefiksem adresów szczegółowych. Test pilnuje liczby
    i tożsamości kart, bo etap, który wypadnie z pulpitu, znika koordynatorowi z oczu.
    """
    client = logged_in(client_for, competition, golden.coordinator)

    rows = client.get("/coordinator/").context["stage_rows"]

    assert {row["stage"].pk for row in rows} == {
        golden.training.pk,
        golden.elim.pk,
        golden.district.pk,
        golden.final.pk,
    }


def test_appeals_queue_renders_for_the_appeals_committee(client_for, competition, golden):
    """Właściwa rola widzi swoją kolejkę. Reguła „zła rola = 403” jest przedmiotem testu niżej."""
    appeal = file_appeal(golden)
    client = logged_in(client_for, competition, golden.appeals_member.user)

    response = client.get("/appeals/")

    assert response.status_code == 200
    assert str(appeal.pk) in response.content.decode()


# --- reguła 403 kontra 404 ---------------------------------------------------------------------


def test_participant_asking_for_the_coordinator_panel_gets_403_not_404(client_for, competition, golden):
    """Zła **rola** to 403, nie 404 – różnicę opisuje § 3.6 i ona się w tym etapie nie zmienia.

    404 znaczy „nie ma tego tutaj” i jest odpowiedzią na pytanie o **cudzy konkurs**. Panel
    koordynatora istnieje w tym konkursie i uczestnik ma prawo o tym wiedzieć; czego nie ma, to
    jego uprawnienia.
    """
    client = logged_in(client_for, competition, golden.participants[0].user)

    assert client.get("/coordinator/").status_code == 403


def test_anonymous_visitor_is_redirected_to_login(anon, golden):
    response = anon.get("/me/")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


# --- zapytania domeny ---------------------------------------------------------------------------


def test_coordinator_sees_every_submission_of_the_competition(golden, competition):
    """``for_user`` koordynatora oddaje **wszystkie** prace – po zakresowaniu: wszystkie swoje.

    Argument ``competition`` podajemy wtedy, gdy metoda już go przyjmuje: zadanie T3 dokłada go
    jako **wymagany** (``for_user(user, competition)``), żeby nie dało się wywołać jej po staremu
    i dostać cudzych danych (§ 3.5). Most jest tu, bo ten test opisuje regułę, która się nie
    zmienia („koordynator widzi wszystko swoje”), a nie sygnaturę, która się zmienia.
    """
    signature = inspect.signature(Submission.objects.for_user)
    arguments = (
        (golden.coordinator, competition) if "competition" in signature.parameters else (golden.coordinator,)
    )

    visible = Submission.objects.for_user(*arguments)

    assert set(visible) == set(Submission.objects.all())
