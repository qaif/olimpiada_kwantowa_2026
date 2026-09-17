"""Oś czasu etapów bieżącej edycji – wspólne źródło dla strony głównej i bloku „terminy etapów”.

Moduł stoi osobno od ``apps.cms.models``, bo czytają go **oba** końce importu: ``models.py``
(strona główna) i ``blocks.py`` (blok ``stage_timeline`` w treści redakcyjnej). ``models.py``
importuje ``blocks.py``, więc funkcja w którymkolwiek z tych plików robiłaby cykl albo kopię
reguły – a reguła jest jedna: **terminy pochodzą z ``competitions.Stage`` i tylko stamtąd**.
Redaktor opisuje etapy słowem, ale nie przepisuje dat; strona nie może pokazać innego terminu
niż ten, który egzekwuje serwer.
"""

from __future__ import annotations

import re
from datetime import date

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from apps.competitions.models import Edition, Stage, StageKind
from apps.competitions.services import current_edition
from apps.results.models import ResultsPublication

from .workshops import WORKSHOPS_SLUG, workshop_rows, workshops_page

#: Stan etapu na osi czasu. Klucz jest maszynowy (klasa CSS, test), etykieta – dla czytelnika.
#: Kolejność jest kolejnością rozstrzygania: ogłoszone wyniki wygrywają z „zamknięty”, bo to
#: ostatnia rzecz, która się z etapem stała, i jedyna, po której czytelnik ma gdzie kliknąć.
STATUS_PUBLISHED = ("published", "wyniki ogłoszone")
STATUS_OPEN = ("open", "otwarty")
STATUS_CLOSED = ("closed", "zamknięty")
STATUS_UPCOMING = ("upcoming", "nadchodzący")

#: Ton odznaki per stan – wyłącznie prezentacja, ta sama paleta co ``web_extras.badge_class``.
STATUS_BADGE = {
    "published": "badge badge--ok",
    "open": "badge badge--accent",
    "closed": "badge badge--neutral",
    "upcoming": "badge badge--neutral",
}


#: Półpauza w zakresie dat. Ta sama, którą organizator ma w swoich pismach („4–7 czerwca 2027”).
DASH = "–"


def format_date_range(start: date, end: date) -> str:
    """Zakres dni po polsku: „4–7 czerwca 2027”, „30 maja – 2 czerwca 2027”, „4 czerwca 2027”.

    Jedna funkcja dla obu ekranów osi czasu, bo to **jedno zdanie ogłoszenia**: gdyby strona główna
    i ``/harmonogram/`` formatowały zakres każda po swojemu, finał miałby dwa brzmienia terminu.

    Reguła odstępów nie jest kosmetyczna, tylko zwyczajowa w polskiej typografii: przy wspólnym
    miesiącu zakres jest jednym wyrażeniem i półpauza stoi bez spacji („4–7 czerwca”), a kiedy po
    obu jej stronach są całe daty, spacje oddzielają je od siebie („30 maja – 2 czerwca 2027”).
    Rok powtarzamy tylko wtedy, gdy zakres go przekracza – w innym wypadku byłby drugą kopią tej
    samej liczby w jednym wierszu.

    Nazwy miesięcy bierzemy z ``date_format`` (wzorzec ``E`` = dopełniacz w polskim locale), a nie
    z własnego słownika: to ta sama droga, którą idzie każda inna data w interfejsie
    (``web_extras.local_datetime``), więc nie powstaje drugie tłumaczenie miesięcy do utrzymania.
    """
    if start == end:
        return date_format(start, "j E Y")
    if start.year != end.year:
        return f"{date_format(start, 'j E Y')} {DASH} {date_format(end, 'j E Y')}"
    if start.month != end.month:
        return f"{date_format(start, 'j E')} {DASH} {date_format(end, 'j E Y')}"
    return f"{start.day}{DASH}{end.day} {date_format(end, 'E Y')}"


def is_onsite_event(stage: Stage) -> bool:
    """Czy etap jest wydarzeniem stacjonarnym ogłaszanym jednym terminem (zjazd), a nie oknem.

    Rozstrzygają **wyłącznie** wpisane dni wydarzenia (``event_range``). Wcześniejsza reguła
    „miejsce + okno na różne dni” była domysłem i zawiodła na produkcji: organizator wpisał
    w polu miejsca „Tryb zdalny” przy etapie trwającym trzy miesiące, a strona schowała mu
    godzinę oddania prac za jednym zakresem dat. Termin zjazdu jest decyzją organizatora
    i ma być wpisany wprost w panelu (pola „Termin wydarzenia”), a nie wywnioskowany.
    """
    return stage.event_range is not None


