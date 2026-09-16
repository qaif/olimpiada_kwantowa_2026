"""Kalendarz osobisty uczestnika – lista terminów i plik iCalendar do subskrypcji.

Skąd biorą się terminy: z ``apps.cms.timeline.timeline_events``, czyli z tego samego złożenia
czterech źródeł, na którym stoi pasek linii czasu w nagłówku (etapy, wydarzenia koordynatora,
okno rejestracji, warsztaty). Kalendarz uczestnika **nie** buduje własnej listy – gdyby budował,
serwis miałby dwa kalendarze tej samej edycji i prędzej czy później dwa różne terminy finału.
Dokładamy do niej dokładnie jedną pozycję, której pasek mieć nie może, bo jest prywatna: własny
termin rozmowy kwalifikacyjnej.

Dlaczego iCalendar pisany ręcznie, a nie biblioteką: plik jest tekstem o kilkunastu polach
i jednym nietrywialnym fragmencie (zawijanie wierszy i cytowanie znaków), a każda zależność
wchodzi do obrazu produkcyjnego, do audytu licencji i do ścieżki aktualizacji bezpieczeństwa.
Kontrakt, którego trzymamy się z RFC 5545:

- wiersze rozdziela **CRLF**, a wiersz dłuższy niż 75 oktetów jest zawijany spacją na początku
  kontynuacji (``_fold``). Liczymy oktety UTF-8, a nie znaki – polskie diakrytyki zajmują po dwa,
- w wartościach tekstowych cytujemy ``\\``, ``;``, ``,`` i zamieniamy złamanie wiersza na ``\\n``,
- wydarzenia całodniowe idą jako ``DTSTART;VALUE=DATE`` z **wyłącznym** ``DTEND`` (dzień po
  ostatnim), bo tak RFC definiuje zakres dat. Termin rozmowy ma godziny, więc idzie w UTC z ``Z``,
- ``UID`` musi być stabilny między pobraniami, inaczej każda subskrypcja dokłada duplikaty
  zamiast aktualizować wpis. Pozycje osi czasu nie mają identyfikatora w bazie (część z nich
  pochodzi z treści redakcyjnej), więc ``UID`` liczymy ze skrótu rodzaju, tytułu i dat.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from django.utils import timezone

from apps.competitions.models import InterviewBooking
from apps.competitions.services import current_edition

# ``_localdate`` i ``_status_for`` są prywatne dla modułu linii czasu, ale kalendarz jest jego
# sąsiadem w tej samej aplikacji i musi liczyć „minione / teraz / przed nami” **dokładnie tak
# samo**. Własna kopia tych trzech porównań rozjechałaby się przy pierwszej zmianie granicy
# (dziś jest nią dzień, nie godzina) i ten sam etap byłby „miniony” w nagłówku, a „trwa”
# w kalendarzu uczestnika.
from .timeline import TL_PAST, _localdate, _status_for, format_date_range, timeline_events

#: Człon domenowy ``UID``-ów. RFC wymaga globalnej unikalności, a nie adresu, pod który da się
#: napisać – dlatego stała, a nie nazwa hosta z żądania: ten sam wpis pobrany z ``localhost``
#: i z produkcji musi mieć ten sam identyfikator, bo inaczej kalendarz zobaczy dwa wydarzenia.
UID_DOMAIN = "olimpiadakwantowa.pl"

#: Identyfikator programu generującego plik. RFC 5545 wymaga ``PRODID`` i nie dopuszcza pustego.
PRODID = "-//Olimpiada Kwantowa//Kalendarz uczestnika//PL"

#: Nazwa pliku w nagłówku ``Content-Disposition``. Bez polskich znaków, bo nagłówek HTTP jest
#: w ASCII, a ``filename*`` byłby tu kosmetyką dla jednego słowa.
ICS_FILENAME = "olimpiada-kwantowa.ics"

#: Maksymalna długość wiersza w oktetach (RFC 5545 §3.1). Kontynuacja zaczyna się od spacji,
#: więc na jej treść zostaje o jeden oktet mniej.
LINE_OCTETS = 75

#: Rodzaj pozycji „własny termin rozmowy”. Pozostałe rodzaje pochodzą z ``timeline._item``.
KIND_INTERVIEW = "interview"

#: Podpisy rodzajów pozycji – to, co uczestnik czyta w kolumnie „rodzaj”.
KIND_LABELS = {
    "stage": "etap",
    "event": "wydarzenie",
    "registration": "rejestracja",
    "workshop": "warsztaty",
    KIND_INTERVIEW: "rozmowa kwalifikacyjna",
}


@dataclass(frozen=True)
class CalendarItem:
    """Jeden termin kalendarza – w postaci wspólnej dla tabeli w panelu i dla pliku ``.ics``.

    Pozycja jest albo całodniowa (``starts_at is None``), albo ma godziny; nigdy jedno i drugie.
    Rozdział jest tutaj, a nie w generatorze pliku, bo to różnica **faktu**, a nie zapisu: zjazd
    finałowy ogłasza się dniami, a rozmowa kwalifikacyjna godziną, na którą trzeba być.
    """

    kind: str
    title: str
    start: date
    end: date
    note: str = ""
    url: str = ""
    #: Stan względem dzisiaj: ``past``/``current``/``upcoming`` – ten sam słownik, co na pasku
    #: linii czasu (``timeline._status_for``), żeby oba ekrany mówiły o „teraz” to samo.
    status: str = ""
    #: Godziny pozycji terminowej. ``None`` dla wydarzeń całodniowych.
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def is_past(self) -> bool:
        return self.status == TL_PAST

    @property
    def is_all_day(self) -> bool:
        return self.starts_at is None

    @property
    def dates(self) -> str:
        """Termin jednym wyrażeniem – tym samym, którym ogłasza go reszta serwisu.

        Zakres dni idzie przez ``timeline.format_date_range``, żeby ten sam finał nie brzmiał
        w kalendarzu inaczej niż na ``/harmonogram/``. Pozycja z godzinami dopisuje je do daty:
        na rozmowę przychodzi się o konkretnej godzinie, a nie „któregoś dnia”.
        """
        if self.is_all_day:
            return format_date_range(self.start, self.end)
        local_start = timezone.localtime(self.starts_at)
        local_end = timezone.localtime(self.ends_at)
        return f"{format_date_range(self.start, self.start)}, {local_start:%H:%M}–{local_end:%H:%M}"

    @property
    def uid(self) -> str:
        """Identyfikator stabilny między pobraniami – skrót rodzaju, tytułu i obu dat.

        Skrót, a nie klucz główny, bo połowa pozycji kalendarza nie ma klucza głównego: warsztaty
        pochodzą z treści redakcyjnej, a okno rejestracji jest polem edycji. Skrót jest tu
        wyłącznie generatorem niepowtarzalnego napisu (RFC 5545 §3.8.4.7 nie wymaga od ``UID``
        niczego poza unikalnością), a nie zabezpieczeniem – stąd ``usedforsecurity=False``
        i obcięcie do 32 znaków, żeby wiersz pliku mieścił się bez zawijania.
        """
        seed = f"{self.kind}|{self.title}|{self.start.isoformat()}|{self.end.isoformat()}"
        digest = hashlib.sha256(seed.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"{digest[:32]}@{UID_DOMAIN}"


def _interview_items(participant, edition, now) -> list[CalendarItem]:
    """Własny termin rozmowy kwalifikacyjnej – o ile uczestnik jest na jakiś zapisany.

    Adresu pokoju wideo (``slot.meeting_url``) w kalendarzu **nie** ma. Link, pod którym wchodzi
    się bez logowania, jest de facto poświadczeniem, a plik ``.ics`` bywa synchronizowany do
    cudzych usług i przesyłany dalej razem z całym kalendarzem.
    """
    if participant is None or edition is None:
        return []
    bookings = (
        InterviewBooking.objects.filter(entry__participant=participant, entry__stage__edition=edition)
        .select_related("slot", "slot__stage")
        .order_by("slot__starts_at")
    )
    today = _localdate(now)
    items = []
    for booking in bookings:
        slot = booking.slot
        start = timezone.localtime(slot.starts_at).date()
        end = timezone.localtime(slot.ends_at).date()
        items.append(
            CalendarItem(
                kind=KIND_INTERVIEW,
                title=f"Rozmowa kwalifikacyjna: {slot.stage.display_name}",
                start=start,
                end=end,
                note=slot.note,
                status=_status_for(start, end, today),
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
            )
        )
    return items


def _timeline_items(edition, now) -> list[CalendarItem]:
    """Kalendarz edycji z linii czasu, przepisany na pozycje całodniowe."""
    return [
        CalendarItem(
            kind=item["kind"],
            title=item["title"],
            start=item["start"],
            end=item["end"],
            note=item["note"],
            url=item["url"],
            status=item["status"],
        )
        for item in timeline_events(edition, now)
    ]


def participant_calendar(participant, *, edition=None, now=None) -> list[CalendarItem]:
    """Terminy uczestnika w bieżącej edycji, uporządkowane od najwcześniejszego.

    Kalendarz jest **kompletny**, a nie zawężony do etapów, w których uczestnik ma wpis: on sam
    najlepiej wie, co go dotyczy, a ukrycie terminu etapu, do którego jeszcze się nie
    zakwalifikował, odebrałoby mu informację, po którą tu przyszedł. Prywatna jest dokładnie
    jedna pozycja – własny termin rozmowy – i tylko ona zależy od tego, kto pyta.
    """
    if edition is None:
        edition = current_edition()
    if edition is None:
        return []
    items = [*_timeline_items(edition, now), *_interview_items(participant, edition, now)]
    items.sort(key=lambda item: (item.start, item.end, item.title))
    return items


# --- iCalendar ----------------------------------------------------------------------------------


def escape_text(value: str) -> str:
    """Cytowanie wartości tekstowej wg RFC 5545 §3.3.11.

    Kolejność ma znaczenie: odwrotny ukośnik musi być podwojony **pierwszy**, inaczej ukośniki
    dopisane przez kolejne podstawienia zostałyby podwojone drugi raz i przecinek w tytule
    wyszedłby z pliku jako ``\\\\,``.
    """
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Zawija wiersz do 75 oktetów, licząc w UTF-8 i nie tnąc znaku na pół.

    Cięcie po znakach Pythona byłoby błędem dla polskich diakrytyków: „ę” to jeden znak i dwa
    oktety, więc wiersz „mieszczący się” w 75 znakach potrafi mieć 90 oktetów i zostać obcięty
    przez klienta pocztowego albo kalendarz.
    """
    chunks: list[str] = []
    current = ""
    budget = LINE_OCTETS
    for char in line:
        size = len(char.encode("utf-8"))
        if len(current.encode("utf-8")) + size > budget:
            chunks.append(current)
            current = char
            # Kontynuacja zaczyna się od spacji, która też liczy się do limitu.
            budget = LINE_OCTETS - 1
        else:
            current += char
    chunks.append(current)
    return [chunks[0], *[f" {chunk}" for chunk in chunks[1:]]]


