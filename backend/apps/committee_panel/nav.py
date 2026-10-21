"""Menu panelu komisji: sekcje, pozycje, pozycja bieżąca i liczniki (UX-PANEL-03 § 3).

Ten sam wzorzec, co ``apps/participant_panel/nav.py`` i ``apps/web/coordinator_nav.py`` – z tych samych
powodów w Pythonie, a nie w szablonie:

- nazwa adresu jest listą kandydatów, a nierozwiązana pozycja znika (``NoReverseMatch``) zamiast
  wywracać każdą stronę panelu,
- pozycja bieżąca wynika z **nazwy widoku** i – na kolejce ``/review/`` – z ``?tab=`` / ``?round=``,
  bo „Do oceny”, „Ocenione” i „Druga ocena” prowadzą pod ten sam ``request.path``,
- widoczność to **istniejące** warunki (D4): role z procesora ``roles`` (``is_reviewer``,
  ``is_appeals_committee``), przepustki Jitsi, uprawnienie do pokoi wideo, webinary, forum, materiały,
  nadanie tłumacza, 2FA. Żadnych nowych flag.

Menu **nie jest zabezpieczeniem**: dostęp rozstrzygają widoki. Tu decyduje się wyłącznie o tym, czego
nie pokazywać.

Zapytania menu: licznik przydziałów (jedno ``aggregate``, tylko recenzent), licznik kolejki reklamacji
(jedno ``COUNT``, tylko komisja odwoławcza) i licznik powiadomień (jedno ``COUNT``) – wszystkie
zapamiętane na żądaniu, więc kafle Startu ich nie powtarzają.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from . import services

#: Zakładki kolejki recenzenta (``apps.web.views.reviewer.REVIEW_TABS``) otwierane z menu.
QUEUE_TAB_KEYS = ("todo", "draft", "submitted", "cancelled")
#: Kotwica kart rozmów, pokoi i webinarów na Starcie.
MEETINGS_ANCHOR = "spotkania"


@dataclass(frozen=True)
class Item:
    """Pozycja menu – pola jak w ``participant_panel.nav.Item``.

    ``match`` – nazwy widoków (bez ``web:``), przy których pozycja jest bieżąca; wzorzec zakończony
    myślnikiem jest przedrostkiem. ``query`` – dopisek adresu (``tab=submitted``); pozycja z dopiskiem
    jest bieżąca tylko przy tych samych parametrach. ``when`` – warunek widoczności z kontekstu.
    """

    key: str
    label: object
    names: tuple[str, ...]
    icon: str
    match: tuple[str, ...] = ()
    query: str = ""
    fragment: str = ""
    badge: str = ""
    when: Callable[[dict], bool] | None = None


@dataclass(frozen=True)
class Group:
    key: str
    label: object
    items: tuple[Item, ...] = field(default_factory=tuple)
    when: Callable[[dict], bool] | None = None


# --- warunki widoczności (D4) -------------------------------------------------------------------


def _competition(ctx: dict):
    return getattr(ctx.get("request"), "competition", None)


def _reviewer(ctx: dict) -> bool:
    return bool(ctx.get("is_reviewer"))


def _appeals(ctx: dict) -> bool:
    return bool(ctx.get("is_appeals_committee"))


def _tiebreak(ctx: dict) -> bool:
    # Pozycja „Druga ocena” tylko u recenzenta, który ma choć jedną recenzję rundy rozjemczej – rundy 2
    # nie włącza flaga, tylko koordynator przy rozbieżności (``grading.services.assign_third_reviewer``).
    return _reviewer(ctx) and bool(ctx["counts"]["tiebreak"])


def _meetings(ctx: dict) -> bool:
    return services.jitsi_passes()


def _own_rooms(ctx: dict) -> bool:
    profile = ctx.get("member")
    return services.jitsi_passes() and bool(getattr(profile, "video_room_issuer", False))


def _webinars(ctx: dict) -> bool:
    from apps.webinars.services import available

    return available(_competition(ctx))


def _forum(ctx: dict) -> bool:
    return bool(ctx.get("can_use_forum"))


def _workshop_materials(ctx: dict) -> bool:
    competition = _competition(ctx)
    if competition is None or not competition.has_feature("workshop_materials"):
        return False
    return bool(ctx.get("workshop_materials_available"))


def _translator(ctx: dict) -> bool:
    from apps.translation_review.services import is_translator

    request = ctx.get("request")
    return is_translator(getattr(request, "user", None))


def _two_factor(ctx: dict) -> bool:
    from apps.accounts.twofactor import is_enabled

    return is_enabled()


GROUPS: tuple[Group, ...] = (
    Group(
        "start",
        _("Start"),
        (
            Item("dashboard", _("Pulpit"), ("web:committee-start",), "home", match=("committee-start",)),
            Item(
                "notifications",
                _("Powiadomienia"),
                ("web:committee-notifications",),
                "bell",
                match=("committee-notifications", "committee-notification-"),
                badge="notifications",
            ),
        ),
    ),
    Group(
        "grading",
        _("Ocenianie"),
        (
            Item(
                "todo",
                _("Do oceny"),
                ("web:review-list",),
                "task",
                match=("review-list", "review-detail", "review-compare"),
                badge="todo",
            ),
            Item("graded", _("Ocenione"), ("web:review-list",), "check", query="tab=submitted"),
            Item(
                "tiebreak",
                _("Druga ocena (runda rozjemcza)"),
                ("web:review-list",),
                "scale",
                query="round=2",
                badge="tiebreak",
                when=_tiebreak,
            ),
            Item(
                "materials",
                _("Materiały dla komisji"),
                ("web:committee-materials",),
                "file",
                match=("committee-materials", "problem-model-solution"),
            ),
        ),
        when=_reviewer,
    ),
    Group(
        "appeals",
        _("Komisja odwoławcza"),
        (
            Item(
                "appeals",
                _("Reklamacje do rozpatrzenia"),
                ("web:appeals",),
                "shield",
                match=("appeals", "appeal-decide"),
                badge="appeals",
            ),
        ),
        when=_appeals,
    ),
    Group(
        "meetings",
        _("Spotkania"),
        (
            Item(
                "interviews",
                _("Rozmowy i pokoje komisji"),
                ("web:committee-start",),
                "calendar",
                fragment=MEETINGS_ANCHOR,
                when=_meetings,
            ),
            Item(
                "video_rooms",
                _("Moje pokoje wideo"),
                ("web:committee-video-rooms",),
                "video",
                match=("committee-video-room",),
                when=_own_rooms,
            ),
            Item(
                "webinars",
                _("Webinary"),
                ("web:webinars",),
                "play",
                match=("webinars", "webinar-"),
                when=_webinars,
            ),
        ),
    ),
    Group(
        "messages",
        _("Wiadomości i materiały"),
        (
            Item("forum", _("Forum"), ("web:forum",), "forum", match=("forum", "forum-"), when=_forum),
            Item(
                "workshop_materials",
                _("Materiały z warsztatów"),
                ("web:workshop-materials",),
                "archive",
                match=("workshop-materials", "workshop-material-"),
                when=_workshop_materials,
            ),
            Item(
                "translations",
                _("Tłumaczenia interfejsu"),
                ("web:translations",),
                "chat",
                match=("translations", "translation-"),
                when=_translator,
            ),
            Item("support", _("Zgłoś problem"), ("web:support-new",), "help", match=("support-",)),
        ),
    ),
    Group(
        "account",
        _("Konto"),
        (
            Item("profile", _("Profil i dane"), ("web:account-profile",), "user", match=("account-profile",)),
            Item("password", _("Hasło"), ("web:password-change",), "key", match=("password-change",)),
            Item(
                "two_factor",
                _("Weryfikacja dwuetapowa"),
                ("web:twofactor-setup",),
                "lock",
                match=("twofactor-",),
                when=_two_factor,
            ),
        ),
    ),
)


def resolve(names: tuple[str, ...]) -> str | None:
    for name in names:
        try:
            return reverse(name)
        except NoReverseMatch:
            continue
    return None


def _query_matches(item: Item, request) -> bool:
    """Pozycja z dopiskiem (``tab=submitted``) jest bieżąca przy tych samych parametrach żądania."""
    get = getattr(request, "GET", {})
    for pair in item.query.split("&"):
        key, _sep, value = pair.partition("=")
        if get.get(key, "") != value:
            return False
    return True


def _matches(item: Item, url_name: str, request) -> bool:
    if item.query:
        return url_name == "review-list" and _query_matches(item, request)
    if item.key == "todo" and url_name == "review-list":
        # „Do oceny” to kolejka bez wybranej zakładki „Ocenione” i bez filtra rundy rozjemczej –
        # dla tych dwóch widoków bieżąca jest ich własna pozycja.
        get = getattr(request, "GET", {})
        return get.get("tab", "") != "submitted" and get.get("round", "") != "2"
    if item.fragment:
        return False
    return any(
        url_name.startswith(pattern) if pattern.endswith("-") else url_name == pattern
        for pattern in item.match
    )


def seed_roles(context) -> None:
    """Profil z procesora ``roles`` do pamięci żądania – bez powtórnego ``has_role`` w menu.

    Procesor już rozstrzygnął ``is_reviewer`` i ``is_appeals_committee`` tymi samymi funkcjami, a profil
    komitetu (``user.committee_member``) Django trzyma po pierwszym odczycie na obiekcie konta.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if request is None or user is None or "is_reviewer" not in context:
        return
    memo = services._memo(request)
    profile = getattr(user, "committee_member", None) if user.is_authenticated else None
    memo.setdefault("reviewer", profile if context.get("is_reviewer") else None)
    memo.setdefault("appeals", profile if context.get("is_appeals_committee") else None)


