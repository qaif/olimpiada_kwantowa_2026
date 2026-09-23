"""Liczenie pobrań plakatów: pobrania łącznie i pobrania z unikalnych adresów IP.

Organizator chce dwóch liczb (uwaga z 23.09.2026): **ile razy** plakat pobrano i **z ilu różnych
adresów IP** – przy czym unikalność ma obowiązywać w całym oglądanym okresie (7 dni, 30 dni, od
początku), a nie w obrębie doby. Pierwsza liczba nie wymaga niczego poza zdarzeniem. Druga wymaga
rozpoznania „ten sam adres co wtedy” – i to jest jedyne miejsce, w którym dotykamy adresu IP.

**Jak:** ``ip_hash = HMAC-SHA256(klucz, adres IP)``. Klucz jest wyprowadzony z ``SECRET_KEY``
z osobnym kontekstem (``promo-ip-hash:``) – tak samo, jak klucz szyfrowania sekretów 2FA
(``apps.accounts.twofactor._fernet_for``) – więc nie leży ani w bazie, ani w kopii zapasowej bazy.
Wynika z tego:

- w bazie nie ma adresu IP; jest jego **pseudonim** (art. 4 pkt 5 RODO). Bez klucza nie da się go
  odwrócić słownikiem – a zwykły SHA-256 adresu łamie się w godzinę, bo adresów IPv4 są tylko
  cztery miliardy. Ten sam adres daje zawsze ten sam skrót, i o to w tej funkcji chodzi,
- nagłówek przeglądarki (``User-Agent``) **nie** wchodzi do skrótu – organizator liczy adresy, nie
  urządzenia; nagłówek służy wyłącznie do odsiania robotów i nigdzie się nie zapisuje,
- pseudonim nadal jest daną osobową (administrator ma klucz), więc ma termin: po
  ``IP_HASH_RETENTION_MONTHS`` miesiącach zadanie ``apps.promo.tasks.clear_expired_ip_hashes``
  zeruje skrót, a zdarzenie zostaje do liczby pobrań łącznie. Wpis w rejestrze czynności
  przetwarzania: ``apps.accounts.processing_register`` (czynność ``plakaty``),
- **zmiana ``SECRET_KEY``** zmienia klucz, więc pobrania sprzed zmiany i po niej z tego samego adresu
  liczą się jako dwa różne adresy. To jest ta sama konsekwencja, co przy 2FA, i akceptowalny błąd
  statystyki – a nie powód, żeby trzymać osobny klucz, o którym trzeba pamiętać przy odtwarzaniu.

Adres klienta bierze ``apps.core.models.client_ip`` – ta sama funkcja, co audyt: ``X-Real-IP`` od
Caddy'ego wyłącznie z zaufanego proxy (``TRUSTED_PROXY_IPS``), inaczej ``REMOTE_ADDR``. Dowolny
``X-Forwarded-For`` od klienta nie ma tu wstępu – inaczej jeden skrypt „pobierałby” z miliona
adresów.

**Podwójne kliknięcie** (ten sam plakat z tego samego adresu w ciągu ``DEBOUNCE_SECONDS``) dostaje
plik, ale nie jest drugim pobraniem. Nadmiar żądań z jednego adresu odbija wcześniej limit
``poster_download`` w widoku (``apps.web.views.posters``) – odbite żądanie w ogóle tu nie dochodzi.

**Czego nie liczymy:** robotów (wyszukiwarki, podglądy linków w komunikatorach, narzędzia
wiersza poleceń – ``BOT_USER_AGENT``), żądań bez nagłówka ``User-Agent`` (przeglądarka zawsze go
wysyła, brak jest podpisem skryptu), żądań ``HEAD`` (sprawdzenie, czy plik istnieje, nie jest
pobraniem) oraz pobrań przez koordynatora tego konkursu – on sprawdza, czy wgrał właściwy plik,
i jego kliknięcia zawyżałyby liczby, o które sam pyta.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import timedelta
from functools import lru_cache

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from apps.core.models import client_ip

from .models import PromoDownload

logger = logging.getLogger(__name__)

#: Okno odsiewania podwójnego kliknięcia (sekundy). Drugie pobranie tego samego plakatu z tego
#: samego adresu w tym oknie dostaje plik, ale nie trafia do statystyk – to jest jedno pobranie
#: klikniętego dwa razy przycisku albo przeglądarka ponawiająca przerwane żądanie, a nie druga osoba.
DEBOUNCE_SECONDS = 10

#: Kontekst wyprowadzenia klucza. Osobny napis dla osobnego zastosowania: ten sam ``SECRET_KEY``
#: podpisuje sesje i szyfruje sekrety 2FA, a klucz tych skrótów nie może być żadnym z tamtych.
KEY_CONTEXT = "promo-ip-hash"

#: Nagłówki przeglądarek, których **nie liczymy**. Wzorzec jest szeroki celowo: pominięcie jednego
#: człowieka z egzotyczną przeglądarką kosztuje jedną jednostkę w statystyce, a policzenie robota
#: indeksującego (który przychodzi codziennie) zawyża ją bez końca.
#:
#: - ``bot`` łapie Googlebota, bingbota, AdsBota, Twitterbota, Discordbota, Telegrambota i dziesiątki
#:   innych; ``(?<!cu)`` wyłącza telefony marki Cubot, które mają „CUBOT” w nagłówku przeglądarki,
#: - ``crawl``, ``spider``, ``slurp`` (Yahoo), ``preview`` (BingPreview, SkypeUriPreview, Google Web
#:   Preview), ``facebookexternalhit``/``facebookcatalog``, ``whatsapp``, ``embedly``, ``iframely`` –
#:   podglądy linków, które komunikator pobiera w chwili wklejenia adresu do rozmowy,
#: - ``curl``, ``wget``, ``python-``, ``httpx``, ``aiohttp``, ``go-http-client``, ``okhttp``,
#:   ``java/``, ``libwww``, ``scrapy`` – narzędzia i biblioteki, nie ludzie,
#: - ``headless``, ``lighthouse``, ``pingdom``, ``uptime``, ``monitor`` – przeglądarki sterowane
#:   skryptem i usługi sprawdzające dostępność.
BOT_USER_AGENT = re.compile(
    r"(?<!cu)bot|crawl|spider|slurp|preview|facebookexternalhit|facebookcatalog|whatsapp|embedly"
    r"|iframely|curl|wget|python-|httpx|aiohttp|go-http-client|okhttp|java/|libwww|scrapy"
    r"|headless|lighthouse|pingdom|uptime|monitor",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    """Czy nagłówek przeglądarki wygląda na robota. Brak nagłówka też jest robotem."""
    user_agent = (user_agent or "").strip()
    if not user_agent:
        return True
    return BOT_USER_AGENT.search(user_agent) is not None


@lru_cache(maxsize=4)
def _key_for(secret_key: str) -> bytes:
    """Klucz HMAC wyprowadzony z ``SECRET_KEY``. Memoizowany po **wartości** ustawienia.

    Ten sam zabieg, co w ``apps.accounts.twofactor._fernet_for``: test podmieniający ``SECRET_KEY``
    dostaje inny klucz, a produkcja wyprowadza go raz. Pojedyncze SHA-256 wystarcza – ``SECRET_KEY``
    ma 64 losowe znaki, więc nie ma tu czego rozciągać; chodzi o oddzielenie kontekstów.
    """
    return hashlib.sha256(f"{KEY_CONTEXT}:{secret_key}".encode()).digest()


def ip_hash(ip: str | None) -> str | None:
    """Pseudonim adresu IP (64 znaki szesnastkowe) albo ``None``, gdy adresu nie ma."""
    if not ip:
        return None
    return hmac.new(_key_for(settings.SECRET_KEY), ip.encode("ascii", "replace"), hashlib.sha256).hexdigest()


def _is_competition_coordinator(request, competition) -> bool:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    return has_role(user, competition, CompetitionRole.COORDINATOR)


def should_record(request, material) -> bool:
    """Czy to żądanie jest pobraniem, które ma trafić do statystyk (patrz docstring modułu)."""
    if request.method != "GET":
        return False
    if is_bot(request.META.get("HTTP_USER_AGENT")):
        return False
    return not _is_competition_coordinator(request, material.competition)


def _is_repeat(material, hashed: str, now) -> bool:
    """Czy ten adres pobrał ten plakat w ostatnich ``DEBOUNCE_SECONDS`` sekundach.

    Zapytanie do bazy, a nie znacznik w pamięci podręcznej: przy awarii Redisa
    (``IGNORE_EXCEPTIONS``) znacznik „milczałby” i albo gubił pobrania, albo przestawał odsiewać.
    Indeks ``promo_download_material`` (plakat, czas) obsługuje je bez przeglądania tabeli. Dwa
    równoległe żądania w tej samej milisekundzie mogą oba przejść – to jest błąd o jedną jednostkę,
    którego nie warto okupować blokadą.
    """
    return PromoDownload.objects.filter(
        material=material,
        ip_hash=hashed,
        downloaded_at__gte=now - timedelta(seconds=DEBOUNCE_SECONDS),
    ).exists()


def record_download(request, material) -> PromoDownload | None:
    """Zapisuje pobranie, jeśli się kwalifikuje. Błąd bazy **nie** blokuje pobrania pliku.

    Nauczyciel, który kliknął „Pobierz”, ma dostać plakat także wtedy, gdy zapis statystyki się nie
    uda – statystyka jest dla organizatora, plik dla szkoły. Awaria trafia do logu z identyfikatorem
    plakatu i niczym więcej (adresu IP nie ma nawet w logu).
    """
    if not should_record(request, material):
        return None
    now = timezone.now()
    hashed = ip_hash(client_ip(request))
    try:
        if hashed and _is_repeat(material, hashed, now):
            return None
        return PromoDownload.objects.create(
            material=material,
            competition_id=material.competition_id,
            downloaded_at=now,
            ip_hash=hashed,
        )
    except DatabaseError:
        logger.warning("Nie udało się zapisać pobrania plakatu #%s.", material.pk, exc_info=True)
        return None
