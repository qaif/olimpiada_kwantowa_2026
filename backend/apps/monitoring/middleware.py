"""Tag ``competition`` na zdarzeniach błędów (OPS-02 § 2).

Dokładany do ``MIDDLEWARE`` **wyłącznie** przy niepustym ``SENTRY_DSN`` (``config/settings/base.py``),
tuż za ``CompetitionMiddleware`` – instalacja bez śledzenia błędów ma listę warstw taką jak przed
OPS-02. Tag to sam slug: nazwa konkursu nic nie dodaje do naprawy błędu, a identyfikator liczbowy
jest gorszy do czytania.
"""

from __future__ import annotations

from .sentry import set_competition_tag


class ErrorTrackingTagMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        competition = getattr(request, "competition", None)
        set_competition_tag(getattr(competition, "slug", None))
        return self.get_response(request)
