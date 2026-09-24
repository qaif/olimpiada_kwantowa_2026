"""Warstwa ustawiająca konkurs żądania: ``request.competition`` i zmienna kontekstowa.

Co ta warstwa robi przy **jednym** konkursie (czyli dziś, na produkcji): jedno rozstrzygnięcie
witryny – to samo, które i tak wykonuje menu CMS-u, więc z pamięci ``request._wagtail_site`` –
jedno zapytanie o konkurs tej witryny i dwa przypisania. Nie dotyka odpowiedzi, nie dokłada
nagłówka, nie przekierowuje i nie zmienia ani jednego adresu. To jest warunek wdrożenia: przy
jednym konkursie każda odpowiedź ma zostać bajt w bajt taka, jaka była.

Miejsce w ``MIDDLEWARE`` (``config/settings/base.py``) jest częścią kontraktu:

- **za** ``SessionMiddleware`` i ``AuthenticationMiddleware``, bo rozstrzygnięcie ma docelowo móc
  zależeć od użytkownika (przełącznik konkursu dla osoby z kilkoma członkostwami),
- **przed** ``PreferencesMiddleware``, bo język domyślny konkursu jest **niższym** priorytetem niż
  wybór człowieka – ustawienie konta ma prawo go nadpisać,
- warstwa CSP zostaje tam, gdzie jest (przed WhiteNoise): nagłówek składa się na **odpowiedzi**,
  czyli już po przejściu przez tę warstwę, więc czyta konkurs z ``request.competition`` bez
  zmiany kolejności.
"""

from __future__ import annotations

from django.http import HttpResponseNotFound
from django.urls import get_script_prefix, set_script_prefix

from apps.tenancy.context import reset_current_competition, set_current_competition
from apps.tenancy.resolution import (
    INTERNAL_URL_PREFIX,
    platform_subdomain_miss,
    resolve_for_request,
)


