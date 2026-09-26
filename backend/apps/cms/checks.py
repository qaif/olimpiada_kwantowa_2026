"""Kontrole konfiguracji części informacyjnej (``manage.py check``).

- ``cms.W010`` – token wewnętrznego API wersji na django CMS (``apps.cms.djcms_api``),
- ``cms.W011`` – ``RESERVED_SLUGS`` nie pokrywa pierwszych segmentów urlconfu (DJ-02 § 6),
- ``cms.W012`` – opublikowana strona Wagtaila pod adresem aplikacji (DJ-02 § 6, reguła S5).
"""

from __future__ import annotations

import re

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

#: Najkrótszy token, przy którym API w ogóle odpowiada. Trzydzieści dwa znaki to 192 bity przy
#: alfabecie base64 – tyle, ile generuje ``scripts/deploy.sh`` z zapasem. Krótszy sekret nie jest
#: „słabszym zabezpieczeniem”, tylko wyłączeniem API (``apps.cms.djcms_api.auth.api_enabled``).
DJCMS_TOKEN_MIN_LENGTH = 32


@checks.register(checks.Tags.security)
def check_djcms_token(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    """``cms.W010``: token ustawiony, ale za krótki – API jest przez to wyłączone.

    Ostrzeżenie, a nie błąd: pusty token jest stanem poprawnym (instalacja bez wersji ``dj.``),
    a krótki działa dokładnie tak samo jak pusty – nic nie wycieka, API odpowiada 404. Tyle że
    operator, który token wpisał, chciał API **włączyć**, więc bez tego komunikatu szukałby
    przyczyny pustych sekcji na ``dj.`` w zupełnie innym miejscu.
    """
    token = getattr(settings, "DJCMS_INTERNAL_TOKEN", "") or ""
    if token and len(token) < DJCMS_TOKEN_MIN_LENGTH:
        return [
            checks.Warning(
                f"DJCMS_INTERNAL_TOKEN ma {len(token)} znaków – API dla wersji django CMS "
                f"wymaga co najmniej {DJCMS_TOKEN_MIN_LENGTH} i do tego czasu odpowiada 404.",
                hint='Wygeneruj token: python -c "import secrets; print(secrets.token_urlsafe(36))".',
                id="cms.W010",
            )
        ]
    return []


@checks.register(checks.Tags.urls)
def check_reserved_slugs_cover_app_routes(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    """``cms.W011``: pierwszy segment adresu aplikacji, którego nie ma w ``RESERVED_SLUGS``.

    Test manifestu (``apps/core/tests/test_app_routes.py``) łapie to samo w CI; kontrola jest dla
    tego, kto dopisał wzorzec i uruchamia serwer lokalnie – dowie się od razu, a nie z czerwonego
    przebiegu. Ostrzeżenie, a nie błąd: brak rezerwacji niczego nie psuje, dopóki redaktor nie
    założy strony pod tym adresem, a błąd blokowałby ``migrate`` na produkcji.

    Wzorzec, którego manifest nie umie zaklasyfikować, zgłaszamy tym samym identyfikatorem –
    ``manage.py djcms_routes --check`` i tak padnie na nim w CI.
    """
    from apps.core.app_routes import build_manifest

    from .models import RESERVED_SLUGS

    try:
        manifest = build_manifest()
    except ImproperlyConfigured as exc:
        return [checks.Warning(str(exc), id="cms.W011")]
    missing = sorted(set(manifest.first_segments) - RESERVED_SLUGS)
    if not missing:
        return []
    return [
        checks.Warning(
            "Adresy aplikacji bez rezerwacji sluga: " + ", ".join(missing) + ". Redaktor mógłby "
            "założyć stronę drugiego poziomu pod tym adresem, a czytelnik zobaczyłby ekran aplikacji.",
            hint="Dopisz je do RESERVED_SLUGS w apps/cms/models.py.",
            id="cms.W011",
        )
    ]


def colliding_pages() -> list[tuple[str, str]]:
    """``(host witryny, ścieżka)`` opublikowanych stron, których adres należy do aplikacji.

    Dopasowanie to samo, co w Caddym po przełączeniu na djcms (``APP_RE`` i ``APP_RE_PREFIXED``
    z manifestu): pierwszy segment aplikacji, adres aplikacji pod stroną (``warsztaty/materialy``)
    i – przez regułę drugiego segmentu (DJ-02 § 1.2 D3) – strona trzeciego poziomu o slugu
    aplikacji (``/o-nas/login/``). Sprawdzamy dwa poziomy pod korzeniem każdej witryny: głębsze
    adresy żadnego z wyrażeń nie dotyczą, a zapytanie ma zostać tanie przy ``migrate``.
    """
    from wagtail.models import Page, Site

    from apps.core.app_routes import build_manifest, caddy_regexes

    app_re, app_re_prefixed = (re.compile(regex) for regex in caddy_regexes(build_manifest()))
    found: list[tuple[str, str]] = []
    for site in Site.objects.select_related("root_page").order_by("hostname", "port"):
        root = site.root_page
        pages = (
            Page.objects.live()
            .descendant_of(root)
            .filter(depth__in=(root.depth + 1, root.depth + 2))
            .only("url_path")
            .order_by("path")
        )
        for page in pages:
            path = "/" + page.url_path[len(root.url_path) :]
            if app_re.match(path) or app_re_prefixed.match(path):
                found.append((site.hostname, path))
    return found


@checks.register(checks.Tags.database)
def check_pages_under_app_routes(app_configs=None, databases=None, **kwargs) -> list[checks.CheckMessage]:
    """``cms.W012``: opublikowana strona pod adresem aplikacji – informacja przed eksportem do djcms.

    Takiej strony nie da się dziś obejrzeć (wygrywa urlconf ``web``) i import do djcms ją odrzuci
    (reguła S5). Najczęstsza przyczyna: slug założony, zanim ``RESERVED_SLUGS`` znało ten segment
    (DJ-02a dopisało siedemnaście). Kontrola bazodanowa (``Tags.database``) – Django uruchamia ją
    tylko przy ``migrate`` i ``check --database default``, a nie przy każdej komendzie.
    """
    if not databases:
        return []
    try:
        found = colliding_pages()
    except DatabaseError, ImproperlyConfigured:
        # Baza przed migracjami Wagtaila albo urlconf, którego manifest nie zna (to zgłasza W011).
        return []
    return [
        checks.Warning(
            f"Strona „{path}” witryny {host} leży pod adresem aplikacji: czytelnik jej nie zobaczy, "
            "a import do django CMS ją odrzuci.",
            hint="Zmień slug strony w /cms/ (Wagtail doda przekierowanie ze starego adresu).",
            id="cms.W012",
        )
        for host, path in found
    ]
