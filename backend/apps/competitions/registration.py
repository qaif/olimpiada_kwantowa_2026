"""Bramka rejestracji uczestników: jedno „czy wolno” i jedno zdanie „dlaczego nie”.

Moduł jest osobny od ``services.py`` świadomie. ``services`` zaciąga ``apps.accounts.models``
(przez ``models``) i sam bywa importowany z ``apps.accounts.services`` – a to właśnie stamtąd
wchodzi tu rejestracja. Wąski moduł bez zależności od kont trzyma tę drogę prostą i pozwala
importować go z widoku, z procesora kontekstu i z serwisu kont bez ceremonii.

Komunikaty są tutaj, a nie w szablonach, bo tę samą treść dostają trzy różne kanały: formularz
HTML (błąd formularza), API (pole ``detail`` w 409) i nawigacja (zapowiedź startu). Rozjazd między
nimi oznaczałby, że uczestnik czyta na stronie inną datę, niż zwraca serwer.
"""

from __future__ import annotations

from django.utils.formats import date_format
from django.utils.timezone import localtime
from rest_framework import status

from apps.core.api import DomainError

from .models import (
    REGISTRATION_CLOSED,
    REGISTRATION_DISABLED,
    REGISTRATION_NOT_YET,
    RegistrationStatus,
    current_registration_status,
)

#: Kod maszynowy odmowy. Jeden dla wszystkich powodów – powód niesie ``reason`` w stanie i treść
#: komunikatu; klient API nie musi rozróżniać „jeszcze nie” od „już nie”, żeby wiedzieć, że konta
#: nie założy.
REGISTRATION_CLOSED_CODE = "REGISTRATION_CLOSED"

#: Format daty i godziny w komunikatach: „8 września 2026 o 00:00”. Ten sam kształt, co filtr
#: ``local_time`` w ``apps.web.templatetags.web_extras`` (``j E Y``, ``H:i``) – tylko rozdzielony
#: słowem „o”, bo komunikat jest zdaniem, a nie podpisem w tabeli.
DATE_FORMAT = "j E Y"
TIME_FORMAT = "H:i"


def format_moment(value) -> str:
    """„8 września 2026 o 00:00” – zawsze w czasie polskim, niezależnie od strefy serwera.

    ``localtime`` przelicza wartość z bazy (UTC) na ``settings.TIME_ZONE``. Bez tego kroku
    komunikat podawałby godzinę przesuniętą o 1–2 h względem tej, którą uczestnik widzi na
    harmonogramie.
    """
    if value is None:
        return ""
    local = localtime(value)
    return f"{date_format(local, DATE_FORMAT)} o {date_format(local, TIME_FORMAT)}"


def registration_message(state: RegistrationStatus) -> str:
    """Zdanie dla użytkownika albo pusty tekst, gdy rejestracja jest otwarta.

    Pusty tekst przy otwartej rejestracji jest zamierzony: szablony pytają najpierw o ``is_open``,
    a komunikat pokazują dopiero w gałęzi „nie”. Zdanie „rejestracja jest otwarta” nie miałoby
    gdzie stanąć – zamiast niego stoi po prostu formularz.
    """
    if state.is_open:
        return ""
    if state.reason == REGISTRATION_NOT_YET and state.opens_at is not None:
        return f"Rejestracja rusza {format_moment(state.opens_at)}."
    if state.reason == REGISTRATION_CLOSED and state.closes_at is not None:
        return f"Rejestracja została zamknięta {format_moment(state.closes_at)}."
    # ``disabled`` oraz każdy stan bez daty (np. brak bieżącej edycji): jedno zdanie bez terminu.
    # Podanie daty, której nie ma, byłoby obietnicą – a wyłącznik nie jest zapowiedzią.
    return "Rejestracja uczestników jest obecnie wyłączona."


def ensure_registration_open(now=None) -> RegistrationStatus:
    """Bramka przed założeniem konta uczestnika. Zamknięta rejestracja to 409 ``REGISTRATION_CLOSED``.

    Wołają ją **serwisy** (``accounts.services.register_participant`` i jego odpowiednik
    społecznościowy), a nie widoki – dzięki temu formularz HTML, ``POST /api/auth/register/…``
    i rejestracja przez Google/Facebooka przechodzą przez dokładnie ten sam warunek. Ukrycie
    przycisku w szablonie jest wyłącznie uprzejmością wobec użytkownika; regułą jest to zdanie.

    409, a nie 403: żądanie jest poprawne i uprawnione, tylko stan serwisu na nie nie pozwala –
    ta sama semantyka, co przy pozostałych odmowach domenowych panelu (``STAGE_CLOSED``).
    """
    state = current_registration_status(now)
    if not state.is_open:
        raise DomainError(registration_message(state), REGISTRATION_CLOSED_CODE, status.HTTP_409_CONFLICT)
    return state


__all__ = [
    "REGISTRATION_CLOSED",
    "REGISTRATION_CLOSED_CODE",
    "REGISTRATION_DISABLED",
    "REGISTRATION_NOT_YET",
    "RegistrationStatus",
    "current_registration_status",
    "ensure_registration_open",
    "format_moment",
    "registration_message",
]
