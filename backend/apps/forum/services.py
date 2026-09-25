"""Reguły forum: kto może pisać, co widać i co robi moderacja.

Widoki tego modułu nie dublują (§ 2.1). Cała wiedza o tym, czy wpis jest widoczny, czy autor może
go jeszcze poprawić i co zapisuje się w audycie, mieszka tutaj – ekran uczestnika, ekran
koordynatora i (gdyby kiedyś powstało) API odpowiadają dzięki temu tak samo.

**Dlaczego tryb moderacji nie jest samą kolumną.** ``ForumSettings.mode`` mówi, czego chce
organizator; :func:`effective_mode` mówi, co obowiązuje **teraz**. Różnica jest jedna i jest
regułą zawodów: dopóki którykolwiek etap edycji przyjmuje rozwiązania, forum chodzi w trybie
``PRE`` niezależnie od ustawienia. Powód nie jest ostrożnościowy, tylko regulaminowy – § 10 ust. 2
i § 17 regulaminu zabraniają omawiania rozwiązań zadań otwartego etapu, a wpis widoczny przez
kwadrans, zanim moderator go zdejmie, zdąży zostać przeczytany przez tych, którzy jeszcze nie
oddali pracy. Odwrócenie tej reguły („ufamy uczestnikom”) kosztowałoby unieważnienie etapu, a nie
jeden nieprzyjemny wątek.

**Czego tu nie ma:** wysyłki listów. Do 25.09.2026 forum nie pisało do nikogo, bo trzeci kanał
poczty, wyzwalany każdym akapitem nastolatka, zamieniłby skrzynkę koordynatora w kanał RSS.
Powiadomienia doszły na prośbę organizatora, ale ta obawa została warunkiem ich kształtu: ten
moduł **nie wysyła** niczego – w chwili publikacji albo decyzji zostawia wyłącznie ślad
(``apps.forum.notifications.post_published``, ``record_decision``), a zbiorczy list składa
później zadanie okresowe (``apps.forum.tasks``). Odrzucony wpis razem z notatką moderatora nadal
czeka na autora także na ekranie „Twoje wpisy” – list jest dodatkiem, nie jedyną drogą.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from . import notifications
from .models import (
    EDIT_WINDOW_MINUTES,
    MAX_POST_LENGTH,
    MAX_REASON_LENGTH,
    MAX_TITLE_LENGTH,
    PENDING_STATUSES,
    DecisionKind,
    ForumCategory,
    ForumPost,
    ForumReport,
    ForumSettings,
    ForumThread,
    ModerationMode,
    ModerationStatus,
)

logger = logging.getLogger(__name__)

#: Stany, w których wpis **istnieje dla czytelnika**: opublikowany albo własny i czekający.
#: Jedna krotka, bo lista wątków, strona wątku i licznik odpowiedzi muszą pokazywać to samo.
READABLE_STATUSES = (ModerationStatus.PUBLISHED,)

#: Akcje audytu. Napisy w jednym miejscu, bo po nich filtruje się ekran audytu, a literówka
#: w jednym z dziesięciu wywołań byłaby zdarzeniem, którego nikt nie znajdzie.
AUDIT_THREAD_APPROVED = "forum.thread_approved"
AUDIT_THREAD_REJECTED = "forum.thread_rejected"
AUDIT_THREAD_HIDDEN = "forum.thread_hidden"
AUDIT_THREAD_LOCKED = "forum.thread_locked"
AUDIT_THREAD_PINNED = "forum.thread_pinned"
AUDIT_POST_APPROVED = "forum.post_approved"
AUDIT_POST_REJECTED = "forum.post_rejected"
AUDIT_POST_HIDDEN = "forum.post_hidden"
AUDIT_POST_RESTORED = "forum.post_restored"
AUDIT_THREAD_RESTORED = "forum.thread_restored"
AUDIT_REPORT_RESOLVED = "forum.report_resolved"
AUDIT_CATEGORY_SAVED = "forum.category_saved"
AUDIT_SETTINGS_CHANGED = "forum.settings_changed"


# --- ustawienia i tryb ----------------------------------------------------------------------------


def settings_for(competition) -> ForumSettings:
    """Ustawienia forum tego konkursu. Brak wiersza znaczy „domyślne”, a nie „brak danych”.

    Zwracany obiekt bywa **niezapisany** i tak ma być: odczyt strony forum nie może zakładać
    wiersza w bazie, bo wtedy pierwsze wejście gościa na ``/forum/`` byłoby zapisem wykonanym
    w środku ``GET``-a. Wiersz powstaje dopiero wtedy, gdy koordynator zapisze ustawienia
    (:func:`save_settings`).
    """
    row = ForumSettings.objects.for_competition(competition).first()
    return row if row is not None else ForumSettings(competition=competition)


def stage_forcing_pre_moderation(competition, now=None):
    """Etap bieżącej edycji, który **właśnie** przyjmuje rozwiązania, albo ``None``.

    Zwracamy etap, a nie ``True``: formularz pisze w ostrzeżeniu, o który etap chodzi, a zdanie
    „trwa etap” bez nazwy nie mówi uczestnikowi, czego nie wolno omawiać.

    Zakresem jest **bieżąca edycja tego konkursu** – ta sama, o którą pyta pulpit i menu panelu
    (``apps.competitions.services.current_edition``). Edycja zeszłoroczna z otwartym etapem nie
    istnieje, a gdyby istniała, nie byłaby powodem, żeby zamykać dzisiejszą rozmowę.

    Etap treningowy jest **pomijany** – ta sama reguła i to samo uzasadnienie, co w
    ``apps.competitions.services.current_stage``: trening jest otwarty bez końca (sentynel
    ``TRAINING_DEADLINE`` w roku 2099), więc gdyby wchodził do tej pętli, forum zamykałoby się
    w tryb ``PRE`` na stałe, a ostrzeżenie nad formularzem nazywałoby piaskownicę zamiast realnych
    zawodów. Trening nie jest etapem, którego § 10 ust. 2 i § 17 dotyczą – nikt nie oddaje w nim
    rozwiązań na ocenę, więc nie ma czego chronić przed przedwczesnym ujawnieniem.
    """
    from apps.competitions.models import Stage
    from apps.competitions.services import current_edition

    if competition is None:
        return None
    edition = current_edition(competition)
    if edition is None:
        return None
    now = now or timezone.now()
    stages = Stage.objects.filter(edition=edition).order_by("opens_at", "id")
    for stage in stages:
        if not stage.is_training and stage.is_open_for_submissions(now):
            return stage
    return None


def effective_mode(competition, now=None) -> ModerationMode:
    """Tryb moderacji obowiązujący **teraz**: ustawienie konkursu albo wymuszone ``PRE``.

    Uzasadnienie wymuszenia stoi w docstringu modułu. Kolejność sprawdzeń jest tu treścią:
    najpierw pytamy o zawody, potem o ustawienie – odwrotna kolejność dawałaby konkurs, który
    ustawieniem ``POST`` wyłącza sobie regulamin.
    """
    if stage_forcing_pre_moderation(competition, now) is not None:
        return ModerationMode.PRE
    return ModerationMode(settings_for(competition).mode)


def initial_status(competition, now=None) -> ModerationStatus:
    """Stan, z którym rodzi się nowy wpis albo wątek."""
    if effective_mode(competition, now) == ModerationMode.PRE:
        return ModerationStatus.PENDING
    return ModerationStatus.PUBLISHED


# --- dostęp ----------------------------------------------------------------------------------------


def can_read(user, competition) -> bool:
    """Czy ta osoba ma prawo czytać forum tego konkursu.

    Forum **nie jest publiczne** i to jest jego najważniejsza własność. Pod adresem rozmawiają
    osoby niepełnoletnie, a rozmowa widoczna bez logowania byłaby zbiorem wypowiedzi dzieci
    zindeksowanym przez wyszukiwarki – razem z imionami. Dlatego czytać mogą wyłącznie aktywni
    uczestnicy **tego** konkursu, członkowie jego komitetu i koordynator.

    Uczestnik bez aktywacji konta też nie czyta: konto bez potwierdzonego adresu jest kontem,
    o którym nie wiadomo, czy należy do osoby, która je założyła.
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role, participant_for

    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if competition is None:
        return False
    roles = {CompetitionRole.COORDINATOR, CompetitionRole.REVIEWER, CompetitionRole.APPEALS}
    if any(has_role(user, competition, role) for role in roles):
        return True
    return (
        has_role(user, competition, CompetitionRole.PARTICIPANT)
        and participant_for(user, competition) is not None
    )


