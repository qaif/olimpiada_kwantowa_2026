"""Szkielet panelu koordynatora: menu, liczniki, wyszukiwarka, wyprowadzone ekrany i powroty.

Testy trzymają się tego, co widzi człowiek (HTML odpowiedzi) albo co rozstrzyga adres – a nie
kształtu struktury w ``coordinator_nav``: menu ma prawo zmienić podział na sekcje, a ekran ma
nie zmienić tego, że z pulpitu da się dojść do kolejki aktywacji.

Liczniki mają minutową pamięć podręczną wspólną dla całego panelu; czyści ją przed każdym testem
fikstura ``_reset_panel_counters`` z ``conftest.py`` – inaczej wynik zależałby od tego, który test
biegł wcześniej.
"""

import pytest

from apps.accounts.models import CommitteeStatus
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.web.coordinator_nav import attention_counters, invalidate_counters

pytestmark = pytest.mark.django_db


def test_nav_renders_for_the_coordinator(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert 'aria-label="Menu panelu koordynatora"' in content
    # Sekcje menu – po nich widać, że panel ma jedną mapę, a nie listę odnośników.
    for label in ("Etapy", "Ocenianie", "Uczestnicy i konta", "Komitet", "Raporty", "Ustawienia"):
        assert f'<span class="panel-nav__heading-label">{label}</span>' in content
    assert "/coordinator/activations/" in content
    assert "/coordinator/committee/" in content
    assert elim_stage.display_name in content


def test_nav_is_not_rendered_for_other_roles(web_client, participant):
    """Menu panelu jest częścią panelu: uczestnik nie dostaje go nigdzie w serwisie."""
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Menu panelu" not in content
    assert web_client.get("/coordinator/").status_code == 403


def test_active_item_is_marked_by_view_name(web_client, coordinator, elim_stage):
    """Pozycja aktywna wynika z nazwy widoku, więc świeci się też na ekranie z parametrem."""
    web_client.force_login(coordinator)

    content = web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").content.decode()

    assert "panel-nav__sublink panel-nav__sublink--active" in content


def test_counters_feed_badges_and_attention_cards(web_client, coordinator, elim_stage):
    CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    UserFactory(is_active=False, email_verified_at=None)
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "Co wymaga uwagi" in content
    assert "Komitet do zatwierdzenia" in content
    assert "Konta do aktywacji" in content
    assert "panel-nav__badge" in content


def test_counters_are_cached_for_a_minute(coordinator, elim_stage):
    """Druga odpowiedź wraca z pamięci podręcznej – panel nie liczy tego na każdej stronie."""
    CommitteeMemberFactory(status=CommitteeStatus.PENDING)

    first = attention_counters([elim_stage.pk])
    CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    second = attention_counters([elim_stage.pk])

    assert first == second == {**first, "committee": 1}
    invalidate_counters()
    assert attention_counters([elim_stage.pk])["committee"] == 2


def test_search_groups_results(web_client, coordinator, participant, problems):
    ActiveReviewerFactory(user=UserFactory(email="komisja@example.test", last_name="Nazwiskowski"))
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/search/", {"q": "Nazwiskowski"}).content.decode()

    assert "Uczestnicy" in content
    assert "Członkowie komisji" in content
    assert participant.public_code in content


def test_search_finds_a_problem_by_title(web_client, coordinator, problems):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/search/", {"q": "Nierówność"}).content.decode()

    assert "Zadanie 1: Nierówność ze średnimi" in content


def test_search_refuses_a_one_letter_query(web_client, coordinator, participant):
    """Jedna litera pasuje do połowy bazy – ekran mówi to wprost zamiast wypisywać wszystko."""
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/search/", {"q": "a"}).content.decode()

    assert "Za krótka fraza" in content
    assert participant.public_code not in content


@pytest.mark.parametrize(
    ("url", "marker"),
    [
        ("/coordinator/committee/", "Kod zaproszenia"),
        ("/coordinator/activations/", "Konta oczekujące na aktywację"),
        ("/coordinator/moderation/", "Moderacja (rozjazdy ocen)"),
        ("/coordinator/search/", "Wyniki wyszukiwania"),
    ],
)
def test_new_pages_render(web_client, coordinator, elim_stage, url, marker):
    web_client.force_login(coordinator)

    response = web_client.get(url)

    assert response.status_code == 200
    assert marker in response.content.decode()


def test_stage_results_page_does_not_compute_on_entry(web_client, coordinator, elim_stage, entry):
    """Wejście na wyniki niczego nie zapisuje: przeliczenie jest decyzją, a nie skutkiem GET-a."""
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/results/")

    entry.refresh_from_db()
    assert response.status_code == 200
    assert "Przelicz wyniki (podgląd)" in response.content.decode()
    # ``None``, a nie zero: przeliczenie wpisuje sumę punktów, więc nietknięte pole dowodzi,
    # że samo otwarcie adresu niczego nie policzyło.
    assert entry.total_points is None


@pytest.mark.parametrize(
    ("url", "marker"),
    [
        ("/coordinator/committee/", "Nikt nie czeka na zatwierdzenie"),
        ("/coordinator/activations/", "Brak kont oczekujących na aktywację"),
        ("/coordinator/moderation/", "Bez rozjazdów"),
    ],
)
def test_empty_states_explain_what_is_missing(web_client, coordinator, url, marker):
    web_client.force_login(coordinator)

    assert marker in web_client.get(url).content.decode()


def test_action_returns_to_the_page_it_was_called_from(web_client, coordinator):
    """Zatwierdzenie z ekranu komitetu wraca na ekran komitetu, a nie na pulpit."""
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/committee/{member.pk}/approve/",
        HTTP_REFERER="http://testserver/coordinator/committee/",
    )

    assert response.headers["Location"] == "/coordinator/committee/"


