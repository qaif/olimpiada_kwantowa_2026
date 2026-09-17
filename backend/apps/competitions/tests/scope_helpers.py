"""Wspólne narzędzia testów zakresowania konkursem (zadanie T3).

Jeden moduł zamiast pięciu kopii tej samej fikstury: testy krzyżowe powstają w pięciu aplikacjach
domeny zawodów, a każdy z nich potrzebuje dokładnie dwóch rzeczy – klienta API mówiącego pod
domenę wskazanego konkursu oraz krótkiej drogi do „zbuduj świat drugiego konkursu”.

Dlaczego to **nie** jest ``conftest.py``: konftesty pakietów testowych (``apps/*/tests/conftest.py``)
należą do zadania T7 i dwa zadania nie edytują tego samego pliku. Zwykły moduł importowany wprost
daje ten sam efekt i nie wchodzi nikomu w drogę.

Fikstury dwóch konkursów (``competition``, ``other_competition``, ``client_for``, autouse
``_bind_competition``) stoją w konfteście projektowym ``backend/conftest.py`` i to z nich te
narzędzia korzystają – kopii świata testowego tu nie ma.
"""

from __future__ import annotations

from rest_framework.test import APIClient

# Import z konftestu korzenia (moduł ``conftest``), a nie kopia listy hostów: domeny konkursów
# testowych mają mieć jedną wartość, inaczej klient i rozstrzyganie hosta patrzyłyby na dwie różne.
from conftest import allow_test_hosts


def host_of(competition) -> str:
    """Domena, pod którą żyje ten konkurs w testach.

    Ta sama kolejność, co w ``client_for``: ``primary_domain`` jest polem konkursu i to ono jest
    prawdą o adresie; ``site.hostname`` jest odwrotem dla konkursu, któremu domeny nie wpisano.
    """
    return competition.primary_domain or competition.site.hostname


def api_client_factory(settings):
    """Zwraca ``api_for(konkurs, user=...)`` – klienta DRF pod domeną wskazanego konkursu.

    Funkcja, a nie fikstura: fikstura o tej samej nazwie, co argument testu, jest dla lintera
    przesłonięciem nazwy (``F811``), a wyciszanie tego ostrzeżenia w pięciu plikach byłoby gorsze
    niż trzy linijki opakowania. Każdy moduł testowy deklaruje więc własną fiksturę ``api_for``,
    która woła tę funkcję – reguła budowania klienta zostaje mimo to w jednym miejscu.

    ``HTTP_HOST`` **i** ``SERVER_NAME``: po pierwszym rozstrzyga się konkurs żądania
    (``CompetitionMiddleware`` → ``Site.find_for_request``), drugi widzi ``build_absolute_uri``,
    czyli adresy w odpowiedziach i w listach. Rozjazd między nimi dałby test, w którym żądanie
    idzie do konkursu A, a link w odpowiedzi prowadzi do B.

    ``force_authenticate`` zamiast logowania: te testy sprawdzają **zakres**, a nie uwierzytelnianie,
    a przejście przez formularz logowania dokładałoby im powodów do padania.
    """

    def api_for(competition, user=None) -> APIClient:
        allow_test_hosts(settings)
        host = host_of(competition)
        client = APIClient(HTTP_HOST=host, SERVER_NAME=host)
        if user is not None:
            client.force_authenticate(user=user)
        return client

    return api_for