#: Odznaki przy podpisie autora. Napis, nie kolor: odznaka ma się dać przeczytać na głos
#: i wydrukować (``docs/UI.md`` – „badge zawsze niesie słowo”).
BADGE_COORDINATOR = "Organizator"
BADGE_COMMITTEE = "Komitet"


def author_badge(user, competition) -> str:
    """Napis odznaki przy podpisie autora albo pusty napis dla uczestnika.

    Odznaka nie jest ozdobą: uczestnik ma odróżnić zdanie kolegi od zdania osoby, która
    rozstrzyga o zawodach. Bez niej „nie martwcie się tym zadaniem” brzmi tak samo z obu stron.
    „Organizator” wygrywa z „Komitetem”, bo jest węższą rolą.

    Pytamy ``roles_for`` (jedno zapytanie na osobę), a nie trzy razy ``has_role`` – reguła roli
    zostaje jedna, a strona wątku nie płaci za odznakę trzema zapytaniami na każdy podpis.
    Wołający, który renderuje listę wpisów, i tak zapamiętuje wynik per autor (patrz
    ``apps.web.views.forum.render_posts``).
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import roles_for

    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    names = roles_for(user, competition)
    if CompetitionRole.COORDINATOR in names:
        return BADGE_COORDINATOR
    if names & {CompetitionRole.REVIEWER, CompetitionRole.APPEALS}:
        return BADGE_COMMITTEE
    return ""


def ensure_can_write(user, competition) -> None:
    """Podnosi ``DomainError``, gdy ta osoba nie może teraz nic napisać.

    Trzy różne „nie” i trzy różne zdania, bo prowadzą do trzech różnych czynności: „nie masz tu
    czego szukać” (rola), „forum jest zamknięte” (ustawienie organizatora) i „ten dział nie
    przyjmuje wątków” (kategoria). Jedno zdanie zbiorcze zostawiałoby uczestnika bez wskazówki,
    co ma z tym zrobić.
    """
    if not can_read(user, competition):
        raise DomainError(
            "Forum jest dostępne dla uczestników tego konkursu.", "FORUM_FORBIDDEN", http.HTTP_403_FORBIDDEN
        )
    if settings_for(competition).is_read_only:
        raise DomainError(
            "Forum jest w trybie tylko do odczytu – nowych wpisów nie przyjmujemy.",
            "FORUM_READ_ONLY",
            http.HTTP_400_BAD_REQUEST,
        )


# --- widoczność --------------------------------------------------------------------------------------


def visible_threads(competition, user):
    """Wątki, które ta osoba ma prawo zobaczyć na liście.

    Opublikowane widzą wszyscy dopuszczeni; **własny** wątek czekający na moderację widzi jego
    autor – inaczej po założeniu wątku uczestnik wracałby na listę, na której nie ma po nim śladu,
    i zakładał go drugi raz.

    Odrzuconego i ukrytego nie widać nawet autorowi: ich miejsce jest na ekranie „Twoje wpisy”,
    razem z notatką moderatora, a nie w dziale, w którym miałyby udawać rozmowę.
    """
    rows = ForumThread.objects.for_competition(competition)
    own = Q(author=user, status__in=PENDING_STATUSES) if _identified(user) else Q(pk__in=[])
    return rows.filter(Q(status__in=READABLE_STATUSES) | own)


def visible_posts(thread, user):
    """Wpisy wątku widoczne dla tej osoby – ta sama reguła, co przy wątkach."""
    rows = ForumPost.objects.filter(thread=thread)
    own = Q(author=user, status__in=PENDING_STATUSES) if _identified(user) else Q(pk__in=[])
    return rows.filter(Q(status__in=READABLE_STATUSES) | own).order_by("created_at", "id")


def own_posts(user, competition):
    """„Twoje wpisy”: wszystko, co ta osoba napisała w tym konkursie, razem ze stanem.

    Tutaj **są** wpisy odrzucone i ukryte – to jest jedyne miejsce, w którym autor dowiaduje się,
    co się z nimi stało i dlaczego. Wpisów usuniętych przez samego autora też nie chowamy: „gdzie
    podział się mój wpis” z odpowiedzią „skasowałeś go w środę” jest lepsze niż cisza.
    """
    if not _identified(user):
        return ForumPost.objects.none()
    return (
        ForumPost.objects.for_competition(competition)
        .filter(author=user)
        .select_related("thread", "thread__category")
        .order_by("-created_at", "-id")
    )


def own_threads(user, competition):
    """Wątki założone przez tę osobę – razem z odrzuconymi i ukrytymi.

    Bliźniak :func:`own_posts` i istnieje z tego samego powodu: odrzucenie **wątku** jest osobną
    decyzją od odrzucenia wpisu (``apps.forum.models.ForumThread``) i niesie własne uzasadnienie.
    Bez tej funkcji autor, któremu moderator odrzucił cały temat, nie miałby w serwisie ani
    jednego miejsca, w którym się o tym dowie – wątek znika mu z działu, a lista wpisów pokazuje
    jego pierwszy wpis jako „opublikowany”, bo ``moderate_thread`` nie rusza wpisów już
    zatwierdzonych. List o decyzji (``apps.forum.notifications``) dostaje wyłącznie ten, kto
    listów nie wyłączył – dla pozostałych cisza w tym miejscu byłaby ciszą ostateczną.
    """
    if not _identified(user):
        return ForumThread.objects.none()
    return (
        ForumThread.objects.for_competition(competition)
        .filter(author=user)
        .select_related("category")
        .order_by("-created_at", "-id")
    )


def thread_visible_to(thread: ForumThread, user) -> bool:
    """Czy ta osoba otworzy stronę tego wątku, czy dostanie 404.

    Ta sama reguła, co w :func:`visible_threads`, wyciągnięta dla **jednego** wiersza: ekran
    „Twoje wpisy” wypisuje wątki, z których część jest odrzucona albo ukryta, i odnośnik do takiego
    wątku prowadziłby donikąd. Odnośnik, o którym z góry wiadomo, że da 404, jest gorszy niż jego
    brak – dlatego szablon pyta o to tutaj, zamiast zgadywać ze statusu.
    """
    if thread.status in READABLE_STATUSES:
        return True
    return thread.status in PENDING_STATUSES and _identified(user) and thread.author_id == user.pk


def categories_with_counts(competition, user):
    """Kategorie konkursu razem z liczbą widocznych wątków – jedno zapytanie na całą stronę.

    Licznik liczy **to, co widać**: kategoria z dziesięcioma wątkami czekającymi na moderację ma
    pokazać zero, bo tyle po kliknięciu zobaczy czytelnik. Licznik liczący wiersze w tabeli byłby
    obietnicą, której strona działu nie dotrzyma.
    """
    visible = Q(threads__status__in=READABLE_STATUSES)
    if _identified(user):
        visible |= Q(threads__author=user, threads__status__in=PENDING_STATUSES)
    return (
        ForumCategory.objects.for_competition(competition)
        .annotate(thread_count=Count("threads", filter=visible, distinct=True))
        .order_by("ordering", "name", "id")
    )


def _identified(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


# --- pisanie --------------------------------------------------------------------------------------


def _clean(value: str, *, limit: int, field: str, label: str) -> str:
    """Tekst od użytkownika: przycięty z brzegów, niepusty, w granicach limitu.

    Ta sama reguła, co przy zgłoszeniach do organizatora (``apps.support.services._clean_text``):
    tekst za długi jest **odrzucany**, a nie obcinany – obcięty wpis kończyłby się w połowie zdania.
    """
    text = (value or "").strip()
    if not text:
        raise DomainError(f"{label} nie może być puste.", f"{field}_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if len(text) > limit:
        raise DomainError(
            f"{label} jest za długie (limit {limit} znaków).",
            f"{field}_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    return text


@transaction.atomic
def create_thread(*, user, competition, category, title: str, body: str, request=None) -> ForumThread:
    """Nowy wątek razem z jego pierwszym wpisem. Oba dostają ten sam stan moderacyjny.

    Jedna transakcja, bo wątek bez wpisu nie jest wątkiem – byłby tematem bez treści, którego
    moderator nie ma jak ocenić, a czytelnik otwiera i zastaje pustą stronę.
    """
    ensure_can_write(user, competition)
    if category.competition_id != getattr(competition, "pk", None):
        # Kategoria z cudzego konkursu nie jest „nieprawidłowym wyborem”, tylko adresem, którego
        # tu nie ma – widok zamienia to na 404 (§ 3.6).
        raise DomainError("Nie ma takiej kategorii.", "FORUM_CATEGORY_NOT_FOUND", http.HTTP_404_NOT_FOUND)
    if not category.is_open:
        raise DomainError(
            "Ten dział jest zamknięty dla nowych wątków.",
            "FORUM_CATEGORY_CLOSED",
            http.HTTP_400_BAD_REQUEST,
        )
    clean_title = _clean(title, limit=MAX_TITLE_LENGTH, field="TITLE", label="Temat")
    clean_body = _clean(body, limit=MAX_POST_LENGTH, field="BODY", label="Treść")
    now = timezone.now()
    status = initial_status(competition, now)
    thread = ForumThread.objects.create(
        competition=competition,
        category=category,
        author=user,
        title=clean_title,
        created_at=now,
        last_activity_at=now,
        status=status,
    )
    ForumPost.objects.create(
        competition=competition,
        thread=thread,
        author=user,
        body=clean_body,
        created_at=now,
        status=status,
    )
    # Autor obserwuje swój wątek od pierwszej chwili: odpowiedź na własne pytanie jest tym,
    # o czym chce się dowiedzieć, nawet jeśli nie zajrzy na forum przez tydzień.
    notifications.follow_on_posting(user, thread)
    return thread


@transaction.atomic
def reply(*, user, competition, thread: ForumThread, body: str, request=None) -> ForumPost:
    """Odpowiedź w wątku."""
    ensure_can_write(user, competition)
    if thread.is_locked:
        raise DomainError("Ten wątek jest zamknięty.", "FORUM_THREAD_LOCKED", http.HTTP_400_BAD_REQUEST)
    if not thread.is_published:
        # Do wątku czekającego na moderację nie dopisuje nikt, także jego autor: moderator ma
        # ocenić temat, a nie rozmowę, która zdążyła pod nim urosnąć.
        raise DomainError(
            "Ten wątek czeka na moderację.", "FORUM_THREAD_NOT_PUBLISHED", http.HTTP_400_BAD_REQUEST
        )
    clean_body = _clean(body, limit=MAX_POST_LENGTH, field="BODY", label="Treść")
    now = timezone.now()
    status = initial_status(competition, now)
    post = ForumPost.objects.create(
        competition=competition,
        thread=thread,
        author=user,
        body=clean_body,
        created_at=now,
        status=status,
    )
    notifications.follow_on_posting(user, thread)
    if status == ModerationStatus.PUBLISHED:
        _touch(thread, now)
        notifications.post_published(post, now)
    return post


def _touch(thread: ForumThread, moment) -> None:
    """Przesuwa znacznik aktywności wątku. Robi to **wyłącznie** publikacja wypowiedzi.

    Wpis czekający na moderację nie podnosi wątku na liście: inaczej kolejność działu zdradzałaby,
    gdzie ktoś właśnie coś napisał, jeszcze zanim ktokolwiek to przeczytał.
    """
    ForumThread.objects.filter(pk=thread.pk).update(last_activity_at=moment)
    thread.last_activity_at = moment


def can_edit(post: ForumPost, user, now=None) -> bool:
    """Czy autor może jeszcze poprawić ten wpis (okno :data:`EDIT_WINDOW_MINUTES`)."""
    if not _identified(user) or post.author_id != user.pk:
        return False
    if post.status in (ModerationStatus.REJECTED, ModerationStatus.HIDDEN):
        return False
    return (now or timezone.now()) <= post.created_at + timedelta(minutes=EDIT_WINDOW_MINUTES)


@transaction.atomic
def edit_post(*, post: ForumPost, user, body: str, request=None) -> ForumPost:
    """Poprawka własnego wpisu w oknie kwadransa.

    Poprawiony wpis w trybie ``PRE`` **wraca do kolejki**, i to jest cała ostrożność tej funkcji:
    bez tego wystarczyłoby napisać zdanie nijakie, doczekać zatwierdzenia i podmienić treść.
    Tryb ``POST`` zostawia wpis widoczny – tam moderator i tak ogląda treść po publikacji.
    """
    if not can_edit(post, user):
        raise DomainError(
            f"Wpis można poprawić tylko przez {EDIT_WINDOW_MINUTES} minut od dodania.",
            "FORUM_EDIT_WINDOW_CLOSED",
            http.HTTP_400_BAD_REQUEST,
        )
    ensure_can_write(user, post.competition)
    post.body = _clean(body, limit=MAX_POST_LENGTH, field="BODY", label="Treść")
    post.edited_at = timezone.now()
    fields = ["body", "edited_at"]
    if effective_mode(post.competition) == ModerationMode.PRE:
        post.status = ModerationStatus.PENDING
        post.moderated_by = None
        post.moderated_at = None
        post.moderation_note = ""
        fields += ["status", "moderated_by", "moderated_at", "moderation_note"]
    post.save(update_fields=fields)
    return post


@transaction.atomic
def delete_own_post(*, post: ForumPost, user, request=None) -> ForumPost:
    """Usunięcie własnego wpisu – **miękkie**, do stanu ``HIDDEN``.

    Wiersz zostaje z dwóch powodów. Pierwszy jest rozmową: pod wpisem stoją odpowiedzi, a wycięcie
    akapitu, do którego ktoś się odniósł, zamienia je w bełkot. Drugi jest moderacyjny: wpis
    zgłoszony przez innego uczestnika nie może znikać na żądanie autora, bo wtedy „napisz i skasuj”
    byłoby drogą do pisania rzeczy, za które nikt nie odpowiada.

    Dla czytelnika różnicy nie ma – wpis znika z wątku natychmiast.
    """
    if not _identified(user) or post.author_id != user.pk:
        raise DomainError("To nie jest Twój wpis.", "FORUM_NOT_AUTHOR", http.HTTP_403_FORBIDDEN)
    if post.status == ModerationStatus.HIDDEN:
        return post
    post.status = ModerationStatus.HIDDEN
    post.save(update_fields=["status"])
    return post


@transaction.atomic
def report_post(*, post: ForumPost, user, reason: str, request=None) -> ForumReport:
    """Zgłoszenie wpisu do moderatora.

    Zgłoszenie **nie ukrywa** wpisu i to jest decyzja, a nie brak funkcji: automatyczne zdejmowanie
    po zgłoszeniu dałoby każdemu uczestnikowi przycisk „usuń cudzy wpis”, a na forum, na którym
    toczy się rywalizacja, ktoś by go w końcu użył.
    """
    if not can_read(user, post.competition):
        raise DomainError(
            "Forum jest dostępne dla uczestników tego konkursu.", "FORUM_FORBIDDEN", http.HTTP_403_FORBIDDEN
        )
    clean_reason = _clean(reason, limit=MAX_REASON_LENGTH, field="REASON", label="Powód zgłoszenia")
    return ForumReport.objects.create(
        competition=post.competition, post=post, reporter=user, reason=clean_reason
    )


# --- moderacja -------------------------------------------------------------------------------------
#
# Każda czynność moderatora zostawia wpis audytowy i **żaden z nich nie niesie treści wpisu**.
# Powód jest ten sam, co przy ``account.deleted`` (``apps.accounts.profile``): audyt zostaje
# w bazie na stałe i nie podlega ani retencji edycji, ani anonimizacji konta, więc skopiowana tam
# treść byłaby danymi osobowymi, których nie zdejmie już żadne żądanie z art. 17. W ``diff``
# stoi identyfikator, stan przed zmianą i – przy odrzuceniu – **sama obecność** notatki.


def _moderation_diff(before: str, after: str, **extra) -> dict:
    return {"status_before": str(before), "status_after": str(after), **extra}


@transaction.atomic
def moderate_post(
    *, post: ForumPost, actor, status: ModerationStatus, note: str = "", request=None
) -> ForumPost:
    """Ustawia stan wpisu decyzją moderatora. Jedno wejście dla zatwierdzenia, odrzucenia i ukrycia.

    Jedna funkcja, a nie cztery, bo cztery różniłyby się wyłącznie napisem – a wtedy pierwsza
    z nich, która zapomni o wpisie audytowym albo o ``moderated_at``, byłaby czynnością bez śladu.
    """
    before = post.status
    note_text = (note or "").strip()[:MAX_REASON_LENGTH]
    if status == ModerationStatus.REJECTED and not note_text:
        # Odrzucenie bez uzasadnienia jest dla autora komunikatem „nie” bez dalszego ciągu –
        # a ekran „Twoje wpisy” istnieje właśnie po to, żeby ten dalszy ciąg tam stał.
        raise DomainError(
            "Odrzucenie wymaga uzasadnienia – autor zobaczy je przy swoim wpisie.",
            "FORUM_NOTE_REQUIRED",
            http.HTTP_400_BAD_REQUEST,
        )
    now = timezone.now()
    post.status = status
    post.moderated_by = actor
    post.moderated_at = now
    post.moderation_note = note_text
    post.save(update_fields=["status", "moderated_by", "moderated_at", "moderation_note"])
    if status == ModerationStatus.PUBLISHED:
        _touch(post.thread, max(post.created_at, post.thread.last_activity_at))
        if before != ModerationStatus.PUBLISHED:
            notifications.post_published(post, now)
    if before == ModerationStatus.PENDING and status in _DECISION_NOTICES["post"]:
        notifications.record_decision(
            kind=_DECISION_NOTICES["post"][status], thread=post.thread, post=post, actor=actor
        )
    audit(
        actor,
        _POST_ACTIONS[status],
        post,
        _moderation_diff(before, status, thread_id=post.thread_id, has_note=bool(note_text)),
        request=request,
    )
    return post


#: Stan wpisu po decyzji → nazwa zdarzenia audytu. Słownik, a nie ``if``: dopisanie stanu bez
#: dopisania zdarzenia podniesie ``KeyError`` przy pierwszym wywołaniu, zamiast po cichu zapisać
#: czynność pod nazwą poprzedniego stanu.
_POST_ACTIONS = {
    ModerationStatus.PUBLISHED: AUDIT_POST_APPROVED,
    ModerationStatus.REJECTED: AUDIT_POST_REJECTED,
    ModerationStatus.HIDDEN: AUDIT_POST_HIDDEN,
    ModerationStatus.PENDING: AUDIT_POST_RESTORED,
}

_THREAD_ACTIONS = {
    ModerationStatus.PUBLISHED: AUDIT_THREAD_APPROVED,
    ModerationStatus.REJECTED: AUDIT_THREAD_REJECTED,
    ModerationStatus.HIDDEN: AUDIT_THREAD_HIDDEN,
    ModerationStatus.PENDING: AUDIT_THREAD_RESTORED,
}

#: Które decyzje o pozycji z kolejki idą listem do autora. Tylko dwie – zatwierdzenie
#: i odrzucenie – i tylko **z kolejki** (stan przed decyzją ``PENDING``): to są odpowiedzi na
#: pytanie „co z moim wpisem”, na które autor czeka. Ukrycie wpisu już wiszącego jest decyzją
#: porządkową moderatora i list o nim byłby zaproszeniem do sporu mailowego – autor widzi ją na
#: ekranie „Twoje wpisy”.
_DECISION_NOTICES = {
    "post": {
        ModerationStatus.PUBLISHED: DecisionKind.POST_APPROVED,
        ModerationStatus.REJECTED: DecisionKind.POST_REJECTED,
    },
    "thread": {
        ModerationStatus.PUBLISHED: DecisionKind.THREAD_APPROVED,
        ModerationStatus.REJECTED: DecisionKind.THREAD_REJECTED,
    },
}


@transaction.atomic
def moderate_thread(
    *, thread: ForumThread, actor, status: ModerationStatus, note: str = "", request=None
) -> ForumThread:
    """Decyzja moderatora o całym wątku.

    Zatwierdzenie wątku zatwierdza **jego pierwszy wpis**, bo to on jest treścią, którą moderator
    właśnie przeczytał. Pozostałych wpisów nie rusza: każdy z nich jest osobną wypowiedzią i ma
    przejść przez kolejkę na własnych prawach.
    """
    before = thread.status
    note_text = (note or "").strip()[:MAX_REASON_LENGTH]
    if status == ModerationStatus.REJECTED and not note_text:
        raise DomainError(
            "Odrzucenie wymaga uzasadnienia – autor zobaczy je przy swoim wątku.",
            "FORUM_NOTE_REQUIRED",
            http.HTTP_400_BAD_REQUEST,
        )
    now = timezone.now()
    thread.status = status
    thread.moderated_by = actor
    thread.moderated_at = now
    thread.moderation_note = note_text
    thread.save(update_fields=["status", "moderated_by", "moderated_at", "moderation_note"])
    first = thread.posts.order_by("created_at", "id").first()
    if first is not None and first.status == ModerationStatus.PENDING:
        ForumPost.objects.filter(pk=first.pk).update(
            status=status, moderated_by=actor, moderated_at=now, moderation_note=note_text
        )
    if before == ModerationStatus.PENDING and status in _DECISION_NOTICES["thread"]:
        # Jedna decyzja, jeden wiersz: pierwszy wpis zatwierdzany razem z wątkiem nie dokłada
        # drugiej pozycji „Twój wpis został zatwierdzony” – autor napisał jedną rzecz.
        notifications.record_decision(
            kind=_DECISION_NOTICES["thread"][status], thread=thread, post=None, actor=actor
        )
    audit(
        actor,
        _THREAD_ACTIONS[status],
        thread,
        _moderation_diff(before, status, has_note=bool(note_text)),
        request=request,
    )
    return thread


@transaction.atomic
def set_thread_flag(*, thread: ForumThread, actor, field: str, value: bool, request=None) -> ForumThread:
    """Przypięcie albo zamknięcie wątku. Dwie decyzje o jednym kształcie, więc jedna funkcja."""
    if field not in ("is_pinned", "is_locked"):  # pragma: no cover - błąd wołającego, nie danych
        raise ValueError(f"Nieznana właściwość wątku: {field!r}.")
    setattr(thread, field, bool(value))
    thread.save(update_fields=[field])
    action = AUDIT_THREAD_PINNED if field == "is_pinned" else AUDIT_THREAD_LOCKED
    audit(actor, action, thread, {field: bool(value)}, request=request)
    return thread


@transaction.atomic
def bulk_approve(*, competition, actor, thread_ids=(), post_ids=(), request=None) -> int:
    """Zatwierdza naraz wskazane wątki i wpisy z kolejki. Zwraca liczbę zatwierdzonych pozycji.

    Zbiorcze zatwierdzenie przechodzi przez **te same** funkcje, co pojedyncze, a nie przez jeden
    ``update()``: liczy się wpis audytowy na każdą pozycję. Kolejka moderacyjna, która po jednym
    kliknięciu zostawia jeden ślad na sto wpisów, przestaje odpowiadać na pytanie „kto to
    przepuścił”. Cena to sto zapytań na sto wpisów i jest to cena świadoma – kolejka kwadransa
    pracy moderatora ma kilkanaście pozycji, a nie kilka tysięcy.
    """
    approved = 0
    queue_threads = ForumThread.objects.for_competition(competition).filter(
        pk__in=list(thread_ids), status=ModerationStatus.PENDING
    )
    for thread in queue_threads:
        moderate_thread(thread=thread, actor=actor, status=ModerationStatus.PUBLISHED, request=request)
        approved += 1
    queue_posts = (
        ForumPost.objects.for_competition(competition)
        .filter(pk__in=list(post_ids), status=ModerationStatus.PENDING)
        .select_related("thread")
    )
    for post in queue_posts:
        moderate_post(post=post, actor=actor, status=ModerationStatus.PUBLISHED, request=request)
        approved += 1
    return approved


@transaction.atomic
def resolve_report(*, report: ForumReport, actor, request=None) -> ForumReport:
    """Zamyka zgłoszenie wpisu. Nie rusza samego wpisu – to osobna decyzja i osobny przycisk."""
    if report.resolved_at is not None:
        return report
    report.resolved_by = actor
    report.resolved_at = timezone.now()
    report.save(update_fields=["resolved_by", "resolved_at"])
    audit(actor, AUDIT_REPORT_RESOLVED, report, {"post_id": report.post_id}, request=request)
    return report


# --- konfiguracja panelu ---------------------------------------------------------------------------


@transaction.atomic
def save_category(
    *,
    competition,
    actor,
    name: str,
    description: str = "",
    ordering: int = 100,
    is_open: bool = True,
    category: ForumCategory | None = None,
    request=None,
) -> ForumCategory:
    """Zakłada albo zmienia dział forum. Slug powstaje z nazwy i nie zmienia się przy zmianie nazwy.

    Slug stoi w adresie, a adres bywa wklejony w cudzej wiadomości – przeliczanie go przy każdej
    poprawce nazwy zamieniałoby te odnośniki w 404. Nowy slug dostaje przyrostek liczbowy, gdy
    w tym konkursie jest już zajęty.
    """
    clean_name = _clean(name, limit=120, field="NAME", label="Nazwa działu")
    if category is None:
        category = ForumCategory(competition=competition, slug=_free_slug(competition, clean_name))
    category.name = clean_name
    category.description = (description or "").strip()[:300]
    category.ordering = int(ordering or 0)
    category.is_open = bool(is_open)
    category.save()
    audit(
        actor,
        AUDIT_CATEGORY_SAVED,
        category,
        {"slug": category.slug, "is_open": category.is_open},
        request=request,
    )
    return category


def _free_slug(competition, name: str) -> str:
    """Wolny identyfikator działu w tym konkursie. Pusty wynik ``slugify`` dostaje nazwę zastępczą."""
    base = slugify(name)[:50] or "dzial"
    taken = set(ForumCategory.objects.for_competition(competition).values_list("slug", flat=True))
    if base not in taken:
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if candidate not in taken:
            return candidate
    raise DomainError(
        "Nie udało się nadać identyfikatora działu.", "FORUM_SLUG_TAKEN", http.HTTP_400_BAD_REQUEST
    )


@transaction.atomic
def save_settings(*, competition, actor, mode: str, is_read_only: bool, request=None) -> ForumSettings:
    """Zapisuje tryb moderacji i przełącznik „tylko do odczytu”. Wiersz powstaje przy pierwszym zapisie."""
    if mode not in ModerationMode.values:
        raise DomainError("Nieznany tryb moderacji.", "FORUM_MODE_UNKNOWN", http.HTTP_400_BAD_REQUEST)
    row, _ = ForumSettings.objects.get_or_create(competition=competition)
    row.mode = mode
    row.is_read_only = bool(is_read_only)
    row.updated_at = timezone.now()
    row.save(update_fields=["mode", "is_read_only", "updated_at"])
    audit(
        actor, AUDIT_SETTINGS_CHANGED, row, {"mode": row.mode, "read_only": row.is_read_only}, request=request
    )
    return row


# --- kolejka i liczniki --------------------------------------------------------------------------


def pending_threads(competition):
    return (
        ForumThread.objects.for_competition(competition)
        .filter(status__in=PENDING_STATUSES)
        .select_related("author", "category")
        .order_by("created_at", "id")
    )


def pending_posts(competition):
    """Wpisy czekające na decyzję – **bez** pierwszych wpisów wątków, które też czekają.

    Wątek i jego pierwszy wpis to dla moderatora jedna pozycja kolejki (zatwierdza je razem,
    patrz :func:`moderate_thread`), więc pokazanie ich dwa razy dawałoby kolejkę dłuższą niż praca,
    którą opisuje.
    """
    pending_thread_ids = list(
        ForumThread.objects.for_competition(competition)
        .filter(status__in=PENDING_STATUSES)
        .values_list("pk", flat=True)
    )
    return (
        ForumPost.objects.for_competition(competition)
        .filter(status__in=PENDING_STATUSES)
        .exclude(thread_id__in=pending_thread_ids)
        .select_related("author", "thread", "thread__category")
        .order_by("created_at", "id")
    )


def open_reports(competition):
    return (
        ForumReport.objects.for_competition(competition)
        .filter(resolved_at__isnull=True)
        .select_related("post", "post__thread", "reporter")
        .order_by("created_at", "id")
    )


def moderation_count(competition=None) -> int:
    """Ile pozycji czeka na moderatora – liczba z odznaki w menu panelu.

    Ta sama definicja, co kolejka na ekranie (wątki + wpisy spoza nich + nierozpatrzone
    zgłoszenia): odznaka prowadząca do kolejki z inną liczbą wierszy byłaby odznaką, która kłamie.

    ``competition=None`` znaczy „konkurs z kontekstu” – ta sama umowa, co
    ``apps.support.services.open_ticket_count``. Konkurs bez włączonego forum oddaje zero **bez
    ani jednego zapytania**: flaga jest polem wiersza, który wołający już trzyma.
    """
    from apps.competitions.scoping import resolve_competition

    from .models import FORUM_FLAG

    competition = resolve_competition(competition)
    if competition is None or not competition.has_feature(FORUM_FLAG):
        return 0
    return (
        pending_threads(competition).count()
        + pending_posts(competition).count()
        + open_reports(competition).count()
    )
