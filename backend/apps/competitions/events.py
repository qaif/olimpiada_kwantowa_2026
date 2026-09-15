"""Wydarzenia edycji dopisywane przez koordynatora – reguły domenowe jednej tabeli.

Osobny moduł od ``services.py``, bo to inny przedmiot: tam jest kalendarz, którego **serwer
pilnuje** (otwarcie etapu, deadline uploadu, okno reklamacji), a tutaj kalendarz, który serwis
wyłącznie **ogłasza**. Rozdział nie jest kosmetyczny: reguły etapu mówią „nie cofaj terminu, bo
ktoś już oddał pracę”, a reguły wydarzenia – „koniec nie może być przed początkiem” i nic więcej.
Gdyby obie rodziny stały w jednym pliku, pierwsza literówka w imporcie zamieniłaby galę w etap.

Zasady są te same, co w całym projekcie: reguła mieszka tutaj, widok wyłącznie orkiestruje,
każda zmiana zostawia wpis audytowy z różnicą pól, a ``full_clean()`` powtarza to, czego pilnuje
constraint w bazie.

Czego tu **nie ma**: samego bufora linii czasu – ten mieszka w ``apps.cms.timeline``. Serwis
wyłącznie go zdejmuje po każdym zapisie (``invalidate_timeline_cache``), żeby pasek w nagłówku
nie czekał pięciu minut na wydarzenie dopisane przed chwilą.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.db import transaction
from rest_framework import status

from apps.core.api import DomainError

from .models import Edition, EditionEvent

logger = logging.getLogger(__name__)

#: Pola, które koordynator może zmienić. Lista jest zamknięta, bo widok przekazuje do serwisu
#: słownik z formularza: bez niej literówka w nazwie pola cicho zapisywałaby atrybut obok modelu.
EVENT_EDITABLE_FIELDS = ("title", "starts_on", "ends_on", "note", "url", "show_on_timeline")

#: Schematy, które wolno wpisać w ``url``. Ścieżka względna (``/warsztaty/``) jest tu przypadkiem
#: **najczęstszym**, a nie wyjątkiem: wydarzenie ma zwykle własną stronę w tym samym serwisie.
#: Wszystko poza tą listą – w szczególności ``javascript:`` i ``data:`` – jest odrzucane, bo
#: odnośnik z linii czasu klika publiczność, a nie koordynator.
ALLOWED_URL_SCHEMES = ("http", "https")


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_400_BAD_REQUEST)


def validate_url(value: str) -> str:
    """Odnośnik wydarzenia: pusty, ``http(s)://…`` albo ścieżka w tym serwisie (``/warsztaty/``).

    Sprawdzamy **schemat**, a nie „czy zaczyna się od http”: ``javascript:alert(1)`` też zaczyna
    się od litery i też przeszedłby przez naiwny test, a wstawiony w ``href`` wykonuje się po
    kliknięciu. Ścieżka musi zaczynać się pojedynczym ukośnikiem – ``//example.com`` jest w HTML
    adresem **obcego hosta** (schemat dziedziczony ze strony), a wyglądałby jak ścieżka lokalna.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("/"):
        if value.startswith("//"):
            raise _bad_request(
                "Odnośnik zaczynający się od „//” prowadzi do obcego serwisu. Wpisz pełny adres "
                "z „https://” albo ścieżkę w tym serwisie, np. „/warsztaty/”.",
                "EVENT_URL_INVALID",
            )
        return value
    scheme = urlsplit(value).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise _bad_request(
            "Odnośnik musi być adresem „http://…”, „https://…” albo ścieżką w tym serwisie, "
            "np. „/warsztaty/”.",
            "EVENT_URL_INVALID",
        )
    return value


def _clean_fields(fields: dict) -> dict:
    """Wspólna walidacja dla dodania i zmiany – jedna reguła, dwa wejścia.

    Tytuł i termin są sprawdzane tutaj, a nie tylko w ``full_clean()``, bo komunikat ma być
    zdaniem dla koordynatora („Wydarzenie musi mieć nazwę”), a nie wynikiem walidatora pola.
    """
    cleaned = dict(fields)
    if "title" in cleaned:
        cleaned["title"] = (cleaned["title"] or "").strip()
        if not cleaned["title"]:
            raise _bad_request("Wydarzenie musi mieć nazwę.", "EVENT_TITLE_REQUIRED")
    if "note" in cleaned:
        cleaned["note"] = (cleaned["note"] or "").strip()
    if "url" in cleaned:
        cleaned["url"] = validate_url(cleaned["url"])
    return cleaned


def _assert_range(starts_on, ends_on) -> None:
    """Koniec nie przed początkiem. Pusty koniec znaczy „jeden dzień”, więc przechodzi."""
    if starts_on is None:
        raise _bad_request("Wydarzenie musi mieć datę początku.", "EVENT_START_REQUIRED")
    if ends_on is not None and ends_on < starts_on:
        raise _bad_request("Koniec wydarzenia nie może być przed jego początkiem.", "EVENT_RANGE_INVALID")


