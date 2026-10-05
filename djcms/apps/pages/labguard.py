"""Strażnik żądań z laboratorium notatników (QC-02 § 4–5, przegląd M2) – kopia logiki aplikacji głównej.

Przy ``NOTEBOOK_LAB_HOST`` kod uczniów działa pod osobnym hostem (``lab.<domena>`` albo osobna domena).
djcms odpowiada pod tymi samymi hostami co aplikacja główna (strony publiczne, ``/djcms/…``,
panel redakcji), a jego ``CSRF_TRUSTED_ORIGINS`` przy subdomenach platformy zawiera
``https://*.<SITE_DOMAIN>`` – czyli także host laboratorium. Z podrzuconym ``djcms_csrftoken``
(``Domain=<domena>``) i ciasteczkiem sesji redaktora (``Lax`` jedzie na żądania same-site) kod
z notatnika mógłby zapisać coś w CMS-ie. Ta warstwa, przed ``CsrfViewMiddleware``:

1. odrzuca (403) żądanie z ``Origin``/``Referer`` hosta laboratorium, które nie jest zwykłą nawigacją GET,
2. odrzuca (best-effort, L1) żądanie same-site zmieniające stan z ``Origin: null`` albo bez ``Origin``
   i ``Referer``, gdy laboratorium dzieli z hostem żądania domenę nadrzędną,
3. wygasza podrzucone ciasteczka (``djcms_sessionid``, ``djcms_csrftoken``, ``djcms_language``): ta sama
   nazwa dwa razy w nagłówku ``Cookie`` → przekierowanie pod ten sam adres z ``Max-Age=0`` dla każdej
   wspólnej domeny nadrzędnej i ścieżki-przodka; gdyby nie pomogło – żądanie bez tych ciasteczek.

Kod jest kopią ``backend/apps/notebooks/cookieguard.py`` i strażnika z ``backend/apps/notebooks/
middleware.py`` – djcms to osobny projekt i obraz, bez kodu aplikacji głównej. Bez ``NOTEBOOK_LAB_HOST``
warstwa nie robi nic.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseRedirect

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORBIDDEN_TEXT = "Requests from the notebook lab to the platform are not allowed."
MARKER = "djcms_cookie_wipe"
MAX_SEGMENTS = 8
EXPIRED = "Thu, 01 Jan 1970 00:00:00 GMT"


def _netloc(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def shared_parent_domains(request_host: str, lab_host: str) -> list[str]:
    host = request_host.split(":")[0].lower().rstrip(".")
    lab = lab_host.split(":")[0].lower().rstrip(".")
    labels = host.split(".")
    return [
        ".".join(labels[start:])
        for start in range(len(labels) - 1)
        if lab.endswith(f".{'.'.join(labels[start:])}")
    ]


def duplicated_names(raw_cookie: str, names) -> list[str]:
    wanted = set(names)
    counts: dict[str, int] = {}
    for chunk in raw_cookie.split(";"):
        name = chunk.split("=", 1)[0].strip()
        if name in wanted:
            counts[name] = counts.get(name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def ancestor_paths(path: str) -> list[str]:
    paths = ["/"]
    current = ""
    for segment in [part for part in path.split("/") if part][:MAX_SEGMENTS]:
        current = f"{current}/{segment}"
        paths += [current, f"{current}/"]
    return paths


class CookieWipeRedirect(HttpResponseRedirect):
    """Przekierowanie z wieloma ``Set-Cookie`` o tej samej nazwie (``response.cookies`` trzyma jedno)."""

    def __init__(self, url: str, wipes: list[str], *, status: int):
        super().__init__(url)
        self.status_code = status
        self.cookie_wipes = wipes

    def items(self):
        return [*super().items(), *(("Set-Cookie", header) for header in self.cookie_wipes)]


class LabGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lab = getattr(settings, "NOTEBOOK_LAB_HOST", "")
        if not lab:
            return self.get_response(request)
        host = request.get_host().lower()
        if host == lab:
            return self.get_response(request)
        if self.refused(request, lab):
            return HttpResponseForbidden(FORBIDDEN_TEXT, content_type="text/plain")
        names = (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME, settings.LANGUAGE_COOKIE_NAME)
        duplicates = duplicated_names(request.META.get("HTTP_COOKIE", ""), names)
        domains = shared_parent_domains(host, lab) if duplicates else []
        if domains:
            if MARKER not in request.COOKIES:
                logger.warning(
                    "Zdublowane ciasteczka %s na %s – wygaszam i powtarzam żądanie.", duplicates, host
                )
                attrs = f"Max-Age=0; Expires={EXPIRED}; SameSite=Lax" + (
                    "; Secure" if request.is_secure() else ""
                )
                wipes = [
                    f"{name}=; Domain={domain}; Path={path}; {attrs}"
                    for name in duplicates
                    for domain in domains
                    for path in ancestor_paths(request.path)
                ]
                status = 302 if request.method in ("GET", "HEAD") else 307
                response = CookieWipeRedirect(request.get_full_path(), wipes, status=status)
                response.set_cookie(
                    MARKER, "1", max_age=60, httponly=True, secure=request.is_secure(), samesite="Lax"
                )
                return response
            logger.warning(
                "Zdublowane ciasteczka %s na %s mimo wygaszenia – żądanie bez nich.", duplicates, host
            )
            request.COOKIES = {
                name: value for name, value in request.COOKIES.items() if name not in duplicates
            }
        return self.get_response(request)

    @staticmethod
    def refused(request, lab: str) -> bool:
        meta = request.META
        unsafe = request.method not in SAFE_METHODS
        mode = meta.get("HTTP_SEC_FETCH_MODE", "")
        origin = meta.get("HTTP_ORIGIN", "")
        from_lab = (origin and _netloc(origin) == lab) or _netloc(meta.get("HTTP_REFERER", "")) == lab
        if from_lab:
            return unsafe or bool(mode and mode != "navigate")
        if not unsafe or meta.get("HTTP_SEC_FETCH_SITE", "") != "same-site":
            return False
        if not shared_parent_domains(request.get_host(), lab):
            return False
        if origin == "null":
            return True
        return not origin and not meta.get("HTTP_REFERER") and mode != "navigate"
