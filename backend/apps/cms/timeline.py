"""Oś czasu etapów bieżącej edycji – wspólne źródło dla strony głównej i bloku „terminy etapów”.

Moduł stoi osobno od ``apps.cms.models``, bo czytają go **oba** końce importu: ``models.py``
(strona główna) i ``blocks.py`` (blok ``stage_timeline`` w treści redakcyjnej). ``models.py``
importuje ``blocks.py``, więc funkcja w którymkolwiek z tych plików robiłaby cykl albo kopię
reguły – a reguła jest jedna: **terminy pochodzą z ``competitions.Stage`` i tylko stamtąd**.
Redaktor opisuje etapy słowem, ale nie przepisuje dat; strona nie może pokazać innego terminu
niż ten, który egzekwuje serwer.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from django.utils.formats import date_format

from apps.competitions.models import Edition, Stage
from apps.competitions.services import current_edition
from apps.results.models import ResultsPublication

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
    """Czy etap jest wydarzeniem stacjonarnym rozłożonym na kilka dni (finał).

    Kryterium jest opisowe, a nie oparte na ``kind == FINAL``: rodzaj etapu mówi, **które** to
    zawody w kolejności, a nie jak przebiegają. Taki etap nie ma „otwarcia” i „terminu oddania
    pliku”: ma termin, na który się przyjeżdża, i to jedna informacja, a nie dwie. Kolejna edycja
    może zrobić tak z innym etapem – i strona zachowa się właściwie bez poprawki w szablonie.

    Wydarzeniem jest etap, który spełnia **którykolwiek** z dwóch warunków:

    - ma wpisane dni wydarzenia (``event_range``) – organizator ogłosił zjazd wprost, więc nie ma
      czego domyślać się z okna uploadu. To jedyna droga do terminu, który różni się od sesji:
      finał trwa 4–7 czerwca, a prace przyjmuje się przez kilka godzin 5 czerwca,
    - albo ma miejsce (``location``) i okno rozłożone na różne dni – dotychczasowa reguła. Zostaje,
      bo opisuje etapy wprowadzone przed tymi polami: ich terminu nikt nie przepisywał, a strona
      ma dalej pokazywać jeden zakres, nie dwie godziny.
    """
    if stage.event_range is not None:
        return True
    if not stage.location:
        return False
    return timezone.localtime(stage.opens_at).date() != timezone.localtime(stage.deadline_at).date()


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


def stage_rows(edition: Edition | None = None, now=None) -> list[dict]:
    """Etapy edycji uporządkowane po ``opens_at`` wraz ze stanem i informacją o wynikach.

    Bez argumentu bierze edycję bieżącą – tak woła ją blok w treści redakcyjnej, który nie ma
    skąd znać edycji. Jedno zapytanie o etapy i jedno o publikacje: lista rośnie o wiersze,
    nie o zapytania, niezależnie od liczby etapów.
    """
    if edition is None:
        edition = current_edition()
    if edition is None:
        return []
    now = now or timezone.now()
    stages = list(edition.stages.order_by("opens_at", "id"))
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
