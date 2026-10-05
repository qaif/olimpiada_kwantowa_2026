"""Pamięć profilu uczestnika na czas **jednego żądania** (PERF-01, docs/OPERACJE.md § 42.5).

Test obciążenia z 4.10.2026 pokazał, że ``/me/`` pytał bazę o ten sam wiersz ``accounts_participant``
**dziewięć razy** na jedno wejście, a odpytanie czatu (co 15 s u każdego ucznia z otwartą rozmową)
– dwa razy: ``participant_for`` woła mixin roli, ``self.participant`` widoku (właściwość, więc każde
sięgnięcie to zapytanie), procesor kontekstu ról, czat, okna czasowe, karty panelu. Każde z tych miejsc
pyta słusznie – to jest jedyna droga do profilu (``accounts.services.participant_for``) – ale odpowiedź
w obrębie jednego żądania się nie zmienia.

Zasady, które trzymają tę pamięć poprawną:

- **zasięg = jedno żądanie.** Pamięć istnieje wyłącznie między ``open_scope`` a ``close_scope``, które
  woła ``PreferencesMiddleware`` (ta sama warstwa, która i tak ustawia stan wątku na czas żądania
  i sprząta go w ``finally``). Poza żądaniem – komenda, zadanie Celery, test wołający serwis wprost –
  ``participant_for`` pyta bazę jak dotąd, bez żadnej pamięci,
- **zmienna kontekstowa, nie atrybut wątku ani obiektu.** gunicorn ``gthread`` obsługuje kolejne
  żądania w tym samym wątku; ``close_scope`` w ``finally`` gwarantuje, że następne żądanie zaczyna
  od pustej pamięci, także po wyjątku,
- **tylko trafienia.** ``None`` (brak profilu) nie jest zapamiętywane: w tym samym żądaniu profil
  bywa zakładany (dołączenie do konkursu, rejestracja przez dostawcę zewnętrznego), a zapamiętane
  „nie ma” zostałoby nieprawdą,
- **każde wywołanie dostaje własną kopię** (``copy.copy`` – płytka kopia instancji modelu, ułamek
  mikrosekundy; Django kopiuje też pamięć relacji). Przed tą pamięcią każde wywołanie dostawało świeży
  obiekt z bazy i część kodu tak to wykorzystuje: przypisuje atrybut „na chwilę” albo przestawia pole
  przed własnym zapisem. Wspólna instancja przeniosłaby taką zmianę do innego miejsca tego samego
  żądania (przegląd PERF-01, L2),
- **zapis albo skasowanie profilu czyści pamięć** (sygnały ``post_save``/``post_delete`` niżej).
  Zmiana przez ``QuerySet.update`` sygnału nie wysyła – w kodzie są cztery takie miejsca i żadne nie
  czyta potem profilu tego samego konta w tym samym żądaniu (alumni i tak przepisuje pola na obiekt).
"""

from __future__ import annotations

import contextvars
import copy

#: ``{(user_pk, competition_pk | None): Participant}`` albo ``None`` poza żądaniem.
_participants: contextvars.ContextVar[dict | None] = contextvars.ContextVar("participant_memo", default=None)


def open_scope() -> contextvars.Token:
    """Początek żądania: pusta pamięć. Token oddaje się ``close_scope`` w ``finally``."""
    return _participants.set({})


def close_scope(token: contextvars.Token) -> None:
    _participants.reset(token)


def get(user_pk, competition_pk):
    """Zapamiętany profil albo ``None`` (brak pamięci albo brak wpisu – wołający pyta wtedy bazę)."""
    memo = _participants.get()
    if memo is None:
        return None
    remembered = memo.get((user_pk, competition_pk))
    return copy.copy(remembered) if remembered is not None else None


def remember(user_pk, competition_pk, participant) -> None:
    memo = _participants.get()
    if memo is not None and participant is not None:
        # Kopia także przy zapisie: obiekt oddany pierwszemu wołającemu jest jego, nie pamięci.
        memo[(user_pk, competition_pk)] = copy.copy(participant)


def forget_all(**_kwargs) -> None:
    """Odbiornik sygnałów ``Participant``: każda zmiana profilu w żądaniu czyści całą pamięć.

    Całą, a nie jeden wpis: profil zapisany z kodu koordynatora nie jest profilem zalogowanego,
    a koszt ponownego odczytu po rzadkim zapisie jest pomijalny wobec ryzyka nieaktualnej odpowiedzi.
    """
    memo = _participants.get()
    if memo:
        memo.clear()
