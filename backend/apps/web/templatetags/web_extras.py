"""Drobne filtry szablonów interfejsu WWW.

Świadomie ubogie: żaden z tych filtrów nie generuje HTML-a i żaden nie oznacza wyniku jako
bezpiecznego (``mark_safe``). Wszystko, co pochodzi od użytkownika, wychodzi z szablonu przez
domyślne autoescapowanie.
"""

from django import template
from django.utils.formats import date_format

from apps.core.points import format_points, input_value

register = template.Library()

#: Mapa kodu stanu → „ton” wizualny odznaki. Wyłącznie prezentacja: nazwy stanów pochodzą
#: z ``TextChoices`` modeli (SubmissionStatus, AvStatus, ReviewStatus, StageEntryStatus,
#: AppealStatus, CommitteeStatus), a szablon nie podejmuje na ich podstawie żadnej decyzji poza
#: doborem koloru. Stan spoza mapy dostaje ton neutralny – nowy status w modelu nie wywraca strony.
BADGE_TONES = {
    # rozwiązania (SubmissionStatus)
    "SUBMITTED": "info",
    "SCANNING": "warn",
    "REJECTED_INFECTED": "danger",
    "LOCKED": "neutral",
    "IN_REVIEW": "info",
    "MODERATION": "warn",
    "GRADED_PROVISIONAL": "accent",
    "APPEALED": "warn",
    "FINAL": "ok",
    # skan antywirusowy (AvStatus)
    "PENDING": "warn",
    "CLEAN": "ok",
    "INFECTED": "danger",
    "ERROR": "danger",
    # udział w etapie (StageEntryStatus)
    "REGISTERED": "info",
    "QUALIFIED": "ok",
    "NOT_QUALIFIED": "danger",
    "DISQUALIFIED": "danger",
    # recenzje (ReviewStatus)
    "ASSIGNED": "neutral",
    "DRAFT": "warn",
    "CANCELLED": "neutral",
    # reklamacje (AppealStatus)
    "OPEN": "warn",
    "ACCEPTED": "ok",
    "PARTIALLY_ACCEPTED": "accent",
    "REJECTED": "danger",
    # komitet (CommitteeStatus)
    "ACTIVE": "ok",
    "SUSPENDED": "danger",
}

#: Ton dla wartości spoza mapy.
DEFAULT_BADGE_TONE = "neutral"

#: Domyślny format daty i godziny w interfejsie: „15 października 2026, 12:00”.
LOCAL_DATETIME_FORMAT = "j E Y, H:i"

#: Dopisek przy każdej godzinie. Etykieta strefy, a nie „(UTC)”: w bazie jest UTC, ale szablon
#: i tak renderuje czas lokalny (``USE_TZ=True`` + ``TIME_ZONE="Europe/Warsaw"``), więc dawny
#: dopisek „(UTC)” podawał uczestnikowi godzinę przesuniętą o 1–2 h względem tego, co widział.
LOCAL_TIME_LABEL = "czas polski"


@register.filter
def dict_get(mapping, key):
    """Odczyt ``mapping[key]`` dla klucza wyliczonego w pętli (Django nie ma tego wbudowanego).

    Klucze punktów w snapshotcie wyników są tekstowe (``"1"``, ``"2"``), a numery zadań bywają
    liczbami – stąd druga próba po konwersji na tekst.
    """
    if not hasattr(mapping, "get"):
        return None
    value = mapping.get(key)
    if value is None:
        value = mapping.get(str(key))
    return value


@register.filter
def badge_class(status) -> str:
    """Klasy CSS odznaki dla kodu stanu: ``{{ submission.status|badge_class }}``.

    Zwraca sam tekst klas (``badge badge--ok``) – filtr nie generuje HTML-a i niczego nie oznacza
    jako bezpieczne. Etykietę stanu szablon nadal bierze z ``get_..._display``, żeby tłumaczenia
    zostały tam, gdzie są zdefiniowane, czyli w modelu.
    """
    tone = BADGE_TONES.get(str(status or "").upper(), DEFAULT_BADGE_TONE)
    return f"badge badge--{tone}"


