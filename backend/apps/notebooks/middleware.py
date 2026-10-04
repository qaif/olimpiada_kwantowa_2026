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
Pełną izolację daje dopiero osobna domena rejestrowalna laboratorium (``docs/OPERACJE.md`` § 40.6).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseForbidden

from apps.web.middleware import NOTEBOOK_LAB_SEGMENT, NOTEBOOK_STARTER_PATH

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def from_lab(request) -> bool:
    referer = request.META.get("HTTP_REFERER", "")
    if not referer:
        return False
    parts = urlsplit(referer)
    if parts.netloc and parts.netloc != request.get_host():
        return False
    return parts.path.startswith(f"{settings.STATIC_URL}{NOTEBOOK_LAB_SEGMENT}")


class NotebookLabRequestGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
                return HttpResponseForbidden(
                    "Requests from the notebook lab to the platform are not allowed.",
                    content_type="text/plain",
                )
        return self.get_response(request)
