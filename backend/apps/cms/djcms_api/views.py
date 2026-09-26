"""Widoki wewnętrznego API dla serwisu na django CMS – v1 (DJ-01 § 3.3) i v2 (DJ-02 § 4).

Dwie wersje tego samego rdzenia. Ciało każdego endpointu (``_chrome``, ``_stages``…) jest jedno
i dostaje konkurs z zewnątrz; różnią się wyłącznie opakowania:

- ``endpoint`` (v1, ``/internal/djcms/v1/<endpoint>``) – konkurs z konfiguracji
  (``auth.djcms_competition``), brak konkursu = 503,
- ``endpoint_v2`` (v2, ``/internal/djcms/v2/c/<slug>/<endpoint>``) – konkurs **ze ścieżki**:
  klucz bufora, dziennik i test widzą go wprost (DJ-02 D11). Nieistniejący albo nieaktywny
  konkurs = ``404 {"error": "no-competition"}`` – ale dopiero **po** bramkach, więc z domeny
  publicznej i bez tokenu odpowiedź jest ta sama pusta 404, co dla każdego innego adresu.

Wspólny rdzeń jest warunkiem niezmienników z DJ-01 § 7 w obu wersjach naraz: zadania po
``opens_at``, wyniki ze snapshotu, brak danych osobowych – to są cechy ciał, nie opakowań.
v1 zostaje bez zmian do DJ-02k (klient djcms przechodzi na v2 w DJ-02d).

Każdy widok:

- przechodzi przez bramki ``auth.internal_api`` (host wewnętrzny, token, ``GET``; porażka = 404),
- ustala konkurs sam (v1: ``auth.djcms_competition``, v2: slug ze ścieżki) –
  ``CompetitionMiddleware`` celowo nie rozstrzyga go dla ``/internal/*`` – i wykonuje się wewnątrz
  ``competition_context``, więc kod
  czytający „konkurs na teraz” (``current_edition(None)``, ``resolve_competition()``) widzi ten sam
  konkurs, co przy stronie Wagtaila. Brak konkursu → ``503 {"error": "no-competition"}``, a wersja
  ``dj.`` pokazuje wtedy komunikat o niedostępności zamiast cudzych danych,
- buduje adresy aplikacji pod adresem **tego** konkursu (``serializers.competition_public_base``),
  a nie pod ``DJCMS_MAIN_PUBLIC_URL``, które opisuje wyłącznie konkurs domeny głównej. Konkurs bez
  ustalonego adresu → ``503 {"error": "no-public-url"}``: odnośniki „Zaloguj” czy „Wyniki” pod
  cudzą domeną byłyby gorsze niż komunikat o niedostępności,
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
from datetime import date
from functools import wraps
from urllib.parse import quote

from django.http import FileResponse, HttpResponseNotFound, JsonResponse
from django.templatetags.static import static
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

#: Wersja kontraktu per konkurs (DJ-02 § 4) – adresy ``/internal/djcms/v2/…``.
API_VERSION_V2 = 2

#: Kształt sluga konkursu w ścieżce v2. Ten sam wzorzec stoi w ``urls_v2`` (``re_path``) – inny
#: kształt nie dociera do widoku, tylko kończy się pustą 404 catch-alla.
SLUG_PATTERN = r"[a-z0-9-]{1,50}"

#: Rendition logotypu i favikony konkursu (``Competition.logo``/``favicon``) w ramie v2. Logotyp
#: ma zapas na ekrany o dużej gęstości – statyczny logotyp w ``templates/base.html`` ma 600×231.
COMPETITION_LOGO_SPEC = "max-600x240"
COMPETITION_FAVICON_SPEC = "max-512x512"

#: Obraz ``og:image`` i opis domyślny z ``templates/base.html`` (``{% block description %}``) –
#: parytet ``<head>`` (DJ-02 § 8). Test pilnuje, że napis wciąż stoi w szablonie Wagtaila.
OG_IMAGE_STATIC = "img/og-image.png"
DEFAULT_DESCRIPTION = (
    "Ogólnopolska olimpiada dla uczniów szkół ponadpodstawowych: terminy etapów, zadania, wyniki "
    "oraz panel uczestnika, recenzenta i komitetu."
)

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


def _envelope(payload: dict, api_version: int = API_VERSION) -> dict:
    """Wspólne pola obiektu głównego: wersja kontraktu i chwila wygenerowania (ISO z offsetem)."""
    return {"api_version": api_version, "generated_at": timezone.localtime().isoformat(), **payload}


def _serve(view, request, competition, api_version: int, *args, **kwargs):
    """Wspólny rdzeń obu opakowań: adres konkursu, kontekst, język, koperta.

    Ciało endpointu oddaje słownik (treść JSON-a bez koperty) albo gotową odpowiedź (paczka ZIP).
    JSON powstaje **wewnątrz** kontekstów: napisy leniwe (``gettext_lazy``) zamieniają się w tekst
    przy serializacji, a ta ma się odbyć w języku polskim i w kontekście tego konkursu.
    """
    base = s.competition_public_base(competition)
    if base is None:
        logger.warning(
            "Konkurs „%s” nie ma adresu w aplikacji głównej – API dla djcms odpowiada 503. Potrzebna "
            "domena konkursu albo prefiks ścieżki z otwartą bramką path_prefix_routing konkursu platformy.",
            competition.slug,
        )
        return _json({"api_version": api_version, "error": "no-public-url"}, status=503)
    with competition_context(competition), s.public_base_context(base), translation.override("pl"):
        result = view(request, competition, *args, api_version=api_version, **kwargs)
        if isinstance(result, dict):
            return _json(_envelope(result, api_version))
        return result


def endpoint(view):
    """v1: bramki + konkurs z konfiguracji + kontekst + język polski."""

    @internal_api
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        competition = djcms_competition()
        if competition is None:
            return _json({"api_version": API_VERSION, "error": "no-competition"}, status=503)
        return _serve(view, request, competition, API_VERSION, *args, **kwargs)

    return wrapped


def endpoint_v2(view):
    """v2: bramki + konkurs **ze ścieżki** (``c/<slug>/…``) + kontekst + język polski.

    Kolejność ma znaczenie: bramki (``internal_api``) stoją **przed** odczytem konkursu, więc
    z domeny publicznej, bez tokenu albo z ``POST`` każdy slug – istniejący czy nie – daje tę samą
    pustą 404. Dopiero zaufany wołający dowiaduje się, że konkursu nie ma (JSON 404) – djcms
    wygasza wtedy jego hosty, zamiast traktować to jak awarię ``web``.

    Nieaktywny konkurs jest dla API nieistniejący: jego terminy, wyniki i rama nie mają już się
    gdzie pokazać, a djcms dowiaduje się o wyłączeniu z listy ``competitions``.
    """

    @internal_api
    @wraps(view)
    def wrapped(request, slug, *args, **kwargs):
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=slug, is_active=True).select_related("site").first()
        if competition is None:
            return _json({"api_version": API_VERSION_V2, "error": "no-competition"}, status=404)
        return _serve(view, request, competition, API_VERSION_V2, *args, **kwargs)

    return wrapped


@internal_api
def competitions(request):
    """Lista konkursów platformy z danymi do rozstrzygania hosta (``competitions.py``, DJ-02 § 4.2)."""
    from .competitions import competitions_payload

    with translation.override("pl"):
        return _json(_envelope(competitions_payload(), API_VERSION_V2))


# --- rama serwisu --------------------------------------------------------------------------------


def _organizer_logo(settings_row) -> dict | None:
    """Logotyp organizatora do stopki. Plik, którego nie da się przygotować, to brak logotypu."""
    return s.rendition_dto(settings_row.organizer_logo, ORGANIZER_LOGO_SPEC, label="logotyp organizatora")


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


def _competition_v2(competition) -> dict:
    """Marka konkursu w ramie v2: nazwy, kolor akcentu, logotyp i favikona (``Competition``).

    ``logo``/``favicon`` = ``None`` znaczy „brak własnego znaku” – djcms rysuje wtedy znak
    domyślny ze swoich statyków, tak jak ``templates/base.html`` (``img/logo-olimpiada-kwantowa.png``,
    ``img/favicon.svg``).
    """
    favicon = s.rendition_dto(competition.favicon, COMPETITION_FAVICON_SPEC, label="favikona konkursu")
    return {
        "slug": competition.slug,
        "name": competition.name,
        "short_name": competition.short_name,
        "accent_colour": competition.accent_colour,
        "logo": s.rendition_dto(competition.logo, COMPETITION_LOGO_SPEC, label="logotyp konkursu"),
        "favicon": {"src": favicon["src"]} if favicon is not None else None,
    }


def _chrome(request, competition, *, api_version):
    """Rama serwisu: dane witryny, rejestracja, odnośniki, komunikaty, slider, pasek osi czasu.

    v2 dokłada pola, które ``templates/base.html`` czyta poza tym, co v1 już niosło (parytet
    ``<head>`` i stopki – DJ-02 § 4.3, § 8): markę konkursu (``competition.*``), identyfikator GA4
    (``site.ga_measurement_id`` – ten sam warunek ładowania ``gtag``/``consent.js``) i ``seo``
    (``og:image``, opis domyślny). Pola v1 zostają bez zmian – v1 nie dostaje żadnego z nowych.
    """
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
    site_dto = _site_dto(settings_row)
    competition_dto = {"slug": competition.slug, "name": competition.name}
    extra: dict = {}
    if api_version >= API_VERSION_V2:
        site_dto["ga_measurement_id"] = settings_row.ga_measurement_id or ""
        competition_dto = _competition_v2(competition)
        extra["seo"] = {
            "og_image": s.api_href(static(OG_IMAGE_STATIC)),
            "default_description": DEFAULT_DESCRIPTION,
        }
    return {
        "competition": competition_dto,
        "site": site_dto,
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
            "posters": s.api_href(reverse("web:posters")) if has_public_materials(competition) else None,
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
        **extra,
    }


# --- dane zawodów --------------------------------------------------------------------------------


def _stages(request, competition, *, api_version):
    """Edycja, etap „na teraz” i wiersze osi czasu – ``live_data.competition_state``."""
    from apps.cms.live_data import competition_state

    state = competition_state(competition)
    published = {row["stage"].pk for row in state.stage_rows if row["has_results"]}
    if state.current_stage is not None and state.current_stage.pk not in published:
        published |= s.published_stage_ids([state.current_stage])
    return {
        "edition": s.edition_dto(state.edition),
        "current_stage": s.stage_dto(state.current_stage, published),
        "rows": [s.stage_row_dto(row, published) for row in state.stage_rows],
    }


def _problems(request, competition, *, api_version):
    """Zadania etapu bieżącego i treningowego – ``live_data.problems_state``.

    Lista ``problems`` jest pusta, dopóki etap się nie otworzy: funkcja wspólna nie oddaje zadań
    przed ``opens_at``, więc w JSON-ie nie ma ani tytułu, ani adresu PDF-u.
    """
    from apps.cms.live_data import problems_state

    state = problems_state(competition)
    published = s.published_stage_ids([state.stage, state.training_stage])
    return {
        "edition": s.edition_dto(state.edition),
        "stage": s.stage_dto(state.stage, published),
        "stage_has_opened": state.stage_has_opened,
        "problems": [s.problem_dto(problem) for problem in state.problems],
        "training_stage": s.stage_dto(state.training_stage, published),
        "training_problems": [s.problem_dto(problem) for problem in state.training_problems],
    }


def _results(request, competition, *, api_version):
    """Ogłoszone tabele bieżącej edycji i odnośniki archiwalne – ``live_data.results_state``."""
    from apps.cms.live_data import results_state

    state = results_state(competition)
    return {
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


def _editions(request, competition, *, api_version):
    """Edycje konkursu – lista wyboru w metadanych strony archiwum na ``dj.``."""
    from apps.competitions.models import Edition

    rows = Edition.objects.for_competition(competition).order_by("-created_at", "id")
    return {"editions": [s.edition_dto(edition) for edition in rows]}


def _edition_results(request, competition, edition_id: int, *, api_version):
    """Odnośniki do ogłoszonych tabel jednej edycji. Edycja cudzego konkursu = pusta odpowiedź."""
    from apps.cms.live_data import archive_result_links
    from apps.competitions.models import Edition

    edition = Edition.objects.for_competition(competition).filter(pk=edition_id).first()
    links = archive_result_links(edition.pk, competition) if edition is not None else []
    stage_ids = {link["stage"].pk for link in links}
    return {
        "edition": s.edition_dto(edition),
        "links": [
            {
                "stage": s.stage_dto(link["stage"], stage_ids),
                "results_url": s.api_href(reverse("web:results", args=[link["stage"].pk])),
            }
            for link in links
        ],
    }


def _page_path(page, competition) -> str | None:
    """Ścieżka strony względem korzenia witryny konkursu (``/warsztaty/``) – ta sama, co w paczce.

    Z ``url_path`` (``export_bundle.site_path``), a nie z ``get_url_parts``: dla konkursu pod
    prefiksem ścieżki Wagtail (przez ``apps.tenancy.page_urls``) dokleja ``/<prefiks>/``, którego
    na ``dj.`` nie ma. Strona z ograniczonym dostępem (hasło, logowanie, grupy – także odziedziczone
    po przodku) nie trafia do paczki, więc i jej ścieżki nie podajemy: na ``dj.`` byłaby 404.
    """
    from wagtail.models import Page

    from apps.cms.export_bundle import site_path

    if page is None:
        return None
    root = competition.site.root_page
    if not page.url_path.startswith(root.url_path):
        return None
    if not Page.objects.live().public().filter(pk=page.pk).exists():
        return None
    return site_path(page, root)


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


def _richtext(value, competition) -> str:
    """Pole ``RichTextField`` jako HTML frontowy – ten sam przebieg, co w paczce (``export_richtext``).

    **Nie** jest sanityzowany: tak samo jak treść z paczki przechodzi przez sanityzator djcms przed
    wstawieniem do strony (DJ-01 § 7 reguła 4 – zero ``|safe`` bez sanityzacji).
    """
    from apps.cms.export_bundle import export_richtext

    return export_richtext(value or "", site=competition.site, main_public_url=s.current_public_base())


def _schedule_dto(value) -> dict:
    """Jeden blok ``schedule`` strony warsztatów – **cała** tabela, tak jak rysuje ją Wagtail.

    ``rows`` (v1) to wiersze z datą, posortowane – do paska i zapowiedzi. Tabela na stronie
    ``/warsztaty/`` pokazuje więcej: wiersze bez odczytanej daty („do potwierdzenia”), kolejność
    redakcyjną, podpis, własne nagłówki kolumn i ukrywanie pustych kolumn
    (``cms/blocks/schedule.html``). Wtyczka ``WorkshopSchedulePlugin`` rysuje ją z tych pól,
    więc tabela w djcms nie może się rozjechać z tabelą obecności i zaświadczeń (DJ-02 D9).
    """
    rows = [
        {
            "topic": row.get("topic", "") or "",
            "date": row.get("date", "") or "",
            "date_value": row["date_value"].isoformat() if isinstance(row.get("date_value"), date) else None,
            "time": row.get("time", "") or "",
            "lecturer": row.get("lecturer", "") or "",
        }
        for row in value.get("rows", [])
    ]
    return {
        "caption": value.get("caption", "") or "",
        "topic_label": value.get("topic_label", "") or "",
        "date_label": value.get("date_label", "") or "",
        "time_label": value.get("time_label", "") or "",
        "lecturer_label": value.get("lecturer_label", "") or "",
        # Ta sama reguła, co ``ScheduleValue.has_time``/``has_lecturer`` (spacje nie wypełniają kolumny).
        "has_time": any(row["time"].strip() for row in rows),
        "has_lecturer": any(row["lecturer"].strip() for row in rows),
        "rows": rows,
    }


def _workshops(request, competition, *, api_version):
    """Najbliższe warsztaty (≤ 3), cały harmonogram i zapowiedź materiałów – z tabeli Wagtaila.

    ``upcoming`` liczy ta sama funkcja, co zapowiedź na stronie głównej (``upcoming_workshops``),
    która prowadzącego nie zwraca – pole ``lecturer`` jest tam puste. Pełne wiersze (z prowadzącym)
    są w ``rows``.

    v2 dokłada ``schedules`` (bloki tabeli w kolejności strony) i ``page`` (tytuł i wprowadzenie):
    strona „Warsztaty” zostaje redagowana w Wagtailu także po przełączeniu (decyzja użytkownika
    z 26.09.2026, DJ-02 D9), więc djcms pokazuje ją na żywo, a nie z jednorazowego importu.
    Strona z ograniczonym dostępem nie oddaje w nich nic – tak jak ``page_path``.
    """
    from apps.cms.workshops import upcoming_workshops, workshop_rows, workshops_page

    page = workshops_page(competition)
    page_path = _page_path(page, competition)
    payload = {
        "page_path": page_path,
        "upcoming": [s.workshop_dto(row) for row in upcoming_workshops(page)],
        "rows": [s.workshop_dto(row) for row in workshop_rows(page)],
        "materials": _materials_dto(competition),
    }
    if api_version >= API_VERSION_V2:
        public = page is not None and page_path is not None
        payload["page"] = (
            {"title": page.title, "intro": _richtext(page.intro, competition)} if public else None
        )
        payload["schedules"] = (
            [_schedule_dto(block.value) for block in page.body if block.block_type == "schedule"]
            if public
            else []
        )
    return payload


def _partners(request, competition, *, api_version):
    """Partnerzy konkursu na żywo ze strony ``PartnersPage`` Wagtaila (DJ-02 § 4.3, D9).

    To samo zapytanie, co ekran slidera w panelu koordynatora
    (``apps.web.views.coordinator_sponsor_slider._partners_page``) – plus ``public()``: strona za
    hasłem albo logowaniem jest zamknięta także jako źródło danych dla djcms.

    ``partners`` w kolejności strony (redakcyjnej); grupowanie po poziomie robi djcms według
    ``levels`` (kolejność ``PARTNER_LEVELS``), tak jak ``PartnersPage.groups``. ``page`` niesie
    resztę pól strony (wprowadzenie, zaproszenie do współpracy) – strona zostaje redagowana
    w Wagtailu, więc i te pola djcms czyta na żywo. Tekst formatowany niesanityzowany (patrz
    ``_richtext``).
    """
    from apps.cms.blocks import PARTNER_LEVELS
    from apps.cms.export_bundle import site_path
    from apps.cms.models import PartnersPage

    root = competition.site.root_page
    page = PartnersPage.objects.live().public().child_of(root).first()
    levels = [[key, label] for key, label in PARTNER_LEVELS]
    if page is None:
        return {"page_path": None, "levels": levels, "partners": [], "page": None}
    return {
        "page_path": site_path(page, root),
        "levels": levels,
        "partners": [s.partner_dto(block.value) for block in page.partners if block.block_type == "partner"],
        "page": {
            "title": page.title,
            "intro": _richtext(page.intro, competition),
            "become_partner_title": page.become_partner_title,
            "become_partner_body": _richtext(page.become_partner_body, competition),
            "contact_email": page.contact_email,
        },
    }


# --- adres nieistniejący -------------------------------------------------------------------------


def not_found(request, *args, **kwargs):
    """Pusta 404 dla każdego adresu gałęzi, którego nie ma w ``urls.urlpatterns``.

    Bez tego wzorca ``CommonMiddleware`` (``APPEND_SLASH``) odpowiadał na ``…/v1/nie-ma`` 301 na
    adres z ukośnikiem (dopasowuje go catch-all Wagtaila), a tam – stroną 404 z marką konkursu. Adres
    prawdziwy za zamkniętą bramką odpowiada pustą 404, więc różnica mówiła, które nazwy istnieją.
    Bez bramek: odpowiedź i tak jest jedna, niezależnie od hosta, tokenu i metody.
    """
    return HttpResponseNotFound()


# --- eksport treści (DJ-01b, v2: DJ-02 § 4.4) ----------------------------------------------------


def _export(request, competition, *, api_version):
    """Paczka treści redakcyjnej (``apps.cms.export_bundle``) – ZIP z manifestem i obrazami.

    Paczka powstaje w pliku tymczasowym (w pamięci do ``EXPORT_SPOOL_BYTES``), a nie w jednym
    ``bytes``: oryginały obrazów potrafią ważyć kilkadziesiąt megabajtów, a ten proces obsługuje
    jednocześnie ruch uczestników. Wersja paczki idzie za wersją API: v1 oddaje paczkę 1 (importer
    DJ-01 innej nie przyjmie), v2 – paczkę 2 (przekierowania, strony-dane).
    """
    from apps.cms.export_bundle import build_bundle

    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_BYTES)  # noqa: SIM115 - zamyka FileResponse
    report = build_bundle(
        competition, stream=spool, main_public_url=s.current_public_base(), version=api_version
    )
    spool.seek(0)
    logger.info(
        "Eksport paczki CMS (v%s) konkursu %s dla djcms: %s stron, %s obrazów, %s dokumentów, %s pominięć.",
        api_version,
        competition.slug,
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


# --- widoki v1 (``urls.py``) i v2 (``urls_v2.py``) – te same ciała --------------------------------

chrome = endpoint(_chrome)
stages = endpoint(_stages)
problems = endpoint(_problems)
results = endpoint(_results)
editions = endpoint(_editions)
edition_results = endpoint(_edition_results)
workshops = endpoint(_workshops)
export = endpoint(_export)

chrome_v2 = endpoint_v2(_chrome)
stages_v2 = endpoint_v2(_stages)
problems_v2 = endpoint_v2(_problems)
results_v2 = endpoint_v2(_results)
editions_v2 = endpoint_v2(_editions)
edition_results_v2 = endpoint_v2(_edition_results)
workshops_v2 = endpoint_v2(_workshops)
partners_v2 = endpoint_v2(_partners)
export_v2 = endpoint_v2(_export)
