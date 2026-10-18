"""Składanie publicznego kalendarza iCalendar (CAL-01) – część bez bazy danych.

Ten moduł nie wie nic o etapach, stronach CMS ani konkursach: dostaje gotowe wydarzenia
(:class:`FeedEvent`) i nazwę strefy, a oddaje tekst pliku. Rozdział jest po to, żeby to, co w RFC 5545
najłatwiej zepsuć – strefa czasowa, zawijanie, cytowanie – dało się sprawdzić testem bez bazy,
a zbieranie terminów (``apps.calendar_feed.feed``) osobno.

Dlaczego tekst pisany ręcznie, a nie biblioteką ``icalendar``: powód jest ten sam, co przy pliku
uczestnika (``apps.cms.calendar``) – kilkanaście pól i jedna zależność mniej w obrazie, audycie licencji
i aktualizacjach. **Cytowanie i zawijanie bierzemy stamtąd** (``escape_text``, ``_fold``), żeby oba pliki
serwisu nie rozjechały się w regule, którą RFC opisuje raz.

Różnica wobec pliku uczestnika jest jedna i świadoma: tam momenty idą w UTC (``…Z``), tu – w **czasie
lokalnym konkursu z ``TZID``** i z blokiem ``VTIMEZONE``. Warsztat „w sobotę o 11:00” ma zostać o 11:00
także wtedy, gdy klient kalendarza źle zna reguły zmiany czasu albo gdy ktoś przegląda plik ręcznie,
a zasubskrybowany kalendarz po przestawieniu zegarów w październiku nie może przesunąć zajęć o godzinę.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings

# ``_fold`` jest prywatne dla modułu kalendarza uczestnika, ale reguła zawijania do 75 oktetów jest
# jedna dla całego serwisu – własna kopia rozjechałaby się przy pierwszej poprawce (patrz docstring).
from apps.cms.calendar import _fold, escape_text

#: Ile trwa warsztat, którego harmonogram podaje tylko godzinę początku („11:00”). Półtorej godziny,
#: bo tyle trwa typowy blok zajęć; dokładniejszej odpowiedzi tabela redakcyjna po prostu nie zna.
DEFAULT_DURATION = timedelta(minutes=90)

#: Punkt zerowy ``SEQUENCE``. Numer wersji wydarzenia liczymy jako sekundy od tej chwili do ostatniej
#: zmiany źródła – rośnie przy każdej publikacji, bez tabeli z licznikami. Sekundy, a nie minuty, bo
#: redaktor potrafi opublikować poprawkę literówki pół minuty po pierwszej wersji; 32-bitowa liczba
#: całkowita (RFC 5545 §3.3.8) wystarcza do 2094 roku. Stała, bo zmiana punktu zerowego cofnęłaby
#: numery, a RFC 5545 §3.8.7.4 wymaga, żeby nie malały.
SEQUENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: Podpowiedź dla klienta, jak często odświeżać subskrypcję (RFC 7986 ``REFRESH-INTERVAL`` i starszy
#: odpowiednik Outlooka ``X-PUBLISHED-TTL``). Sześć godzin: harmonogram zmienia się rzadko, a Google
#: i tak odświeża we własnym rytmie – to tylko górna granica dla klientów, które słuchają.
REFRESH_INTERVAL = "PT6H"

#: Godzina w harmonogramie redakcyjnym: „11:00”, „11.00”, opcjonalnie z końcem po myślniku albo „do”.
_CLOCK = r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)"
HOURS_RE = re.compile(rf"{_CLOCK}(?:\s*(?:-|–|—|do)\s*{_CLOCK})?")

#: Hosty pokoi wideo. Kanał publiczny **nie może** nieść linku do spotkania (CAL-01 § 0): link jest
#: poświadczeniem wejścia, a plik subskrypcji bywa synchronizowany do cudzych usług. Dzisiejsze źródła
#: kanału takiego linku nie mają, ale wydarzenie koordynatora ma pole „odnośnik” i wolny „dopisek” –
#: wklejony tam adres Meet trafiłby do kalendarza każdego subskrybenta. Ten filtr jest bezpiecznikiem.
MEETING_HOSTS = (
    "meet.google.com",
    "zoom.us",
    "teams.microsoft.com",
    "teams.live.com",
    "meet.jit.si",
    "whereby.com",
    "webex.com",
)
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class FeedEvent:
    """Jedno wydarzenie kanału: albo całodniowe (``start_date``), albo z godziną (``starts_at``).

    Moment z godziną jest **naiwną godziną lokalną** strefy konkursu – tak, jak stoi w pliku po
    ``TZID=``. Warsztat ma w harmonogramie godzinę ścienną („11:00”), a nie moment w UTC, i dopiero
    taki zapis przeżywa zmianę czasu bez przesunięcia. ``ends_at = None`` znaczy „chwila” (termin
    oddania prac): RFC 5545 §3.6.1 dopuszcza ``VEVENT`` bez ``DTEND`` i czyta go jako moment.
    """

    uid: str
    summary: str
    description: str = ""
    location: str = ""
    url: str = ""
    start_date: date | None = None
    #: Ostatni dzień zakresu **włącznie** – w pliku staje się wyłącznym ``DTEND`` (dzień po nim).
    end_date: date | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    last_modified: datetime | None = None

    @property
    def is_all_day(self) -> bool:
        return self.starts_at is None


def zone_for(name: str | None) -> ZoneInfo:
    """Strefa konkursu, a przy wpisie nieznanym bazie IANA – strefa instalacji (``TIME_ZONE``)."""
    try:
        return ZoneInfo(name or settings.TIME_ZONE)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo(settings.TIME_ZONE)


def parse_hours(text: str) -> tuple[time, time | None] | None:
    """Godziny z pola „godziny” harmonogramu: ``(początek, koniec | None)`` albo ``None``.

    Pole jest tekstem redakcyjnym („11:00–14:00”, „11.00-13.00”, „od 17:00”, „do potwierdzenia”),
    więc czytamy z niego wyłącznie to, co jednoznacznie jest zegarem. Koniec nie później niż początek
    (literówka albo zajęcia po północy) odrzucamy i zostaje sam początek – lepszy warsztat o domyślnej
    długości niż wydarzenie, które kończy się, zanim się zaczęło.
    """
    match = HOURS_RE.search(text or "")
    if match is None:
        return None
    start = time(int(match.group(1)), int(match.group(2)))
    if match.group(3) is None:
        return start, None
    end = time(int(match.group(3)), int(match.group(4)))
    return (start, end) if end > start else (start, None)


def local_naive(moment: datetime, zone: ZoneInfo) -> datetime:
    """Moment z bazy (aware) jako godzina ścienna strefy konkursu, bez informacji o strefie."""
    return moment.astimezone(zone).replace(tzinfo=None)


def sequence_for(last_modified: datetime | None) -> int:
    """``SEQUENCE`` z chwili ostatniej zmiany – sekundy od :data:`SEQUENCE_EPOCH`, nigdy ujemne."""
    if last_modified is None:
        return 0
    return max(0, int((last_modified - SEQUENCE_EPOCH).total_seconds()))


def is_meeting_url(url: str) -> bool:
    """Czy adres prowadzi do pokoju wideo (Meet, Zoom, Teams, Jitsi…) – patrz :data:`MEETING_HOSTS`."""
    candidate = url if "://" in url else f"https://{url}"
    host = (urlsplit(candidate).hostname or "").lower()
    return any(host == known or host.endswith(f".{known}") for known in MEETING_HOSTS)


def strip_meeting_links(text: str) -> str:
    """Tekst bez adresów pokoi wideo. Pozostałe adresy (strona warsztatów, regulamin) zostają."""
    if not text:
        return ""
    return _URL_RE.sub(lambda match: "" if is_meeting_url(match.group(0)) else match.group(0), text).strip()


# --- VTIMEZONE ------------------------------------------------------------------------------------


def _offset(seconds: int, zone: ZoneInfo) -> timedelta:
    return datetime.fromtimestamp(seconds, UTC).astimezone(zone).utcoffset()


def _transitions(zone: ZoneInfo, first_year: int, last_year: int) -> list[tuple[int, timedelta, timedelta]]:
    """Zmiany przesunięcia strefy w latach ``first_year..last_year`` jako ``(sekunda UTC, z, na)``.

    Krok jest dobowy, a dokładną sekundę znajduje bisekcja – żadna strefa IANA nie zmienia czasu
    dwa razy w ciągu doby, a trzy lata to tysiąc kroków, czyli ułamek milisekundy.
    """
    start = int(datetime(first_year, 1, 1, tzinfo=UTC).timestamp())
    end = int(datetime(last_year + 1, 1, 1, tzinfo=UTC).timestamp())
    found = []
    previous = _offset(start, zone)
    moment = start
    while moment < end:
        step = min(moment + 86400, end)
        current = _offset(step, zone)
        if current != previous:
            low, high = moment, step
            while high - low > 1:
                middle = (low + high) // 2
                if _offset(middle, zone) == previous:
                    low = middle
                else:
                    high = middle
            found.append((high, previous, current))
            previous = current
        moment = step
    return found


def _format_offset(delta: timedelta) -> str:
    """``+0100`` / ``-0330`` – zapis ``UTC-OFFSET`` z RFC 5545 §3.3.14 (sekundy tylko, gdy są)."""
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{sign}{hours:02d}{minutes:02d}{seconds:02d}" if seconds else f"{sign}{hours:02d}{minutes:02d}"


def _local_stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _observance(zone: ZoneInfo, moment: int, before: timedelta, after: timedelta) -> list[str]:
    """Jeden blok ``STANDARD``/``DAYLIGHT``. ``DTSTART`` to godzina ścienna **sprzed** zmiany."""
    instant = datetime.fromtimestamp(moment, UTC)
    local = instant.astimezone(zone)
    kind = "DAYLIGHT" if local.dst() else "STANDARD"
    wall = (instant + before).replace(tzinfo=None)
    return [
        f"BEGIN:{kind}",
        f"DTSTART:{_local_stamp(wall)}",
        f"TZOFFSETFROM:{_format_offset(before)}",
        f"TZOFFSETTO:{_format_offset(after)}",
        f"TZNAME:{escape_text(local.tzname() or '')}",
        f"END:{kind}",
    ]


def vtimezone_lines(zone: ZoneInfo, first_year: int, last_year: int) -> list[str]:
    """Blok ``VTIMEZONE`` z bazy IANA na lata wydarzeń – jawne przejścia zamiast reguł ``RRULE``.

    Jawne przejścia, bo reguła („ostatnia niedziela marca o 2:00”) jest prawdziwa tylko dla części
    stref i części lat, a ``zoneinfo`` zna faktyczne daty – tak samo poprawne dla Warszawy, jak dla
    strefy konkursu za oceanem. Blok zaczyna się obserwacją na początek pierwszego roku (z tym samym
    przesunięciem po obu stronach), żeby wydarzenie sprzed pierwszej zmiany czasu też miało swoją.
    Strefa bez zmian czasu dostaje dokładnie tę jedną obserwację.
    """
    start = int(datetime(first_year, 1, 1, tzinfo=UTC).timestamp())
    initial = _offset(start, zone)
    lines = ["BEGIN:VTIMEZONE", f"TZID:{zone.key}"]
    lines.extend(_observance(zone, start, initial, initial))
    for moment, before, after in _transitions(zone, first_year, last_year):
        lines.extend(_observance(zone, moment, before, after))
    lines.append("END:VTIMEZONE")
    return lines


# --- wydarzenia i plik ----------------------------------------------------------------------------


def _utc_stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _safe_uri(url: str) -> str:
    """Adres do ``URL``: wyłącznie ``http(s)``, bez znaków sterujących i bez pokoi wideo.

    ``URL`` ma typ ``URI``, a nie ``TEXT`` (RFC 5545 §3.8.4.6), więc **nie** przechodzi przez
    ``escape_text`` – przecinek w adresie z ukośnikiem przed nim byłby innym adresem. Zamiast tego
    odrzucamy to, co mogłoby rozbić wiersz (CR, LF, inne znaki sterujące).
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")) or any(ord(char) < 32 for char in url):
        return ""
    return "" if is_meeting_url(url) else url


