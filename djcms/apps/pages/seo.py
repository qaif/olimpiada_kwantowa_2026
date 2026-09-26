"""SEO witryny konkursu w trybie ``primary`` (DJ-02 § 8): canonical, ``robots.txt``, ``sitemap.xml``, GA4.

**Adresy bezwzględne** budujemy wyłącznie z adresu publicznego konkursu z rejestru
(``CompetitionSite.public_origin`` + ``public_path_prefix`` = ``public_base``), nigdy z
``Site.domain`` ani z nagłówka ``Host``: ``Site.domain`` to tylko unikalna etykieta dla
``django.contrib.sites``, a ``Host`` pochodzi od klienta. Dlatego ``sitemap.xml`` i ``robots.txt``
są własne – ``cms.sitemaps.CMSSitemap`` składa adresy z ``Site.domain`` i nie zna prefiksu ścieżki
konkursu (ustalenia § 5.1). Konkurs bez adresu publicznego (bramka aplikacji głównej zamknięta)
nie ma adresu kanonicznego ani mapy witryny.

Ścieżka strony względem korzenia konkursu to ``PageUrl.path`` (``""`` = strona główna) w kształcie
adresów ``cms.urls`` (``/<path>/``, ``APPEND_SLASH``), więc adres nie zależy od prefiksu skryptu
wątku – ten sam wynik w żądaniu, w komendzie i w teście.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from xml.sax.saxutils import escape

from django.conf import settings
from django.db.models import Max, Q
from django.utils import timezone

from . import mode
from .validation import path_collides_with_app

LANGUAGE = "pl"

#: Kształt identyfikatora GA4 – ten sam co ``GA_MEASUREMENT_ID_PATTERN`` aplikacji głównej
#: (``backend/apps/cms/models.py``). Wartość z API trafia do adresu skryptu i do atrybutów, więc
#: sprawdzamy ją drugi raz: odpowiedź API nie jest źródłem, któremu szablon ufa ślepo.
GA_MEASUREMENT_ID_RE = re.compile(r"^G-[A-Z0-9]{6,}$")

#: Atrybut żądania: strona wczytuje Google Analytics – CSP dokłada wtedy hosty GA
#: (``apps.pages.middleware.ContentSecurityPolicyMiddleware``). Ustawia go wyłącznie
#: ``analytics_id`` (szablon ``dj/base.html``), więc hosty dostaje tylko odpowiedź, która tag ma.
ANALYTICS_REQUEST_ATTR = "dj_analytics"

#: Opis domyślny – wyłącznie na wypadek braku ``chrome.seo.default_description`` (martwe API);
#: ten sam tekst co w ``backend/templates/base.html``.
FALLBACK_DESCRIPTION = (
    "Ogólnopolska olimpiada dla uczniów szkół ponadpodstawowych: terminy etapów, zadania, wyniki "
    "oraz panel uczestnika, recenzenta i komitetu."
)

SITEMAP_MAX_AGE = 3600
ROBOTS_MAX_AGE = 3600


# --- adresy --------------------------------------------------------------------------------------


def page_relative_url(path: str | None) -> str:
    """``PageUrl.path`` → adres względem korzenia konkursu: ``""`` → ``/``, ``a/b`` → ``/a/b/``."""
    path = (path or "").strip("/")
    return f"/{path}/" if path else "/"


def public_url(competition, relative: str) -> str:
    """Adres bezwzględny w konkursie (``public_base`` + ścieżka); pusty, gdy konkurs go nie ma."""
    base = getattr(competition, "public_base", "") if competition is not None else ""
    if not base:
        return ""
    return f"{base.rstrip('/')}{relative if relative.startswith('/') else '/' + relative}"


def _current_page(request):
    page = getattr(request, "current_page", None)
    # ``SimpleLazyObject`` (``PrefixedCurrentPageMiddleware``) – ``bool()`` rozwija go do strony/None.
    return page if page else None


def canonical_url(request) -> str:
    """``<link rel="canonical">`` / ``og:url`` bieżącej strony CMS – bez zapytania, pusty poza stroną."""
    page = _current_page(request)
    competition = getattr(request, "competition_site", None)
    if page is None or competition is None:
        return ""
    if page.is_home:
        return public_url(competition, "/")
    path = page.get_path(LANGUAGE)
    if path is None:  # strona bez osiągalnego adresu (np. nieopublikowany rodzic)
        return ""
    return public_url(competition, page_relative_url(path))


# --- Google Analytics 4 --------------------------------------------------------------------------


def analytics_id(request, chrome) -> str:
    """Identyfikator GA4 dla tej odsłony albo ``""``; niepusty oznacza żądanie do CSP (atrybut).

    Warunki (wszystkie):

    - tryb ``primary`` – w podglądzie GA byłoby dublem odsłon tych samych adresów w statystykach
      organizatora (tę samą stronę liczy wtedy Wagtail), a podgląd ogląda wyłącznie redakcja,
    - nie personel djcms – polityka redaktora nie ma hostów GA (i nie liczymy redakcji),
    - ``chrome.site.ga_measurement_id`` w kształcie ``G-…`` (ten sam warunek co ``base.html``
      aplikacji głównej: bez identyfikatora ani tagu, ani pytania o zgodę).
    """
    if request is None or not mode.is_primary(request):
        return ""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        return ""
    site = getattr(chrome, "site", None) or {}
    value = site.get("ga_measurement_id") if isinstance(site, dict) else None
    if not isinstance(value, str) or not GA_MEASUREMENT_ID_RE.match(value.strip()):
        return ""
    setattr(request, ANALYTICS_REQUEST_ATTR, True)
    return value.strip()


# --- robots.txt ----------------------------------------------------------------------------------

PREVIEW_ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def _private_prefixes() -> list[str]:
    """Segmenty paneli aplikacji (``app_routes.json`` → ``private_prefixes``, w tym ``djcms``)."""
    return sorted(
        {segment.strip("/") for segment in settings.DJ_APP_ROUTES.get("private_prefixes", []) if segment}
    )


def _hosted_path_competitions(competition):
    """Konkursy pod prefiksem ścieżki obsługiwane pod hostem ``competition`` (``hosts_path_prefixes``)."""
    from apps.sites.models import CompetitionSite, RoutingMode

    if competition is None or not competition.hosts_path_prefixes:
        return []
    return list(
        CompetitionSite.objects.filter(is_active=True, routing_mode=RoutingMode.PATH)
        .exclude(pk=competition.pk)
        .exclude(path_prefix="")
        .order_by("path_prefix")
    )


def robots_txt(request) -> str:
    """``robots.txt`` żądania: ``Disallow: /`` w podglądzie, w ``primary`` – panele i mapy witryn.

    Robot czyta ``/robots.txt`` z korzenia hosta, więc odpowiedź pod korzeniem hosta platformy
    obejmuje także konkursy pod prefiksem ścieżki na tym hoście (``/druga/admin/``,
    ``Sitemap: …/druga/sitemap.xml``) – bez listy prefiksów w Caddym. Żądanie pod prefiksem
    (``/druga/robots.txt`` – robotom niepotrzebne, ale Caddy je tu przyśle) opisuje tylko swój
    konkurs, z prefiksem.

    Zakazy dotyczą wyłącznie paneli aplikacji (Wagtail nie ma dziś ``robots.txt``), więc nic, co jest
    dziś indeksowane, nie znika z indeksu po przełączeniu.
    """
    if not mode.is_primary(request):
        return PREVIEW_ROBOTS_TXT
    competition = getattr(request, "competition_site", None)
    request_prefix = getattr(request, "competition_path_prefix", "")
    scopes: list[tuple[str, object]] = [(f"/{request_prefix}" if request_prefix else "", competition)]
    if not request_prefix:
        scopes += [(f"/{other.path_prefix}", other) for other in _hosted_path_competitions(competition)]

    private = _private_prefixes()
    lines = ["User-agent: *"]
    for prefix, _item in scopes:
        lines += [f"Disallow: {prefix}/{segment}/" for segment in private]
    sitemaps = [url for _prefix, item in scopes if (url := public_url(item, "/sitemap.xml"))]
    if sitemaps:
        lines.append("")
        lines += [f"Sitemap: {url}" for url in sitemaps]
    return "\n".join(lines) + "\n"


# --- sitemap.xml ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: datetime | None


def sitemap_entries(competition) -> list[SitemapEntry]:
    """Opublikowane strony witryny konkursu, które robot może zobaczyć.

    Tak jak ``CMSSitemap``: bez stron z ``login_required``, bez stron-przekierowań (``redirect``)
    i bez stron bez osiągalnego adresu; dodatkowo bez stron pod adresem aplikacji (S5 – takiego
    adresu djcms i tak nie dostaje). ``lastmod`` = chwila publikacji wersji (djangocms-versioning,
    ``Version.modified``). Opublikowaną treść daje domyślny menedżer ``PageContent`` (versioning).
    """
    from cms.models import PageContent, PageUrl

    if competition is None or not competition.public_base:
        return []
    contents = (
        PageContent.objects.filter(page__site=competition.site, language=LANGUAGE, page__login_required=False)
        .filter(Q(redirect="") | Q(redirect__isnull=True))
        .select_related("page")
        .annotate(published_at=Max("versions__modified"))
        .order_by("page__path")
    )
    rows = list(contents)
    paths = dict(
        PageUrl.objects.filter(page__in=[row.page for row in rows], language=LANGUAGE).values_list(
            "page_id", "path"
        )
    )
    entries = []
    for row in rows:
        page = row.page
        if page.is_home:
            relative = "/"
        else:
            path = paths.get(page.pk)
            if not path or path_collides_with_app(path):
                continue
            relative = page_relative_url(path)
        entries.append(SitemapEntry(public_url(competition, relative), row.published_at))
    return entries


def sitemap_xml(entries: list[SitemapEntry]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for entry in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(entry.loc)}</loc>")
        if entry.lastmod is not None:
            lines.append(f"    <lastmod>{timezone.localtime(entry.lastmod).date().isoformat()}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"
