"""Warstwa przekierowań: 404 witryny konkursu → ``dj_seo.Redirect`` (DJ-02 § 8).

Port ``wagtail.contrib.redirects.middleware.RedirectMiddleware`` + ``apps.cms.redirects
.CompetitionRedirectMiddleware`` aplikacji głównej (prefiks ścieżki konkursu):

- szukamy **w witrynie konkursu żądania** (``request.site`` z ``CompetitionSiteMiddleware``),
- adres liczony od ``path_info`` (bez prefiksu konkursu) + zapytanie, znormalizowany jak w Wagtailu,
- kolejność prób jak w Wagtailu: pełny adres, ten sam po odkodowaniu (``uri_to_iri``), potem bez
  zapytania,
- cel względny (``/…``) pod prefiksem dostaje prefiks z przodu (inaczej wyprowadziłby do konkursu
  gospodarza); adres bezwzględny zostaje bez zmian,
- 301 dla stałych, 302 dla tymczasowych; zapytanie z ``\\0`` – bez wyszukiwania (Postgres).

Dwie drogi, jedna funkcja: ``process_exception`` łapie ``Http404`` z widoku **zanim** ``handler404``
wyrenderuje ramę 404 (stary adres po przełączeniu to częsty przypadek – bez kosztu ramy i API),
a ``__call__`` – 404 zwrócone wprost przez widok. Adresy aplikacyjne ``/djcms/…`` i żądania bez
konkursu – bez wyszukiwania.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django import http
from django.utils.encoding import escape_uri_path, iri_to_uri, uri_to_iri

from .models import Redirect, normalise_path

APP_PATH_PREFIX = "/djcms/"


def _full_path_without_prefix(request) -> str:
    query = request.META.get("QUERY_STRING", "")
    return escape_uri_path(request.path_info) + (f"?{iri_to_uri(query)}" if query else "")


def _lookup(site, path: str) -> Redirect | None:
    if "\0" in path:
        return None
    for candidate in dict.fromkeys((path, uri_to_iri(path))):
        found = Redirect.objects.filter(site=site, old_path=candidate).first()
        if found is not None:
            return found
    return None


def find_redirect(request) -> Redirect | None:
    competition = getattr(request, "competition_site", None)
    if competition is None or request.path_info.startswith(APP_PATH_PREFIX):
        return None
    path = normalise_path(_full_path_without_prefix(request))
    redirect = _lookup(competition.site, path)
    if redirect is None:
        without_query = urlsplit(path).path
        if without_query != path:
            redirect = _lookup(competition.site, without_query)
    return redirect


def target_url(request, redirect: Redirect) -> str:
    link = redirect.new_path
    prefix = getattr(request, "competition_path_prefix", "")
    if prefix and link.startswith("/") and not link.startswith("//"):
        return f"/{prefix}{link}"
    return link


def redirect_response(request) -> http.HttpResponse | None:
    redirect = find_redirect(request)
    if redirect is None:
        return None
    link = target_url(request, redirect)
    if redirect.is_permanent:
        return http.HttpResponsePermanentRedirect(link)
    return http.HttpResponseRedirect(link)


#: Atrybut żądania: przekierowanie już sprawdzone w ``process_exception`` – bez drugiego zapytania.
CHECKED_ATTR = "_dj_seo_redirect_checked"


class RedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code != 404 or getattr(request, CHECKED_ATTR, False):
            return response
        return redirect_response(request) or response

    def process_exception(self, request, exception):
        if not isinstance(exception, http.Http404):
            return None
        setattr(request, CHECKED_ATTR, True)
        return redirect_response(request)
