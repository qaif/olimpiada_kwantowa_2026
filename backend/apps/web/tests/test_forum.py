"""Ekrany forum uczestnika: bramki adresu, podpis pod wpisem, pisanie i „Twoje wpisy”.

Ten plik pilnuje **ekranu**, a nie reguły: reguły ma własną suitę (``apps/forum/tests/test_forum.py``)
i to tam stoi zdanie o tym, w jakim stanie rodzi się wpis albo kiedy zamyka się okno poprawek.
Tutaj sprawdzamy to, czego serwis nie widzi: kod odpowiedzi pod adresem, treść wyrenderowanego
HTML-a i to, co pasek konta pokazuje, a czego nie.

Trzy rzeczy, o które chodzi tu najbardziej:

- **wyłączone forum znaczy 404 pod każdym z dziewięciu adresów i brak pozycji w pasku konta.** Flaga,
  która chowa link, ale zostawia działający adres, jest funkcją włączoną po cichu,
- **strona wątku nie wypisuje adresu e-mail, szkoły ani kodu ``OLM-…``** – to jest najważniejszy
  test tego pliku i ma własne uzasadnienie przy sobie,
- **treść wpisu wychodzi zescapowana**, bo jest tekstem od uczestnika, a jedyną drogą z tekstu
  do HTML-a jest filtr ``post_body``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.forum.models import (
    EDIT_WINDOW_MINUTES,
    POSTS_PER_PAGE,
    ForumPost,
    ForumReport,
    ModerationStatus,
)
from apps.forum.tests.factories import ForumCategoryFactory, ForumPostFactory, ForumThreadFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

FORUM_URL = "/forum/"
NEW_THREAD_URL = "/forum/new/"
MINE_URL = "/forum/mine/"

#: Zwykła strona serwisu, na której widać pasek konta. Pytamy o **ten** ekran, a nie o samo forum:
#: pozycja „Forum” ma się pojawiać tam, gdzie stoją pozostałe pozycje konta.
ACCOUNT_SCREEN_URL = "/me/"

#: Wszystkie dziewięć adresów forum, każdy w postaci gotowej do wpisania. Identyfikatory są zmyślone
#: i tak ma być: przy wyłączonym przełączniku odmowa pada **przed** sięgnięciem do bazy
#: (``ForumAccessMixin``), więc test nie musi zakładać ani jednego wiersza, żeby ją sprawdzić.
ALL_FORUM_URLS = (
    FORUM_URL,
    NEW_THREAD_URL,
    MINE_URL,
    "/forum/t/1/",
    # „Obserwuj wątek” (powiadomienia z 25.09.2026) – ta sama bramka, co reszta. Wypisu
    # (``/forum/unsubscribe/…``) tu nie ma świadomie: ma działać bez logowania i bez flagi.
    "/forum/t/1/follow/",
    "/forum/p/1/edit/",
    "/forum/p/1/delete/",
    "/forum/p/1/report/",
    "/forum/dowolny-dzial/",
)


def participant_of(competition, **kwargs):
    """Aktywny uczestnik **tego** konkursu – konto razem z rolą, bo forum wymaga obu."""
    profile = ParticipantFactory(competition=competition, **kwargs)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile


def with_forum(competition):
    """Konkurs z **włączonym** forum – tak, jak zrobi to organizator w panelu.

    Różnica wobec bliźniaczego helpera z suity serwisu jest jedna i wynika z warstwy: tam flaga
    wystarcza w pamięci, bo ``has_feature`` czyta pole wiersza, który test już trzyma. Tutaj
    konkurs wyjmuje z bazy ``CompetitionMiddleware`` na podstawie hosta żądania, więc flaga
    niezapisana byłaby flagą, której widok nigdy nie zobaczy.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def closed_edition(competition):
    """Bieżąca edycja z etapem **zamkniętym** – stan spoza okna przyjmowania rozwiązań.

    Etap przesuwamy w przeszłość zamiast go nie tworzyć, bo edycja bez ani jednego etapu jest
    stanem, którego w zawodach nie ma, a test ma opisywać forum po deadline'cie, a nie przed
    założeniem etapów.
    """
    edition = CurrentEditionFactory(competition=competition)
    past = timezone.now() - timedelta(days=30)
    StageFactory(
        competition=competition,
        edition=edition,
        opens_at=past,
        deadline_at=past + timedelta(days=1),
    )
    return edition


def rest_framework_with(**rates) -> dict:
    """Kopia ``REST_FRAMEWORK`` z podmienionymi stawkami – reszta konfiguracji bez zmian."""
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def thread_url(thread) -> str:
    return f"/forum/t/{thread.pk}/"