def stage_date_range(stage: Stage) -> str:
    """Termin etapu jako jedno wyrażenie – dla etapów, w których dwie daty są jedną informacją.

    Pierwszeństwo mają wpisane dni wydarzenia: kiedy organizator ogłosił „4–7 czerwca 2027”,
    to jest termin do ogłoszenia, a okno ``opens_at``–``deadline_at`` jest sesją, w której serwer
    przyjmuje pliki (na finale bywa nią kilka godzin jednego dnia). Bez pierwszeństwa strona
    ogłaszałaby godziny sesji jako termin zjazdu – czyli zapraszałaby na jeden dzień z czterech.
    """
    event_range = stage.event_range
    if event_range is not None:
        return format_date_range(*event_range)
    return format_date_range(
        timezone.localtime(stage.opens_at).date(), timezone.localtime(stage.deadline_at).date()
    )


def _status(stage: Stage, *, has_results: bool, now) -> tuple[str, str]:
    if has_results:
        return STATUS_PUBLISHED
    if stage.is_open_for_submissions(now):
        return STATUS_OPEN
    if stage.has_opened(now):
        return STATUS_CLOSED
    return STATUS_UPCOMING


def stage_rows(edition: Edition | None = None, now=None, *, competition=None) -> list[dict]:
    """Etapy edycji uporządkowane po ``opens_at`` wraz ze stanem i informacją o wynikach.

    Bez edycji bierze bieżącą edycję **wskazanego konkursu** – tak woła ją blok w treści
    redakcyjnej, który edycji nie zna, ale stronę (a więc i konkurs) zna. Bez konkursu wchodzi
    odwrót z ``current_edition``: konkurs z kontekstu żądania, a w instalacji jednokonkursowej –
    ten jedyny. ``None`` na wyjściu znaczy „nie wiadomo, czyj harmonogram” i daje pustą tabelę,
    a nie cudzą.

    Jedno zapytanie o etapy i jedno o publikacje: lista rośnie o wiersze, nie o zapytania,
    niezależnie od liczby etapów.

    Etapu treningowego na tej liście nie ma. Oś czasu ogłasza **harmonogram zawodów**, a trening
    jest piaskownicą bez terminu (``TRAINING_DEADLINE``): stanąłby na końcu tabeli ze stanem
    „otwarty” i datą 2099, czyli jako etap, na który wszyscy czekają najdłużej.
    """
    if edition is None:
        edition = current_edition(competition)
    if edition is None:
        return []
    now = now or timezone.now()
    stages = list(edition.stages.exclude(kind=StageKind.TRAINING).order_by("opens_at", "id"))
    published = set(ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True))
    rows = []
    for stage in stages:
        has_results = stage.pk in published
        status, label = _status(stage, has_results=has_results, now=now)
        # Etap stacjonarny trwający kilka dni dostaje gotowy zakres terminu. Liczymy go tutaj,
        # a nie filtrem w szablonie, bo oba ekrany osi czasu mają ogłaszać jedno brzmienie.
        onsite = is_onsite_event(stage)
        rows.append(
            {
                "stage": stage,
                "is_open": stage.is_open_for_submissions(now),
                "has_opened": stage.has_opened(now),
                "has_results": has_results,
                "status": status,
                "status_label": label,
                "badge_class": STATUS_BADGE[status],
                "is_onsite_event": onsite,
                "date_range": stage_date_range(stage) if onsite else "",
            }
        )
    return rows


# --- pasek linii czasu w nagłówku ---------------------------------------------------------------
#
# Dalsza część modułu obsługuje **drugą** oś czasu w serwisie: wąski pasek „terminalowy” stojący
# w nagłówku każdej strony (wzorowany na oi.edu.pl). Stoi tu, a nie w osobnym module, bo pyta
# dokładnie o to samo, co ``stage_rows`` – „co się w tej edycji dzieje i kiedy” – i musi
# odpowiadać tak samo. Różnica jest w zakresie i w postaci: strona główna opisuje **etapy**
# w tabeli, a pasek składa **cały kalendarz edycji** (etapy, wydarzenia koordynatora, warsztaty,
# okno rejestracji) w jedną linijkę znaków.

