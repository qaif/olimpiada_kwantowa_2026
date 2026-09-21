"""Moderacja forum w panelu koordynatora: bramki, kolejka, decyzje i konfiguracja.

Ten plik pilnuje **ekranu** moderatora; reguły mają własną suitę (``apps/forum/tests/test_forum.py``).
Tutaj sprawdzamy to, czego serwis nie widzi: kod odpowiedzi pod adresem, nazwy czynności
przychodzące z formularza (``action``) i to, co widać w wyrenderowanym HTML-u.

Dwie rzeczy, o które chodzi tu najbardziej:

- **kolejność bramek jest odwrotna niż na ekranach uczestnika** i jest to decyzja, a nie przypadek.
  Tam flaga stoi przed rolą, bo rolą jest „prawie każdy zalogowany uczestnik”. Tutaj rolą jest
  koordynator, więc rola idzie pierwsza: uczestnik dostaje 403 **niezależnie** od stanu
  przełącznika i nie dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany
  (docstring modułu ``apps.web.views.coordinator_forum``),
- **izolacja konkursów**: kolejka jednego konkursu nie wie o pozycjach drugiego, a podrzucony
  identyfikator z sąsiedniej olimpiady niczego nie znajduje.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.models import AuditLog
from apps.forum.models import (
    ForumCategory,
    ForumSettings,
    ModerationMode,
    ModerationStatus,
)
from apps.forum.services import effective_mode, save_settings
from apps.forum.tests.factories import ForumPostFactory, ForumReportFactory, ForumThreadFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

QUEUE_URL = "/coordinator/forum/"
THREADS_URL = "/coordinator/forum/threads/"
CATEGORIES_URL = "/coordinator/forum/categories/"
SETTINGS_URL = "/coordinator/forum/settings/"

#: Pulpit panelu – po nim pytamy o pozycję „Forum uczestników” w menu bocznym.
PANEL_URL = "/coordinator/"

#: Wszystkie pięć ekranów moderacji. Identyfikator wątku jest zmyślony, bo przy wyłączonym
#: przełączniku odmowa pada przed sięgnięciem po wiersz (``CoordinatorForumMixin``).
ALL_PANEL_URLS = (QUEUE_URL, THREADS_URL, CATEGORIES_URL, SETTINGS_URL, "/coordinator/forum/t/1/")

#: Napis pozycji w menu panelu. Stała, bo pytamy o niego dwa razy: z flagą i bez.
MENU_LABEL = "Forum uczestników"


def participant_of(competition, **kwargs):
    """Aktywny uczestnik **tego** konkursu – konto razem z rolą, bo forum wymaga obu."""
    profile = ParticipantFactory(competition=competition, **kwargs)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile


def coordinator_of(competition):
    """Koordynator **tego** konkursu. Fabryka nadaje grupę, ``grant_membership`` – rolę w konkursie.

    Oba naraz, bo ``has_role`` czyta jedno albo drugie zależnie od tego, czy model członkostw już
    istnieje – i tak samo robią pozostałe suity panelu.
    """
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def with_forum(competition):
    """Konkurs z **włączonym** forum – tak, jak zrobi to organizator w panelu.

    Zapis do bazy, a nie zmiana w pamięci: konkurs wyjmuje z bazy ``CompetitionMiddleware`` na
    podstawie hosta żądania, więc flaga niezapisana byłaby flagą, której widok nigdy nie zobaczy.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def closed_edition(competition):
    """Bieżąca edycja z etapem **zamkniętym** – stan spoza okna przyjmowania rozwiązań."""
    edition = CurrentEditionFactory(competition=competition)
    past = timezone.now() - timedelta(days=30)
    StageFactory(
        competition=competition,
        edition=edition,
        opens_at=past,
        deadline_at=past + timedelta(days=1),
    )
    return edition


def open_edition(competition):
    """Bieżąca edycja z etapem **przyjmującym rozwiązania teraz** (fabryka tak właśnie ustawia)."""
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(competition=competition, edition=edition)
    return edition


def thread_url(thread) -> str:
    return f"/coordinator/forum/t/{thread.pk}/"


