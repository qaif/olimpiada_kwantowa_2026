"""Konkurs żądania dla szablonów.

Szablony potrzebują nazwy, znaku i koloru akcentu na **każdej** stronie (nagłówek, stopka, tytuł
zakładki), a sięganie po nie przez ``request.competition`` w każdym widoku z osobna znaczyłoby, że
pierwszy widok, który o tym zapomni, wyrenderuje stronę bez marki.

Procesor niczego nie pyta bazy: warstwa ``CompetitionMiddleware`` już rozstrzygnęła konkurs, a tu
jest wyłącznie przepisanie atrybutu. ``None`` (host bez konkursu, żądanie spoza łańcucha warstw –
np. renderowanie szablonu w teście) jest wartością poprawną i szablon ma to znieść.
"""

from __future__ import annotations

from django.urls import get_script_prefix


def competition(request) -> dict:
    """``{"competition": <Competition|None>, "site_root": "/"}`` dla każdego szablonu.

    ``site_root`` to korzeń serwisu **tego** konkursu, zawsze z ukośnikiem na końcu: ``/`` pod własną
    domeną, ``/druga/`` w konkursie adresowanym prefiksem ścieżki (uwaga T43). Szablon bazowy pisze
    nim odnośniki do strony głównej i do stron CMS o stałym adresie (``{{ site_root }}dokumenty/rodo/``)
    – napis ``/`` wpisany na sztywno wyprowadzałby czytelnika konkursu pod prefiksem do
    konkursu-gospodarza. Bez prefiksu wynik jest co do znaku ten sam, co dawny napis.
    """
    return {"competition": getattr(request, "competition", None), "site_root": get_script_prefix()}