def event_lines(event: FeedEvent, stamp: str, zone: ZoneInfo) -> list[str]:
    """Jeden ``VEVENT`` – pola tekstowe przez ``escape_text`` i filtr linków do spotkań."""
    lines = ["BEGIN:VEVENT", f"UID:{event.uid}", f"DTSTAMP:{stamp}"]
    if event.is_all_day:
        last = event.end_date or event.start_date
        lines.append(f"DTSTART;VALUE=DATE:{event.start_date:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}")
    else:
        lines.append(f"DTSTART;TZID={zone.key}:{_local_stamp(event.starts_at)}")
        if event.ends_at is not None:
            lines.append(f"DTEND;TZID={zone.key}:{_local_stamp(event.ends_at)}")
    lines.append(f"SUMMARY:{escape_text(strip_meeting_links(event.summary))}")
    description = strip_meeting_links(event.description)
    if description:
        lines.append(f"DESCRIPTION:{escape_text(description)}")
    location = strip_meeting_links(event.location)
    if location:
        lines.append(f"LOCATION:{escape_text(location)}")
    url = _safe_uri(event.url)
    if url:
        lines.append(f"URL:{url}")
    if event.last_modified is not None:
        lines.append(f"LAST-MODIFIED:{_utc_stamp(event.last_modified)}")
    lines.append(f"SEQUENCE:{sequence_for(event.last_modified)}")
    # Całodniowe i „chwile” nie zajmują czasu w kalendarzu – termin oddania prac o 23:59 nie może
    # oznaczyć uczestnika jako zajętego, kiedy ktoś proponuje mu spotkanie.
    lines.append(
        "TRANSP:OPAQUE" if event.ends_at is not None and not event.is_all_day else "TRANSP:TRANSPARENT"
    )
    lines.append("END:VEVENT")
    return lines