def edit_url(post) -> str:
    return f"/forum/p/{post.pk}/edit/"


def delete_url(post) -> str:
    return f"/forum/p/{post.pk}/delete/"


def report_url(post) -> str:
    return f"/forum/p/{post.pk}/report/"


def category_url(category) -> str:
    return f"/forum/{category.slug}/"


# --- bramki adresu --------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ALL_FORUM_URLS)
def test_without_the_flag_every_forum_address_is_a_404(web_client, competition, url):
    """Wyłączone forum ma znaczyć „pod tym adresem w tym konkursie nic nie stoi” – wszędzie.

    Dziewięć adresów, bo dziewięć ich jest: jeden zapomniany byłby dziurą wyglądającą jak działająca
    funkcja, a odmowa pada tu **przed** sprawdzeniem roli, więc nie da się jej pomylić z 403.
    """
    profile = participant_of(competition)
    web_client.force_login(profile.user)

    assert web_client.get(url).status_code == 404


@pytest.mark.parametrize("url", ALL_FORUM_URLS)
def test_an_anonymous_visitor_is_sent_to_the_login_page(web_client, competition, url):
    """Forum nie jest publiczne: pod adresem rozmawiają osoby niepełnoletnie (``can_read``).

    Przekierowanie stoi **przed** jedną i drugą bramką, więc pytanie o stan przełącznika nie ma
    jak wyjść na zewnątrz gościowi, który nawet się nie zalogował.
    """
    with_forum(competition)

    response = web_client.get(url)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_an_account_without_a_role_in_this_competition_is_refused(web_client, competition):
    """403, a nie 404: forum tego konkursu **istnieje**, tylko nie dla tego konta.

    Odwrotnie niż przy wyłączonym przełączniku – tam nie ma czego ukrywać, bo brak forum nie jest
    niczyją tajemnicą; tutaj odmowa dotyczy roli i ma się czytać jako „jesteś, ale nie tobie”.
    """
    with_forum(competition)
    web_client.force_login(UserFactory())

    assert web_client.get(FORUM_URL).status_code == 403


def test_the_account_bar_has_no_forum_link_when_the_flag_is_off(web_client, competition):
    """Strona konkursu bez forum ma wyglądać co do bajtu tak, jak przed dołożeniem forum."""
    profile = participant_of(competition)
    web_client.force_login(profile.user)

    body = web_client.get(ACCOUNT_SCREEN_URL).content.decode()

    assert 'href="/forum/"' not in body


def test_the_account_bar_leads_to_the_forum_when_the_flag_is_on(web_client, competition):
    with_forum(competition)
    profile = participant_of(competition)
    web_client.force_login(profile.user)

    body = web_client.get(ACCOUNT_SCREEN_URL).content.decode()

    assert 'href="/forum/"' in body


def test_the_account_bar_costs_no_extra_query_for_a_participant_when_the_flag_is_on(web_client, competition):
    """``roles()`` liczy profil uczestnika **raz** dla ``is_participant`` i ``can_use_forum``.

    ``_forum_visible`` wołał kiedyś ``participant_for`` drugi raz, więc pasek konta uczestnika
    z włączonym forum kosztował jedno zapytanie więcej niż z flagą wyłączoną – ten sam profil
    liczony dwa razy w jednym renderowaniu.
    """
    profile = participant_of(competition)
    web_client.force_login(profile.user)
    web_client.get(ACCOUNT_SCREEN_URL)  # rozgrzewka: sesja i liczniki menu

    with CaptureQueriesContext(connection) as without_flag:
        web_client.get(ACCOUNT_SCREEN_URL)

    with_forum(competition)

    with CaptureQueriesContext(connection) as with_flag:
        web_client.get(ACCOUNT_SCREEN_URL)

    assert len(with_flag) == len(without_flag)


# --- izolacja konkursów ----------------------------------------------------------------------------


def test_a_thread_of_another_competition_is_not_found_rather_than_forbidden(
    web_client, competition, other_competition
):
    """404, a nie 403: identyfikatory są kolejne, więc 403 byłby licznikiem rozmów w serwisie."""
    with_forum(competition)
    reader = participant_of(competition)
    stranger_thread = ForumThreadFactory(competition=other_competition)
    web_client.force_login(reader.user)

    assert web_client.get(thread_url(stranger_thread)).status_code == 404


