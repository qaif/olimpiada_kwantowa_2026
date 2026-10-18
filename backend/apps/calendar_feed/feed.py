"""Publiczny kanał ``/kalendarz.ics`` konkursu (CAL-01) – zbieranie terminów i pamięć podręczna.

**Skąd terminy.** Z tych samych miejsc, z których bierze je pasek linii czasu (``apps.cms.timeline``):
warsztaty z bloku ``schedule`` strony „Warsztaty” **tego konkursu** (``workshops_page``), etapy i okno
rejestracji z bieżącej edycji, wydarzenia koordynatora z ``EditionEvent``. Pasek oddaje jednak same
dni, a kalendarz potrzebuje więcej: godzin warsztatu, prowadzącego, chwili otwarcia etapu i terminu
oddania prac. Dlatego czytamy źródła wprost (``workshop_rows``, ``Edition.stages``), a nie przerabiamy
wyniku ``timeline_events`` – i stosujemy **te same zawężenia** (bez etapu treningowego, wydarzenia
z ``show_on_timeline``, rejestracja tylko włączona), żeby kalendarz nie ogłaszał niczego, czego strona
nie ogłasza.

**Czego tu nie ma.** Linku do spotkania (Meet) i czegokolwiek o konkretnym uczestniku – kanał jest
publiczny i bywa synchronizowany do cudzych usług (CAL-01 § 0, § 4). Webinarów też nie: mają grupę
odbiorców, a nie są ogłoszeniem publicznym.

**Pamięć podręczna.** Kanał nie stoi na allow-liście pamięci stron (``apps.web.page_cache``) – tamta
warstwa obsługuje wyłącznie HTML z nonce'em i tokenem CSRF. Ma własny wpis, ale z **tymi samymi
licznikami wersji**: publikacja strony, zapis edycji, etapu i wydarzenia już dziś podbijają wersję
witryny konkursu, więc zmiana harmonogramu unieważnia kanał bez nowego sygnału (CAL-01 § 3).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime

from django.core.cache import cache
from django.urls import get_script_prefix
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.cms.calendar import uid_domain
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_rows, workshops_page
from apps.competitions.models import StageFormat, StageKind
from apps.competitions.services import current_edition
from apps.tenancy.branding import branded_text, competition_name

from .ics import DEFAULT_DURATION, FeedEvent, local_naive, parse_hours, render_calendar, zone_for

logger = logging.getLogger(__name__)

#: ``PRODID`` kanału publicznego – inny niż pliku uczestnika, bo to inny program w tym samym serwisie.
PRODID = "-//Olimpiada Kwantowa//Kalendarz publiczny//PL"
PRODID_TEMPLATE = "-//%(competition)s//Kalendarz publiczny//PL"

#: Czas życia wpisu kanału w Redisie (sekundy). Unieważnienie i tak przychodzi z licznikami wersji
#: pamięci stron; kwadrans dotyczy tylko zmian robionych z pominięciem sygnałów (komenda, shell).
CACHE_TTL_SECONDS = 900
CACHE_PREFIX = "calendar_feed:v1"


def workshops_url(base_url: str) -> str:
    """Bezwzględny adres ``/warsztaty/`` – ta sama reguła, co odnośnik warsztatu na pasku linii czasu.

    Prefiks skryptu (konkurs pod prefiksem ścieżki) bierzemy z żądania, tak jak ``timeline._workshop_items``;
    ``base_url`` to sam schemat i host. Bez adresu bazowego (wywołanie spoza żądania) – pusty napis,
    bo adres względny w ``DESCRIPTION`` nie byłby klikalny w żadnym kliencie kalendarza.
    """
    if not base_url:
        return ""
    return f"{base_url.rstrip('/')}{get_script_prefix()}{WORKSHOPS_SLUG}/"


def workshop_event(row: dict, *, competition, page_url: str = "", last_modified=None) -> FeedEvent:
    """Wiersz harmonogramu (``workshop_rows``) jako wydarzenie – wspólne dla kanału i linku Google.

    ``UID`` z klucza warsztatu (``workshop_key``: data + temat) i identyfikatora konkursu: ten sam
    klucz, przy którym zapisuje się obecność, więc poprawka godzin albo prowadzącego **aktualizuje**
    wpis u subskrybenta, a nie dokłada drugiego. Identyfikator konkursu jest w ``UID`` dlatego, że
    człon domenowy (``uid_domain``) bez flagi marki jest wspólny dla wszystkich konkursów instalacji,
    a dwie olimpiady mogą mieć warsztat o tym samym temacie tego samego dnia.
    """
    topic = row.get("topic", "")
    summary = _("Warsztaty: %(topic)s") % {"topic": topic} if topic else _("Warsztaty")
    parts = []
    lecturer = (row.get("lecturer") or "").strip()
    if lecturer:
        parts.append(_("Prowadzący: %(lecturer)s") % {"lecturer": lecturer})
    parts.append(_("Warsztaty online. Link do spotkania podajemy zalogowanym uczestnikom w Wiadomościach."))
    if page_url:
        parts.append(_("Szczegóły: %(url)s") % {"url": page_url})
    common = {
        "uid": f"workshop-c{competition.pk}-{row['key']}@{uid_domain(competition)}",
        "summary": summary,
        "description": "\n".join(parts),
        "location": _("online"),
        "url": page_url,
        "last_modified": last_modified,
    }
    hours = parse_hours(row.get("time", ""))
    day: date = row["date_value"]
    if hours is None:
        return FeedEvent(start_date=day, end_date=day, **common)
    start = datetime.combine(day, hours[0])
    end = datetime.combine(day, hours[1]) if hours[1] is not None else start + DEFAULT_DURATION
    return FeedEvent(starts_at=start, ends_at=end, **common)


def _workshop_events(competition, base_url: str) -> list[FeedEvent]:
    page = workshops_page(competition)
    if page is None:
        return []
    page_url = workshops_url(base_url)
    modified = page.last_published_at
    return [
        workshop_event(row, competition=competition, page_url=page_url, last_modified=modified)
        for row in workshop_rows(page)
    ]


def _absolute(url: str, base_url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/") and base_url:
        return f"{base_url.rstrip('/')}{url}"
    return ""


def _stage_events(edition, zone, domain: str) -> list[FeedEvent]:
    """Etapy bieżącej edycji – bez treningowego, jak na osi czasu.

    Etap z dniami wydarzenia (finał stacjonarny) idzie jako jedno wydarzenie całodniowe z miejscem:
    to są dni, na które się przyjeżdża, a sesja oddawania prac jest w programie zjazdu. Etap bez nich
    dostaje dwie chwile, o które pytają uczestnicy: otwarcie i termin oddania prac (``deadline_at``,
    bez tolerancji – tolerancja jest zabezpieczeniem serwera, a nie terminem do ogłaszania).
    """
    events = []
    for stage in edition.stages.exclude(kind=StageKind.TRAINING).order_by("opens_at", "id"):
        name = stage.display_name
        if stage.event_range is not None:
            start, end = stage.event_range
            events.append(
                FeedEvent(
                    uid=f"stage-{stage.pk}-event@{domain}",
                    summary=name,
                    location=stage.location,
                    start_date=start,
                    end_date=end,
                )
            )
            continue
        closing = (
            _("%(stage)s: termin oddania prac")
            if stage.format == StageFormat.SUBMISSIONS
            else _("%(stage)s: zamknięcie etapu")
        )
        events.append(
            FeedEvent(
                uid=f"stage-{stage.pk}-opens@{domain}",
                summary=_("%(stage)s: otwarcie") % {"stage": name},
                location=stage.location,
                starts_at=local_naive(stage.opens_at, zone),
            )
        )
        events.append(
            FeedEvent(
                uid=f"stage-{stage.pk}-deadline@{domain}",
                summary=closing % {"stage": name},
                location=stage.location,
                starts_at=local_naive(stage.deadline_at, zone),
            )
        )
    return events


def _registration_events(edition, zone, domain: str) -> list[FeedEvent]:
    """Otwarcie i zamknięcie rejestracji – tylko dla rejestracji włączonej (reguła paska)."""
    if not edition.registration_enabled:
        return []
    events = []
    if edition.registration_opens_at is not None:
        events.append(
            FeedEvent(
                uid=f"edition-{edition.pk}-registration-opens@{domain}",
                summary=_("Rejestracja uczestników: start"),
                starts_at=local_naive(edition.registration_opens_at, zone),
            )
        )
    if edition.registration_closes_at is not None:
        events.append(
            FeedEvent(
                uid=f"edition-{edition.pk}-registration-closes@{domain}",
                summary=_("Rejestracja uczestników: koniec"),
                starts_at=local_naive(edition.registration_closes_at, zone),
            )
        )
    return events


def _edition_events(edition, domain: str, base_url: str) -> list[FeedEvent]:
    """Wydarzenia koordynatora pokazywane na osi czasu – całodniowe, jak na pasku."""
    return [
        FeedEvent(
            uid=f"event-{event.pk}@{domain}",
            summary=event.title,
            description=event.note,
            url=_absolute(event.url, base_url),
            start_date=event.date_range[0],
            end_date=event.date_range[1],
        )
        for event in edition.events.filter(show_on_timeline=True).order_by("starts_on", "id")
    ]


def _sort_key(event: FeedEvent):
    start = (
        event.starts_at
        if event.starts_at is not None
        else datetime.combine(event.start_date, datetime.min.time())
    )
    return (start, event.uid)


def feed_events(competition, *, base_url: str = "") -> list[FeedEvent]:
    """Wszystkie wydarzenia kanału tego konkursu, od najwcześniejszego.

    Edycja wyłącznie bieżąca **tego** konkursu (``current_edition(competition)``) – etapów innego
    konkursu nie ma tu skąd wziąć, a warsztaty idą przez ``workshops_page(competition)``, czyli przez
    drzewo stron jego witryny.
    """
    zone = zone_for(getattr(competition, "time_zone", None))
    domain = uid_domain(competition)
    events = _workshop_events(competition, base_url)
    edition = current_edition(competition)
    if edition is not None:
        events += _stage_events(edition, zone, domain)
        events += _registration_events(edition, zone, domain)
        events += _edition_events(edition, domain, base_url)
    events.sort(key=_sort_key)
    return events


def build_feed(competition, *, base_url: str = "", now=None) -> str:
    """Tekst pliku ``.ics`` konkursu – w jego języku domyślnym, nie w języku żądania.

    Serwer Google czy Apple pobierający subskrypcję nie ma preferencji językowych, a treść tego samego
    adresu ma być stała: plik w języku pierwszego pobierającego zmieniałby tytuły wszystkich wpisów
    u wszystkich subskrybentów przy każdym przeliczeniu.
    """
    language = getattr(competition, "default_language", "") or None
    with translation.override(language):
        events = feed_events(competition, base_url=base_url)
        return render_calendar(
            events,
            zone=zone_for(getattr(competition, "time_zone", None)),
            name=competition_name(competition),
            prodid=branded_text(PRODID_TEMPLATE, PRODID, competition),
            now=now or timezone.now(),
        )


def _cache_key(competition, base_url: str) -> str | None:
    """Klucz wpisu z licznikami wersji pamięci stron – ``None``, gdy ta pamięć jest wyłączona.

    Liczniki (wydanie, wersja globalna, wersja witryny konkursu) są prywatne dla ``page_cache``, ale to
    jest dokładnie ta sama umowa unieważniania i własna kopia sygnałów rozjechałaby się z tamtą przy
    pierwszym nowym źródle zmian. Adres bazowy jest w kluczu, bo trafia do treści (link do warsztatów).
    """
    from apps.web import page_cache

    if not page_cache.feature_enabled():
        return None
    global_version, site_version = page_cache._versions(competition.pk)
    return (
        f"{CACHE_PREFIX}:{page_cache._release()}:{global_version}:{site_version}:{competition.pk}:"
        f"{hashlib.sha256(base_url.encode('utf-8'), usedforsecurity=False).hexdigest()[:16]}"
    )


def cached_feed(competition, *, base_url: str = "") -> str:
    """:func:`build_feed` przez pamięć podręczną. Awaria Redisa = plik liczony na żywo, nie 500."""
    key = _cache_key(competition, base_url)
    if key is None:
        return build_feed(competition, base_url=base_url)
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 - awaria pamięci nie ma prawa zabrać kalendarza
        logger.warning("Kanał ICS: błąd pamięci podręcznej (get).", exc_info=True)
        cached = None
    if cached is not None:
        return cached
    body = build_feed(competition, base_url=base_url)
    try:
        cache.set(key, body, CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("Kanał ICS: błąd pamięci podręcznej (set).", exc_info=True)
    return body
