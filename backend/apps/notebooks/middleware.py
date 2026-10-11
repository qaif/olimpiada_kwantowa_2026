"""Strażnik serwera: żądania wysłane z dokumentu laboratorium notatników (QC-01 § 3.5).

Pierwszą linią jest polityka CSP laboratorium (``apps.web.middleware.build_notebook_lab_policy``):
przeglądarka nie wyśle z laboratorium ``fetch``/XHR poza ścieżki laboratorium i notatnika
startowego, ani formularza. Ten strażnik to druga, **słabsza** linia po stronie serwera: odrzuca
(403) żądanie, które wg nagłówka ``Referer`` przyszło z dokumentu laboratorium, gdy

- zmienia stan (metoda inna niż GET/HEAD/OPTIONS) albo idzie do ``/api/``, albo
- jest żądaniem skryptu (``Sec-Fetch-Dest: empty`` – ``fetch``/XHR) do czegokolwiek poza
  notatnikiem startowym.

Słabsza, bo ``Referer`` da się stłumić (``referrerpolicy="no-referrer"`` w ``fetch``) – nie jest
więc granicą bezpieczeństwa, tylko siatką na przypadek, w którym przeglądarka nie egzekwuje CSP.

Pełną izolację daje osobny host laboratorium (QC-02, ``NOTEBOOK_LAB_HOST``): wtedy hosty rozdziela
``NotebookLabHostMiddleware`` niżej, a strażnik rozpoznaje laboratorium po ``Origin`` i ``Referer``
**hosta** laboratorium (``docs/tasks/QC-02.md`` § 3–4).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseNotFound, HttpResponseRedirect

from apps.web.middleware import NOTEBOOK_LAB_SEGMENT, NOTEBOOK_STARTER_PATH, is_notebook_lab_path

from . import cookieguard, lab

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORBIDDEN_TEXT = "Requests from the notebook lab to the platform are not allowed."
STARTER_RE = re.compile(rf"^{re.escape(NOTEBOOK_STARTER_PATH)}(?P<token>[^/]+)/(?P<filename>[^/]+)$")
#: Nagłówki każdej odpowiedzi hosta laboratorium (w produkcji ten sam zestaw dokłada Caddy – blok
#: hosta laboratorium w scripts/render_caddyfile.sh). ``strict-origin``: adres laboratorium niesie
#: token w ``?fromURL=``, więc na zewnątrz wychodzi sam origin – i po nim strażnik serwisu
#: rozpoznaje żądania z laboratorium.
LAB_HOST_HEADERS = {
    "Referrer-Policy": "strict-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}
#: Wszystko, co host laboratorium odpowiada spoza plików laboratorium (404, notatnik startowy).
LAB_HOST_OTHER_POLICY = "default-src 'none'; frame-ancestors 'none'; sandbox"


def _netloc(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def from_lab(request) -> bool:
    host = lab.lab_host()
    if host:
        # Osobny host (QC-02): ścieżki laboratorium na hostach serwisu już nie ma – liczy się host.
        # ``Origin`` jest na każdym żądaniu zmieniającym stan i na każdym CORS, ``Referer``
        # (``strict-origin`` laboratorium) – także na zwykłych żądaniach zasobów.
        origin = request.META.get("HTTP_ORIGIN", "")
        if origin and _netloc(origin) == host:
            return True
        return _netloc(request.META.get("HTTP_REFERER", "")) == host
    referer = request.META.get("HTTP_REFERER", "")
    if not referer:
        return False
    parts = urlsplit(referer)
    if parts.netloc and parts.netloc != request.get_host():
        return False
    return parts.path.startswith(f"{settings.STATIC_URL}{NOTEBOOK_LAB_SEGMENT}")


def refused_from_lab(request) -> bool:
    """Żądanie z laboratorium (``from_lab``) inne niż zwykła nawigacja GET – przy osobnym hoście.

    Przechodzi wyłącznie nawigacja (odnośnik z notatnika do serwisu) – jak z każdej obcej strony.
    Reszta, także POST z ``Origin``, który Django uznałby za zaufany (``https://*.<domena>`` przy
    subdomenach platformy), kończy się przed ochroną CSRF.
    """
    mode = request.META.get("HTTP_SEC_FETCH_MODE", "")
    return (
        request.method not in SAFE_METHODS
        or request.path_info.startswith("/api/")
        or bool(mode and mode != "navigate")
    )


def anonymous_same_site(request) -> bool:
    """Przegląd L1: żądanie same-site, którego nie da się przypisać, gdy laboratorium jest same-site.

    Kod z notatnika może wyciąć ``Referer`` i zamienić ``Origin`` na ``null``
    (``referrerpolicy: "no-referrer"``). Gdy host laboratorium dzieli z hostem żądania domenę nadrzędną
    (wariant A), odrzucamy (best-effort, po Fetch Metadata) żądania same-site **zmieniające stan albo
    do ``/api/``**, które przyszły z ``Origin: null`` albo bez ``Origin`` i ``Referer`` i nie są
    nawigacją. Zwykłe żądania zasobów (GET) same-site bez nagłówków przechodzą – mogą pochodzić
    z subdomen konkursów (ich ``Referrer-Policy: same-origin`` nie wysyła ``Referer``), a odczytać
    ich odpowiedzi laboratorium i tak nie może (CORS, CSP laboratorium). Osobna domena (wariant B):
    nic – przeglądarka nie dołączy ciasteczek ``Lax`` do takich żądań.
    """
    if request.META.get("HTTP_SEC_FETCH_SITE", "") != "same-site":
        return False
    if not (request.method not in SAFE_METHODS or request.path_info.startswith("/api/")):
        return False
    if not cookieguard.shared_parent_domains(request.get_host(), lab.lab_host()):
        return False
    origin = request.META.get("HTTP_ORIGIN", "")
    if origin == "null":
        return True
    mode = request.META.get("HTTP_SEC_FETCH_MODE", "")
    return not origin and not request.META.get("HTTP_REFERER") and mode != "navigate"


class NotebookLabRequestGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if lab.lab_host():
            if not lab.is_lab_host(request) and (
                (from_lab(request) and refused_from_lab(request)) or anonymous_same_site(request)
            ):
                return HttpResponseForbidden(FORBIDDEN_TEXT, content_type="text/plain")
            return self.get_response(request)
        if from_lab(request):
            path = request.path_info
            script_request = request.META.get("HTTP_SEC_FETCH_DEST", "") == "empty"
            if (
                request.method not in SAFE_METHODS
                or path.startswith("/api/")
                or (
                    script_request
                    and not path.startswith(NOTEBOOK_STARTER_PATH)
                    and not path.startswith(f"{settings.STATIC_URL}{NOTEBOOK_LAB_SEGMENT}")
                )
            ):
                return HttpResponseForbidden(FORBIDDEN_TEXT, content_type="text/plain")
        return self.get_response(request)


class NotebookLabHostMiddleware:
    """Rozdział hostów przy ``NOTEBOOK_LAB_HOST`` (QC-02 § 3). Bez ustawienia – przezroczysty.

    W produkcji pliki laboratorium podaje Caddy, a ten sam rozdział robi konfiguracja proxy
    (``scripts/render_caddyfile.sh``); tutaj jest druga zapora i jedyna w dev (WhiteNoise).

    - host laboratorium: pliki laboratorium – dalej w łańcuchu (WhiteNoise); notatnik startowy –
      widok wołany **od razu**, bez sesji, uwierzytelnienia, konkursu i CSRF (host nie dostaje
      ciasteczek serwisu, a rozstrzyganie konkursu po hoście dałoby tu 404 albo cudzy konkurs);
      reszta – goła 404,
    - pozostałe hosty: pliki laboratorium – 302 na host laboratorium (stare zakładki), notatnik
      startowy – 404.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = lab.lab_host()
        if not host:
            return self.get_response(request)
        path = request.path_info
        if not lab.is_lab_host(request):
            if is_notebook_lab_path(path):
                return HttpResponseRedirect(f"{request.scheme}://{host}{request.get_full_path()}")
            if path.startswith(NOTEBOOK_STARTER_PATH):
                return HttpResponseNotFound("Not found.", content_type="text/plain")
            # Podrzucone ciasteczka sesji/CSRF/języka (przegląd M1) – przed sesją i CSRF.
            wipe = cookieguard.guard(
                request,
                host,
                (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME, settings.LANGUAGE_COOKIE_NAME),
            )
            return wipe or self.get_response(request)
        if is_notebook_lab_path(path):
            response = self.get_response(request)
            if response.status_code >= 400:
                # Brak pliku przeszedłby przez całą aplikację (strona 404 serwisu, może z ciasteczkiem
                # CSRF) – host laboratorium odpowiada gołą 404, bez niczego z serwisu.
                response = HttpResponseNotFound("Not found.", content_type="text/plain")
        else:
            match = STARTER_RE.match(path)
            if match:
                from .views import lab_host_starter

                response = lab_host_starter(request, match["token"], match["filename"])
            else:
                response = HttpResponseNotFound("Not found.", content_type="text/plain")
            response["Content-Security-Policy"] = LAB_HOST_OTHER_POLICY
        for name, value in LAB_HOST_HEADERS.items():
            response[name] = value
        return response