#: Ile komórek ma pasek. Setka jest wygodna dwa razy: ułamek upływu osi to wprost numer komórki,
#: a cały pasek (102 znaki z klamrami) mieści się w jednym wierszu monospace w rozmiarze, który
#: na desktopie jest jeszcze czytelny. Skalowanie do szerokości okna robi arkusz (``clamp``),
#: a nie ta liczba – inaczej ta sama strona miałaby różną treść na różnych ekranach.
BAR_SIZE = 100

#: Stan wydarzenia na pasku. Trzy, a nie cztery jak w tabeli etapów: pasek nie ma miejsca na
#: rozróżnienie „zamknięty” od „wyniki ogłoszone”, a odpowiada na pytanie „gdzie jesteśmy”.
TL_PAST = "past"
TL_CURRENT = "current"
TL_UPCOMING = "upcoming"

#: Pierwszeństwo przy nakładaniu się wydarzeń na tej samej komórce. „Teraz” wygrywa zawsze:
#: czerwony odcinek jest jedyną informacją, dla której czytelnik w ogóle patrzy na pasek, więc
#: nie może go przykryć sąsiad, który akurat zaczyna się tego samego dnia.
_STATUS_PRIORITY = {TL_PAST: 0, TL_UPCOMING: 1, TL_CURRENT: 2}

#: Klucz i czas życia bufora. Pięć minut to kompromis: pasek renderuje się na **każdej** stronie,
#: a kalendarz edycji zmienia się kilka razy w roku. Wydarzenia koordynatora czyszczą bufor same
#: (``invalidate_timeline_cache``), więc pięć minut dotyczy wyłącznie zmian robionych inną drogą
#: (etapy z panelu, treść strony warsztatów, okno rejestracji).
CACHE_PREFIX = "cms:timeline-strip"
CACHE_TTL_SECONDS = 300

#: „I edycja 2026/2027”, „XV (2026/2027)” – z obu wyjmujemy rocznik szkolny i numer edycji.
#: Czytamy ``year_label``, bo to jedyne miejsce, w którym numer edycji w ogóle istnieje; brak
#: dopasowania nie jest awarią, tylko brakiem nagłówka nad paskiem.
SCHOOL_YEAR_RE = re.compile(r"(\d{4})\s*/\s*(\d{4})")
EDITION_NUMBER_RE = re.compile(r"^\s*([IVXLCDM]+|\d+)(?=[\s.)]|$)")

#: Pierwszy i ostatni dzień roku szkolnego – oś zapasowa dla edycji, która ma mniej niż dwa
#: wydarzenia. Bez niej oś miałaby zerową długość i cały pasek byłby jedną komórką.
SCHOOL_YEAR_START = (9, 1)
SCHOOL_YEAR_END = (8, 31)

#: Ile wydarzeń musi mieć edycja, żeby oś dało się policzyć z nich samych.
MIN_ITEMS_FOR_AXIS = 2


def format_compact_range(start: date, end: date) -> str:
    """Termin w postaci, która mieści się pod komórką paska: „20.11.2026”, „12.10 – 16.11.2026”.

    Osobno od ``format_date_range``, bo to inny nośnik, a nie inny gust: tam jest zdanie
    ogłoszenia („4–7 czerwca 2027”) czytane w akapicie, tutaj – podpis w siatce znaków, w której
    „30 maja – 2 czerwca 2027” zajmuje jedną piątą całej osi i zasłania sąsiadów. Liczby są
    dwucyfrowe, żeby podpisy dwóch wydarzeń miały tę samą szerokość i kolumny się nie rozjeżdżały.
    """
    if start == end:
        return f"{start.day:02d}.{start.month:02d}.{start.year}"
    if start.year == end.year:
        return f"{start.day:02d}.{start.month:02d} {DASH} {end.day:02d}.{end.month:02d}.{end.year}"
    return f"{start.day:02d}.{start.month:02d}.{start.year} {DASH} {end.day:02d}.{end.month:02d}.{end.year}"


def _localdate(now=None) -> date:
    """Dzisiejszy dzień w strefie serwisu. ``now`` (aware albo naive) dla testów bez freezegunu."""
    if now is None:
        return timezone.localdate()
    if timezone.is_aware(now):
        return timezone.localtime(now).date()
    return now.date()