@pytest.mark.parametrize(
    "referer",
    [
        "https://zlosliwy.example/coordinator/committee/",
        "http://testserver/me/",
        "javascript:alert(1)",
        "//zlosliwy.example/coordinator/",
    ],
)
def test_action_ignores_a_referer_from_outside_the_panel(web_client, coordinator, referer):
    """Powrót bierze się z nagłówka nadawcy, więc obcy adres musi wrócić na pulpit."""
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/committee/{member.pk}/approve/", HTTP_REFERER=referer)

    assert response.headers["Location"] == "/coordinator/"


def test_moderation_queue_shows_the_work_and_its_reviews(
    web_client, coordinator, elim_stage, entry, problems
):
    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.MODERATION)
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/moderation/").content.decode()

    assert entry.participant.public_code in content
    assert f"praca-{submission.pk}" in content


def test_dashboard_no_longer_carries_the_moved_sections(web_client, coordinator, elim_stage):
    """Pulpit zostaje przy „co wymaga uwagi” – kolejki mają własne adresy."""
    ParticipantFactory()
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "Wysłane zaproszenia" not in content
    assert "Województwa członków komitetu" not in content
    assert "/coordinator/moderation/" in content


def test_nav_groups_are_collapsible_and_the_active_group_is_pinned(web_client, coordinator, elim_stage):
    """Sekcje menu są elementami ``<details>``; sekcja z pozycją aktywną i pulpit są przypięte."""
    web_client.force_login(coordinator)

    content = web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").content.decode()

    assert '<details class="panel-nav__group" data-nav-group="' in content
    assert 'data-nav-group="etapy" data-nav-pinned="1" open' in content
    assert 'data-nav-group="pulpit" data-nav-pinned="1" open' in content
    # Sekcja bez pozycji aktywnej nie jest przypięta – skrypt może ją zwinąć z pamięci przeglądarki.
    assert 'data-nav-group="raporty" data-nav-pinned="1"' not in content
    assert "js/coordinator-nav.js" in content
