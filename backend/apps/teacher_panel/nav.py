"""Menu panelu nauczyciela (opiekuna szkolnego): sekcje, pozycje i pozycja bieżąca (UX-PANEL-02 § 3).

Ten sam wzorzec, co ``apps/participant_panel/nav.py`` – i te same klocki: ``Item``, ``Group``
i ``resolve`` są **importowane** stamtąd, a nie kopiowane ani przenoszone (D1). Równolegle powstaje
panel komisji na tym samym wzorcu; wspólny plik zmieniany przez dwa zadania naraz byłby konfliktem,
a import niczego w nim nie zmienia.

Pozycja bieżąca wynika z nazwy widoku (``request.resolver_match.url_name``), widoczność –
z **istniejących** warunków funkcji (D2), liczonych z kontekstu szablonu bez zapytań: flaga
konkursu jest polem wiersza, który ``CompetitionMiddleware`` już trzyma, a ``promo_available``
i ``workshop_materials_available`` są leniwymi wartościami z pamięci podręcznej, czytanymi dopiero
po fladze. Menu nie jest zabezpieczeniem – dostęp rozstrzygają widoki.

Podpisy celowo **nie** zawierają „Menu panelu…” – to jest podpis menu koordynatora, a testy
złote pilnują, że nie pojawia się on poza jego panelem.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.participant_panel.nav import Group, Item, resolve


def _competition(ctx: dict):
    return getattr(ctx.get("request"), "competition", None)


def _workshop_materials(ctx: dict) -> bool:
    competition = _competition(ctx)
    if competition is None or not competition.has_feature("workshop_materials"):
        return False
    return bool(ctx.get("workshop_materials_available"))


def _posters(ctx: dict) -> bool:
    return bool(ctx.get("promo_available"))


def _school_statistics(ctx: dict) -> bool:
    from apps.school_stats.services import enabled

    return enabled(_competition(ctx))


def _two_factor(ctx: dict) -> bool:
    from apps.accounts.twofactor import is_enabled

    return is_enabled()


#: Ikony: ``pp-…`` z arkusza panelu uczestnika, ``tp-…`` z ``teacher_panel/_icons.html``.
GROUPS: tuple[Group, ...] = (
    Group(
        "start",
        _("Start"),
        (Item("dashboard", _("Pulpit"), ("web:supervisor",), "pp-i-home", match=("supervisor",)),),
    ),
    Group(
        "students",
        _("Uczniowie"),
        (
            Item(
                "students",
                _("Moi uczniowie"),
                ("web:supervisor-students",),
                "tp-i-users",
                match=("supervisor-students", "supervisor-resend-invitation"),
            ),
            Item(
                "import",
                _("Zaproś uczniów z listy"),
                ("web:supervisor-import",),
                "tp-i-upload",
                match=("supervisor-import",),
            ),
            Item(
                "progress",
                _("Postęp klasy"),
                ("web:supervisor-progress",),
                "tp-i-chart",
                match=("supervisor-progress",),
            ),
        ),
    ),
    Group(
        "learning",
        _("Materiały i warsztaty"),
        (
            Item(
                "materials",
                _("Materiały z warsztatów"),
                ("web:workshop-materials",),
                "pp-i-play",
                match=("workshop-materials", "workshop-material-"),
                when=_workshop_materials,
            ),
            Item(
                "posters",
                _("Plakaty do powieszenia w szkole"),
                ("web:posters",),
                "tp-i-poster",
                match=("posters", "poster-download"),
                when=_posters,
            ),
            Item("calendar", _("Kalendarz konkursu (.ics)"), ("web:calendar-feed",), "pp-i-calendar"),
        ),
    ),
    Group(
        "school",
        _("Szkoła"),
        (
            Item(
                "school",
                _("Udział szkoły i zaświadczenia"),
                ("web:supervisor-school",),
                "tp-i-school",
                match=("supervisor-school", "supervisor-participation", "supervisor-certificate-download"),
            ),
            Item(
                "statistics",
                _("Statystyki szkoły"),
                ("web:supervisor-statistics",),
                "pp-i-trophy",
                match=("supervisor-statistics", "supervisor-statistics-report"),
                when=_school_statistics,
            ),
        ),
    ),
    Group(
        "help",
        _("Pomoc"),
        (Item("support", _("Zgłoś problem"), ("web:support-new",), "pp-i-help", match=("support-",)),),
    ),
    Group(
        "account",
        _("Konto"),
        (
            Item(
                "profile",
                _("Profil i dane"),
                ("web:account-profile",),
                "pp-i-user",
                match=("account-profile",),
            ),
            Item("password", _("Hasło"), ("web:password-change",), "pp-i-key", match=("password-change",)),
            Item(
                "two_factor",
                _("Weryfikacja dwuetapowa"),
                ("web:twofactor-setup",),
                "pp-i-lock",
                match=("twofactor-",),
                when=_two_factor,
            ),
        ),
    ),
)


def _matches(item: Item, url_name: str) -> bool:
    return any(
        url_name.startswith(pattern) if pattern.endswith("-") else url_name == pattern
        for pattern in item.match
    )


def navigation(context) -> dict:
    """Kontekst ``teacher_panel/_nav.html``: sekcje i pozycja bieżąca. Zero zapytań do bazy."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    url_name = getattr(match, "url_name", "") or ""
    ctx = {"request": request}
    # Leniwe wartości procesorów kontekstu – czytane dopiero w warunku, po fladze.
    for key in ("workshop_materials_available", "promo_available"):
        ctx[key] = _LazyContext(context, key)
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
            active = current is None and _matches(item, url_name)
            row = {"key": item.key, "label": item.label, "url": url, "icon": item.icon, "active": active}
            if active:
                current = row
            items.append(row)
        if items:
            groups.append({"key": group.key, "label": group.label, "items": items})
    return {"nav_groups": groups, "nav_current": current}


class _LazyContext:
    """Odczyt klucza kontekstu dopiero przy ``bool()`` – wartość procesora bywa leniwa i kosztowna."""

    def __init__(self, context, key: str):
        self.context = context
        self.key = key

    def __bool__(self) -> bool:
        return bool(self.context.get(self.key))