def render_calendar(events: list[FeedEvent], *, zone: ZoneInfo, name: str, prodid: str, now: datetime) -> str:
    """Kompletny plik ``.ics``: koperta, ``VTIMEZONE`` (gdy jest choć jeden moment) i wydarzenia.

    Kalendarz bez wydarzeń jest poprawnym plikiem i taki wychodzi – subskrybent ma zobaczyć pusty
    kalendarz, a nie błąd pobierania.
    """
    stamp = _utc_stamp(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{escape_text(prodid)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}",
        f"NAME:{escape_text(name)}",
        f"X-WR-TIMEZONE:{zone.key}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH_INTERVAL}",
        f"X-PUBLISHED-TTL:{REFRESH_INTERVAL}",
    ]
    years = [event.starts_at.year for event in events if not event.is_all_day]
    if years:
        lines.extend(vtimezone_lines(zone, min(years), max(years)))
    for event in events:
        lines.extend(event_lines(event, stamp, zone))
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"


def etag_for(body: str) -> str:
    """``ETag`` treści **bez** ``DTSTAMP`` – ten wiersz zmienia się przy każdym złożeniu pliku.

    Bez wycięcia każde przeliczenie (koniec życia wpisu w pamięci podręcznej) dawałoby nowy znacznik
    i klient pobierałby pełny plik, choć żadne wydarzenie się nie zmieniło.
    """
    stable = "\r\n".join(line for line in body.split("\r\n") if not line.startswith("DTSTAMP:"))
    return '"' + hashlib.sha256(stable.encode("utf-8"), usedforsecurity=False).hexdigest()[:32] + '"'