def test_a_participant_of_another_competition_does_not_see_our_threads(
    client_for, competition, other_competition
):
    """Rola jest zawsze rolą **w konkursie** – forum sąsiada nie jest niczyim forum."""
    with_forum(competition)
    with_forum(other_competition)
    stranger = participant_of(other_competition)
    ForumThreadFactory(competition=competition, title="WATEK-TUTEJSZY")
    ForumThreadFactory(competition=other_competition, title="WATEK-SASIADA")
    client = client_for(other_competition)
    client.force_login(stranger.user)

    response = client.get(FORUM_URL)

    body = response.content.decode()
    # Własny wątek w odpowiedzi **jest** – inaczej test przechodziłby także wtedy, gdyby forum
    # sąsiada w ogóle się nie otworzyło.
    assert response.status_code == 200
    assert "WATEK-SASIADA" in body
    assert "WATEK-TUTEJSZY" not in body


# --- podpis pod wpisem -----------------------------------------------------------------------------


def test_the_thread_page_never_leaks_the_e_mail_the_school_or_the_public_code(web_client, competition):
    """**Najważniejszy test tego pliku.**

    Kod publiczny (``OLM-…``) jest kluczem anonimizacji w ocenianiu: recenzent widzi pracę
    podpisaną kodem i nie ma prawa wiedzieć, czyja ona jest. Gdyby strona wątku wypisała obok
    imienia kod, adres e-mail albo szkołę, wystarczyłby jeden wpis, żeby powiązanie
    kod → osoba stało się publiczne – i anonimowość oceniania przestałaby istnieć dla wszystkich
    naraz, a nie dla jednej pracy.

    Dlatego szablon dostaje **gotowy podpis** z kontekstu (``render_posts``), a nie obiekt autora:
    dopóki w kontekście nie ma użytkownika, żadna przyszła poprawka tego pliku nie dopisze
    przypadkiem odwołania do jego profilu. Ten test pilnuje skutku tamtej decyzji.
    """
    with_forum(competition)
    author = participant_of(
        competition,
        user__first_name="Ania",
        user__last_name="Testowa",
        user__email="ania@example.invalid",
        school="LO im. Testowego",
    )
    reader = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(competition=competition, thread=thread, author=author.user)
    web_client.force_login(reader.user)

    body = web_client.get(thread_url(thread)).content.decode()

    assert "Ania T." in body
    assert author.user.email not in body
    assert author.school not in body
    assert author.public_code not in body
    # Sam prefiks też nie: kod wypisany w cudzym kształcie pozostaje kodem.
    assert "OLM-" not in body


# --- treść wpisu ------------------------------------------------------------------------------------


def test_a_post_with_a_script_tag_comes_out_escaped(web_client, competition):
    """Formatowanie tekstu nie jest warte jednej luki XSS na koncie niepełnoletniego uczestnika.

    Nigdzie na forum nie ma ``|safe`` i nie ma po co go dodawać – jedyną drogą z tekstu wpisu do
    HTML-a jest filtr ``post_body``, który escapuje **przed** zrobieniem odnośników.
    """
    with_forum(competition)
    reader = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(competition=competition, thread=thread, body="<script>alert(1)</script>")
    web_client.force_login(reader.user)

    body = web_client.get(thread_url(thread)).content.decode()

    assert "&lt;script&gt;" in body
    assert "<script>alert" not in body


def test_a_link_in_a_post_carries_the_full_safe_rel(web_client, competition):
    """``nofollow`` daje Django, ``noopener`` i ``noreferrer`` dokładamy my – wprost, nie w nadziei.

    Odnośnik z forum nie ma ``target`` ustawionego przez autora, więc żadna domyślna reguła
    przeglądarki tu nie zadziała; test pilnuje, że podmiana atrybutu nadal trafia w to, co
    generuje ``urlize``.
    """
    with_forum(competition)
    reader = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(
        competition=competition,
        thread=thread,
        body="Podpowiedź stoi na https://example.invalid/zadanie",
    )
    web_client.force_login(reader.user)

    body = web_client.get(thread_url(thread)).content.decode()

    assert 'rel="nofollow noopener noreferrer"' in body


# --- widoczność i stronicowanie ----------------------------------------------------------------------


def test_the_author_sees_their_own_pending_post_and_nobody_else_does(web_client, competition):
    """Bez tego uczestnik wracałby na stronę, na której nie ma śladu po tym, co przed chwilą napisał."""
    with_forum(competition)
    author = participant_of(competition)
    reader = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(
        competition=competition,
        thread=thread,
        author=author.user,
        body="WPIS-CZEKAJACY",
        status=ModerationStatus.PENDING,
    )

    web_client.force_login(author.user)
    mine = web_client.get(thread_url(thread)).content.decode()
    web_client.force_login(reader.user)
    theirs = web_client.get(thread_url(thread)).content.decode()

    assert "WPIS-CZEKAJACY" in mine
    assert "WPIS-CZEKAJACY" not in theirs


