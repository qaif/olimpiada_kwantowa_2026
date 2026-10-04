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
from django.utils.functional import SimpleLazyObject


def competition(request) -> dict:
    """``{"competition": <Competition|None>, "site_root": "/"}`` dla każdego szablonu.

    ``site_root`` to korzeń serwisu **tego** konkursu, zawsze z ukośnikiem na końcu: ``/`` pod własną
    domeną, ``/druga/`` w konkursie adresowanym prefiksem ścieżki (uwaga T43). Szablon bazowy pisze
    nim odnośniki do strony głównej i do stron CMS o stałym adresie (``{{ site_root }}dokumenty/rodo/``)
    – napis ``/`` wpisany na sztywno wyprowadzałby czytelnika konkursu pod prefiksem do
    konkursu-gospodarza. Bez prefiksu wynik jest co do znaku ten sam, co dawny napis.
    """
    current = getattr(request, "competition", None)
    return {
        "competition": current,
        "site_root": get_script_prefix(),
        # „Województwo” / „Kraj” / „Region” – nagłówki kolumn i etykiety podziału terytorialnego
        # (docs/tasks/REG-01.md § 1.2). Leniwie: odpowiedź przy włączonym ``custom_regions`` kosztuje
        # zapytanie, a pyta o nią garstka szablonów, nie każda strona.
        "region_noun": SimpleLazyObject(lambda: _region_noun(current)),
    }


def _region_noun(current) -> str:
    from apps.accounts.regions import region_noun

    return region_noun(current)
