"""Widoki wewnętrznego API dla wersji ``dj.`` (``GET /internal/djcms/v1/…``, DJ-01 § 3.3).

Każdy widok:

- przechodzi przez bramki ``auth.internal_api`` (host wewnętrzny, token, ``GET``; porażka = 404),
- ustala konkurs sam (``auth.djcms_competition``) – ``CompetitionMiddleware`` celowo nie
  rozstrzyga go dla ``/internal/*`` – i wykonuje się wewnątrz ``competition_context``, więc kod
  czytający „konkurs na teraz” (``current_edition(None)``, ``resolve_competition()``) widzi ten sam
  konkurs, co przy stronie Wagtaila. Brak konkursu → ``503 {"error": "no-competition"}``, a wersja
  ``dj.`` pokazuje wtedy komunikat o niedostępności zamiast cudzych danych,
- formatuje w języku polskim niezależnie od nagłówków żądania: wołający (klient ``dj.``) nie
  wysyła ``Accept-Language``, a napisy mają brzmieć tak samo jak na polskiej stronie Wagtaila,
- oddaje ``Cache-Control: no-store``: stan etapu zmienia się z zegarem, a buforowanie jest
  zadaniem klienta (``djcms/apps/live/client.py``: 60 s świeże, 600 s kopia awaryjna).

Po stronie aplikacji głównej nie ma żadnego **dodatkowego** bufora – działają wyłącznie te, które
mają i strony Wagtaila (pasek osi czasu 300 s, komunikaty 60 s, slider 300 s).

Żaden widok nie dotyka ``accounts`` ani ``submissions``: w odpowiedziach nie ma e-maili, imion,
szkół, identyfikatorów uczestników, ocen ani wpisów do etapów.
"""

from __future__ import annotations

import logging
import tempfile
from functools import wraps
from urllib.parse import quote

from django.http import FileResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format

from apps.tenancy.context import competition_context

from . import serializers as s
from .auth import djcms_competition, internal_api

logger = logging.getLogger(__name__)

#: Wersja kontraktu. Klient ``dj.`` odrzuca odpowiedź z inną wartością („version”), więc zmiana
#: kształtu, która nie jest dopisaniem pola, podnosi tę liczbę **i** prefiks adresu (``v2``).
API_VERSION = 1

#: Rendition logotypu organizatora w stopce – ten sam, co w ``templates/base.html``.
ORGANIZER_LOGO_SPEC = "max-240x80"

#: Napis daty otwarcia rejestracji – filtr ``|date:"j E Y"`` z przycisku „Rejestracja rusza …”.
REGISTRATION_DATE_FORMAT = "j E Y"

#: Pamięć paczki eksportu: do tylu bajtów w RAM-ie, powyżej – plik tymczasowy.
EXPORT_SPOOL_BYTES = 16 * 1024 * 1024

#: Nazwa pliku paczki w ``Content-Disposition``.
EXPORT_FILENAME = "olimpiada-cms-bundle.zip"


def _json(payload: dict, *, status: int = 200) -> JsonResponse:
    """Odpowiedź API: JSON w UTF-8 bez ucieczek ``\\u``, bez bufora, bez zgadywania typu."""
    response = JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False},
        content_type="application/json; charset=utf-8",
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _envelope(payload: dict) -> dict:
    """Wspólne pola obiektu głównego: wersja kontraktu i chwila wygenerowania (ISO z offsetem)."""
    return {"api_version": API_VERSION, "generated_at": timezone.localtime().isoformat(), **payload}


def endpoint(view):
    """Bramki + konkurs + kontekst + język polski. Widok dostaje konkurs drugim argumentem."""

    @internal_api
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        competition = djcms_competition()
        if competition is None:
            return _json({"api_version": API_VERSION, "error": "no-competition"}, status=503)
        with competition_context(competition), translation.override("pl"):
            return view(request, competition, *args, **kwargs)

    return wrapped


# --- rama serwisu --------------------------------------------------------------------------------


def _organizer_logo(settings_row) -> dict | None:
    """Logotyp organizatora do stopki. Plik, którego nie da się przygotować, to brak logotypu."""
    logo = settings_row.organizer_logo
    if logo is None:
        return None
    try:
        rendition = logo.get_rendition(ORGANIZER_LOGO_SPEC)
    except Exception:  # noqa: BLE001 - zepsuty plik w magazynie nie może położyć ramy serwisu
        logger.warning("Nie udało się przygotować logotypu organizatora #%s dla dj.", logo.pk, exc_info=True)
        return None
    return {"src": s.api_href(rendition.url), "width": rendition.width, "height": rendition.height}


def _site_dto(settings_row) -> dict:
    return {
        "site_name": settings_row.site_name,
        "tagline": settings_row.tagline,
        "organizer_name": settings_row.organizer_name,
        "organizer_logo": _organizer_logo(settings_row),
        "organizer_address": settings_row.organizer_address,
        "organizer_registry": settings_row.organizer_registry,
        "contact_email": settings_row.contact_email,
        "contact_phone": settings_row.contact_phone,
        "contact_url": s.safe_http_url(settings_row.contact_url),
        "social_links": [
            {"url": s.safe_http_url(link["url"]), "label": link["label"], "icon": link["icon"]}
            for link in settings_row.social_links
            if s.safe_http_url(link["url"])
        ],
        "registration_note": settings_row.registration_note,
    }