@register.filter(expects_localtime=True, is_safe=False)
def local_time(value, fmt: str = LOCAL_DATETIME_FORMAT) -> str:
    """Data i godzina w strefie serwisu, z jawną etykietą strefy.

    ``expects_localtime=True`` sprawia, że Django konwertuje wartość na ``TIME_ZONE`` **zanim**
    filtr ją zobaczy – to ta sama ścieżka, którą idzie wbudowany filtr ``date``. Etykieta jest
    doklejana tutaj, a nie w szablonach, żeby jedna zmiana wystarczyła dla całego serwisu
    (i żeby nikt nie dopisał znowu „(UTC)” z ręki).
    """
    if value in (None, ""):
        return ""
    return f"{date_format(value, fmt)} ({LOCAL_TIME_LABEL})"


@register.filter(expects_localtime=True, is_safe=False)
def local_datetime(value, fmt: str = LOCAL_DATETIME_FORMAT) -> str:
    """To samo bez etykiety – do kolumn tabel, gdzie nagłówek mówi już o strefie."""
    if value in (None, ""):
        return ""
    return date_format(value, fmt)


@register.filter
def event_dates(stage) -> str:
    """Dni wydarzenia etapu jako jedno wyrażenie („4–7 czerwca 2027”) albo pusty tekst.

    Filtr, a nie wartość dokładana w widoku, bo o termin wydarzenia pyta kilka niezależnych
    ekranów (pulpit koordynatora, pulpit uczestnika, strona zadań), a każdy z nich ma inny
    kontekst. Formatowanie idzie przez ``apps.cms.timeline.format_date_range`` – ten sam, z którego
    korzysta publiczna oś czasu, żeby termin finału brzmiał jednakowo na całym serwisie.

    Import jest w środku funkcji świadomie: ``apps.cms.timeline`` ciągnie modele zawodów i wyników,
    a to moduł znaczników ``apps.web`` – ładowany przy renderowaniu dowolnego szablonu.
    """
    from apps.cms.timeline import format_date_range

    event_range = getattr(stage, "event_range", None)
    return format_date_range(*event_range) if event_range else ""


@register.filter
def edition_title(value, prefix="Edycja"):
    """„I edycja 2026/2027” zostaje bez zmian; „XV (2026/2027)” dostaje przedrostek.

    Chodzi o to, żeby nie dublować słowa „edycja” w nagłówku i w hero.
    """
    label = str(value or "").strip()
    if not label:
        return ""
    if "edycj" in label.lower():
        return label
    return f"{prefix} {label}"


@register.filter
def precheck_link(meeting_url):
    """Adres pustego pokoju „na próbę” dla danego linku rozmowy (``apps.competitions.video``).

    Filtr, a nie wartość w kontekście, bo ten sam link jest potrzebny na dwóch ekranach (panel
    uczestnika i terminy rozmów u koordynatora) i w liście – a reguła jego budowania ma zostać
    w jednym miejscu, w module wideo.
    """
    from apps.competitions.video import precheck_url

    return precheck_url(meeting_url)


@register.filter
def points(value) -> str:
    """Punkty do pokazania: „5”, „4,25”, „3,5” (po angielsku „4.25”); brak wartości → „”.

    Jedyny sposób wyświetlania punktów w szablonach (wydanie 0.35.0). Kolumny ocen są dziesiętne,
    więc ``{{ review.score }}`` dałoby „5,00” – a ocena całkowita ma wyglądać tak, jak przed tym
    wydaniem. Postać liczby składa ``apps.core.points.format_points``, ta sama funkcja, której
    używa PDF dyplomu i karta uczestnika: jedna ocena nie może wyglądać inaczej na ekranie
    i na wydruku. Filtr przyjmuje też ``int`` i ``float`` ze snapshotów wyników (starych i nowych).
    """
    return format_points(value)


@register.filter
def points_input(value) -> str:
    """Wartość atrybutu ``value`` pola ``<input type="number">``: zawsze z kropką („4.25”).

    Przeglądarka czyta wartość pola liczbowego wyłącznie z kropką – „4,25” w atrybucie daje
    puste pole, więc tu nie wolno użyć ``points``.
    """
    return input_value(value)
