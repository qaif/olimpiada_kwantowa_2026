"""Czy domena w ogóle przyjmuje pocztę – twarda blokada w formularzach (MAIL-02 § 1.2).

Odpowiedź ma trzy wartości i to jest sedno modułu:

- ``True`` – domena ma rekord MX (albo, bez MX, rekord A – „implicit MX”, RFC 5321 § 5.1),
- ``False`` – **na pewno** nie przyjmuje: nie ma ani MX, ani A (NXDOMAIN albo pusta odpowiedź na
  oba pytania), albo ogłasza „null MX” (``MX 0 .``, RFC 7505 – „ta domena nie przyjmuje poczty”),
- ``None`` – nie wiadomo: błąd DNS, przekroczony czas, nazwa, której nie da się zakodować, domena
  zarezerwowana (``.test``, ``example.com``) albo wyłączony przełącznik ``EMAIL_DOMAIN_DNS_CHECK``.

Formularz blokuje wyłącznie ``False``. Każda niepewność przepuszcza adres (fail-open): awaria DNS
nie może zatrzymać rejestracji, a literówka, która prześlizgnie się w takiej chwili, i tak wróci jako
odbicie (§ 2). Rekordów AAAA nie pytamy – relay wysyła wyłącznie po IPv4
(``POSTFIX_inet_protocols: ipv4``), więc domena tylko z IPv6 i tak nie dostałaby od nas listu.

Klient DNS to ``apps.mail_domains.dnsquery`` (biblioteka standardowa, MAIL-01) – z krótkim limitem
czasu i jedną próbą, bo pytanie stoi w środku żądania HTTP. Odpowiedzi trzyma cache Django, żeby
sto rejestracji z ``@uczelnia.edu.pl`` kosztowało jedno pytanie, a nie sto.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from encodings import idna

from django.conf import settings
from django.core.cache import cache

from apps.mail_domains.dnsquery import DnsError, Resolver

from .typos import KNOWN_DOMAINS, unicode_domain

logger = logging.getLogger(__name__)

#: Limit jednego pytania (sekundy) i liczba prób. Dwa pytania (MX, potem A) to w najgorszym razie
#: 3 s dopisane do wysłania formularza – i tylko przy pierwszym adresie z danej domeny.
DNS_TIMEOUT = 1.5
DNS_TRIES = 1

#: Czas życia odpowiedzi w cache'u (sekundy): pewne „tak” – doba, pewne „nie” – godzina (ktoś mógł
#: właśnie założyć domenę), „nie wiadomo” – 5 minut (awaria DNS nie może kosztować 3 s na każdym
#: wysłaniu formularza, ale też nie może zamrozić odpowiedzi na długo).
TTL_ACCEPTS = 24 * 3600
TTL_REJECTS = 3600
TTL_UNKNOWN = 300

#: Ile pytań DNS naraz w jednym procesie ``web`` (wątki gunicorna). Pytanie zadaje wyłącznie formularz,
#: który przeszedł całą pozostałą walidację (``fields.EmailDomainCheckMixin``), więc 4 to dużo.
MAX_CONCURRENT_LOOKUPS = 4
_LOOKUPS = threading.BoundedSemaphore(MAX_CONCURRENT_LOOKUPS)

#: Domeny zarezerwowane (RFC 2606, RFC 6761) – nie pytamy o nie DNS-u i niczego nie blokujemy.
#: Używają ich testy, środowiska e2e i przykłady w dokumentacji.
RESERVED_TLDS = frozenset({"test", "example", "invalid", "localhost", "local"})
RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})

_CODES = {True: 1, False: 0, None: -1}
_VALUES = {1: True, 0: False, -1: None}


def ascii_domain(domain: str) -> str | None:
    """Domena w postaci ASCII do pytania DNS (IDNA 2003 biblioteki standardowej) albo ``None``."""
    labels = []
    for label in unicode_domain(domain).split("."):
        if not label:
            return None
        try:
            labels.append(idna.ToASCII(label).decode("ascii"))
        except UnicodeError:
            return None
    return ".".join(labels)


def is_reserved(domain: str) -> bool:
    domain = unicode_domain(domain)
    return domain in RESERVED_DOMAINS or domain.rpartition(".")[2] in RESERVED_TLDS


def _cache_key(domain: str) -> str:
    # Skrót, a nie domena wprost: ``nazwisko.pl`` bywa daną osobową, a podgląd Redisa nie ma być listą.
    return "email_delivery:dns:" + hashlib.sha256(domain.encode()).hexdigest()[:32]


def resolver() -> Resolver:
    """Klient DNS – osobna funkcja, żeby testy podstawiały atrapę (``FakeResolver`` z MAIL-01)."""
    return Resolver(timeout=DNS_TIMEOUT, tries=DNS_TRIES)


def lookup(domain: str, client=None) -> bool | None:
    """Odpowiedź DNS bez cache'u: ``True`` / ``False`` / ``None`` (patrz docstring modułu)."""
    name = ascii_domain(domain)
    if name is None:
        return None
    client = client or resolver()
    try:
        exchanges = client.mx(name)
        if exchanges:
            # Null MX: jedyny rekord z pustą nazwą (korzeń). Domena mówi wprost „nie przyjmuję”.
            return any(host not in ("", ".") for _preference, host in exchanges)
        return bool(client.a(name))
    except (DnsError, OSError) as exc:
        logger.info("Sprawdzenie DNS domeny adresu e-mail nie dało odpowiedzi: %s", exc)
        return None


def domain_accepts_mail(domain: str) -> bool | None:
    """Czy domena przyjmuje pocztę – z cache'em i wszystkimi wyjątkami z docstringu modułu."""
    domain = unicode_domain(domain)
    if not domain or "." not in domain:
        return None
    if domain in KNOWN_DOMAINS:
        return True
    if is_reserved(domain) or not getattr(settings, "EMAIL_DOMAIN_DNS_CHECK", True):
        return None
    key = _cache_key(domain)
    cached = cache.get(key)
    if cached in _VALUES:
        return _VALUES[cached]
    # Najwyżej MAX_CONCURRENT_LOOKUPS pytań naraz w procesie (przegląd PR #98, L3): fala rejestracji
    # z różnych domen nie może zająć wszystkich wątków gunicorna czekaniem na DNS. Brak miejsca w ciągu
    # limitu jednego pytania = „nie wiadomo” (fail-open), bez zapisu w cache'u.
    if not _LOOKUPS.acquire(timeout=DNS_TIMEOUT):
        logger.warning("Sprawdzenie DNS domeny adresu pominięte – za dużo równoczesnych pytań.")
        return None
    try:
        result = lookup(domain)
    finally:
        _LOOKUPS.release()
    ttl = TTL_ACCEPTS if result is True else TTL_REJECTS if result is False else TTL_UNKNOWN
    cache.set(key, _CODES[result], ttl)
    return result
