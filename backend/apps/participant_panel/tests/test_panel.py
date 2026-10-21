"""Rama panelu uczestnika (UX-PANEL-01): menu, pozycja bieżąca, widoczność wg flag, kafle, narzędzia."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from apps.chat.tests.helpers import configure, participant_of
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.notifications import services as notifications
from apps.notifications.kinds import NotificationKind

pytestmark = pytest.mark.django_db

ME = "/me/"


def flag(competition, name: str, on: bool = True):
    competition.feature_flags = {**(competition.feature_flags or {}), name: on}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def alice(competition):
    return participant_of(competition)


@pytest.fixture
def client(client_for, competition, alice):
    client = client_for(competition)
    client.force_login(alice.user)
    return client


def nav_keys(html: str) -> list[str]:
    return re.findall(r'data-nav="([a-z_]+)"', html)


def current_keys(html: str) -> list[str]:
    return re.findall(r'aria-current="page" data-nav="([a-z_]+)"', html)


def page(client, path=ME) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode()


# --- menu -------------------------------------------------------------------------------------------


def test_menu_has_the_sections_and_is_a_details_on_the_phone(client):
    html = page(client)

    assert 'aria-label="Nawigacja panelu uczestnika"' in html
    assert '<details class="pp-nav">' in html
    assert "Menu uczestnika" in html
    # „Menu panelu” to podpis menu koordynatora – uczestnik go nie widzi (test_coordinator_shell).
    assert "Menu panelu" not in html
    for label in ("Start", "Zawody", "Nauka", "Wiadomości", "Konto"):
        assert f">{label}</p>" in html
    assert {"dashboard", "notifications", "tasks", "results", "calendar", "profile", "consents"} <= set(
        nav_keys(html)
    )


@pytest.mark.parametrize(
    ("path", "current"),
    [
        (ME, "dashboard"),
        ("/me/?tab=zadania", "tasks"),
        ("/me/?tab=wyniki", "results"),
        ("/me/?tab=zgody", "consents"),
        ("/me/?tab=nieznana", "dashboard"),
        ("/me/calendar/", "calendar"),
        ("/me/archive/", "archive"),
        ("/me/profile/", "profile"),
        ("/me/notifications/", "notifications"),
    ],
)
def test_exactly_one_current_item(client, path, current):
    assert current_keys(page(client, path)) == [current]


def test_optional_items_are_hidden_without_their_switches(client, competition, settings):
    configure(competition, enabled=False)
    settings.WEBTEX_SHARED_KEY = ""
    html = page(client)

    for key in ("chat", "forum", "materials", "webinars", "notebooks", "latex", "student_status"):
        assert key not in nav_keys(html), key


def test_optional_items_follow_existing_switches(client, competition, settings):
    configure(competition)
    flag(competition, "quantum_notebooks")
    flag(competition, "latex_editor")
    flag(competition, "student_status_certificate")
    settings.WEBTEX_SHARED_KEY = "k" * 40
    settings.WEBTEX_URL = "https://tex.example.test"

    keys = nav_keys(page(client))

    for key in ("chat", "notebooks", "latex", "student_status"):
        assert key in keys, key


def test_latex_needs_the_integration_not_only_the_flag(client, competition, settings):
    flag(competition, "latex_editor")
    settings.WEBTEX_SHARED_KEY = ""

    assert "latex" not in nav_keys(page(client))
    assert client.get(reverse("web:participant-latex")).status_code == 404


def test_bell_shows_the_unread_count(client, competition, alice):
    notifications.notify(alice.user_id, competition, NotificationKind.APPEAL_DECIDED, "web:me")
    notifications.notify(
        alice.user_id, competition, NotificationKind.RESULTS_PUBLISHED, "web:me", params={"stage": "x"}
    )

    html = page(client)

    assert "Powiadomienia, nieprzeczytane: 2" in html
    assert 'class="pp-bell__count" aria-hidden="true">2<' in html


def test_menu_frames_the_chat_inbox(client, competition):
    configure(competition)

    html = page(client, "/me/messages/")

    assert current_keys(html) == ["chat"]
    assert 'class="chat' in html


def test_profile_for_other_roles_has_no_participant_menu(client_for, competition):
    from apps.chat.tests.helpers import coordinator_of

    coordinator = coordinator_of(competition)
    client = client_for(competition)
    client.force_login(coordinator)

    html = page(client, "/account/profile/")

    assert "pp-nav" not in html


# --- kafle „Start” ----------------------------------------------------------------------------------


def test_start_tiles_show_the_deadline_of_an_open_stage(client, competition, alice):
    stage = StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))
    StageEntryFactory(stage=stage, participant=alice)

    html = page(client)

    assert "Najbliższy termin" in html
    assert "Przejdź do zadań" in html


def test_student_status_tile_has_the_upload_button(client, competition):
    flag(competition, "student_status_certificate")
    CurrentEditionFactory(competition=competition)

    html = page(client)

    assert 'id="status-ucznia"' in html
    assert reverse("web:student-status") in html


def test_tools_tile_only_with_a_tool(client, competition):
    assert "pp-tile--tools" not in page(client)

    flag(competition, "quantum_notebooks")
    html = page(client)

    assert "pp-tile--tools" in html
    assert reverse("web:participant-notebooks") in html


def test_next_workshop_tile_without_meeting_link(client, monkeypatch):
    import datetime

    from apps.cms import workshops

    monkeypatch.setattr(workshops, "workshops_page", lambda competition=None: object())
    monkeypatch.setattr(
        workshops,
        "upcoming_workshops",
        lambda page, **kwargs: [
            {
                "topic": "Kubity i bramki",
                "date": "",
                "date_value": datetime.date(2026, 11, 12),
                "time": "17:00",
            }
        ],
    )

    html = page(client)

    assert "Najbliższy warsztat" in html
    assert "Kubity i bramki" in html
    assert "meet.google.com" not in html


# --- narzędzia --------------------------------------------------------------------------------------


def test_notebooks_hub_lists_problems_with_a_notebook_in_open_stages(client, competition, alice):
    from apps.notebooks.models import NotebookTask

    flag(competition, "quantum_notebooks")
    stage = StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))
    StageEntryFactory(stage=stage, participant=alice)
    with_notebook = ProblemFactory(stage=stage, number=1, title="Splątanie")
    ProblemFactory(stage=stage, number=2, title="Bez notatnika")
    NotebookTask.objects.create(problem=with_notebook)

    html = page(client, reverse("web:participant-notebooks"))

    assert "Splątanie" in html
    assert "Bez notatnika" not in html
    assert reverse("web:participant-notebook", args=[with_notebook.pk]) in html


def test_notebooks_hub_hides_stages_without_an_entry(client, competition):
    from apps.notebooks.models import NotebookTask

    flag(competition, "quantum_notebooks")
    stage = StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))
    NotebookTask.objects.create(problem=ProblemFactory(stage=stage, number=1, title="Cudzy etap"))

    html = page(client, reverse("web:participant-notebooks"))

    assert "Cudzy etap" not in html
    assert "Na razie nie ma tu zadań" in html


def test_notebooks_hub_is_404_without_the_flag(client):
    assert client.get(reverse("web:participant-notebooks")).status_code == 404


def test_latex_hub_links_to_the_sso_view(client, competition, alice, settings):
    flag(competition, "latex_editor")
    settings.WEBTEX_SHARED_KEY = "k" * 40
    settings.WEBTEX_URL = "https://tex.example.test"
    stage = StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))
    StageEntryFactory(stage=stage, participant=alice)
    problem = ProblemFactory(stage=stage, number=3, title="Pomiar")

    html = page(client, reverse("web:participant-latex"))

    assert reverse("web:problem-latex", args=[stage.pk, problem.number]) in html
    assert 'rel="noopener noreferrer"' in html


def test_hubs_skip_stages_closed_by_the_proctoring_gate(client, competition, alice, monkeypatch):
    from apps.notebooks.models import NotebookTask
    from apps.proctoring import services as proctoring

    flag(competition, "quantum_notebooks")
    flag(competition, "proctoring")
    stage = StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))
    StageEntryFactory(stage=stage, participant=alice)
    NotebookTask.objects.create(problem=ProblemFactory(stage=stage, number=1, title="Pod nadzorem"))
    monkeypatch.setattr(proctoring, "gate_decision", lambda *args, **kwargs: proctoring.GATE_CONSOLE)

    html = page(client, reverse("web:participant-notebooks"))

    assert "Pod nadzorem" not in html