def _status_for(start: date, end: date, today: date) -> str:
    """Stan wydarzenia względem dzisiaj. „Teraz” obejmuje oba końce zakresu.

    Granicą jest **dzień**, a nie godzina, i to jest świadome: pasek ogłasza kalendarz, a nie
    okno uploadu. Etap, którego deadline mija dziś o 23:59, przez cały dzień jest „teraz” –
    inaczej od rana wyglądałby na miniony.
    """
    if end < today:
        return TL_PAST
    if start > today:
        return TL_UPCOMING
    return TL_CURRENT


def _stage_items(edition: Edition, today: date) -> list[dict]:
    """Etapy zawodów jako wydarzenia paska – bez treningowego, jak wszędzie na osi czasu.

    Termin bierzemy stąd, skąd bierze go reszta serwisu: wpisane dni wydarzenia mają
    pierwszeństwo przed oknem oddawania prac (patrz ``stage_date_range``). Odnośnik pojawia się
    dopiero z ogłoszonymi wynikami – wcześniej nie ma dokąd prowadzić, a link do pustej tabeli
    byłby obietnicą bez pokrycia.
    """
    stages = list(edition.stages.exclude(kind=StageKind.TRAINING).order_by("opens_at", "id"))
    published = set(ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True))
    items = []
    for stage in stages:
        event_range = stage.event_range
        if event_range is not None:
            start, end = event_range
        else:
            start = timezone.localtime(stage.opens_at).date()
            end = timezone.localtime(stage.deadline_at).date()
        items.append(
            _item(
                kind="stage",
                title=stage.display_name,
                start=start,
                end=end,
                today=today,
                url=reverse("web:results", args=[stage.pk]) if stage.pk in published else "",
                note=stage.location,
            )
        )
    return items


def _event_items(edition: Edition, today: date) -> list[dict]:
    """Wydarzenia dopisane przez koordynatora (``competitions.EditionEvent``)."""
    events = edition.events.filter(show_on_timeline=True).order_by("starts_on", "id")
    return [
        _item(
            kind="event",
            title=event.title,
            start=event.date_range[0],
            end=event.date_range[1],
            today=today,
            url=event.url,
            note=event.note,
        )
        for event in events
    ]


def _registration_item(edition: Edition, today: date) -> list[dict]:
    """Okno rejestracji uczestników jako jedno wydarzenie – o ile w ogóle jest oknem.

    Wyłączona rejestracja nie trafia na pasek nawet z wpisanymi terminami: pasek ogłaszałby
    wtedy termin, pod którym stoi formularz odmawiający założenia konta. Bez daty otwarcia też
    nie ma czego ogłaszać – „od zaraz do odwołania” nie jest odcinkiem na osi.
    """
    if not edition.registration_enabled or edition.registration_opens_at is None:
        return []
    start = timezone.localtime(edition.registration_opens_at).date()
    end = (
        timezone.localtime(edition.registration_closes_at).date()
        if edition.registration_closes_at is not None
        else start
    )
    return [
        _item(
            kind="registration",
            title="Rejestracja uczestników",
            start=start,
            end=end,
            today=today,
            url=reverse("web:register"),
            note="",
        )
    ]


def _workshop_items(today: date, competition=None) -> list[dict]:
    """Warsztaty z tabeli na ``/warsztaty/`` **tego konkursu** – każdy osobno, wiersz = termin.

    Grupowanie po miesiącu („Warsztaty (3)”) było tu wcześniej i zostało wycofane na wyraźną
    prośbę organizatora: warsztat jest osobnym wydarzeniem, na które zapisuje się osobno, więc
    zbiorczy podpis odbierał mu tożsamość i kazał czytelnikowi szukać tematu gdzie indziej.
    Znaczniki, które wypadają na tej samej komórce, po prostu się schodzą – komórka pokazuje
    wtedy jedną kreskę, a rozwinięty panel wylicza wszystkie jej wydarzenia po kolei.

    Wiersz bez odczytanej daty jest pomijany – dokładnie tak, jak w zapowiedzi na stronie
    głównej (``upcoming_workshops``): pasek umie ustawić tylko to, co da się porównać z zegarem.

    Strony szukamy przez ``workshops_page``, a nie samym slugiem: w instalacji wielokonkursowej
    stron o slugu ``warsztaty`` jest tyle, co konkursów, a na pasek ma trafić ta z **tego** drzewa.
    """
    page = workshops_page(competition)
    return [
        _item(
            kind="workshop",
            title=f"Warsztaty: {row['topic']}" if row["topic"] else "Warsztaty",
            start=row["date_value"],
            end=row["date_value"],
            today=today,
            url=f"/{WORKSHOPS_SLUG}/",
            note="online",
        )
        for row in workshop_rows(page)
    ]