def category_payload(name: str) -> dict:
    """Komplet pól formularza działu. Identyfikatora w adresie tu nie ma – nadaje go serwis."""
    return {"name": name, "description": "", "ordering": 100, "is_open": "on"}


# --- bramki ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ALL_PANEL_URLS)
def test_without_the_flag_every_screen_is_a_404_for_the_coordinator(web_client, competition, url):
    """Konkurs bez forum nie ma ekranu moderacji forum – pod żadnym z pięciu adresów."""
    web_client.force_login(coordinator_of(competition))

    assert web_client.get(url).status_code == 404


@pytest.mark.parametrize("flag_on", [False, True])
@pytest.mark.parametrize("url", ALL_PANEL_URLS)
def test_a_participant_is_refused_whatever_the_flag_says(web_client, competition, url, flag_on):
    """403 w obu stanach przełącznika i to jest cała treść tego testu.

    Gdyby bramka flagi stała przed rolą (jak na ekranach uczestnika, i tam słusznie), uczestnik
    dostawałby 404 przy wyłączonym forum i 403 przy włączonym – czyli odpowiedź serwisu mówiłaby
    mu, jak ten konkurs jest skonfigurowany. Rola idzie pierwsza właśnie po to.
    """
    if flag_on:
        with_forum(competition)
    profile = participant_of(competition)
    web_client.force_login(profile.user)

    assert web_client.get(url).status_code == 403


def test_the_panel_menu_has_no_forum_entry_without_the_flag(web_client, competition):
    """Pozycja prowadząca w 404 jest gorsza niż jej brak – menu bramkuje tą samą flagą, co widok."""
    web_client.force_login(coordinator_of(competition))

    body = web_client.get(PANEL_URL).content.decode()

    assert MENU_LABEL not in body


def test_the_panel_menu_leads_to_the_queue_with_the_flag(web_client, competition):
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    body = web_client.get(PANEL_URL).content.decode()

    assert MENU_LABEL in body
    assert QUEUE_URL in body


# --- kolejka --------------------------------------------------------------------------------------


def test_the_queue_shows_threads_posts_and_reports_on_one_screen(web_client, competition):
    """Trzy listy razem, bo to jedna czynność: „przejrzyj, co przyszło”."""
    with_forum(competition)
    ForumThreadFactory(competition=competition, title="WATEK-CZEKA", status=ModerationStatus.PENDING)
    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(
        competition=competition, thread=thread, body="WPIS-CZEKA", status=ModerationStatus.PENDING
    )
    ForumReportFactory(competition=competition, reason="POWOD-ZGLOSZENIA")
    web_client.force_login(coordinator_of(competition))

    body = web_client.get(QUEUE_URL).content.decode()

    assert "WATEK-CZEKA" in body
    assert "WPIS-CZEKA" in body
    assert "POWOD-ZGLOSZENIA" in body


def test_an_empty_queue_says_so_instead_of_showing_nothing(web_client, competition):
    """Kolejka moderacyjna ma być pusta, a pusty ekran bez zdania wygląda jak ekran zepsuty."""
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    body = web_client.get(QUEUE_URL).content.decode()

    assert "Kolejka jest pusta" in body


# --- decyzje moderatora ------------------------------------------------------------------------------


def test_approving_a_post_publishes_it_and_leaves_an_audit_entry_without_the_body(web_client, competition):
    """Audyt zostaje w bazie na stałe i nie podlega ani retencji edycji, ani anonimizacji konta –
    skopiowana tam treść byłaby danymi, których nie zdejmie już żadne żądanie z art. 17."""
    with_forum(competition)
    actor = coordinator_of(competition)
    post = ForumPostFactory(
        competition=competition, body="TAJNA-TRESC-WPISU", status=ModerationStatus.PENDING
    )
    web_client.force_login(actor)

    web_client.post(QUEUE_URL, {"action": "approve-post", "post": post.pk})

    post.refresh_from_db()
    entry = AuditLog.objects.filter(action="forum.post_approved").get()
    assert post.status == ModerationStatus.PUBLISHED
    assert entry.actor_id == actor.pk
    assert entry.target_id == str(post.pk)
    assert "TAJNA-TRESC-WPISU" not in str(entry.diff)