def navigation(context) -> dict:
    """Kontekst ``committee_panel/_nav.html``: sekcje, pozycja bieżąca, liczniki."""
    from apps.notifications.services import unread_count

    request = context.get("request")
    seed_roles(context)
    match = getattr(request, "resolver_match", None)
    url_name = getattr(match, "url_name", "") or ""
    is_reviewer = bool(context.get("is_reviewer"))
    is_appeals = bool(context.get("is_appeals_committee"))
    counts = services.review_counts(request) if is_reviewer and request is not None else {"tiebreak": 0}
    counters = {
        "notifications": unread_count(request) if request is not None else 0,
        "todo": counts.get("todo", 0),
        "tiebreak": counts.get("tiebreak_open", 0),
        "appeals": services.appeals_count(request) if is_appeals and request is not None else 0,
    }
    ctx = {
        key: context.get(key)
        for key in (
            "request",
            "is_reviewer",
            "is_appeals_committee",
            "can_use_forum",
            "workshop_materials_available",
        )
    }
    ctx["counts"] = counts
    ctx["member"] = services.member(request) if request is not None else None
    groups = []
    current = None
    for group in GROUPS:
        if group.when is not None and not group.when(ctx):
            continue
        items = []
        for item in group.items:
            if item.when is not None and not item.when(ctx):
                continue
            url = resolve(item.names)
            if url is None:
                continue
            if item.query:
                url = f"{url}?{item.query}"
            if item.fragment:
                url = f"{url}#{item.fragment}"
            active = _matches(item, url_name, request)
            row = {
                "key": item.key,
                "label": item.label,
                "url": url,
                "icon": item.icon,
                "active": active,
                "badge": counters.get(item.badge) or None,
            }
            if active and current is None:
                current = row
            items.append(row)
        if items:
            groups.append({"key": group.key, "label": group.label, "items": items})
    for group in groups:
        for row in group["items"]:
            row["active"] = row is current
    return {
        "nav_groups": groups,
        "nav_current": current,
        "nav_unread_notifications": counters["notifications"],
        "nav_notifications_url": resolve(("web:committee-notifications",)) or "",
        # Suma na zwiniętym „Menu komisji” (telefon): powiadomienia, prace do oceny i reklamacje.
        "nav_badge_total": counters["notifications"] + counters["todo"] + counters["appeals"],
    }