def _item(
    *,
    kind: str,
    title: str,
    start: date,
    end: date,
    today: date,
    url: str = "",
    note: str = "",
) -> dict:
    """Jeden wiersz kalendarza – w postaci, którą rozumie i znacznik na pasku, i chip w legendzie."""
    return {
        "kind": kind,
        "title": title,
        "start": start,
        "end": end,
        "status": _status_for(start, end, today),
        "dates": format_compact_range(start, end),
        "url": url or "",
        "note": note or "",
    }


def _competition_of(edition: Edition):
    """Właściciel edycji, bez zapytania tam, gdzie i tak wyszłoby ``None``.

    ``competition_id`` jest kolumną wiersza edycji, więc sprawdzenie „czy w ogóle ma właściciela”
    jest darmowe; dopiero odczyt samego obiektu kosztuje zapytanie. Różnica ma znaczenie, bo ta
    funkcja stoi na drodze paska w nagłówku, czyli kodu wołanego przy każdej odsłonie serwisu,
    a w wydaniu C kolumna jest jeszcze nullowalna (§ 4.1).

    Wołający, który konkurs **zna** (żądanie, strona), podaje go argumentem i tu nie zagląda –
    patrz ``timeline_events`` i ``timeline_strip``.
    """
    if edition is None or edition.competition_id is None:
        return None
    return edition.competition


def timeline_events(edition: Edition | None = None, now=None, *, competition=None) -> list[dict]:
    """Cały kalendarz edycji w jednej liście, uporządkowany po dacie początku.

    Cztery źródła, bo tyle jest rodzajów terminu w tej olimpiadzie i każdy mieszka gdzie indziej:
    etapy i okno rejestracji w ``competitions`` (serwer ich pilnuje), warsztaty w treści
    redakcyjnej (nikt ich nie egzekwuje), a wydarzenia koordynatora we własnej tabeli. Pasek ma
    być **jednym** kalendarzem, więc scalanie jest tutaj – gdyby robił je szablon, każdy ekran
    składałby inny zestaw.

    Każda pozycja niesie już swój stan (``past``/``current``/``upcoming``) policzony względem
    **dnia** w strefie serwisu; pozycji na osi tu nie ma, bo osi jeszcze nie znamy – dokłada ją
    ``timeline_strip``, kiedy zna już komplet wydarzeń.

    Trzy z czterech źródeł są zakresowane **przez edycję** (etapy, wydarzenia, okno rejestracji):
    edycja należy do konkursu (``Edition.competition``), więc nie mają czego filtrować drugi raz.
    Czwarte – warsztaty – mieszka w drzewie stron, czyli poza domeną zawodów, i jako jedyne
    potrzebuje konkursu wprost. Bierzemy go z edycji, żeby pasek nie mógł złożyć harmonogramu
    jednego konkursu z warsztatami drugiego, a argument ``competition`` zostaje dla wołającego,
    który edycji nie ma.
    """
    if edition is None:
        edition = current_edition(competition)
    if edition is None:
        return []
    today = _localdate(now)
    items = [
        *_stage_items(edition, today),
        *_event_items(edition, today),
        *_registration_item(edition, today),
        *_workshop_items(today, competition if competition is not None else _competition_of(edition)),
    ]
    items.sort(key=lambda item: (item["start"], item["end"], item["title"]))
    return items


def _school_year(edition: Edition, today: date) -> tuple[int, int]:
    """Rocznik szkolny edycji: z ``year_label``, a w ostateczności z kalendarza.

    Zapasowe liczenie z dzisiejszej daty nie jest zgadywaniem na ślepo – rok szkolny zaczyna się
    we wrześniu i tylko to trzeba wiedzieć. Edycja testowa albo etykieta bez rocznika dostaje
    dzięki temu sensowną oś zamiast wyjątku w środku nagłówka strony.
    """
    match = SCHOOL_YEAR_RE.search(edition.year_label or "")
    if match:
        return (int(match.group(1)), int(match.group(2)))
    if today.month >= SCHOOL_YEAR_START[0]:
        return (today.year, today.year + 1)
    return (today.year - 1, today.year)


def _edition_number(edition: Edition) -> str:
    """Numer edycji z początku etykiety („I edycja 2026/2027” → „I”). Brak – pusty napis."""
    match = EDITION_NUMBER_RE.match(edition.year_label or "")
    return match.group(1) if match else ""