def _date_value(value: date) -> str:
    return value.strftime("%Y%m%d")


def _utc_value(value: datetime) -> str:
    """Moment w UTC z sufiksem ``Z``. Naiwną datę uznajemy za czas serwisu i dopiero wtedy tłumaczymy."""
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _event_lines(item: CalendarItem, stamp: str) -> list[str]:
    """Jeden ``VEVENT``. Wydarzenie całodniowe ma ``DTEND`` na dzień **po** ostatnim (RFC 5545)."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{item.uid}",
        f"DTSTAMP:{stamp}",
    ]
    if item.is_all_day:
        lines.append(f"DTSTART;VALUE=DATE:{_date_value(item.start)}")
        lines.append(f"DTEND;VALUE=DATE:{_date_value(item.end + timedelta(days=1))}")
    else:
        lines.append(f"DTSTART:{_utc_value(item.starts_at)}")
        lines.append(f"DTEND:{_utc_value(item.ends_at)}")
    lines.append(f"SUMMARY:{escape_text(item.title)}")
    description = "; ".join(part for part in (item.kind_label, item.note) if part)
    if description:
        lines.append(f"DESCRIPTION:{escape_text(description)}")
    if item.url and item.url.startswith(("http://", "https://")):
        # Adres względny („/warsztaty/”) nie jest URI, którym kalendarz umie cokolwiek otworzyć –
        # wpisany do ``URL`` byłby polem psującym import u części klientów.
        lines.append(f"URL:{escape_text(item.url)}")
    lines.append("END:VEVENT")
    return lines


def calendar_ics(items: list[CalendarItem], *, now=None) -> str:
    """Kompletny plik ``.ics`` z podanych pozycji. Wiersze rozdzielone CRLF, zawinięte do 75 oktetów.

    Kalendarz bez wydarzeń też jest poprawnym plikiem i taki właśnie wychodzi: klient subskrybujący
    adres ma zobaczyć pusty kalendarz, a nie błąd pobierania.
    """
    stamp = _utc_value(now or timezone.now())
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Olimpiada Kwantowa",
    ]
    for item in items:
        lines.extend(_event_lines(item, stamp))
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"