class CompetitionMiddleware:
    """Ustala konkurs żądania i udostępnia go obu drogami: atrybutem i kontekstem.

    Dwie drogi, bo mają dwóch różnych odbiorców: widok i szablon dostają ``request.competition``
    (jawnie, bez magii), a kod wołany głęboko – serwis składający list, funkcja licząca termin –
    sięga po ``current_competition()``, bo żądania nie widzi i widzieć nie powinien.

    ``finally`` przy zdejmowaniu kontekstu jest obowiązkowe: wątek z puli obsłuży za chwilę
    kolejne żądanie, a konkurs zostawiony po wyjątku byłby wtedy cudzy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        previous_script_prefix = get_script_prefix()
        competition = self._resolve(request)
        request.competition = competition
        token = set_current_competition(competition)
        try:
            if platform_subdomain_miss(request, competition):
                # Subdomena platformy bez konkursu: 404 **zanim** cokolwiek się wyrenderuje, a nie
                # strona domyślnej witryny. Powód stoi w ``apps/tenancy/resolution.py`` przy
                # ``platform_subdomain_miss``; tutaj liczy się miejsce: odpowiedź składamy w tej
                # warstwie, bo dopiero ona wie, czyj jest host, a niżej w łańcuchu odpowiada już
                # drzewo stron Wagtaila (catch-all w korzeniu), które o konkursach nie wie nic.
                #
                # Odpowiedź jest **pusta** i taka ma zostać: szablon 404 niesie markę konkursu,
                # a tu właśnie nie ma konkursu, którego markę wolno pokazać. Przy wyłączonym
                # przełączniku ta gałąź nie wykonuje ani jednej instrukcji poza sprawdzeniem flagi.
                return HttpResponseNotFound()
            return self.get_response(request)
        finally:
            reset_current_competition(token)
            # Prefiks skryptu też jest stanem wątku. Serwer aplikacji ustawia go co żądanie, ale
            # kod wołany **po** odpowiedzi (zadanie ``on_commit``, warstwa wyżej w łańcuchu)
            # biegnie jeszcze w tym wątku i ma widzieć prefiks zastany, a nie cudzy.
            set_script_prefix(previous_script_prefix)

    def _resolve(self, request):
        """Rozstrzyga konkurs i – w trybie prefiksu – zdejmuje prefiks z adresu."""
        if request.path_info.startswith(INTERNAL_URL_PREFIX):
            # Adresy wewnętrzne (``/internal/tls-allowed``) woła infrastruktura z sieci docker
            # z nagłówkiem ``Host: web:8000``, czyli hostem, który z żadnym konkursem nie ma nic
            # wspólnego. Rozstrzyganie zwróciłoby dla niego witrynę **domyślną**, więc pytanie
            # Caddy'ego o certyfikat kosztowałoby zapytanie do bazy i odpowiadałoby w kontekście
            # cudzego konkursu. Odpowiedź tych adresów nie zależy od konkursu żądania – zależy
            # wyłącznie od parametru ``domain`` – więc kontekst zostaje pusty.
            return None
        resolution = resolve_for_request(request)
        if not resolution.path_prefix:
            return resolution.competition

        # Prefiks znika z adresu, **zanim** urlconf w ogóle go zobaczy: dzięki temu ani jeden
        # wzorzec w ``config/urls.py`` nie musi się zmienić, a Wagtail – catch-all w korzeniu –
        # dostaje ścieżkę liczoną od strony głównej **swojej** witryny.
        # ``request.path`` zostaje **z prefiksem**: to jest adres, o który poprosiła przeglądarka,
        # i to on ma wyjść z ``get_full_path()`` do parametru ``?next=`` przy logowaniu.
        # Rozdział jest ten sam, co przy ``FORCE_SCRIPT_NAME``: ``path`` = prefiks + ``path_info``.
        # Odcinamy **segment**, a nie stałą liczbę znaków: adres z podwojonym ukośnikiem
        # (``//fizyczna/me/`` – przeglądarka wysyła go po sklejeniu linku) ma dać tę samą resztę.
        rest = request.path_info.lstrip("/")[len(resolution.path_prefix) :]
        # Ścieżka musi zostać ścieżką: ``/fizyczna`` (bez ukośnika na końcu) zostawiłoby pusty
        # napis, a urlconf dostałby adres, którego nie zna żaden wzorzec.
        request.path_info = rest if rest.startswith("/") else f"/{rest}"
        request.competition_script_prefix = f"/{resolution.path_prefix}"
        # ``set_script_prefix`` sprawia, że **wszystkie** ``reverse()`` i ``{% url %}`` w tym
        # żądaniu produkują adresy z prefiksem – ten sam mechanizm, którego Django używa dla
        # aplikacji zamontowanej pod podścieżką (``FORCE_SCRIPT_NAME``). Bez niego formularze
        # POST-owałyby pod adres bez prefiksu, czyli do innego konkursu.
        set_script_prefix(request.competition_script_prefix)
        # Drzewo stron **tego** konkursu. ``Site.find_for_request`` zapamiętuje witrynę na żądaniu
        # (``request._wagtail_site``), a czyta ją stamtąd wszystko, co wybiera drzewo stron:
        # ``wagtail.views.serve`` (strona pod adresem), menu, ustawienia witryny
        # (``SiteSettings.for_request``), przekierowania i adresy stron. Dopasowanie po hoście
        # oddałoby tu witrynę **platformy**, czyli stronę konkursu-gospodarza pod adresem konkursu
        # z prefiksem – i odwrotnie, stron tego konkursu nie dałoby się otworzyć wcale. Podmieniamy
        # zapamiętaną wartość, a nie regułę dopasowania: Wagtail nie ma innego miejsca, w którym
        # dałoby się powiedzieć „ta witryna, choć host jest cudzy”.
        request._wagtail_site = resolution.competition.site
        # Witryna hosta zostaje pod ręką: adres bezwzględny strony konkursu pod prefiksem zaczyna
        # się od adresu platformy (``apps.tenancy.page_urls``), a sprawdzenie subdomen platformy
        # (``platform_subdomain_miss``) pyta o witrynę dopasowaną po hoście, a nie o podmienioną.
        request.competition_host_site = resolution.host_site
        return resolution.competition
