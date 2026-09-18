"""``GET /internal/tls-allowed?domain=…`` — pytanie Caddy'ego „czy wystawić certyfikat na ten host”.

Konkursy stojące pod subdomenami platformy (``<slug>.<SITE_DOMAIN>``) powstają **z panelu**, a nie
z ``.env``: koordynator zakłada konkurs, a adres ma działać od razu. Caddy nie może więc trzymać
listy domen w konfiguracji — musi pytać, i pyta właśnie tutaj (``on_demand_tls { ask … }``).

**Dlaczego to jest ostrożniejsze, niż wygląda.** On-demand TLS bez pytania jest otwartym
generatorem certyfikatów: każdy host wskazany DNS-em na nasz adres IP dostałby certyfikat, a limity
Let's Encrypt (50 certyfikatów na domenę na tydzień) da się wtedy wyczerpać z zewnątrz w kilka
minut. To pytanie zawęża wystawianie do trzech warunków naraz:

1. instalacja **w ogóle** prowadzi konkursy w subdomenach (``PLATFORM_SUBDOMAINS``),
2. pytany host jest **jednoczłonową** subdomeną ``SITE_DOMAIN`` — tyle, ile obejmuje jeden
   certyfikat, i tyle, ile umie założyć panel,
3. pod tym hostem stoi **aktywny** konkurs: jego domena główna albo witryna aliasu (drugie drzewo
   językowe, ``tenancy.CompetitionSiteAlias``).

**Dlaczego bez uwierzytelnienia.** Caddy woła ten adres z sieci ``internal`` compose'a, po HTTP,
jako ``http://web:8000/internal/tls-allowed?domain=…`` — czyli zanim istnieje jakikolwiek
certyfikat i bez możliwości podania nagłówka z sekretem (dyrektywa ``ask`` go nie ma). Zamiast
poświadczenia bramką jest więc **nagłówek ``Host``**: odpowiadamy wyłącznie żądaniu, które
przyszło na adres wewnętrzny (``web``, ``localhost``, ``127.0.0.1`` — z portem lub bez). Żądanie
z hosta publicznego dostaje 404, więc z internetu ten adres wygląda dokładnie tak, jakby go nie
było. Jest to ta sama reguła, którą ma healthcheck kontenera (``docker-compose.yml`` puka pod
``http://127.0.0.1:8000/healthz/``) — nie dokładamy żadnego nowego hosta do ``ALLOWED_HOSTS``,
bo ``web``, ``localhost`` i ``127.0.0.1`` są tam od pierwszego wdrożenia.

**Odpowiedź jest pusta.** Caddy czyta wyłącznie kod: 2xx = wystawiaj, cokolwiek innego = nie.
Treść byłaby informacją wysyłaną do kogoś, kto jej nie czyta — i jedyną rzeczą, którą dałoby się
z tego adresu wyciągnąć o cudzych konkursach.

**Bufor 60 sekund.** Caddy pyta przy pierwszym uścisku dłoni z każdym hostem, a boty skanujące
domenę potrafią zapytać o setkę nazw w minutę. Minuta to zarazem czas, po którym konkurs założony
przed chwilą jest widoczny dla proxy — tyle, ile mówi ekran po założeniu konkursu („certyfikat
wystawia się przy pierwszym wejściu, może to potrwać do minuty”).

Ten widok **nic nie zapisuje** i nie ma limitu prób: limit liczony po adresie IP byłby liczony po
adresie kontenera proxy, czyli po jednym adresie dla całego ruchu, a przekroczenie go znaczyłoby
brak certyfikatów dla wszystkich konkursów naraz. Ochroną jest bramka hosta i bufor.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.http import require_GET

from apps.tenancy.models import Competition
from apps.tenancy.resolution import is_platform_subdomain, platform_subdomains_enabled

#: Nazwy hostów, spod których wolno zadać to pytanie. Bez portu — porównujemy samą nazwę, więc
#: ``web:8000`` (tak puka Caddy) i ``127.0.0.1:8000`` (tak puka healthcheck) mieszczą się w tym
#: samym zbiorze. Wszystkie trzy są nierozwiązywalne spoza sieci compose'a.
INTERNAL_HOST_NAMES = frozenset({"web", "localhost", "127.0.0.1"})

#: Nazwa parametru adresu — kształt wymuszony przez Caddy'ego (``ask`` dokleja ``?domain=<host>``).
DOMAIN_QUERY_PARAM = "domain"

#: Przedrostek klucza bufora i czas jego życia.
CACHE_KEY_PREFIX = "tenancy:tls-allowed:"
CACHE_SECONDS = 60


@require_GET
def tls_allowed(request):
    """200 z pustą treścią, gdy wolno wystawić certyfikat na ``?domain=``; w każdym innym razie 404.

    Kolejność sprawdzeń jest kolejnością od najtańszego do najdroższego i zarazem od
    najostrożniejszego: host żądania (nagłówek, zero zapytań), przełącznik instalacji (ustawienie),
    kształt nazwy (napis), dopiero na końcu baza — i ta jedna odpowiedź jedzie do bufora.
    """
    if not _from_internal_host(request):
        return HttpResponseNotFound()
    if not platform_subdomains_enabled():
        # Przełącznik czytamy **przed** buforem, a nie w funkcji buforowanej: inaczej odpowiedź
        # zapamiętana przy jednym stanie ustawień przeżyłaby jego zmianę o minutę.
        return HttpResponseNotFound()

    domain = _normalise(request.GET.get(DOMAIN_QUERY_PARAM, ""))
    if not is_platform_subdomain(domain):
        return HttpResponseNotFound()

    key = f"{CACHE_KEY_PREFIX}{domain}"
    allowed = cache.get(key)
    if allowed is None:
        allowed = _has_active_competition(domain)
        cache.set(key, allowed, CACHE_SECONDS)
    return HttpResponse(status=200) if allowed else HttpResponseNotFound()


def _from_internal_host(request) -> bool:
    """Czy żądanie przyszło na adres wewnętrzny kontenera, a nie na domenę publiczną."""
    host = (request.get_host() or "").strip().lower()
    return host.partition(":")[0].rstrip(".") in INTERNAL_HOST_NAMES


def _has_active_competition(domain: str) -> bool:
    """Czy pod tym hostem stoi aktywny konkurs — jako domena główna albo jako witryna aliasu.

    Trzy alternatywy w jednym zapytaniu, a nie trzy zapytania: ``primary_domain`` jest odpowiedzią
    na pytanie „czyj jest ten adres” poza żądaniem, ``site__hostname`` – tym, po czym host
    dopasowuje Wagtail, a ``site_aliases`` – drugą witryną tego samego konkursu (drugi język
    treści). Rozjazd między dwoma pierwszymi wyłapuje ``manage.py check_domains``; tutaj wystarczy,
    że host jest **którymkolwiek** z nich, bo w obu wypadkach certyfikat jest potrzebny.

    Błąd bazy daje ``False``: „nie wiem” musi tu znaczyć „nie wystawiaj”. Odwrotna decyzja
    zamieniałaby awarię bazy w otwarty generator certyfikatów.
    """
    try:
        return (
            Competition.objects.filter(is_active=True)
            .filter(
                Q(primary_domain__iexact=domain)
                | Q(site__hostname__iexact=domain)
                | Q(site_aliases__site__hostname__iexact=domain)
            )
            .exists()
        )
    except DatabaseError:  # pragma: no cover - baza bez migracji albo bez połączenia
        return False


def _normalise(value: str) -> str:
    """Host z parametru adresu do porównania: bez portu, bez kropki końcowej, małymi literami."""
    return (value or "").strip().lower().partition(":")[0].rstrip(".")