def test_the_thread_is_paginated_by_the_strona_parameter(web_client, competition):
    """Parametr nazywa się po polsku, bo adres wątku bywa przesyłany między uczestnikami."""
    with_forum(competition)
    reader = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    for _ in range(25):
        ForumPostFactory(competition=competition, thread=thread)
    web_client.force_login(reader.user)

    first = web_client.get(thread_url(thread)).content.decode()
    second = web_client.get(f"{thread_url(thread)}?strona=2").content.decode()

    assert first.count('id="wpis-') == POSTS_PER_PAGE
    assert second.count('id="wpis-') == 25 - POSTS_PER_PAGE


def test_a_category_screen_lists_the_threads_of_that_category_only(web_client, competition):
    with_forum(competition)
    reader = participant_of(competition)
    category = ForumCategoryFactory(competition=competition)
    ForumThreadFactory(competition=competition, category=category, title="WATEK-W-DZIALE")
    ForumThreadFactory(competition=competition, title="WATEK-OBOK")
    web_client.force_login(reader.user)

    body = web_client.get(category_url(category)).content.decode()

    assert "WATEK-W-DZIALE" in body
    assert "WATEK-OBOK" not in body


# --- pisanie ------------------------------------------------------------------------------------------


def test_a_reply_is_saved_and_the_message_says_it_awaits_approval(web_client, competition):
    """Uczestnik ma wiedzieć, dlaczego jego wpisu nie widać – forum nie wysyła o tym listu."""
    with_forum(competition)
    closed_edition(competition)
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    web_client.force_login(profile.user)

    response = web_client.post(thread_url(thread), {"body": "Czy wyniki będą w lutym?"}, follow=True)

    post = ForumPost.objects.for_competition(competition).get(author=profile.user)
    assert response.redirect_chain[-1] == (f"{thread_url(thread)}?strona=ostatnia", 302)
    assert post.status == ModerationStatus.PENDING
    assert "Wpis czeka na zatwierdzenie" in response.content.decode()


def test_the_author_may_fix_a_fresh_post(web_client, competition):
    with_forum(competition)
    closed_edition(competition)
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, body="Pierwotna treść")
    web_client.force_login(profile.user)

    response = web_client.post(edit_url(post), {"body": "Poprawiona treść"})

    post.refresh_from_db()
    assert response.status_code == 302
    assert post.body == "Poprawiona treść"


def test_a_post_older_than_the_window_comes_back_with_an_error(web_client, competition):
    """Wpis sprzed godziny, który brzmi inaczej niż odpowiedź pod nim, jest gorszy niż literówka.

    Widok nie przekierowuje i nie wyrzuca 403: odmowa serwisu wraca jako błąd formularza, czyli
    kod 400 i ten sam ekran – a treść w bazie zostaje taka, jaka była. Błąd ma się dać **przeczytać**:
    ``form.non_field_errors`` stał wcześniej wyłącznie w gałęzi ``{% if can_edit %}``, a przy
    zamkniętym oknie ta gałąź się nie renderuje – autor dostawał 400 i pustą stronę bez ani
    jednego słowa o tym, co się stało.
    """
    with_forum(competition)
    profile = participant_of(competition)
    old = timezone.now() - timedelta(minutes=EDIT_WINDOW_MINUTES + 1)
    post = ForumPostFactory(
        competition=competition, author=profile.user, created_at=old, body="Pierwotna treść"
    )
    web_client.force_login(profile.user)

    response = web_client.post(edit_url(post), {"body": "Podmiana"})

    post.refresh_from_db()
    assert response.status_code == 400
    assert post.body == "Pierwotna treść"
    body = response.content.decode()
    assert f"Wpis można poprawić tylko przez {EDIT_WINDOW_MINUTES} minut od dodania." in body