def _axis(items: list[dict], edition: Edition, today: date) -> tuple[date, date]:
    """Oś: od najwcześniejszego początku do najpóźniejszego końca, albo cały rok szkolny.

    Próg przy dwóch wydarzeniach, a nie przy zerze: jedno wydarzenie dałoby oś długości tego
    wydarzenia, czyli pasek wypełniony w całości od pierwszego dnia i „miniony” od ostatniego –
    wykres bez żadnej informacji. Rok szkolny jest wtedy uczciwszym tłem.
    """
    if len(items) >= MIN_ITEMS_FOR_AXIS:
        start = min(item["start"] for item in items)
        end = max(item["end"] for item in items)
        if start < end:
            return (start, end)
    first_year, last_year = _school_year(edition, today)
    return (date(first_year, *SCHOOL_YEAR_START), date(last_year, *SCHOOL_YEAR_END))


def _fraction(day: date, axis_start: date, total_days: int) -> float:
    return min(max((day - axis_start).days / total_days, 0.0), 1.0)


def _place_items(items: list[dict], axis_start: date, total_days: int) -> None:
    """Dokłada każdej pozycji ułamek położenia i długości oraz zakres komórek paska.

    Zakres jest półotwarty (``cell_from`` włącznie, ``cell_to`` wyłącznie) i ma co najmniej jedną
    komórkę: wydarzenie jednodniowe na rocznej osi to 1/365 paska, czyli zero komórek po
    zaokrągleniu – a gala, której nie widać, nie jest na pasku obecna.
    """
    for number, item in enumerate(items):
        # Numer w liście jest jedynym powiązaniem między kreską na pasku a chipem w legendzie:
        # skrypt podświetla parę po tej liczbie, a nie po tytule, który bywa ten sam dwa razy.
        item["index"] = number
        position = _fraction(item["start"], axis_start, total_days)
        length = max((item["end"] - item["start"]).days, 1) / total_days
        cell_from = min(round(position * BAR_SIZE), BAR_SIZE - 1)
        cell_to = min(max(cell_from + round(length * BAR_SIZE), cell_from + 1), BAR_SIZE)
        item["position"] = position
        item["span"] = min(length, 1.0)
        item["cell_from"] = cell_from
        item["cell_to"] = cell_to


def _cell_statuses(items: list[dict]) -> list[str]:
    """Kolor każdej komórki paska. Nakładające się wydarzenia rozstrzyga ``_STATUS_PRIORITY``."""
    statuses = [""] * BAR_SIZE
    for item in items:
        for index in range(item["cell_from"], item["cell_to"]):
            current = statuses[index]
            if not current or _STATUS_PRIORITY[item["status"]] > _STATUS_PRIORITY[current]:
                statuses[index] = item["status"]
    return statuses


def cell_char(index: int, head: int, marks: set[int]) -> str:
    """Znak w komórce paska. **Ta sama reguła jest w static/js/timeline-strip.js.**

    Duplikat jest świadomy i pilnowany testem: serwer musi narysować pasek kompletny bez
    JavaScriptu (inaczej strona bez skryptów nie ma kalendarza), a skrypt musi umieć przesunąć
    głowicę w karcie zostawionej otwartej przez noc – czyli narysować dokładnie to samo.
    """
    if index == head:
        return ">"
    if index in marks:
        return "|"
    return "=" if index < head else "."


def _markers(items: list[dict]) -> dict[int, list[dict]]:
    """Wydarzenia przypięte do komórki, w której się zaczynają.

    Kilka wydarzeń bywa w jednej komórce (na rocznej osi jedna komórka to blisko cztery dni),
    więc znacznik jest jeden i wskazuje wszystkie – zamiast dwóch kresek jedna na drugiej albo
    dwóch sąsiednich, które rozjechałyby siatkę o znak. Od czasu, gdy każdy warsztat jest osobnym
    wydarzeniem, jest to sytuacja **normalna**, a nie skraj: dwa terminy w tym samym tygodniu
    wypadają na tej samej komórce rocznej osi.
    """
    markers: dict[int, list[dict]] = {}
    for item in items:
        markers.setdefault(item["cell_from"], []).append(item)
    return markers


