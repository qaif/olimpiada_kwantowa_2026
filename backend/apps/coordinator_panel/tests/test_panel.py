"""Panel koordynatora (UX-PANEL-04): Start, przypięte, okruszki, ekrany etapów, wyszukiwarka, paleta, RODO."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import BroadcastGroup, BroadcastStatus, CommitteeStatus, MessageBroadcast
from apps.accounts.tests.factories import (
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.coordinator_panel import services, shell, start
from apps.coordinator_panel.models import PinnedItem
from apps.web.coordinator_nav import invalidate_counters

pytestmark = pytest.mark.django_db

START = "/coordinator/"
PINS = "/coordinator/pins/"
MESSAGES_PATH = "coordinator/messages/"


@pytest.fixture(autouse=True)
def _fresh_caches(competition):
    """Pakiety Startu i liczniki menu mają pamięć podręczną per konkurs – każdy test liczy od zera."""
    invalidate_counters()
    start.invalidate(competition)
    yield
    invalidate_counters()
    start.invalidate(competition)


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition)


@pytest.fixture
def stage(edition):
    return StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap pierwszy próbny")


@pytest.fixture
def logged(client_for, competition):
    def make(user):
        client = client_for(competition)
        client.force_login(user)
        return client

    return make


def page(client, path=START) -> str:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.content.decode()


def due_keys(html: str) -> list[str]:
    return re.findall(r'class="attention__item attention__item--due" data-attention="([a-z_]+)"', html)


def broadcast(competition, status, **kwargs):
    return MessageBroadcast.objects.create(
        competition=competition,
        group=BroadcastGroup.choices[0][0],
        subject=kwargs.pop("subject", "Temat próbny"),
        body="Treść",
        status=status,
        **kwargs,
    )


# --- Start --------------------------------------------------------------------------------------------


def test_start_keeps_the_dashboard_and_adds_the_new_sections(logged, coordinator, stage):
    client = logged(coordinator)

    response = client.get(START)
    html = response.content.decode()

    assert response.status_code == 200
    # Dotychczasowy kontekst pulpitu zostaje (złote testy czytają ``attention`` i ``stage_rows``).
    assert response.context["attention"]
    assert [row["stage"] for row in response.context["stage_rows"]] == [stage]
    for marker in (
        "Co wymaga uwagi",
        "Najbliższe terminy",
        "Konkurs w liczbach",
        "Przypięte",
        "Szybkie skróty",
    ):
        assert marker in html
    assert 'data-shortcut="messages"' in html
    assert stage.display_name in html


def test_scheduled_and_failed_broadcasts_are_due_and_counted_per_competition(
    logged, coordinator, competition, other_competition, stage
):
    broadcast(competition, BroadcastStatus.SCHEDULED, scheduled_for=timezone.now() + timedelta(days=2))
    broadcast(competition, BroadcastStatus.FAILED)
    # Stara awaria (sprzed 30 dni) nie jest sprawą na dziś; cudzy konkurs nie istnieje.
    old = broadcast(competition, BroadcastStatus.FAILED)
    MessageBroadcast.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=45))
    broadcast(other_competition, BroadcastStatus.FAILED)
    broadcast(other_competition, BroadcastStatus.SCHEDULED, scheduled_for=timezone.now() + timedelta(days=1))

    response = logged(coordinator).get(START)
    rows = {row["key"]: row["value"] for row in response.context["start_due"]}

    assert rows["scheduled"] == 1
    assert rows["failed"] == 1
    assert {"scheduled", "failed"} <= set(due_keys(response.content.decode()))
    # Zaplanowany komunikat stoi też w „najbliższych terminach”, z kotwicą sekcji na ekranie komunikatów.
    planned = [row for row in response.context["start_deadlines"] if row["kind"] == "broadcast"]
    assert [row["url"] for row in planned] == ["/coordinator/messages/#zaplanowane"]


def test_zero_queues_are_quiet_and_still_reachable(logged, coordinator, stage):
    html = page(logged(coordinator))

    assert due_keys(html) == []
    assert "Nic nie czeka na koordynatora" in html
    # Kolejka z zerem nie krzyczy, ale da się do niej dojść z jednej zwiniętej linii.
    assert '<details class="kp-quiet">' in html
    assert "/coordinator/activations/" in html


def test_duplicate_accounts_tile_counts_groups(logged, coordinator, competition, stage):
    for email in ("jan@example.test", "jan.kopia@example.test"):
        ParticipantFactory(
            competition=competition,
            school="XIV LO",
            user=UserFactory(email=email, first_name="Jan", last_name="Powtórzony"),
        )

    response = logged(coordinator).get(START)

    assert {row["key"]: row["value"] for row in response.context["start_due"]}["duplicates"] == 1


def test_student_status_tile_only_with_the_flag(logged, coordinator, competition, stage):
    html = page(logged(coordinator))
    assert 'data-attention="student_status"' not in html

    competition.feature_flags = {**(competition.feature_flags or {}), "student_status_certificate": True}
    competition.save(update_fields=["feature_flags"])
    start.invalidate(competition)

    html = page(logged(coordinator))
    assert 'data-attention="student_status"' in html
    assert "status=oczekujace" in html


def test_summary_counts_only_this_competition(logged, coordinator, competition, other_competition, stage):
    first = ParticipantFactory(competition=competition, school="LO nr 1")
    ParticipantFactory(competition=competition, school="lo NR 1")
    ParticipantFactory(competition=competition, school="Technikum nr 2")
    ParticipantFactory(competition=other_competition, school="Szkoła cudza")
    StageEntryFactory(participant=first, stage=stage)
    CommitteeMemberFactory(competition=competition, status=CommitteeStatus.ACTIVE)
    CommitteeMemberFactory(competition=other_competition, status=CommitteeStatus.ACTIVE)

    summary = logged(coordinator).get(START).context["start_summary"]

    assert summary["participants"] == 3
    assert summary["schools"] == 2  # „LO nr 1” i „lo NR 1” to ta sama szkoła
    assert summary["in_edition"] == 1
    assert summary["committee"] == 1


def test_start_cost_is_constant_when_warm(
    logged, coordinator, competition, stage, django_assert_max_num_queries
):
    """Na ciepło pakiety Startu nie pytają bazy – koszt nie zależy od liczby uczestników i komunikatów."""
    client = logged(coordinator)
    client.get(START)
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as warm:
        client.get(START)
    for index in range(5):
        ParticipantFactory(competition=competition, school=f"Szkoła {index}")
        broadcast(competition, BroadcastStatus.FAILED)

    with django_assert_max_num_queries(len(warm.captured_queries)):
        assert client.get(START).status_code == 200


def test_start_service_checks_the_role(rf, competition):
    request = rf.get(START)
    request.user = UserFactory()
    request.competition = competition

    with pytest.raises(services.PermissionDenied):
        start.start_context(request, {})


# --- przypinanie ------------------------------------------------------------------------------------------


def test_pin_and_unpin_a_menu_item(logged, coordinator, competition, stage):
    client = logged(coordinator)

    response = client.post(PINS, {"path": "/coordinator/messages/", "next": "/coordinator/messages/"})

    assert response.status_code == 302
    assert response["Location"] == "/coordinator/messages/"
    assert list(PinnedItem.objects.filter(user=coordinator).values_list("path", flat=True)) == [MESSAGES_PATH]
    html = page(client)
    assert '<div class="kp-quick" data-kp-pins>' in html
    assert re.search(r'class="kp-pinlist__link" href="/coordinator/messages/"', html)
    # Na ekranie przypiętej pozycji przycisk mówi „Przypięte” i odpina.
    assert 'aria-pressed="true"' in page(client, "/coordinator/messages/")

    client.post(PINS, {"path": MESSAGES_PATH})

    assert not PinnedItem.objects.filter(user=coordinator).exists()
    assert "data-kp-pins" not in page(client)


def test_only_menu_items_can_be_pinned(logged, coordinator, stage):
    client = logged(coordinator)

    for path in ("coordinator/accounts/1/edit/", "admin/", "https://zlosliwy.example/coordinator/", ""):
        client.post(PINS, {"path": path})

    assert not PinnedItem.objects.exists()


def test_pin_limit(logged, coordinator, competition, stage):
    client = logged(coordinator)
    flat = [row["url"] for row in shell._flat(_nav_groups(client))]
    for url in flat[1 : shell.MAX_PINS + 2]:
        client.post(PINS, {"path": url})

    assert PinnedItem.objects.filter(user=coordinator).count() == shell.MAX_PINS


def _nav_groups(client):
    response = client.get(START)
    return response.context["nav_groups"]


def test_pinning_requires_the_coordinator_role(client_for, competition, stage):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    assert client.post(PINS, {"path": MESSAGES_PATH}).status_code == 302  # anonim → logowanie
    client.force_login(participant.user)

    assert client.post(PINS, {"path": MESSAGES_PATH}).status_code == 403
    assert not PinnedItem.objects.exists()


def test_pins_of_another_competition_are_invisible(logged, coordinator, other_competition, stage):
    PinnedItem.objects.create(user=coordinator, competition=other_competition, path=MESSAGES_PATH)

    html = page(logged(coordinator))

    assert "data-kp-pins" not in html


def test_next_must_stay_in_the_panel(logged, coordinator, stage):
    client = logged(coordinator)

    for target in ("https://zlosliwy.example/", "//zlosliwy.example/coordinator/", "/me/"):
        response = client.post(PINS, {"path": MESSAGES_PATH, "next": target})
        assert response["Location"] == START


# --- okruszki, ekrany etapu, eyebrow ------------------------------------------------------------------------


def test_breadcrumbs_follow_the_current_menu_item(logged, coordinator, stage):
    html = page(logged(coordinator), f"/coordinator/stages/{stage.pk}/problems/")

    crumbs = re.search(r'<nav class="kp-crumbs".*?</nav>', html, re.S).group(0)
    assert "Etapy" in crumbs
    assert f'href="/coordinator/stages/{stage.pk}/edit/"' in crumbs
    assert '<span aria-current="page">Zadania</span>' in crumbs
    # Okruszki zaczynają się od Startu, więc domyślny „eyebrow” znika.
    assert '<span class="eyebrow"></span>' in html


def test_start_has_no_breadcrumbs_and_keeps_its_eyebrow(logged, coordinator, stage):
    html = page(logged(coordinator))

    assert "kp-crumbs" not in html
    assert ">Panel koordynatora</a>" in html


def test_only_the_current_stage_shows_its_screens(logged, coordinator, edition):
    first = StageFactory(edition=edition, kind=StageKind.ELIM, name="Pierwszy")
    second = StageFactory(edition=edition, kind=StageKind.DISTRICT, name="Drugi")

    html = page(logged(coordinator), f"/coordinator/stages/{second.pk}/problems/")

    toggles = re.findall(
        r'<details class="kp-sub"( open)?>\s*<summary class="kp-sub__toggle">.*?Ekrany etapu: (\w+)',
        html,
        re.S,
    )
    assert dict((name, bool(opened)) for opened, name in toggles) == {
        first.display_name: False,
        second.display_name: True,
    }


def test_summary_of_the_collapsed_menu_names_the_current_item(logged, coordinator, stage):
    html = page(logged(coordinator), "/coordinator/messages/")

    assert '<span class="kp-summary__where">Komunikaty</span>' in html


# --- wyszukiwarka i paleta --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "label"), [("lejek", "Lejek uczestnika"), ("zaswiadczenia opiek", "Zaświadczenia opiekunów")]
)
def test_search_finds_panel_screens(logged, coordinator, stage, query, label):
    html = page(logged(coordinator), f"/coordinator/search/?q={query}")

    assert 'id="grupa-screens"' in html
    assert label in html


def test_palette_is_a_static_script_with_server_side_strings(logged, coordinator, stage):
    html = page(logged(coordinator))

    assert '<template id="kp-palette-template">' in html
    assert 'data-search-url="/coordinator/search/"' in html
    assert "coordinator_panel/shell.js" in html
    # CSP: każdy skrypt panelu jest plikiem z ``nonce`` – żadnego kodu inline.
    for tag in re.findall(r"<script\b[^>]*>", html):
        if "application/ld+json" in tag or "application/json" in tag:
            continue
        assert "src=" in tag, tag
    # Przycisk palety jest ukryty bez JS (odsłania go skrypt).
    assert re.search(r'<button type="button" class="kp-palette-open" data-kp-palette-open hidden', html)


def test_menu_label_and_sections_are_unchanged_for_other_roles(client_for, competition):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    html = client.get("/me/").content.decode()

    assert "Menu panelu" not in html
    assert "kp-palette" not in html


# --- RODO -------------------------------------------------------------------------------------------------


def test_pins_are_exported_and_erased(coordinator, competition):
    from apps.accounts.data_export import export_payload

    PinnedItem.objects.create(user=coordinator, competition=competition, path=MESSAGES_PATH)

    section = export_payload(coordinator)["skroty_panelu_koordynatora"]
    assert [row["sciezka"] for row in section] == [MESSAGES_PATH]

    assert services.erase_for_user(coordinator) == 1
    assert not PinnedItem.objects.exists()


def test_new_strings_have_an_english_catalogue():
    from django.utils import translation

    with translation.override("en"):
        assert translation.gettext("Konkurs w liczbach") == "The competition in numbers"
        assert translation.gettext("Przypnij") == "Pin"
