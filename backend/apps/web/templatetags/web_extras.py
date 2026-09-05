"""Drobne filtry szablonów interfejsu WWW.

Świadomie ubogie: żaden z tych filtrów nie generuje HTML-a i żaden nie oznacza wyniku jako
bezpiecznego (``mark_safe``). Wszystko, co pochodzi od użytkownika, wychodzi z szablonu przez
domyślne autoescapowanie.
"""

from django import template
from django.utils.formats import date_format

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
