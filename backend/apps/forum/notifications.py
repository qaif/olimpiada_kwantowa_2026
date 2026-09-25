"""Powiadomienia e-mail z forum: kto, o czym, kiedy – i czego list **nigdy** nie niesie.

Prośba organizatora z 25.09.2026. Do tej pory (v0.28.0) jedynym sygnałem był licznik w menu
panelu, a decyzja „forum nie pisze do nikogo” stała w docstringu ``apps.forum.services``. Ta
decyzja miała jeden powód – skrzynka koordynatora zamieniona w kanał RSS, wyzwalany każdym
akapitem nastolatka – i ten moduł odpowiada na **ten powód**, a nie go omija: żaden list
z forum nie wychodzi „za wpis”. Wychodzą trzy rodzaje, każdy zbiorczy:

1. **list o kolejce moderacji** do koordynatorów konkursu: pierwszy po
   ``FORUM_MODERATION_DIGEST_DELAY_MINUTES`` minutach od pojawienia się pozycji (koordynator
   siedzący w panelu zdąży ją rozpatrzyć, zanim list w ogóle powstanie), kolejne najwyżej raz na
   ``FORUM_MODERATION_DIGEST_INTERVAL_HOURS`` godzin, dopóki coś czeka. Tylko koordynator –
   wyłącznie on moderuje,
2. **list o nowych odpowiedziach** w obserwowanych wątkach: o jednym wątku najwyżej jeden list na
   ``FORUM_THREAD_NOTIFY_INTERVAL_HOURS`` godzin, a wszystkie wątki, które są akurat do zgłoszenia,
   jadą w jednym liście. Temat mówi wprost, gdy odpowiedział organizator albo komitet – to jest
   ta odpowiedź, na którą uczestnik czeka,
3. **list o decyzji moderatora** w sprawie własnego wpisu albo wątku – zbierany tak samo, bo
   zatwierdzenie zbiorcze to kilkanaście decyzji w jednej sekundzie.

Odbiorca, który wybrał „raz dziennie”, dostaje punkty 2 i 3 w jednym liście o stałej porze
(``FORUM_DAILY_DIGEST_HOUR_UTC``), a „nigdy” – nic.

**Czego list nie niesie, choć mógłby.** Treści wpisów – także opublikowanych. Regulamin pozwala
pokazać w liście treść publiczną, ale forum **nie jest publiczne**: czytanie wymaga zalogowania
i roli w konkursie, a to jest główny środek ochrony wypowiedzi osób niepełnoletnich w rejestrze
czynności (``apps.accounts.processing_register.FORUM_ACTIVITY``). List przekazywany dalej,
czytany na wspólnym komputerze albo leżący latami w skrzynce rodzica byłby drogą obok tego
zabezpieczenia. Dlatego list mówi **że** i **gdzie**, a nie **co**: temat wątku (tylko
opublikowanego), liczbę nowych wpisów, rolę odpowiadającego i odnośnik. Nie ma w nim też imion
piszących – podpis istnieje na forum, za logowaniem, i tam ma zostać.

Wpis albo wątek **niepublikowany** nie zostawia w liście nawet tematu: list do koordynatora
podaje wyłącznie liczby, a list o odrzuceniu – uzasadnienie moderatora (zdanie skierowane do
autora, do jego własnej skrzynki), bez tematu odrzuconego wątku.

**Co jest sprawdzane w chwili wysyłki, a nie w chwili zdarzenia.** Wszystko, co mogło się
zmienić w międzyczasie: wpis zatwierdzony i zaraz ukryty nie wychodzi, wątek odrzucony po
odpowiedzi nie wychodzi, konto zablokowane, niepotwierdzone albo zanonimizowane nie dostaje nic
(``DELIVERABLE`` i ``exclude_anonymised`` – te same reguły, co komunikaty organizatora), a osoba,
która straciła rolę w konkursie, przestaje dostawać listy o jego forum (``can_read``). Stan
wysyłki (``ForumSubscription``, ``ForumDecisionNotice``) nie trzyma ani jednego zdania treści –
list składa się z tego, co jest opublikowane **w chwili przebiegu**.

**Podstawa prawna i rejestr czynności.** Listy są kontaktem w ramach usługi, z której odbiorca
korzysta (forum konkursu, do którego się zapisał), a nie nowym celem: celem forum od początku
jest „sprawna komunikacja w czasie zawodów”, a list o odpowiedzi na własne pytanie jest tego celu
częścią. Podstawa zostaje ta sama (art. 6 ust. 1 lit. f RODO), a prawo sprzeciwu (art. 21) ma
postać wypisu jednym kliknięciem w każdym liście. Nowego wiersza w rejestrze więc nie ma; wiersz
forum dostał za to nowego **odbiorcę** (dostawca poczty wychodzącej) i nową **kategorię danych**
(obserwowane wątki, ustawienia powiadomień) – patrz ``REGISTER_VERSION`` 1.9.

**Wypis bez logowania.** Każdy list ma odnośnik z podpisanym tokenem (``django.core.signing``,
sól :data:`UNSUBSCRIBE_SALT`) i nagłówki ``List-Unsubscribe`` / ``List-Unsubscribe-Post``
(RFC 8058). Token wskazuje konto i zakres (wszystkie listy o forum, listy o kolejce albo jeden
wątek) i niczego poza wypisem nie umożliwia – dlatego nie wygasa: link wypisu, który przestaje
działać po miesiącu, jest dokładnie tym, co odbiorca zgłasza potem jako spam.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import F, Min, Q, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import (
    FORUM_FLAG,
    DecisionKind,
    ForumDecisionNotice,
    ForumModerationDigest,
    ForumNotificationSettings,
    ForumPost,
    ForumSubscription,
    ForumThread,
    ModerationStatus,
    NotificationFrequency,
)

logger = logging.getLogger(__name__)

#: Sól tokenu wypisu. Osobna od każdej innej w serwisie: podpis z linku aktywacyjnego albo z listu
#: do opiekuna nie może dać się użyć jako wypis – i odwrotnie.
UNSUBSCRIBE_SALT = "apps.forum.notifications.unsubscribe"

#: Zakresy wypisu. Krótkie, bo jadą w adresie.
SCOPE_ALL = "all"
SCOPE_MODERATION = "mod"
SCOPE_THREAD_PREFIX = "t"

#: Ile znaków tematu wątku wchodzi do tematu listu. Temat listu ma się zmieścić w jednym wierszu
#: skrzynki razem z prefiksem konkursu.
SUBJECT_TITLE_LIMIT = 80

#: Tematy listów. Leniwe, bo moduł ładuje się przy starcie procesu, a tłumaczenie ma się
#: rozstrzygnąć w języku **odbiorcy** (``language_for``). Prefiks konkursu doklejamy osobno –
#: ta sama reguła, co przy przekazaniu rozwiązania (``apps.submissions.forwarding.subject_for``):
#: listy są nowe, więc nie zmieniają nikomu reguły w skrzynce, a „[Olimpiada Kwantowa] Forum:”
#: jest dokładnie tym, po czym da się je odfiltrować.
SUBJECT_MODERATION = gettext_lazy("Forum: wpisy czekają na moderację (%(count)s)")
SUBJECT_REPLY = gettext_lazy("Forum: nowe odpowiedzi w wątku „%(title)s”")
SUBJECT_REPLY_COORDINATOR = gettext_lazy("Forum: organizator odpowiedział w wątku „%(title)s”")
SUBJECT_REPLY_COMMITTEE = gettext_lazy("Forum: komitet odpowiedział w wątku „%(title)s”")
SUBJECT_DECISIONS = gettext_lazy("Forum: decyzja organizatora w sprawie Twojego wpisu")
SUBJECT_NEWS = gettext_lazy("Forum: nowości w obserwowanych wątkach")
SUBJECT_DAILY = gettext_lazy("Forum: podsumowanie dnia")


# --- ustawienia konta ------------------------------------------------------------------------------


def preferences_for(user) -> ForumNotificationSettings:
    """Ustawienia powiadomień tego konta. Brak wiersza znaczy „domyślne” – obiekt bywa niezapisany.

    Ta sama umowa, co ``apps.forum.services.settings_for``: odczyt ekranu profilu nie zakłada
    wiersza w bazie, wiersz powstaje dopiero przy świadomym zapisie (:func:`save_preferences`).
    """
    row = ForumNotificationSettings.objects.filter(user=user).first() if _identified(user) else None
    return row if row is not None else ForumNotificationSettings(user=user if _identified(user) else None)


def save_preferences(user, *, frequency: str, moderation_digest: bool) -> ForumNotificationSettings:
    """Zapisuje ustawienia powiadomień konta. Nieznana częstotliwość to błąd wołającego."""
    frequency = NotificationFrequency(frequency).value
    row, _ = ForumNotificationSettings.objects.update_or_create(
        user=user, defaults={"frequency": frequency, "moderation_digest": bool(moderation_digest)}
    )
    if frequency == NotificationFrequency.NEVER:
        # „Nigdy” ma znaczyć także „nie to, co już czeka”: bez tego przełączenie z powrotem na
        # „na bieżąco” za miesiąc wysłałoby list o rozmowie sprzed miesiąca.
        _drop_pending_for(user.pk)
    return row


def _drop_pending_for(user_id: int) -> None:
    ForumSubscription.objects.filter(user_id=user_id, pending_since__isnull=False).update(pending_since=None)
    ForumDecisionNotice.objects.filter(user_id=user_id, handled_at__isnull=True).update(
        handled_at=timezone.now()
    )


def erase_for_user(user) -> int:
    """Kasuje stan powiadomień konta: ustawienia, obserwacje i decyzje czekające na list.

    Woła to anonimizacja konta (``apps.accounts.profile``). Wpisy zostają bez podpisu, ale to,
    **które wątki** ta osoba obserwowała i kiedy dostała list, nie jest częścią rozmowy – to stan
    wysyłki do skrzynki, której już nie ma. Zwraca liczbę skasowanych wierszy.
    """
    removed = 0
    for model in (ForumSubscription, ForumDecisionNotice, ForumNotificationSettings):
        removed += model.objects.filter(user=user).delete()[0]
    return removed


def _identified(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


# --- obserwowane wątki -----------------------------------------------------------------------------


def is_following(user, thread: ForumThread) -> bool:
    """Czy ta osoba obserwuje ten wątek – odpowiedź dla przycisku na stronie wątku."""
    if not _identified(user):
        return False
    return ForumSubscription.objects.filter(thread=thread, user=user, is_active=True).exists()


def set_following(user, thread: ForumThread, follow: bool) -> ForumSubscription:
    """„Obserwuj wątek” / „Przestań obserwować” – **jawny** wybór, który wygrywa z automatem.

    Wyłączenie czyści też to, co już czekało na list: kto kliknął „przestań obserwować”, nie ma
    dostać za pięć minut listu o odpowiedzi sprzed kliknięcia.
    """
    row, created = ForumSubscription.objects.get_or_create(
        thread=thread, user=user, defaults={"competition_id": thread.competition_id, "is_active": follow}
    )
    if not created and row.is_active != follow:
        row.is_active = follow
        fields = ["is_active"]
        if not follow:
            row.pending_since = None
            fields.append("pending_since")
        row.save(update_fields=fields)
    return row


def follow_on_posting(user, thread: ForumThread) -> None:
    """Obserwacja zakładana **automatycznie** przy pierwszej wypowiedzi w wątku.

    ``get_or_create``, a nie ``set_following``: wiersz, który już jest, zostaje taki, jaki jest.
    Osoba, która kiedyś kliknęła „przestań obserwować”, nie zaczyna obserwować od nowa tylko
    dlatego, że dopisała jeszcze jedno zdanie – jej wybór był jawny, a automat jest domysłem.
    """
    if not _identified(user):
        return
    ForumSubscription.objects.get_or_create(
        thread=thread, user=user, defaults={"competition_id": thread.competition_id}
    )


def post_published(post: ForumPost, moment=None) -> None:
    """Wpis właśnie stał się widoczny: obserwujący wątek (poza autorem) mają na co czekać.

    Wołają to **wyłącznie** dwie drogi publikacji – odpowiedź w trybie ``POST`` i zatwierdzenie
    przez moderatora (``apps.forum.services``). Wpis czekający na moderację nie zostawia tu
    śladu, więc nie ma jak wyjść pocztą, zanim ktoś go przeczytał.

    ``Coalesce``, a nie warunek ``pending_since IS NULL``: aktualizacja bez warunku czeka na
    blokadę wiersza trzymaną przez trwającą wysyłkę i po niej widzi już wyczyszczony znacznik.
    Warunek sprawdziłby stan sprzed wysyłki, pominął wiersz – i odpowiedź opublikowana w tej samej
    sekundzie, w której wychodził list, nie trafiłaby już do żadnego.
    """
    if post.thread.status != ModerationStatus.PUBLISHED:
        return
    moment = moment or timezone.now()
    rows = ForumSubscription.objects.filter(thread_id=post.thread_id, is_active=True)
    if post.author_id is not None:
        rows = rows.exclude(user_id=post.author_id)
    rows.update(pending_since=Coalesce(F("pending_since"), Value(moment)))


def record_decision(*, kind: DecisionKind, thread: ForumThread, post: ForumPost | None, actor) -> None:
    """Zapisuje decyzję moderatora do listu autora – chyba że list nie ma sensu.

    Nie ma sensu, gdy autora już nie ma (konto skasowane) albo gdy autor sam o sobie
    zdecydował (koordynator zatwierdzający własny wątek): list „zatwierdziłeś swój wpis” nie
    mówi nikomu nic nowego.
    """
    author_id = post.author_id if post is not None else thread.author_id
    if author_id is None or author_id == getattr(actor, "pk", None):
        return
    ForumDecisionNotice.objects.create(
        competition_id=thread.competition_id, user_id=author_id, kind=kind, thread=thread, post=post
    )


# --- tokeny wypisu -----------------------------------------------------------------------------------


def unsubscribe_token(user, scope: str) -> str:
    """Podpisany token wypisu dla tego konta i zakresu. Bez terminu ważności – patrz docstring modułu."""
    return signing.dumps({"u": user.pk, "s": scope}, salt=UNSUBSCRIBE_SALT)


def thread_scope(thread_id: int) -> str:
    return f"{SCOPE_THREAD_PREFIX}{thread_id}"


def read_unsubscribe_token(token: str) -> tuple[int, str]:
    """``(identyfikator konta, zakres)`` z tokenu albo ``signing.BadSignature``.

    Zakres spoza trzech znanych jest tak samo nieważny jak zły podpis: token podpisany naszym
    kluczem, ale z nieznanym zakresem, znaczy błąd w kodzie, który go wystawił – a nie polecenie.
    """
    data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    if not isinstance(data, dict):
        raise signing.BadSignature("Nieprawidłowa treść tokenu.")
    user_id, scope = data.get("u"), data.get("s")
    if not isinstance(user_id, int) or not isinstance(scope, str) or not _known_scope(scope):
        raise signing.BadSignature("Nieznany zakres wypisu.")
    return user_id, scope


def _known_scope(scope: str) -> bool:
    if scope in (SCOPE_ALL, SCOPE_MODERATION):
        return True
    return scope.startswith(SCOPE_THREAD_PREFIX) and scope[len(SCOPE_THREAD_PREFIX) :].isdigit()


def unsubscribe_url(user, scope: str, competition=None) -> str:
    from apps.accounts.activation import absolute_url

    return absolute_url(
        reverse("web:forum-unsubscribe", args=[unsubscribe_token(user, scope)]), competition=competition
    )


@transaction.atomic
def apply_unsubscribe(user_id: int, scope: str) -> None:
    """Wykonuje wypis. Powtórzony – nie robi nic więcej niż za pierwszym razem.

    Konto, którego już nie ma, nie jest błędem: link z listu sprzed usunięcia konta ma dać stronę
    „gotowe”, a nie 500 – nie ma już komu wysyłać, więc wypis jest prawdą.
    """
    from apps.accounts.models import User

    if not User.objects.filter(pk=user_id).exists():
        return
    if scope == SCOPE_ALL:
        row, _ = ForumNotificationSettings.objects.get_or_create(user_id=user_id)
        if row.frequency != NotificationFrequency.NEVER:
            row.frequency = NotificationFrequency.NEVER
            row.save(update_fields=["frequency", "updated_at"])
        _drop_pending_for(user_id)
    elif scope == SCOPE_MODERATION:
        row, _ = ForumNotificationSettings.objects.get_or_create(user_id=user_id)
        if row.moderation_digest:
            row.moderation_digest = False
            row.save(update_fields=["moderation_digest", "updated_at"])
    else:
        thread_id = int(scope[len(SCOPE_THREAD_PREFIX) :])
        ForumSubscription.objects.filter(user_id=user_id, thread_id=thread_id).update(
            is_active=False, pending_since=None
        )


def unsubscribe_headers(url: str) -> dict[str, str]:
    """Nagłówki wypisu jednym kliknięciem (RFC 2369 i RFC 8058).

    ``List-Unsubscribe-Post`` mówi klientowi poczty, że wolno mu wysłać ``POST`` pod ten adres bez
    pokazywania strony – i tylko ``POST`` wypisuje. ``GET`` pokazuje stronę z przyciskiem, bo skaner
    odnośników w skrzynce firmowej otwiera każdy link w liście i nie może przy tym nikogo wypisać.
    """
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


# --- wspólne kawałki listu -----------------------------------------------------------------------------


def forum_enabled(competition) -> bool:
    return competition is not None and competition.has_feature(FORUM_FLAG)


def _subject(template, competition, **values) -> str:
    """Temat z prefiksem konkursu. Białe znaki zwinięte – nagłówek nie może nieść końca wiersza."""
    prefix = getattr(competition, "email_subject_prefix", "") or settings.EMAIL_SUBJECT_PREFIX
    text = str(template) % values if values else str(template)
    return prefix + " ".join(text.split())


def _short_title(title: str) -> str:
    title = " ".join((title or "").split())
    return title if len(title) <= SUBJECT_TITLE_LIMIT else title[: SUBJECT_TITLE_LIMIT - 1].rstrip() + "…"


def _link(path: str, competition) -> str:
    from apps.accounts.activation import absolute_url

    return absolute_url(path, competition=competition)


def _settings_link(competition) -> str:
    return _link(reverse("web:account-profile") + "#powiadomienia-forum", competition)


def _moment(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y %H:%M")


def _deliverable_users(user_ids):
    """Konta, do których wolno pisać: aktywne, z potwierdzonym adresem, nie po anonimizacji.

    ``select_related("preference")``, bo ``language_for`` czyta język z tego wiersza przy każdym
    liście – bez tego wysyłka płaciłaby zapytaniem za odbiorcę.
    """
    from apps.accounts.messaging import DELIVERABLE
    from apps.accounts.models import User

    return (
        User.objects.filter(DELIVERABLE, pk__in=user_ids)
        .exclude_anonymised()
        .select_related("preference", "forum_notification_settings")
    )


def _signed(lines: list[str], competition) -> str:
    """Treść listu razem ze stopką. Woła się **w** ``language_for`` – stopka też jest tłumaczona."""
    from apps.accounts.activation import signature_lines

    return "\n".join([*lines, "", *signature_lines(competition)])


def _send(user, competition, subject: str, body: str, unsubscribe: str) -> None:
    from apps.accounts.activation import queue_mail

    queue_mail(subject, body, user.email, competition=competition, headers=unsubscribe_headers(unsubscribe))


# --- 1. list o kolejce moderacji --------------------------------------------------------------------


def moderation_recipients(competition):
    """Koordynatorzy **tego** konkursu, do których wolno pisać i którzy listów nie wyłączyli.

    Rola idzie przez ``_role_filter`` z ``apps.accounts.messaging`` – tę samą regułę, co lista
    odbiorców komunikatów organizatora i ``has_role``: przy włączonym ``memberships_enforced``
    rolą jest ``Membership`` tego konkursu, więc koordynator olimpiady B nie dostaje listu
    o kolejce olimpiady A. Członkowie komitetu listu nie dostają – forum moderuje wyłącznie
    koordynator.
    """
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CompetitionRole, User

    coordinators = User.objects.filter(_role_filter(competition, CompetitionRole.COORDINATOR)).values("pk")
    return (
        _deliverable_users(coordinators)
        .exclude(forum_notification_settings__moderation_digest=False)
        .order_by("pk")
    )


@dataclass(frozen=True)
class QueueState:
    threads: int
    posts: int
    reports: int
    oldest: object

    @property
    def total(self) -> int:
        return self.threads + self.posts + self.reports


def queue_state(competition) -> QueueState:
    """Liczby z kolejki moderacji i chwila, od której czeka najstarsza pozycja.

    Te same trzy zbiory, co ekran kolejki i odznaka w menu (``apps.forum.services.pending_*``,
    ``open_reports``): list, który podaje inną liczbę niż kolejka, do której prowadzi, jest listem,
    któremu przestaje się wierzyć. Wpis poprawiony po zatwierdzeniu wraca do kolejki z datą
    poprawki – dla koordynatora czeka od tej chwili, a nie od chwili napisania.
    """
    from .services import open_reports, pending_posts, pending_threads

    threads = pending_threads(competition)
    posts = pending_posts(competition)
    reports = open_reports(competition)
    moments = [
        threads.aggregate(m=Min("created_at"))["m"],
        posts.aggregate(m=Min(Coalesce("edited_at", "created_at")))["m"],
        reports.aggregate(m=Min("created_at"))["m"],
    ]
    known = [moment for moment in moments if moment is not None]
    return QueueState(
        threads=threads.count(),
        posts=posts.count(),
        reports=reports.count(),
        oldest=min(known) if known else None,
    )


def send_moderation_digest(competition, now=None) -> int:
    """List o kolejce moderacji do koordynatorów tego konkursu, jeśli pora. Zwraca liczbę listów.

    Dwa warunki i oba muszą być spełnione:

    - najstarsza pozycja czeka co najmniej ``FORUM_MODERATION_DIGEST_DELAY_MINUTES`` – kolejka
      rozpatrywana na bieżąco nie produkuje listów wcale,
    - od ostatniego listu o tej kolejce minęło ``FORUM_MODERATION_DIGEST_INTERVAL_HOURS``.

    Znacznik zapisujemy pod blokadą wiersza (``select_for_update``): dwa przebiegi zadania, które
    nałożyły się w czasie (restart beatu, ręczne wywołanie), nie wyślą dwóch listów.
    """
    if not forum_enabled(competition):
        return 0
    now = now or timezone.now()
    state = queue_state(competition)
    if state.total == 0 or state.oldest is None:
        return 0
    if state.oldest > now - timedelta(minutes=settings.FORUM_MODERATION_DIGEST_DELAY_MINUTES):
        return 0
    interval = timedelta(hours=settings.FORUM_MODERATION_DIGEST_INTERVAL_HOURS)
    with transaction.atomic():
        ForumModerationDigest.objects.get_or_create(competition=competition)
        digest = ForumModerationDigest.objects.select_for_update().get(competition=competition)
        if digest.last_sent_at is not None and digest.last_sent_at > now - interval:
            return 0
        recipients = list(moderation_recipients(competition))
        digest.last_sent_at = now
        digest.save(update_fields=["last_sent_at"])
        for user in recipients:
            _send_moderation_mail(user, competition, state)
    return len(recipients)


def _send_moderation_mail(user, competition, state: QueueState) -> None:
    from apps.accounts.preferences import language_for

    unsubscribe = unsubscribe_url(user, SCOPE_MODERATION, competition)
    with language_for(user, competition):
        lines = [
            _("Na forum czekają pozycje do Twojej decyzji:"),
            "",
            _("- nowe wątki: %(count)s") % {"count": state.threads},
            _("- odpowiedzi: %(count)s") % {"count": state.posts},
            _("- zgłoszenia wpisów: %(count)s") % {"count": state.reports},
            "",
            _("Najstarsza pozycja czeka od %(moment)s.") % {"moment": _moment(state.oldest)},
            "",
            _("Kolejka moderacji:"),
            _link(reverse("web:coordinator-forum"), competition),
            "",
            _(
                "Kolejny list o kolejce wyślemy najwcześniej za %(hours)s godz. – i tylko wtedy, "
                "gdy coś nadal będzie czekać."
            )
            % {"hours": settings.FORUM_MODERATION_DIGEST_INTERVAL_HOURS},
            _("Ustawienia powiadomień: %(link)s") % {"link": _settings_link(competition)},
            _("Wyłącz listy o kolejce moderacji: %(link)s") % {"link": unsubscribe},
        ]
        subject = _subject(SUBJECT_MODERATION, competition, count=state.total)
        body = _signed(lines, competition)
    _send(user, competition, subject, body, unsubscribe)


# --- 2. i 3. listy do uczestników i autorów -------------------------------------------------------------


@dataclass
class ThreadNews:
    thread: ForumThread
    count: int
    badge: str


def _frequency_q(frequency: NotificationFrequency) -> Q:
    """Warunek „konto ma tę częstotliwość” – z wartością domyślną dla konta bez wiersza ustawień."""
    field = "user__forum_notification_settings__frequency"
    condition = Q(**{field: frequency})
    if frequency == NotificationFrequency.IMMEDIATE:
        condition |= Q(user__forum_notification_settings__isnull=True)
    return condition


def send_member_notifications(competition, now=None, *, daily: bool = False) -> int:
    """Listy o obserwowanych wątkach i o decyzjach moderatora. Zwraca liczbę wysłanych listów.

    ``daily=False`` – przebieg częsty, dla kont „na bieżąco”: wątek wchodzi do listu, gdy od
    ostatniego listu o nim minęło ``FORUM_THREAD_NOTIFY_INTERVAL_HOURS``. ``daily=True`` – przebieg
    raz na dobę, dla kont „raz dziennie”: wszystko, co czeka, w jednym liście, bez limitu na wątek
    (sam przebieg jest limitem).

    Jeden list na osobę i przebieg, niezależnie od tego, ile wątków i decyzji się w nim zebrało.
    Wiersze stanu blokujemy z ``skip_locked``: przebieg, który nałożył się na poprzedni, pomija to,
    co tamten właśnie wysyła, zamiast wysłać to drugi raz.
    """
    if not forum_enabled(competition):
        return 0
    now = now or timezone.now()
    frequency = NotificationFrequency.DAILY if daily else NotificationFrequency.IMMEDIATE
    with transaction.atomic():
        if not daily:
            _drop_never(competition, now)
        subscriptions = ForumSubscription.objects.for_competition(competition).filter(
            _frequency_q(frequency), is_active=True, pending_since__isnull=False
        )
        if not daily:
            limit = now - timedelta(hours=settings.FORUM_THREAD_NOTIFY_INTERVAL_HOURS)
            subscriptions = subscriptions.filter(
                Q(last_notified_at__isnull=True) | Q(last_notified_at__lte=limit)
            )
        subscriptions = list(subscriptions.select_for_update(skip_locked=True, of=("self",)))
        notices = list(
            ForumDecisionNotice.objects.for_competition(competition)
            .filter(_frequency_q(frequency), handled_at__isnull=True)
            .select_for_update(skip_locked=True, of=("self",))
        )
        if not subscriptions and not notices:
            return 0
        return _deliver(competition, now, subscriptions, notices, daily=daily)


def _drop_never(competition, now) -> None:
    """Konta z „nigdy” nie zbierają zaległości – patrz :func:`save_preferences`."""
    ForumSubscription.objects.for_competition(competition).filter(
        _frequency_q(NotificationFrequency.NEVER), pending_since__isnull=False
    ).update(pending_since=None)
    ForumDecisionNotice.objects.for_competition(competition).filter(
        _frequency_q(NotificationFrequency.NEVER), handled_at__isnull=True
    ).update(handled_at=now)


def _deliver(competition, now, subscriptions, notices, *, daily: bool) -> int:
    from .services import can_read

    by_user: dict[int, tuple[list, list]] = {}
    for row in subscriptions:
        by_user.setdefault(row.user_id, ([], []))[0].append(row)
    for row in notices:
        by_user.setdefault(row.user_id, ([], []))[1].append(row)

    thread_ids = {row.thread_id for row in subscriptions} | {row.thread_id for row in notices}
    threads = {
        thread.pk: thread
        for thread in ForumThread.objects.for_competition(competition)
        .filter(pk__in=thread_ids)
        .select_related("category")
    }
    post_ids = {row.post_id for row in notices if row.post_id}
    posts = {post.pk: post for post in ForumPost.objects.filter(pk__in=post_ids)}
    users = {user.pk: user for user in _deliverable_users(list(by_user))}
    badges: dict[int | None, str] = {}

    sent = 0
    mailed_subscriptions: list[int] = []
    for user_id, (user_subscriptions, user_notices) in by_user.items():
        user = users.get(user_id)
        if user is None or not can_read(user, competition):
            # Konto, do którego nie wolno pisać, albo osoba, która nie ma już roli w tym konkursie:
            # zaległość znika razem z nią, zamiast rosnąć do dnia, w którym ktoś włączy konto.
            continue
        news = [
            item
            for item in (
                _thread_news(row, threads.get(row.thread_id), user, competition, badges)
                for row in user_subscriptions
            )
            if item is not None
        ]
        decisions = [
            item
            for item in (_current_decision(row, threads.get(row.thread_id), posts) for row in user_notices)
            if item is not None
        ]
        if not news and not decisions:
            continue
        _send_member_mail(user, competition, news, decisions, daily=daily)
        reported = {item.thread.pk for item in news}
        mailed_subscriptions.extend(row.pk for row in user_subscriptions if row.thread_id in reported)
        sent += 1

    ForumSubscription.objects.filter(pk__in=[row.pk for row in subscriptions]).update(pending_since=None)
    ForumSubscription.objects.filter(pk__in=mailed_subscriptions).update(last_notified_at=now)
    ForumDecisionNotice.objects.filter(pk__in=[row.pk for row in notices]).update(handled_at=now)
    return sent


def _thread_news(subscription, thread, user, competition, badges) -> ThreadNews | None:
    """Co nowego w wątku od ``pending_since`` – liczone od nowa, z tego, co **teraz** widać.

    „Nowy” znaczy „opublikowany po znaczniku”: napisany po nim w trybie ``POST`` albo zatwierdzony
    po nim w trybie ``PRE`` (``moderated_at``). Wpisy tej osoby nie liczą się nigdy – list „ktoś
    odpowiedział” o własnej odpowiedzi byłby szumem.
    """
    if thread is None or thread.status != ModerationStatus.PUBLISHED:
        return None
    since = subscription.pending_since
    authors = list(
        ForumPost.objects.filter(thread=thread, status=ModerationStatus.PUBLISHED)
        .filter(Q(created_at__gte=since) | Q(moderated_at__gte=since))
        .exclude(author_id=user.pk)
        .values_list("author_id", flat=True)
    )
    if not authors:
        return None
    return ThreadNews(thread=thread, count=len(authors), badge=_strongest_badge(authors, competition, badges))


def _strongest_badge(author_ids, competition, badges) -> str:
    """Najważniejsza odznaka wśród piszących: „Organizator” przed „Komitetem”, oba przed niczym.

    Odznaka liczy się raz na autora na przebieg (``badges``) – ten sam zabieg, co przy stronie
    wątku (``apps.web.views.forum.render_posts``).
    """
    from apps.accounts.models import User

    from .services import BADGE_COMMITTEE, BADGE_COORDINATOR, author_badge

    found = set()
    for author_id in set(author_ids):
        if author_id not in badges:
            author = User.objects.filter(pk=author_id).first() if author_id is not None else None
            badges[author_id] = author_badge(author, competition)
        found.add(badges[author_id])
    for badge in (BADGE_COORDINATOR, BADGE_COMMITTEE):
        if badge in found:
            return badge
    return ""


@dataclass(frozen=True)
class Decision:
    kind: str
    thread: ForumThread
    post: ForumPost | None


def _current_decision(notice, thread, posts) -> Decision | None:
    """Decyzja, jeśli **nadal** jest prawdą – albo ``None``, gdy moderator zdążył ją zmienić.

    Stan czytamy z wpisu i wątku **teraz**. Zatwierdzenie, po którym moderator zdążył wpis ukryć,
    nie wychodzi; odrzucenie, które moderator cofnął, też nie – list mówiłby wtedy coś, co już nie
    jest prawdą, a przy zatwierdzeniu dawałby odnośnik do treści, której nikt nie widzi.
    """
    if thread is None:
        return None
    post = posts.get(notice.post_id) if notice.post_id else None
    published = thread.status == ModerationStatus.PUBLISHED
    still_true = {
        DecisionKind.POST_APPROVED: post is not None
        and post.status == ModerationStatus.PUBLISHED
        and published,
        DecisionKind.THREAD_APPROVED: published,
        DecisionKind.POST_REJECTED: post is not None and post.status == ModerationStatus.REJECTED,
        DecisionKind.THREAD_REJECTED: thread.status == ModerationStatus.REJECTED,
    }.get(notice.kind, False)
    return Decision(kind=notice.kind, thread=thread, post=post) if still_true else None


def _decision_lines(decision: Decision, competition) -> list[str]:
    """Wiersze listu o jednej decyzji. Woła się **w** ``language_for`` – to są zdania dla odbiorcy.

    Temat wątku stoi w liście wyłącznie wtedy, gdy wątek jest opublikowany – odrzucony wątek nie
    ma tematu, który wolno by powtórzyć. Przy odrzuceniu idzie uzasadnienie moderatora: to zdanie
    skierowane do autora, do jego własnej skrzynki, a nie treść wpisu.
    """
    thread, post = decision.thread, decision.post
    title = _short_title(thread.title)
    published = thread.status == ModerationStatus.PUBLISHED
    thread_link = _link(reverse("web:forum-thread", args=[thread.pk]), competition)
    mine_link = _link(reverse("web:forum-mine"), competition)
    if decision.kind == DecisionKind.POST_APPROVED:
        return [
            _("- Twój wpis w wątku „%(title)s” został zatwierdzony i jest widoczny dla innych.")
            % {"title": title},
            f"  {thread_link}",
        ]
    if decision.kind == DecisionKind.THREAD_APPROVED:
        return [
            _("- Twój wątek „%(title)s” został zatwierdzony i jest widoczny dla innych.") % {"title": title},
            f"  {thread_link}",
        ]
    if decision.kind == DecisionKind.POST_REJECTED:
        head = (
            _("- Twój wpis w wątku „%(title)s” nie został opublikowany.") % {"title": title}
            if published
            else _("- Twój wpis nie został opublikowany.")
        )
        return [
            head,
            _("  Uzasadnienie organizatora: %(note)s") % {"note": post.moderation_note},
            f"  {mine_link}",
        ]
    return [
        _("- Twój nowy wątek nie został opublikowany."),
        _("  Uzasadnienie organizatora: %(note)s") % {"note": thread.moderation_note},
        f"  {mine_link}",
    ]


def _member_subject(news: list[ThreadNews], decisions: list[Decision], competition, *, daily: bool) -> str:
    from .services import BADGE_COMMITTEE, BADGE_COORDINATOR

    if daily:
        return _subject(SUBJECT_DAILY, competition)
    if decisions and not news:
        return _subject(SUBJECT_DECISIONS, competition)
    if len(news) == 1 and not decisions:
        item = news[0]
        template = {
            BADGE_COORDINATOR: SUBJECT_REPLY_COORDINATOR,
            BADGE_COMMITTEE: SUBJECT_REPLY_COMMITTEE,
        }.get(item.badge, SUBJECT_REPLY)
        return _subject(template, competition, title=_short_title(item.thread.title))
    return _subject(SUBJECT_NEWS, competition)


def _send_member_mail(
    user, competition, news: list[ThreadNews], decisions: list[Decision], *, daily: bool
) -> None:
    from apps.accounts.preferences import language_for

    from .services import BADGE_COMMITTEE, BADGE_COORDINATOR

    unsubscribe = unsubscribe_url(user, SCOPE_ALL, competition)
    with language_for(user, competition):
        lines: list[str] = []
        if news:
            lines += [_("Nowe odpowiedzi w wątkach, które obserwujesz:"), ""]
            for item in news:
                who = {
                    BADGE_COORDINATOR: _("odpowiedział organizator"),
                    BADGE_COMMITTEE: _("odpowiedział członek komitetu"),
                }.get(item.badge, "")
                line = _("- „%(title)s” – nowe wpisy: %(count)s") % {
                    "title": _short_title(item.thread.title),
                    "count": item.count,
                }
                lines += [
                    f"{line} ({who})" if who else line,
                    "  "
                    + _link(
                        reverse("web:forum-thread", args=[item.thread.pk]) + "?strona=ostatnia", competition
                    ),
                    "  "
                    + _("Przestań obserwować ten wątek: %(link)s")
                    % {"link": unsubscribe_url(user, thread_scope(item.thread.pk), competition)},
                    "",
                ]
        if decisions:
            lines += [_("Decyzje organizatora w sprawie Twoich wpisów:"), ""]
            for decision in decisions:
                lines += [*_decision_lines(decision, competition), ""]
        lines += [
            _("Treści wpisów nie przesyłamy pocztą – forum można czytać wyłącznie po zalogowaniu."),
            _("Ustawienia powiadomień: %(link)s") % {"link": _settings_link(competition)},
            _("Wyłącz wszystkie listy z forum: %(link)s") % {"link": unsubscribe},
        ]
        subject = _member_subject(news, decisions, competition, daily=daily)
        body = _signed(lines, competition)
    _send(user, competition, subject, body, unsubscribe)
