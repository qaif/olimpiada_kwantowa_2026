"""Konkurs żądania dla szablonów.

Szablony potrzebują nazwy, znaku i koloru akcentu na **każdej** stronie (nagłówek, stopka, tytuł
zakładki), a sięganie po nie przez ``request.competition`` w każdym widoku z osobna znaczyłoby, że
pierwszy widok, który o tym zapomni, wyrenderuje stronę bez marki.

Procesor niczego nie pyta bazy: warstwa ``CompetitionMiddleware`` już rozstrzygnęła konkurs, a tu
jest wyłącznie przepisanie atrybutu. ``None`` (host bez konkursu, żądanie spoza łańcucha warstw –
np. renderowanie szablonu w teście) jest wartością poprawną i szablon ma to znieść.
"""

from __future__ import annotations


def competition(request) -> dict:
    """``{"competition": <Competition|None>}`` dla każdego szablonu."""
    return {"competition": getattr(request, "competition", None)}