def _registration_dto(competition) -> dict:
    """Stan rejestracji tą samą funkcją, co procesor kontekstu stron (``web.registration``)."""
    from apps.competitions.registration import current_registration_status, registration_message

    state = current_registration_status(competition=competition)
    opens_at = state.opens_at
    return {
        "is_open": state.is_open,
        "reason": state.reason,
        "opens_at": timezone.localtime(opens_at).isoformat() if opens_at else None,
        "opens_at_display": (
            date_format(timezone.localtime(opens_at), REGISTRATION_DATE_FORMAT) if opens_at else ""
        ),
        "closes_at": timezone.localtime(state.closes_at).isoformat() if state.closes_at else None,
        "message": registration_message(state),
    }


def _timeline_strip(competition) -> dict | None:
    from apps.cms.timeline import timeline_strip

    try:
        strip = timeline_strip(competition=competition)
    except Exception:  # noqa: BLE001 - pasek jest ozdobą ramy; jego awaria nie zdejmuje reszty
        logger.warning("Pasek osi czasu dla dj. niedostępny.", exc_info=True)
        return None
    return s.jsonable(strip) if strip is not None else None


@endpoint
def chrome(request, competition):
    """Rama serwisu: dane witryny, rejestracja, odnośniki, komunikaty, slider, pasek osi czasu."""
    from apps.accounts.supervisors import registration_enabled
    from apps.cms.announcements import cached_announcements
    from apps.cms.models import SiteSettings
    from apps.cms.sponsor_slider import cached_payload
    from apps.competitions.services import current_edition
    from apps.promo.availability import has_public_materials

    site = competition.site
    settings_row = SiteSettings.for_site(site)
    supervisor_enabled = registration_enabled(site.pk)
    slider = cached_payload(competition)
    return _json(
        _envelope(
            {
                "competition": {"slug": competition.slug, "name": competition.name},
                "site": _site_dto(settings_row),
                "edition": s.edition_dto(current_edition(competition)),
                "registration": _registration_dto(competition),
                "supervisor_registration": {
                    "enabled": supervisor_enabled,
                    "url": s.api_href(reverse("web:register-supervisor")) if supervisor_enabled else None,
                },
                "links": {
                    "login": s.api_href(reverse("web:login")),
                    "register": s.api_href(reverse("web:register")),
                    "support": s.api_href(reverse("web:support-new")),
                    "posters": s.api_href(reverse("web:posters"))
                    if has_public_materials(competition)
                    else None,
                    "main_home": s.main_url("/"),
                },
                "announcements": [s.announcement_dto(item) for item in cached_announcements(competition)],
                "sponsor_slider": {
                    "seconds": slider.get("seconds", 0),
                    "entries": [
                        {
                            "name": entry["name"],
                            "url": s.safe_http_url(entry["url"]),
                            "src": s.api_href(entry["src"]),
                            "width": entry["width"],
                            "height": entry["height"],
                        }
                        for entry in slider.get("entries", [])
                    ],
                },
                "timeline_strip": _timeline_strip(competition),
            }
        )
    )


# --- dane zawodów --------------------------------------------------------------------------------


@endpoint
def stages(request, competition):
    """Edycja, etap „na teraz” i wiersze osi czasu – ``live_data.competition_state``."""
    from apps.cms.live_data import competition_state

    state = competition_state(competition)
    published = {row["stage"].pk for row in state.stage_rows if row["has_results"]}
    if state.current_stage is not None and state.current_stage.pk not in published:
        published |= s.published_stage_ids([state.current_stage])
    return _json(
        _envelope(
            {
                "edition": s.edition_dto(state.edition),
                "current_stage": s.stage_dto(state.current_stage, published),
                "rows": [s.stage_row_dto(row, published) for row in state.stage_rows],
            }
        )
    )


@endpoint
def problems(request, competition):
    """Zadania etapu bieżącego i treningowego – ``live_data.problems_state``.

    Lista ``problems`` jest pusta, dopóki etap się nie otworzy: funkcja wspólna nie oddaje zadań
    przed ``opens_at``, więc w JSON-ie nie ma ani tytułu, ani adresu PDF-u.
    """
    from apps.cms.live_data import problems_state

    state = problems_state(competition)
    published = s.published_stage_ids([state.stage, state.training_stage])
    return _json(
        _envelope(
            {
                "edition": s.edition_dto(state.edition),
                "stage": s.stage_dto(state.stage, published),
                "stage_has_opened": state.stage_has_opened,
                "problems": [s.problem_dto(problem) for problem in state.problems],
                "training_stage": s.stage_dto(state.training_stage, published),
                "training_problems": [s.problem_dto(problem) for problem in state.training_problems],
            }
        )
    )