def _invalidate_timeline(edition_id: int) -> None:
    """Czyści bufor paska linii czasu – **dwa razy**, i oba są potrzebne.

    Raz od razu: żądanie, które właśnie zapisało wydarzenie, kończy się przekierowaniem na stronę
    z paskiem w nagłówku, a koordynator ma tam zobaczyć swoją zmianę, nie stan sprzed niej.
    Drugi raz po commicie: między pierwszym czyszczeniem a zatwierdzeniem transakcji inne żądanie
    mogło odbudować bufor ze stanu sprzed zapisu i zamrozić go na pięć minut.

    Zbędne czyszczenie nic nie kosztuje – bufor jest buforem, a nie źródłem prawdy: najgorsze, co
    z niego wynika, to jedno dodatkowe przeliczenie paska.
    """
    from apps.cms.timeline import invalidate_timeline_cache

    invalidate_timeline_cache(edition_id)
    transaction.on_commit(lambda: invalidate_timeline_cache(edition_id))


@transaction.atomic
def create_event(*, edition: Edition, actor, request=None, **fields) -> EditionEvent:
    """Dopisuje wydarzenie do edycji. Zwraca zapisany obiekt."""
    unknown = sorted(set(fields) - set(EVENT_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu wydarzenia: {', '.join(unknown)}.")
    cleaned = _clean_fields(fields)
    _assert_range(cleaned.get("starts_on"), cleaned.get("ends_on"))

    event = EditionEvent(edition=edition, created_by=actor if actor is not None else None, **cleaned)
    event.full_clean(exclude=["created_by"])
    event.save()

    from apps.core.models import audit

    audit(actor, "event.created", event, _audit_snapshot(event), request=request)
    _invalidate_timeline(edition.pk)
    logger.info("Edycja %s: dodano wydarzenie %s.", edition.pk, event.pk)
    return event


@transaction.atomic
def update_event(event: EditionEvent, actor, *, request=None, **fields) -> EditionEvent:
    """Zmiana wydarzenia. Zapisuje **wyłącznie** pola, które faktycznie się zmieniły.

    Blokada wiersza (``select_for_update``) szereguje dwa równoległe zapisy: bez niej druga
    transakcja liczyłaby różnicę względem stanu sprzed pierwszej i wpis audytowy kłamałby
    o tym, co zastała.
    """
    unknown = sorted(set(fields) - set(EVENT_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu wydarzenia: {', '.join(unknown)}.")
    cleaned = _clean_fields(fields)

    locked = EditionEvent.objects.select_for_update().get(pk=event.pk)
    changed = {name: value for name, value in cleaned.items() if getattr(locked, name) != value}
    if not changed:
        return locked

    _assert_range(
        changed.get("starts_on", locked.starts_on),
        changed.get("ends_on", locked.ends_on),
    )
    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    locked.full_clean(exclude=["created_by"])
    locked.save(update_fields=list(changed))

    from apps.core.models import audit

    audit(actor, "event.updated", locked, diff, request=request)
    _invalidate_timeline(locked.edition_id)
    for name, value in changed.items():
        setattr(event, name, value)
    return locked


@transaction.atomic
def delete_event(event: EditionEvent, actor, *, request=None) -> None:
    """Usuwa wydarzenie. Nic go nie blokuje – nie wisi na nim ani jeden wiersz innej tabeli.

    Wpis audytowy powstaje **przed** skasowaniem: po ``delete()`` obiekt nie ma ``pk``, więc
    śladu nie dałoby się już połączyć z niczym.
    """
    from apps.core.models import audit

    edition_id = event.edition_id
    audit(actor, "event.deleted", event, _audit_snapshot(event), request=request)
    event.delete()
    _invalidate_timeline(edition_id)


def _audit_value(value):
    """Wartość do ``diff`` (JSON): daty w ISO, reszta bez zmian – jak w ``services._audit_value``."""
    from datetime import date

    if isinstance(value, date):
        return value.isoformat()
    return value


def _audit_snapshot(event: EditionEvent) -> dict:
    """Wydarzenie w postaci, którą da się przeczytać w historii bez zaglądania do bazy."""
    return {
        "edition": event.edition_id,
        "title": event.title,
        "starts_on": _audit_value(event.starts_on),
        "ends_on": _audit_value(event.ends_on),
        "note": event.note,
        "url": event.url,
        "show_on_timeline": event.show_on_timeline,
    }


def events_for_edition(edition: Edition | None) -> list[EditionEvent]:
    """Wszystkie wydarzenia edycji – także te schowane – dla listy w panelu koordynatora.

    Lista panelu pokazuje **komplet**, a linia czasu tylko ``show_on_timeline``: koordynator ma
    widzieć również to, co dopiero przygotowuje, bo inaczej wiersz schowany znikałby mu z oczu
    i nie dałoby się go już odsłonić.
    """
    if edition is None:
        return []
    return list(edition.events.order_by("starts_on", "id"))