@override_settings(REST_FRAMEWORK=rest_framework_with(forum="3/hour"))
def test_editing_is_throttled_like_every_other_form(web_client, competition):
    """Ten sam scope, co odpowiedź, nowy wątek i zgłoszenie – limit ma chronić kolejkę
    moderacyjną (poprawka opublikowanego wpisu w trybie ``PRE`` wraca do niej), a nie tylko jedną
    z pięciu dróg, którymi się do niej trafia.
    """
    with_forum(competition)
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, body="Pierwotna treść")
    web_client.force_login(profile.user)
    for attempt in range(3):
        response = web_client.post(edit_url(post), {"body": f"Poprawka numer {attempt}"})
        assert response.status_code == 302, attempt

    blocked = web_client.post(edit_url(post), {"body": "Czwarta poprawka"})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_deleting_an_own_post_hides_it_instead_of_dropping_the_row(web_client, competition):
    """Miękko z dwóch powodów: odpowiedzi pod wpisem i zgłoszenie, które nie może zniknąć
    na żądanie autora."""
    with_forum(competition)
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user)
    web_client.force_login(profile.user)

    response = web_client.post(delete_url(post))

    post.refresh_from_db()
    assert response.status_code == 302
    assert post.status == ModerationStatus.HIDDEN
    assert ForumPost.objects.filter(pk=post.pk).exists()


def test_reporting_a_post_leaves_a_row_for_the_coordinator(web_client, competition):
    with_forum(competition)
    reporter = participant_of(competition)
    post = ForumPostFactory(competition=competition)
    web_client.force_login(reporter.user)

    response = web_client.post(report_url(post), {"reason": "Wpis zdradza rozwiązanie zadania 2."})

    report = ForumReport.objects.for_competition(competition).get()
    assert response.status_code == 302
    assert report.post_id == post.pk
    assert report.reporter_id == reporter.user.pk


def test_a_post_that_is_not_published_cannot_be_reported(web_client, competition):
    """Zgłasza się to, co widać. Wpis czekający na moderację nie istnieje dla czytelnika, więc
    jego adres zgłoszenia też nie."""
    with_forum(competition)
    reporter = participant_of(competition)
    post = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)
    web_client.force_login(reporter.user)

    assert web_client.get(report_url(post)).status_code == 404


@override_settings(REST_FRAMEWORK=rest_framework_with(forum="3/hour"))
def test_replies_are_throttled_like_every_other_form(web_client, competition):
    """Limit nie chroni tu cudzej skrzynki – listy forum są zbiorcze – tylko kolejkę moderacyjną.

    Stawkę podmieniamy przez ``REST_FRAMEWORK``, czyli przez tę samą konfigurację, z której
    korzysta DRF. To jest część asercji: gdyby formularze forum miały własne ustawienie, ten
    override by na nie nie zadziałał.
    """
    with_forum(competition)
    closed_edition(competition)
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    web_client.force_login(profile.user)
    for attempt in range(3):
        response = web_client.post(thread_url(thread), {"body": f"Odpowiedź numer {attempt}"})
        assert response.status_code == 302, attempt

    blocked = web_client.post(thread_url(thread), {"body": "Czwarta odpowiedź"})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


# --- „Twoje wpisy” -------------------------------------------------------------------------------------


def test_your_posts_carry_the_moderator_note_of_a_rejected_post(web_client, competition):
    """To jest **jedyne** miejsce, w którym autor dowiaduje się, że jego wpis odrzucono i dlaczego."""
    with_forum(competition)
    profile = participant_of(competition)
    ForumPostFactory(
        competition=competition,
        author=profile.user,
        status=ModerationStatus.REJECTED,
        moderation_note="Wpis zdradza rozwiązanie zadania 2.",
    )
    web_client.force_login(profile.user)

    body = web_client.get(MINE_URL).content.decode()

    assert "Wpis zdradza rozwiązanie zadania 2." in body
    assert "odrzucone" in body


def test_your_posts_carry_a_rejected_thread_with_its_own_note(web_client, competition):
    """Odrzucenie wątku i odrzucenie wpisu to dwie decyzje i dwa uzasadnienia – stąd dwie listy.

    Temat stoi tu **bez odnośnika** i to nie jest niedopatrzenie: odrzucony wątek daje 404 także
    swojemu autorowi, a odnośnik, o którym z góry wiadomo, że nie zadziała, jest gorszy niż brak.
    """
    with_forum(competition)
    profile = participant_of(competition)
    thread = ForumThreadFactory(
        competition=competition,
        author=profile.user,
        title="TEMAT-ODRZUCONY",
        status=ModerationStatus.REJECTED,
        moderation_note="Temat dotyczy zadań otwartego etapu.",
    )
    web_client.force_login(profile.user)

    body = web_client.get(MINE_URL).content.decode()

    assert "TEMAT-ODRZUCONY" in body
    assert "Temat dotyczy zadań otwartego etapu." in body
    assert f'href="{thread_url(thread)}"' not in body