@endpoint
def results(request, competition):
    """Ogłoszone tabele bieżącej edycji i odnośniki archiwalne – ``live_data.results_state``."""
    from apps.cms.live_data import results_state

    state = results_state(competition)
    return _json(
        _envelope(
            {
                "edition": s.edition_dto(state.edition),
                "tables": [s.table_dto(table) for table in state.tables],
                "archive": [
                    {
                        "stage": s.stage_dto(item["stage"], {item["stage"].pk}),
                        "publication": s.publication_dto(item["publication"]),
                    }
                    for item in state.archive
                ],
            }
        )
    )


@endpoint
def editions(request, competition):
    """Edycje konkursu – lista wyboru w metadanych strony archiwum na ``dj.``."""
    from apps.competitions.models import Edition

    rows = Edition.objects.for_competition(competition).order_by("-created_at", "id")
    return _json(_envelope({"editions": [s.edition_dto(edition) for edition in rows]}))


@endpoint
def edition_results(request, competition, edition_id: int):
    """Odnośniki do ogłoszonych tabel jednej edycji. Edycja cudzego konkursu = pusta odpowiedź."""
    from apps.cms.live_data import archive_result_links
    from apps.competitions.models import Edition

    edition = Edition.objects.for_competition(competition).filter(pk=edition_id).first()
    links = archive_result_links(edition.pk, competition) if edition is not None else []
    stage_ids = {link["stage"].pk for link in links}
    return _json(
        _envelope(
            {
                "edition": s.edition_dto(edition),
                "links": [
                    {
                        "stage": s.stage_dto(link["stage"], stage_ids),
                        "results_url": s.api_href(reverse("web:results", args=[link["stage"].pk])),
                    }
                    for link in links
                ],
            }
        )
    )


def _page_path(page) -> str | None:
    """Ścieżka strony względem korzenia jej witryny (``/warsztaty/``) – taka sama po imporcie na ``dj.``."""
    if page is None:
        return None
    parts = page.get_url_parts()
    if parts is None:
        return None
    return s.api_href(parts[2]) or None


def _materials_dto(competition) -> dict:
    """Zapowiedź materiałów z warsztatów – to samo, co ``{% workshop_materials_teaser %}`` dla gościa.

    Gość dostaje wyłącznie liczbę i odnośnik do logowania; adresy plików powstają tylko w widokach
    dla zalogowanych (``apps.workshop_materials``). Wyłączona funkcja nie zadaje ani jednego
    zapytania – tak samo jak znacznik szablonu.
    """
    from apps.workshop_materials.access import feature_enabled
    from apps.workshop_materials.services import visible_materials

    materials_path = reverse("web:workshop-materials")
    login_url = s.api_href(f"{reverse('web:login')}?next={quote(materials_path, safe='/')}")
    materials_url = s.api_href(materials_path)
    if not feature_enabled(competition):
        return {"show": False, "count": 0, "login_url": login_url, "materials_url": materials_url}
    count = visible_materials(competition).count()
    return {"show": count > 0, "count": count, "login_url": login_url, "materials_url": materials_url}


@endpoint
def workshops(request, competition):
    """Najbliższe warsztaty (≤ 3), cały harmonogram i zapowiedź materiałów – z tabeli Wagtaila.

    ``upcoming`` liczy ta sama funkcja, co zapowiedź na stronie głównej (``upcoming_workshops``),
    która prowadzącego nie zwraca – pole ``lecturer`` jest tam puste. Pełne wiersze (z prowadzącym)
    są w ``rows``.
    """
    from apps.cms.workshops import upcoming_workshops, workshop_rows, workshops_page

    page = workshops_page(competition)
    return _json(
        _envelope(
            {
                "page_path": _page_path(page),
                "upcoming": [s.workshop_dto(row) for row in upcoming_workshops(page)],
                "rows": [s.workshop_dto(row) for row in workshop_rows(page)],
                "materials": _materials_dto(competition),
            }
        )
    )


# --- eksport treści (DJ-01b) ---------------------------------------------------------------------


@endpoint
def export(request, competition):
    """Paczka treści redakcyjnej (``apps.cms.export_bundle``) – ZIP z manifestem i obrazami.

    Paczka powstaje w pliku tymczasowym (w pamięci do ``EXPORT_SPOOL_BYTES``), a nie w jednym
    ``bytes``: oryginały obrazów potrafią ważyć kilkadziesiąt megabajtów, a ten proces obsługuje
    jednocześnie ruch uczestników.
    """
    from apps.cms.export_bundle import build_bundle

    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_BYTES)  # noqa: SIM115 - zamyka FileResponse
    report = build_bundle(competition, stream=spool)
    spool.seek(0)
    logger.info(
        "Eksport paczki CMS dla dj.: %s stron, %s obrazów, %s dokumentów, %s pominięć.",
        report.pages,
        report.images,
        report.documents,
        len(report.skipped),
    )
    response = FileResponse(
        spool, content_type="application/zip", as_attachment=True, filename=EXPORT_FILENAME
    )
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
