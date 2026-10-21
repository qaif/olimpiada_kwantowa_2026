"""Menu panelu uczestnika: sekcje, pozycje, pozycja bieżąca i liczniki (UX-PANEL-01 § 3).

Odpowiednik ``apps/web/coordinator_nav.py`` dla uczestnika i z tych samych powodów **w Pythonie**:

- nazwa adresu jest listą kandydatów, a nierozwiązana pozycja znika (``NoReverseMatch``) zamiast
  wywracać każdą stronę panelu,
- pozycja bieżąca wynika z **nazwy widoku** (i – na pulpicie – z ``?tab=``), a nie z porównania
  ścieżek: ``/me/?tab=wyniki`` i ``/me/`` to ten sam ``request.path``,
- widoczność pozycji to **istniejące** warunki funkcji (D2): procesory kontekstu (``chat_visible``,
  ``can_use_forum``, ``workshop_materials_available``) i predykaty aplikacji (webTEX, notatniki,
  webinary, zaświadczenie, 2FA). Żaden z nich nie pyta bazy – flaga konkursu jest polem wiersza,
  który ``CompetitionMiddleware`` już trzyma. Jedyne zapytanie menu to licznik powiadomień.

Menu **nie jest zabezpieczeniem**: dostęp rozstrzygają widoki. Tu decyduje się wyłącznie o tym,
czego nie pokazywać, żeby nie prowadzić do ekranu, którego nie ma.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

#: Zakładki pulpitu (``apps.web.views.participant.PANEL_TABS``) – pozycje menu, które je otwierają.
PANEL_TAB_KEYS = ("zadania", "wyniki", "reklamacje", "zgody")


@dataclass(frozen=True)
class Item:
    """Pozycja menu.

    ``match`` – nazwy widoków (bez przestrzeni ``web:``), przy których pozycja jest bieżąca; wzorzec
    zakończony myślnikiem jest przedrostkiem. ``tab`` – zakładka pulpitu (pozycja bieżąca tylko przy
    tej zakładce). ``when`` – warunek widoczności liczony z kontekstu szablonu i żądania.
    """

    key: str
    label: object
    names: tuple[str, ...]
    icon: str
    match: tuple[str, ...] = ()
    tab: str = ""
    query: str = ""
    fragment: str = ""
    badge: str = ""
    external: bool = False
    when: Callable[[dict], bool] | None = None


@dataclass(frozen=True)
class Group:
    key: str
    label: object
    items: tuple[Item, ...] = field(default_factory=tuple)


# --- warunki widoczności (D2) -------------------------------------------------------------------


def _competition(ctx: dict):
    return getattr(ctx.get("request"), "competition", None)


def _chat(ctx: dict) -> bool:
    return bool(ctx.get("chat_visible"))


def _forum(ctx: dict) -> bool:
    return bool(ctx.get("can_use_forum"))


def _workshop_materials(ctx: dict) -> bool:
    # Leniwe i z pamięci podręcznej (``apps.workshop_materials.availability``); bez flagi – bez odczytu.
    competition = _competition(ctx)
    if competition is None or not competition.has_feature("workshop_materials"):
        return False
    return bool(ctx.get("workshop_materials_available"))


def _webinars(ctx: dict) -> bool:
    from apps.webinars.services import available

    return available(_competition(ctx))


def notebooks_available(competition) -> bool:
    from apps.notebooks.services import is_enabled

    return is_enabled(competition)


def latex_available(competition) -> bool:
    from apps.webtex.services import available

    return available(competition)


def _notebooks(ctx: dict) -> bool:
    return notebooks_available(_competition(ctx))


def _latex(ctx: dict) -> bool:
    return latex_available(_competition(ctx))


def _student_status(ctx: dict) -> bool:
    from apps.student_status.models import enabled

    return enabled(_competition(ctx))


def _two_factor(ctx: dict) -> bool:
    from apps.accounts.twofactor import is_enabled

    return is_enabled()


GROUPS: tuple[Group, ...] = (
    Group(
        "start",
        _("Start"),
        (
            Item("dashboard", _("Pulpit"), ("web:me",), "home"),
            Item(
                "notifications",
                _("Powiadomienia"),
                ("web:participant-notifications",),
                "bell",
                match=("participant-notifications", "participant-notification-"),
                badge="notifications",
            ),
        ),
    ),
    Group(
        "contest",
        _("Zawody"),
        (
            Item(
                "tasks",
                _("Zadania i moje prace"),
                ("web:me",),
                "task",
                tab="zadania",
                query="tab=zadania",
                fragment="sekcja",
                match=("quiz-", "interview-", "proctoring-"),
            ),
            Item(
                "results",
                _("Wyniki"),
                ("web:me",),
                "trophy",
                tab="wyniki",
                query="tab=wyniki",
                fragment="sekcja",
                match=("participant-feedback",),
            ),
            Item(
                "appeals",
                _("Reklamacje"),
                ("web:me",),
                "scale",
                tab="reklamacje",
                query="tab=reklamacje",
                fragment="sekcja",
                match=("appeal-",),
            ),
            Item(
                "certificates",
                _("Dyplomy"),
                ("web:participant-certificates",),
                "award",
                match=("participant-certificates", "participant-certificate-"),
            ),
            Item(
                "archive",
                _("Archiwum zadań"),
                ("web:participant-archive",),
                "archive",
                match=("participant-archive",),
            ),
        ),
    ),
    Group(
        "learning",
        _("Nauka"),
        (
            Item(
                "calendar",
                _("Kalendarz"),
                ("web:participant-calendar",),
                "calendar",
                match=("participant-calendar",),
            ),
            Item(
                "materials",
                _("Materiały z warsztatów"),
                ("web:workshop-materials",),
                "play",
                match=("workshop-materials", "workshop-material-"),
                when=_workshop_materials,
            ),
            Item(
                "webinars",
                _("Webinary"),
                ("web:webinars",),
                "video",
                match=("webinars", "webinar-"),
                when=_webinars,
            ),
            Item(
                "notebooks",
                _("Notatniki Qiskit"),
                ("web:participant-notebooks",),
                "atom",
                match=("participant-notebooks", "participant-notebook"),
                when=_notebooks,
            ),
            Item(
                "latex",
                _("Edytor LaTeX (webTEX)"),
                ("web:participant-latex",),
                "tex",
                match=("participant-latex", "problem-latex"),
                when=_latex,
            ),
        ),
    ),
    Group(
        "messages",
        _("Wiadomości"),
        (
            Item(
                "chat",
                _("Wiadomości"),
                ("web:chat",),
                "chat",
                match=("chat", "chat-"),
                badge="chat",
                when=_chat,
            ),
            Item(
                "announcements",
                _("Ogłoszenia"),
                ("web:me",),
                "megaphone",
                fragment="ogloszenia-organizatora",
            ),
            Item("forum", _("Forum"), ("web:forum",), "forum", match=("forum", "forum-"), when=_forum),
            Item("support", _("Zgłoś problem"), ("web:support-new",), "help", match=("support-",)),
        ),
    ),
    Group(
        "account",
        _("Konto"),
        (
            Item("profile", _("Profil i dane"), ("web:profile",), "user", match=("profile",)),
            Item(
                "consents",
                _("Zgody"),
                ("web:me",),
                "shield",
                tab="zgody",
                query="tab=zgody",
                fragment="sekcja",
            ),
            Item(
                "student_status",
                _("Zaświadczenie o statusie ucznia"),
                ("web:student-status",),
                "file",
                match=("student-status",),
                when=_student_status,
            ),
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


def _matches(item: Item, url_name: str, tab: str) -> bool:
    if item.tab:
        return url_name == "me" and tab == item.tab
    if item.key == "dashboard":
        # Pulpit bez wybranej zakładki (albo z nieznaną – widok otwiera wtedy zakładkę domyślną,
        # ale uczestnik nie prosił o „Zadania”, tylko wszedł na pulpit).
        return url_name == "me" and tab not in PANEL_TAB_KEYS
    return any(
        url_name.startswith(pattern) if pattern.endswith("-") else url_name == pattern
        for pattern in item.match
    )


def navigation(context) -> dict:
    """Kontekst ``participant_panel/_nav.html``: sekcje, pozycja bieżąca, liczniki.

    ``context`` to kontekst szablonu (procesory kontekstu już w nim są). Zapytania: licznik
    powiadomień – raz na żądanie (``notifications.services.unread_count``).
    """
    from apps.notifications.services import unread_count

    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    url_name = getattr(match, "url_name", "") or ""
    tab = request.GET.get("tab", "") if request is not None else ""
    counters = {
        "notifications": unread_count(request) if request is not None else 0,
        "chat": int(context.get("chat_unread") or 0),
    }
    ctx = {key: context.get(key) for key in ("request", "chat_visible", "can_use_forum")}
    # Leniwa wartość procesora kontekstu – czytana dopiero w warunku, po fladze.
    ctx["workshop_materials_available"] = _LazyContext(context, "workshop_materials_available")
    groups = []
    current = None
    for group in GROUPS:
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
            active = _matches(item, url_name, tab)
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
            # Jedna pozycja bieżąca na stronę – „Pulpit” i „Ogłoszenia” prowadzą pod ten sam adres.
            row["active"] = row is current
    return {
        "nav_groups": groups,
        "nav_current": current,
        "nav_unread_notifications": counters["notifications"],
        "nav_notifications_url": resolve(("web:participant-notifications",)) or "",
        # Suma na zwiniętym „Menu uczestnika” (telefon) – zwinięcie nie może chować tego, co czeka.
        "nav_badge_total": (counters["notifications"] or 0)
        + (counters["chat"] if context.get("chat_visible") else 0),
    }


class _LazyContext:
    """Odczyt klucza kontekstu dopiero przy ``bool()`` – wartość procesora bywa leniwa i kosztowna."""

    def __init__(self, context, key: str):
        self.context = context
        self.key = key

    def __bool__(self) -> bool:
        return bool(self.context.get(self.key))
