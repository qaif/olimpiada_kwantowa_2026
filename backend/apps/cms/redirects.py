"""Przekierowania Wagtaila (``wagtail.contrib.redirects``) pod prefiksem ścieżki konkursu (uwaga T43).

Warstwa Wagtaila szuka przekierowania po ``request.get_full_path()``. W konkursie adresowanym
prefiksem ścieżki ten adres **niesie prefiks** (``/druga/stary-regulamin``), a przekierowanie
zapisane w ``/cms/`` dla witryny tego konkursu – nie (``/stary-regulamin``), bo redaktor wpisuje
adres względem strony głównej swojej witryny, tak samo jak w konkursie z własną domeną. Bez tej
warstwy żadne przekierowanie konkursu pod prefiksem by nie zadziałało.

Druga połowa to cel. Przekierowanie na **stronę** liczy adres przez ``page.url`` – ten zna prefiks
(``apps.tenancy.page_urls``). Przekierowanie na **adres** wpisany ręcznie (``/nowy-regulamin/``)
jest względne wobec witryny, więc pod prefiksem dostaje prefiks z przodu: inaczej wyprowadziłoby
czytelnika z konkursu pod prefiksem do konkursu-gospodarza, na jego stronę o tym samym adresie.
Adres bezwzględny (``https://…``) i protokołowo-względny (``//…``) zostają nietknięte – to jest
świadomy wybór redaktora, a nie adres w witrynie.

Żądanie bez prefiksu idzie **niezmienioną** drogą Wagtaila (``super()``): dla konkursu z własną
domeną ta klasa nie zmienia ani jednego bajtu odpowiedzi.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django import http
from django.utils.encoding import escape_uri_path, iri_to_uri
from wagtail.contrib.redirects import models
from wagtail.contrib.redirects.middleware import RedirectMiddleware, get_redirect


def _full_path_without_prefix(request) -> str:
    """``get_full_path()`` liczone od ``path_info`` – czyli adres względem witryny konkursu."""
    query = request.META.get("QUERY_STRING", "")
    return escape_uri_path(request.path_info) + (f"?{iri_to_uri(query)}" if query else "")


def prefixed_link(link: str, prefix: str) -> str:
    """Adres względny witryny z prefiksem konkursu; bezwzględny bez zmian."""
    if link.startswith("/") and not link.startswith("//"):
        return f"{prefix}{link}"
    return link


class CompetitionRedirectMiddleware(RedirectMiddleware):
    """``RedirectMiddleware`` Wagtaila, który zna prefiks ścieżki konkursu. Patrz docstring modułu."""

    def process_response(self, request, response):
        prefix = getattr(request, "competition_script_prefix", "")
        if not prefix or response.status_code != 404:
            return super().process_response(request, response)

        # Ta sama kolejność prób, co u Wagtaila: pełny adres, potem adres bez zapytania.
        path = models.Redirect.normalise_path(_full_path_without_prefix(request), decode_unicode=False)
        redirect = get_redirect(request, path)
        if redirect is None:
            path_without_query = urlparse(path).path
            if path == path_without_query:
                return response
            redirect = get_redirect(request, path_without_query)
            if redirect is None:
                return response

        link = redirect.link
        if link is None:
            return response
        link = prefixed_link(link, prefix)
        if redirect.is_permanent:
            return http.HttpResponsePermanentRedirect(link)
        return http.HttpResponseRedirect(link)