# --- odnośniki do klientów kalendarza -------------------------------------------------------------


def webcal_url(feed_url: str) -> str:
    """``https://host/kalendarz.ics`` → ``webcal://host/kalendarz.ics`` (Outlook, Apple Calendar)."""
    return "webcal://" + feed_url.split("://", 1)[-1]


def google_subscribe_url(feed_url: str) -> str:
    """Odnośnik „Dodaj do Google Calendar” – subskrypcja kanału po adresie ``webcal://``."""
    return "https://calendar.google.com/calendar/r?cid=" + quote(webcal_url(feed_url), safe="")


def google_event_url(event: FeedEvent, zone: ZoneInfo) -> str:
    """Szablon pojedynczego wydarzenia w Google Calendar (``action=TEMPLATE``).

    Godziny idą jako czas ścienny z ``ctz`` – ten sam zapis, co w pliku – a wydarzenie całodniowe
    jako para dat z wyłącznym końcem, jak ``DTEND;VALUE=DATE``.
    """
    if event.is_all_day:
        last = event.end_date or event.start_date
        dates = f"{event.start_date:%Y%m%d}/{last + timedelta(days=1):%Y%m%d}"
    else:
        end = event.ends_at or event.starts_at
        dates = f"{_local_stamp(event.starts_at)}/{_local_stamp(end)}"
    params = {"action": "TEMPLATE", "text": event.summary, "dates": dates, "ctz": zone.key}
    details = strip_meeting_links(event.description)
    if details:
        params["details"] = details
    if event.location:
        params["location"] = strip_meeting_links(event.location)
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)
