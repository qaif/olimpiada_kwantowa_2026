"""``GET /kalendarz.ics`` – publiczny kalendarz konkursu do subskrypcji i pobrania (CAL-01).

Widok jest cienki: konkurs z żądania (brak = 404, jak ``/plakaty/``), treść z ``feed.cached_feed``,
nagłówki HTTP. Bez logowania i bez limitu żądań – to ten sam rodzaj zasobu, co ``/status.json``:
publiczny, tani i odpytywany automatem (serwer kalendarza), a ``ETag`` + ``304`` sprawiają, że
kolejne pobrania bez zmian kosztują jeden odczyt z Redisa.
"""

from __future__ import annotations

from django.http import Http404, HttpResponse
from django.utils.cache import get_conditional_response, patch_cache_control
from django.views.generic import View

from apps.cms.calendar import ics_filename

from . import feed
from .ics import etag_for

#: Jak długo klient i pośrednik mogą trzymać plik bez pytania serwera. Kwadrans – tyle, co wpis
#: w Redisie; serwery kalendarzy i tak odpytują rzadziej (Google – co kilka godzin).
MAX_AGE_SECONDS = 900


def base_url(request) -> str:
    """Schemat i host żądania. Za Caddym ``https`` daje ``SECURE_PROXY_SSL_HEADER``."""
    return f"{request.scheme}://{request.get_host()}"


class PublicCalendarFeedView(View):
    """Plik ``.ics`` z warsztatami i terminami bieżącej edycji konkursu z żądania."""

    http_method_names = ["get", "head", "options"]

    def get(self, request):
        competition = getattr(request, "competition", None)
        if competition is None:
            raise Http404("Pod tym adresem nie stoi żaden konkurs.")
        body = feed.cached_feed(competition, base_url=base_url(request))
        etag = etag_for(body)
        response = get_conditional_response(request, etag=etag)
        if response is None:
            response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
            # ``inline``: subskrypcja i podgląd czytają plik wprost; „Pobierz .ics” ma atrybut
            # ``download`` w odnośniku, więc przeglądarka i tak zapisze go jako plik.
            response["Content-Disposition"] = f'inline; filename="{ics_filename(competition)}"'
        response["ETag"] = etag
        patch_cache_control(response, public=True, max_age=MAX_AGE_SECONDS)
        return response