def _bar_cells(items: list[dict], statuses: list[str], head: int) -> list[dict]:
    """Pasek komórka po komórce – każda jako osobny element, bo każda może zmienić znak.

    Sto elementów zamiast kilkunastu ciągów jest świadomym wyborem i kosztuje po kompresji
    kilkaset bajtów. W zamian skrypt, który przesuwa głowicę w karcie zostawionej otwartej przez
    noc, podmienia **tekst** w gotowych elementach, zamiast przebudowywać kod paska – a w pasku
    stoją odnośniki i dymki, których przebudowa kasowałaby fokus i przerywałaby najechanie.

    Znaczniki wydarzeń (``|``) są elementami aktywnymi: odnośnikiem, gdy wydarzenie ma dokąd
    prowadzić, a przyciskiem, gdy nie ma. Oba są osiągalne klawiszem – i to jest jedyny powód,
    dla którego znacznik bez adresu w ogóle jest przyciskiem: fokus na nim rozwija panel
    (``:focus-within``), więc legenda działa także bez myszy.
    """
    markers = _markers(items)
    cells = []
    for index in range(BAR_SIZE):
        group = markers.get(index)
        char = cell_char(index, head, set(markers))
        classes = ["tl__c"]
        if statuses[index]:
            classes.append(f"tl__c--{statuses[index]}")
        if index == head:
            classes.append("tl__head")
        if group:
            classes.append("tl__mark")
        cells.append(
            {
                "index": index,
                "char": char,
                "status": statuses[index],
                "is_head": index == head,
                "css_class": " ".join(classes),
                "items": group or [],
                # Nazwa dostępna znacznika. Czytnik ekranu czyta ją zamiast kreski, więc niesie
                # dokładnie to, co w legendzie widzi osoba używająca myszy.
                "label": "; ".join(f"{item['title']}, {item['dates']}" for item in group or []),
                "url": group[0]["url"] if group and len(group) == 1 else "",
                # Numery wydarzeń tej komórki, w jednym atrybucie ``data-``: po nich skrypt
                # podświetla chipy legendy, kiedy kursor stoi na kresce (i odwrotnie).
                "indexes": ",".join(str(item["index"]) for item in group or []),
            }
        )
    return cells


