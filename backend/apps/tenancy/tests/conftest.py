"""Fikstury wielodostępności **tego** pakietu.

Fikstura dwóch konkursów mieszka od zadania T7 w konfteście projektowym (``backend/conftest.py``):
``competition``, ``other_competition``, ``as_competition``, ``client_for`` i autouse
``_bind_competition`` stoją tam, bo potrzebują ich także fabryki (``apps/*/tests/factories.py``)
i testy krzyżowe innych aplikacji. Trzymanie ich tutaj znaczyłoby, że reguła izolacji jest
sprawdzalna wyłącznie w aplikacji, która ją wprowadziła – czyli nigdzie, gdzie o nią chodzi.

Zostaje to, co dotyczy wyłącznie tego pakietu: skróty nazw hostów, których używają testy
rozstrzygania i warstwy, oraz dopuszczenie domen testowych w ``ALLOWED_HOSTS`` dla **każdego**
testu pakietu – rozstrzyganie konkursu zaczyna się od nagłówka ``Host``, a Django sprawdza go,
zanim dojdzie do naszej warstwy.
"""

from __future__ import annotations

import pytest

# Import z konftestu korzenia (``backend/conftest.py``, moduł ``conftest``), a nie kopia helperów:
# hosty konkursów testowych mają mieć **jedną** wartość. Dwa napisy w dwóch plikach rozjechałyby
# się przy pierwszej zmianie, a objawem byłby test rozstrzygania hosta, który nagle niczego nie
# rozstrzyga, bo obie domeny są tą samą domeną.
from conftest import (
    HOST_COMPETITION,
    HOST_OTHER_COMPETITION,
    allow_test_hosts,
    make_competition,
    make_site,
    root_page,
)

#: Skróty czytelne w asercjach tego pakietu: „konkurs A” i „konkurs B”. Wartości są wspólne
#: z konftestem projektowym – patrz import wyżej.
HOST_A = HOST_COMPETITION
HOST_B = HOST_OTHER_COMPETITION

#: Helpery budujące świat dwóch konkursów są re-eksportowane, bo korzystają z nich testy tego
#: pakietu (``from .conftest import make_site``). ``__all__`` mówi zarazem lintowi, że import
#: bez użycia w tym module jest tu celem, a nie zapomnianym wierszem.
__all__ = ["HOST_A", "HOST_B", "make_competition", "make_site", "open_path_prefixes", "root_page"]


def open_path_prefixes(host_competition):
    """Włącza konkursowi-gospodarzowi ``path_prefix_routing`` – bramkę trybu prefiksu (uwaga T43).

    Pod hostem konkursu bez tej flagi prefiks ścieżki nie rozstrzyga niczego
    (``apps.tenancy.resolution.hosts_path_prefixes``), więc test konkursu pod prefiksem zaczyna
    od otwarcia bramki – dokładnie tak, jak robi to ``create_competition --path-prefix``.
    """
    host_competition.feature_flags = {**(host_competition.feature_flags or {}), "path_prefix_routing": True}
    host_competition.save(update_fields=["feature_flags"])
    return host_competition


@pytest.fixture(autouse=True)
def _allow_test_hosts(settings):
    """Domeny konkursów testowych na liście dozwolonych – inaczej ``get_host()`` podnosi 400.

    ``autouse`` na poziomie pakietu, a nie projektu: tu **każdy** test posługuje się hostem, a poza
    tym pakietem robi to garstka, która i tak przechodzi przez ``client_for``.
    """
    allow_test_hosts(settings)
