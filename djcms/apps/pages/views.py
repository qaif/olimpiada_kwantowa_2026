"""Widoki aplikacyjne djcms spoza drzewa stron.

- ``/djcms/healthz/`` – healthcheck compose'a,
- ``/djcms/preview/`` – włączenie/wyłączenie podglądu (ciasteczko ``djcms_view``, DJ-02 D1, S8),
- ``/robots.txt`` i ``/sitemap.xml`` witryny konkursu wg trybu (DJ-02 § 8, S7),
- strony błędów 404 (z ramą konkursu) – ``handler404`` w ``config/urls.py``.
"""

from __future__ import annotations

import logging

from django.db import connection
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from . import mode, preview, seo
from .middleware import APP_PATH_PREFIX

logger = logging.getLogger(__name__)

#: Dawna stała (DJ-01: ``robots.txt`` zawsze zakazywał wszystkiego) – dziś treść w trybie ``preview``.
ROBOTS_TXT = seo.PREVIEW_ROBOTS_TXT


def _ping_database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


@never_cache
@require_GET
def healthz(request):
    """Healthcheck compose'a: ``SELECT 1`` na bazie djcms – i **nic więcej**.

    Poza rozstrzyganiem konkursu (``apps.sites.middleware``): odpowiada pod każdym hostem, także przy
    pustym rejestrze konkursów – stan rejestru nie jest stanem zdrowia kontenera.

    Świadomie **nie** pyta API aplikacji głównej: niedostępny ``web`` to stan, w którym djcms ma
    działać dalej i degradować sekcje żywe (§ 8.3), a nie kontener „chory” do restartu. Gdyby
    healthcheck zależał od API, awaria głównego serwisu restartowałaby w pętli także djcms.
    """
    try:
        db_ok = _ping_database()
    except Exception:  # noqa: BLE001 - każdy błąd bazy = „niezdrowy”, szczegóły tylko w logu
        logger.warning("healthz: baza djcms niedostępna", exc_info=True)
        db_ok = False
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=200 if db_ok else 503)


# --- robots.txt i sitemap.xml ------------------------------------------------------------------


@require_GET
def robots_txt(request):
    """``Disallow: /`` w ``preview`` (trzecia droga noindex, S7); w ``primary`` panele + mapy witryn."""
    response = HttpResponse(seo.robots_txt(request), content_type="text/plain; charset=utf-8")
    if mode.is_primary(request):
        patch_cache_control(response, public=True, max_age=seo.ROBOTS_MAX_AGE)
    else:
        # Zakaz podglądu nie może zostać w żadnym buforze po przełączeniu na ``primary``.
        patch_cache_control(response, private=True, no_store=True)
    return response


@require_GET
def sitemap_xml(request):
    """Mapa witryny konkursu żądania – wyłącznie w ``primary`` i przy znanym adresie publicznym."""
    competition = getattr(request, "competition_site", None)
    if not mode.is_primary(request) or competition is None or not competition.public_base:
        return HttpResponseNotFound()
    response = HttpResponse(
        seo.sitemap_xml(seo.sitemap_entries(competition)), content_type="application/xml; charset=utf-8"
    )
    patch_cache_control(response, public=True, max_age=seo.SITEMAP_MAX_AGE)
    return response


# --- podgląd (ciasteczko djcms_view) -------------------------------------------------------------


def _site_root() -> str:
    """Korzeń witryny konkursu żądania (``/`` albo ``/<prefiks>/`` – prefiks skryptu wątku)."""
    try:
        return reverse("pages-root")
    except NoReverseMatch:  # pragma: no cover - cms.urls jest zawsze w urlconfie
        return "/"


def _other_competitions(request) -> list[dict]:
    """Konkursy do listy na stronie podglądu hosta platformy (konkurs domyślny, bez prefiksu).

    Każdy z odnośnikiem do **swojego** ``/djcms/preview/`` – ciasteczko jest host-only, więc
    podgląd konkursu z innym hostem włącza się na jego hoście (D1). Adresy z rejestru, nie z żądania.
    """
    from apps.sites.models import CompetitionSite

    competition = getattr(request, "competition_site", None)
    if competition is None or not competition.is_default or getattr(request, "competition_path_prefix", ""):
        return []
    items = []
    for item in (
        CompetitionSite.objects.filter(is_active=True).exclude(public_origin="").order_by("name", "slug")
    ):
        items.append(
            {
                "name": item.name,
                "is_current": item.pk == competition.pk,
                "preview_url": seo.public_url(item, "/djcms/preview/"),
                "home_url": seo.public_url(item, "/"),
            }
        )
    return items


@never_cache
@ensure_csrf_cookie
@require_http_methods(["GET", "HEAD", "POST"])
def preview_toggle(request):
    """``GET`` – strona z bieżącym stanem i przyciskami; ``POST`` (CSRF) – ustawia/kasuje ``djcms_view``.

    ``POST view=dj|wagtail|off`` + ``next`` (tylko ścieżka – ``preview.safe_next``) → 302 na ``next``
    (domyślnie korzeń witryny konkursu). ``dj.<SITE_DOMAIN>`` przekierowuje tu z ``?next=<ścieżka>``
    (``scripts/render_caddyfile.sh``); ``next`` jedzie dalej w polu formularza.
    """
    root = _site_root()
    if request.method == "POST":
        action = request.POST.get("view", "")
        if action not in preview.ACTIONS:
            return HttpResponseBadRequest(
                "Nieznana akcja podglądu.", content_type="text/plain; charset=utf-8"
            )
        response = HttpResponseRedirect(preview.safe_next(request.POST.get("next"), root))
        preview.apply(response, request, action)
        return response
    context = {
        "is_primary": mode.is_primary(request),
        "view": preview.current_view(request),
        "next": preview.safe_next(request.GET.get("next"), root),
        "site_root": root,
        "host": request.get_host(),
        "competition": getattr(request, "competition_site", None),
        "competitions": _other_competitions(request),
        "cookie_hours": preview.COOKIE_MAX_AGE // 3600,
    }
    return render(request, "dj/preview.html", context)


def not_yet_available(request, *args, **kwargs):
    """Adres zarezerwowany pod ``/djcms/`` na widok kolejnego kroku DJ-02 (SSO, DJ-02g) – pusta 404."""
    return HttpResponseNotFound()


# --- strony błędów -------------------------------------------------------------------------------


def page_not_found(request, exception=None):
    """404 z ramą konkursu (``404.html`` → ``dj/base.html``: ``chrome`` z API, bez API – rama awaryjna).

    Pod adresami aplikacyjnymi (``/djcms/…``) i bez rozstrzygniętego konkursu – strona prosta, bez
    API. Błąd renderowania ramy nie może zamienić 404 w 500: wtedy też strona prosta.
    """
    framed = getattr(request, "competition_site", None) is not None and not request.path_info.startswith(
        APP_PATH_PREFIX
    )
    if framed:
        try:
            return HttpResponseNotFound(render_to_string("404.html", request=request))
        except Exception:  # noqa: BLE001 - każdy błąd ramy = strona prosta, szczegóły w logu
            logger.exception("404: rama konkursu nie wyrenderowała się – strona prosta")
    return HttpResponseNotFound(render_to_string("dj/errors/404_plain.html", request=request))