def test_rejecting_without_a_note_is_refused_and_leaves_the_post_alone(web_client, competition):
    """Ekran „Twoje wpisy” istnieje po to, żeby pod odmową stał dalszy ciąg – więc odmowa bez
    uzasadnienia nie jest decyzją, którą wolno zapisać."""
    with_forum(competition)
    post = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)
    web_client.force_login(coordinator_of(competition))

    response = web_client.post(QUEUE_URL, {"action": "reject-post", "post": post.pk}, follow=True)

    post.refresh_from_db()
    assert "Odrzucenie wymaga uzasadnienia" in response.content.decode()
    assert post.status == ModerationStatus.PENDING
    assert post.moderated_at is None


def test_rejecting_with_a_note_stores_it_for_the_author(web_client, competition):
    with_forum(competition)
    post = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)
    web_client.force_login(coordinator_of(competition))

    web_client.post(
        QUEUE_URL,
        {"action": "reject-post", "post": post.pk, "note": "Wpis zdradza rozwiązanie zadania 2."},
    )

    post.refresh_from_db()
    assert post.status == ModerationStatus.REJECTED
    assert post.moderation_note == "Wpis zdradza rozwiązanie zadania 2."


def test_bulk_approval_leaves_one_audit_entry_per_item(web_client, competition):
    """Kolejka, która po jednym kliknięciu zostawia jeden ślad na dwa wpisy, przestaje odpowiadać
    na pytanie „kto to przepuścił”."""
    with_forum(competition)
    first = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)
    second = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)
    web_client.force_login(coordinator_of(competition))

    web_client.post(QUEUE_URL, {"action": "bulk-approve", "post": [first.pk, second.pk]})

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == ModerationStatus.PUBLISHED
    assert second.status == ModerationStatus.PUBLISHED
    assert AuditLog.objects.filter(action="forum.post_approved").count() == 2


def test_resolving_a_report_closes_the_case_without_touching_the_post(web_client, competition):
    """Moderator bywa innego zdania niż zgłaszający i to też jest rozpatrzeniem sprawy."""
    with_forum(competition)
    report = ForumReportFactory(competition=competition)
    web_client.force_login(coordinator_of(competition))

    web_client.post(QUEUE_URL, {"action": "resolve-report", "report": report.pk})

    report.refresh_from_db()
    report.post.refresh_from_db()
    assert report.resolved_at is not None
    assert report.post.status == ModerationStatus.PUBLISHED


# --- ekran wątku --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "field", "expected", "audit_action"),
    [
        ("pin", "is_pinned", True, "forum.thread_pinned"),
        ("unpin", "is_pinned", False, "forum.thread_pinned"),
        ("lock", "is_locked", True, "forum.thread_locked"),
        ("unlock", "is_locked", False, "forum.thread_locked"),
    ],
)
def test_every_thread_flag_leaves_its_own_audit_entry(
    web_client, competition, action, field, expected, audit_action
):
    """Przypięcie jest decyzją o kolejności, zamknięcie – o prawie do pisania, i żadna z nich nie
    jest decyzją o widoczności. Wątek zaczyna przypięty i zamknięty, żeby każda z czterech
    czynności naprawdę coś zmieniała."""
    with_forum(competition)
    actor = coordinator_of(competition)
    thread = ForumThreadFactory(competition=competition, is_pinned=True, is_locked=True)
    web_client.force_login(actor)

    web_client.post(thread_url(thread), {"action": action})

    thread.refresh_from_db()
    assert getattr(thread, field) is expected
    assert AuditLog.objects.filter(action=audit_action).count() == 1


def test_hiding_a_published_post_from_the_thread_screen(web_client, competition):
    """Moderacja polega na oglądaniu tego, czego nie widać – dlatego czynności na wpisie stoją
    na ekranie moderatora, a nie na stronie uczestnika."""
    with_forum(competition)
    thread = ForumThreadFactory(competition=competition)
    post = ForumPostFactory(competition=competition, thread=thread)
    web_client.force_login(coordinator_of(competition))

    web_client.post(thread_url(thread), {"action": "post-hide", "post": post.pk})

    post.refresh_from_db()
    assert post.status == ModerationStatus.HIDDEN


