"""Konkurs „na teraz”: jedna zmienna kontekstowa dla żądania, zadania i komendy.

Po co to w ogóle jest: kod, który buduje list, składa dokument albo liczy termin, bywa wołany
zarówno z widoku (wtedy konkurs jest w ``request.competition``), jak i z zadania Celery czy
komendy (wtedy żądania nie ma i nie będzie). Przekazywanie konkursu przez wszystkie te warstwy
argumentem jest właściwą drogą tam, gdzie da się to zrobić – ta zmienna jest dla miejsc, gdzie się
nie da, bo wołający jest biblioteką albo sygnałem.

Dlaczego ``ContextVar``, a nie ``threading.local()``: serwis chodzi pod ASGI (``UvicornWorker``),
a Django wykonuje synchroniczne widoki w wątku z puli – wartość zapisana „w wątku” bywałaby przy
kolejnym żądaniu cudza albo pusta. Wzorzec jest ten sam, którego używa ``django.utils.translation``
dla aktywnego języka.

**Czego ta zmienna nie jest:** nie jest autoryzacją. To, że konkurs jest ustawiony, nie znaczy, że
wołający ma prawo do jego danych – o tym rozstrzygają managery (``for_competition``), mixiny widoków
i klasy uprawnień.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - wyłącznie dla podpowiedzi typów
    from collections.abc import Iterator

    from apps.tenancy.models import Competition

#: Domyślnie ``None`` i to jest odpowiedź poprawna, a nie brak danych: „nie wiadomo, o który
#: konkurs chodzi” ma być widoczne w miejscu użycia, bo w bazie wielokonkursowej „pierwszy
#: z brzegu” jest cudzy.
_current: ContextVar[Competition | None] = ContextVar("current_competition", default=None)


def current_competition() -> Competition | None:
    """Konkurs kontekstu albo ``None``.

    W żądaniu ustawia go ``CompetitionMiddleware``; poza żądaniem – wołający, przez
    ``competition_context``.
    """
    return _current.get()


def set_current_competition(competition: Competition | None) -> Token:
    """Ustawia konkurs kontekstu i oddaje żeton do przywrócenia poprzedniej wartości.

    Żeton, a nie „ustaw z powrotem na ``None``”: zagnieżdżenie (zadanie wołane z komendy, która
    sama ustawiła konkurs) ma wrócić do wartości zastanej, a nie do pustki.
    """
    return _current.set(competition)


def reset_current_competition(token: Token) -> None:
    """Przywraca wartość sprzed ``set_current_competition``."""
    _current.reset(token)


@contextmanager
def competition_context(competition: Competition | None) -> Iterator[Competition | None]:
    """Jawne wskazanie konkursu na czas bloku – dla zadań Celery, komend i migracji.

    ``finally`` jest tu istotne: zadanie zakończone wyjątkiem zostawiłoby inaczej swój konkurs
    w kontekście wątku, a następne zadanie w tym samym wątku wzięłoby go za swój.
    """
    token = set_current_competition(competition)
    try:
        yield competition
    finally:
        reset_current_competition(token)