def _header_lines(edition: Edition, today: date) -> list[str]:
    """Dwa wiersze „kodu” z rocznikiem i numerem edycji – jak w nagłówku OI.

    Stoją **pod** paskiem i wyłącznie w rozwiniętym panelu. Nad paskiem byłyby dwoma wierszami,
    które w stanie spoczynku podnoszą nagłówek serwisu o trzydzieści kilka pikseli na każdej
    stronie – a pasek ma być w spoczynku jedną linią i niczym więcej. Pod paskiem mają też tę
    zaletę, że rozwinięcie panelu nie przesuwa samego wykresu: linia zostaje tam, gdzie była.

    Wyśrodkowane, bo pasek jest symetryczny i wyrównanie do lewej zostawiałoby po prawej pustą
    połowę. Wiersz z numerem edycji znika, kiedy etykieta go nie niesie – ``edycja();`` byłoby
    wywołaniem bez argumentu, czyli widoczną usterką.
    """
    first_year, last_year = _school_year(edition, today)
    lines = [f"rok_szkolny({first_year}, {last_year});"]
    number = _edition_number(edition)
    if number:
        lines.append(f"edycja({number});")
    width = BAR_SIZE + 2
    return [" " * max((width - len(line)) // 2, 0) + line for line in lines]


def _lead_item(items: list[dict]) -> dict | None:
    """Wydarzenie, którym podpisujemy pasek na telefonie: to, co trwa – a jeśli nic, to co najbliżej.

    Na wąskim ekranie kalendarz jest zwinięty (``<details>``), bo rozwinięty zajmowałby połowę
    pierwszego ekranu na **każdej** stronie serwisu. W zwiniętym stanie widać jedno zdanie, więc
    musi to być zdanie, po które czytelnik tu przyszedł: „gdzie teraz jesteśmy”. Kolejność
    szukania jest kolejnością przydatności – trwa, zaraz będzie, właśnie się skończyło.
    """
    for status in (TL_CURRENT, TL_UPCOMING):
        found = next((item for item in items if item["status"] == status), None)
        if found is not None:
            return found
    return items[-1] if items else None


def _cache_key(edition_id: int, competition_id: int | None) -> str:
    """Klucz bufora: konkurs i jego edycja.

    Konkurs w kluczu jest **nadmiarowy i ma taki zostać**. Edycja należy do dokładnie jednego
    konkursu (``Edition.competition``), więc jej identyfikator sam w sobie wystarcza – ale w wydaniu
    C kolumna właściciela jest jeszcze nullowalna, a pasek wisi na **każdej** stronie serwisu.
    Nazwanie właściciela wprost w kluczu kosztuje kilkanaście znaków, a kupuje to, że wpis policzony
    dla edycji bez właściciela (``none``) nigdy nie trafi się edycji, która właściciela już dostała.

    Daty w kluczu **nie ma** i to jest świadome, mimo że cały pasek policzono względem dzisiaj.
    Wpis żyje pięć minut, więc zmiana doby unieważnia go sama – i to szybciej, niż ktokolwiek
    zdąży zauważyć, że głowica stoi na wczorajszej komórce. Data w kluczu dokładałaby za to
    problem prawdziwy: zdjęcie bufora (``invalidate_timeline_cache``) musiałoby zgadnąć,
    dla którego dnia policzono wpis, który ma skasować.
    """
    return f"{CACHE_PREFIX}:{competition_id or 'none'}:{edition_id}"


def invalidate_timeline_cache(edition_id: int) -> None:
    """Zdejmuje pasek z bufora – woła to serwis wydarzeń po każdym zapisie.

    Bez tego koordynator dopisywałby wydarzenie i przez pięć minut nie widział go w nagłówku,
    czyli sprawdzałby swoją pracę na ekranie, który jeszcze o niej nie wie.

    Sygnatura zostaje przy **samym identyfikatorze edycji**: woła tę funkcję
    ``apps.competitions.events`` i woła ją z ``transaction.on_commit``, czyli z miejsca, w którym
    obiektu edycji już nie ma pod ręką. Konkurs do klucza dobieramy więc jednym ``values_list``
    – zapytanie wykonuje się przy zapisie wydarzenia (rzadko), a nie przy odsłonie strony.
    """
    competition_id = Edition.objects.filter(pk=edition_id).values_list("competition_id", flat=True).first()
    cache.delete(_cache_key(edition_id, competition_id))


def timeline_strip(edition: Edition | None = None, now=None, *, competition=None) -> dict | None:
    """Komplet danych paska w nagłówku: wiersze „kodu”, komórki paska i lista dla telefonu.

    Zwraca ``None``, kiedy nie ma bieżącej edycji – szablon nie rysuje wtedy niczego. Pasek bez
    kalendarza byłby pustą ramką zajmującą wiersz na każdej stronie serwisu. To samo dotyczy
    żądania spod hosta bez konkursu: „nie wiadomo, czyj to harmonogram” jest odpowiedzią pustą,
    a nie zaproszeniem do pokazania pierwszego z brzegu.

    ``competition`` podaje wołający, który konkurs zna bez zapytania – znacznik szablonu bierze
    go z ``request.competition``. Dzięki temu warsztaty (jedyne źródło paska spoza domeny zawodów)
    odnajdują się w drzewie **tej** witryny, a odczyt właściciela edycji nie dokłada zapytania.

    Wynik idzie do bufora na pięć minut, bo ta funkcja wykonuje się przy **każdym** żądaniu
    strony HTML, a jej koszt to cztery zapytania (etapy, publikacje wyników, wydarzenia, strona
    warsztatów) i trochę arytmetyki. Zmiana zrobiona przez koordynatora czyści bufor od razu
    (``invalidate_timeline_cache``); pięć minut opóźnienia dotyczy wyłącznie zmian robionych
    inną drogą – terminów etapu, okna rejestracji i treści strony warsztatów.
    """
    if edition is None:
        edition = current_edition(competition)
    if edition is None:
        return None
    today = _localdate(now)
    key = _cache_key(edition.pk, edition.competition_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    items = timeline_events(edition, now, competition=competition)
    axis_start, axis_end = _axis(items, edition, today)
    total_days = max((axis_end - axis_start).days, 1)
    _place_items(items, axis_start, total_days)

    progress = _fraction(today, axis_start, total_days)
    head = min(round(progress * BAR_SIZE), BAR_SIZE - 1)
    statuses = _cell_statuses(items)

    strip = {
        "edition": edition.year_label,
        "header_lines": _header_lines(edition, today),
        "items": items,
        "axis_start": axis_start,
        "axis_end": axis_end,
        "progress": progress,
        "head": head,
        "size": BAR_SIZE,
        "lead": _lead_item(items),
        "cells": _bar_cells(items, statuses, head),
    }
    cache.set(key, strip, CACHE_TTL_SECONDS)
    return strip