# --- działy i ustawienia --------------------------------------------------------------------------------


def test_a_new_category_gets_a_slug_made_of_its_name(web_client, competition):
    """Slug stoi w adresie, a adres działu bywa wklejony w cudzej wiadomości."""
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    web_client.post(CATEGORIES_URL, category_payload("Zadania i teoria"))

    category = ForumCategory.objects.for_competition(competition).get()
    assert category.name == "Zadania i teoria"
    assert category.slug == "zadania-i-teoria"


def test_a_second_category_of_the_same_name_gets_its_own_slug(web_client, competition):
    """Dwa działy o tej samej nazwie są pomyłką organizatora, ale nie mogą być błędem bazy:
    więz stoi na parze ``(konkurs, slug)``, więc drugi dostaje przyrostek."""
    with_forum(competition)
    web_client.force_login(coordinator_of(competition))

    web_client.post(CATEGORIES_URL, category_payload("Zadania i teoria"))
    web_client.post(CATEGORIES_URL, category_payload("Zadania i teoria"))

    slugs = list(
        ForumCategory.objects.for_competition(competition).order_by("id").values_list("slug", flat=True)
    )
    assert slugs == ["zadania-i-teoria", "zadania-i-teoria-2"]


def test_the_settings_screen_saves_the_mode_and_the_read_only_switch(web_client, competition):
    with_forum(competition)
    closed_edition(competition)
    web_client.force_login(coordinator_of(competition))

    web_client.post(SETTINGS_URL, {"mode": ModerationMode.POST, "is_read_only": "on"})

    row = ForumSettings.objects.for_competition(competition).get()
    assert row.mode == ModerationMode.POST
    assert row.is_read_only is True


def test_an_open_stage_overrides_the_setting_and_the_screen_says_so(web_client, competition):
    """Pole wyboru pokazujące „po publikacji” w chwili, gdy każdy wpis i tak czeka w kolejce,
    byłoby ustawieniem, które kłamie – a organizator, który mu uwierzy, przestanie zaglądać
    do kolejki. Wymuszenie jest regulaminowe (§ 10 ust. 2 i § 17), nie ostrożnościowe.
    """
    with_forum(competition)
    open_edition(competition)
    actor = coordinator_of(competition)
    save_settings(competition=competition, actor=actor, mode=ModerationMode.POST, is_read_only=False)
    web_client.force_login(actor)

    body = web_client.get(SETTINGS_URL).content.decode()

    assert "niezależnie od" in body
    assert effective_mode(competition) == ModerationMode.PRE


# --- izolacja konkursów -----------------------------------------------------------------------------------


def test_a_thread_of_another_competition_is_a_404(web_client, competition, other_competition):
    """Koordynator jest koordynatorem **konkursu**, a nie instalacji."""
    with_forum(competition)
    stranger = ForumThreadFactory(competition=other_competition)
    web_client.force_login(coordinator_of(competition))

    assert web_client.get(thread_url(stranger)).status_code == 404


def test_the_queue_does_not_list_pending_items_of_another_competition(
    web_client, competition, other_competition
):
    with_forum(competition)
    ForumPostFactory(competition=other_competition, body="WPIS-OBCY", status=ModerationStatus.PENDING)
    web_client.force_login(coordinator_of(competition))

    body = web_client.get(QUEUE_URL).content.decode()

    assert "WPIS-OBCY" not in body
    assert "Kolejka jest pusta" in body


def test_an_action_cannot_reach_a_post_of_another_competition(web_client, competition, other_competition):
    """Podrzucony identyfikator z sąsiedniej olimpiady niczego nie znajduje: zawężenie jedzie
    z managerem, a nie z warunkiem w środku metody."""
    with_forum(competition)
    stranger = ForumPostFactory(competition=other_competition, status=ModerationStatus.PENDING)
    web_client.force_login(coordinator_of(competition))

    response = web_client.post(QUEUE_URL, {"action": "approve-post", "post": stranger.pk})

    stranger.refresh_from_db()
    assert response.status_code == 404
    assert stranger.status == ModerationStatus.PENDING
