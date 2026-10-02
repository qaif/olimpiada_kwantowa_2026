"""Dokąd wolno wysłać webhook – obrona przed SSRF (pakiet 5 po audycie).

Adres odbiorcy wpisuje koordynator, a żądanie wysyła **nasz** worker – z wnętrza sieci compose'a.
Do tej zmiany sprawdzany był wyłącznie schemat (``https``), a ``requests.post`` szedł za
przekierowaniami. Koordynator (albo ktoś, kto przejął jego sesję) mógł więc kazać workerowi pukać
do usług, których z internetu nie widać – ``db``, ``redis``, ``minio``, ``web:8000``, adres
metadanych chmury – i odczytywać skutek z pola „ostatni błąd” w panelu (komunikat wyjątku niósł
host, port i powód odmowy: gotowy skaner portów).

Trzy warstwy, każda łapie co innego:

1. **zapis odbiorcy** (``validate_target_url`` z ``WebhookEndpoint.clean``): odmowa dla literału IP
   z zakresu niepublicznego i dla nazwy jednoczłonowej (``db``, ``redis``, ``localhost``) – takie
   nazwy rozwiązuje wyłącznie DNS compose'a, więc nigdy nie wskazują partnera w internecie,
2. **doręczenie** (``check_delivery_target``): nazwa jest rozwiązywana tuż przed żądaniem i **każdy**
   zwrócony adres (IPv4 i IPv6) musi być publiczny. Jeden adres prywatny wśród publicznych to
   odmowa – ``requests`` mógłby trafić akurat w niego,
3. **bez przekierowań** (``allow_redirects=False`` w ``webhooks.post_payload``): odpowiedź 3xx jest
   porażką doręczenia, a nie zaproszeniem pod adres, którego nikt nie sprawdził.

„Publiczny” = ``ipaddress`` mówi ``is_global`` i adres nie jest multicastem ani nie leży w sieci
z ``WEBHOOK_BLOCKED_NETWORKS`` (domyślnie podsieci compose'a ``172.30.0.0/16`` – wymienione
jawnie, choć i tak mieszczą się w prywatnym 172.16/12, żeby zmiana podsieci w compose'ie na
publiczną pulę nie otworzyła ich po cichu). ``is_global`` odrzuca naraz loopback, sieci prywatne,
link-local (w tym metadane chmury ``169.254.169.254``), CGNAT, zakresy dokumentacyjne
i zarezerwowane. Adres IPv6 z osadzonym IPv4 (``::ffff:10.0.0.1``) oceniamy po części IPv4.

Czego to **nie** zamyka: DNS rebinding. Nazwa jest rozwiązywana dwa razy – przez nas i chwilę
później przez ``requests`` – i złośliwy serwer DNS z zerowym TTL może za drugim razem podać adres
wewnętrzny. Przypięcie adresu w ``requests`` przy HTTPS wymaga własnego adaptera (SNI i weryfikacja
certyfikatu muszą dalej używać nazwy), więc zostaje to długiem; skutki ogranicza to, że pole
błędu nie niesie już żadnej treści odpowiedzi ani wyjątku, a ciało żądania jest stałe i podpisane.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

#: Sieci odrzucane ponad to, co mówi ``ipaddress.is_global``. Nadpisywalne w ustawieniach.
DEFAULT_BLOCKED_NETWORKS = ("172.30.0.0/16",)

#: Komunikat zapisu odbiorcy – jeden dla wszystkich powodów, bez echa adresu.
TARGET_REFUSED_MESSAGE = (
    "Adres webhooka musi wskazywać serwer dostępny publicznie w internecie (pełna nazwa domeny, "
    "nie adres sieci wewnętrznej)."
)

#: Kody zapisywane w ``WebhookDelivery.last_error``, gdy doręczenie odpada przed wysłaniem.
REFUSED_ADDRESS = "Adres odbiorcy niedozwolony"
UNRESOLVED_ADDRESS = "Nie udało się ustalić adresu odbiorcy"


class TargetRefused(Exception):
    """Doręczenie odrzucone przed wysłaniem. ``code`` – ogólna klasa błędu do ``last_error``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def blocked_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    configured = getattr(settings, "WEBHOOK_BLOCKED_NETWORKS", DEFAULT_BLOCKED_NETWORKS)
    return [ipaddress.ip_network(value, strict=False) for value in configured]


def is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Czy pod ten adres workerowi nie wolno wysłać żądania."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global or address.is_multicast:
        return True
    return any(address in network for network in blocked_networks() if network.version == address.version)


def _literal_address(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def validate_target_url(url: str) -> None:
    """Reguły zapisu odbiorcy (warstwa 1). ``ValidationError`` na polu ``url``."""
    host = (urlsplit(url or "").hostname or "").rstrip(".").lower()
    if not host:
        raise ValidationError({"url": TARGET_REFUSED_MESSAGE})
    literal = _literal_address(host)
    if literal is not None:
        if is_forbidden_address(literal):
            raise ValidationError({"url": TARGET_REFUSED_MESSAGE})
        return
    if "." not in host or host == "localhost" or host.endswith(".localhost"):
        raise ValidationError({"url": TARGET_REFUSED_MESSAGE})


def resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Wszystkie adresy nazwy (IPv4 i IPv6). Osobna funkcja – testy podstawiają ją zamiast DNS-u."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]


def check_delivery_target(url: str) -> None:
    """Warstwa 2: każdy adres, pod który może pójść żądanie, musi być publiczny."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise TargetRefused(REFUSED_ADDRESS)
    try:
        validate_target_url(url)
    except ValidationError:
        # Odbiorca zapisany przed tą zmianą (albo z pominięciem ``full_clean``) – reguła zapisu
        # obowiązuje także przy doręczeniu.
        raise TargetRefused(REFUSED_ADDRESS) from None
    try:
        port = parts.port or 443
    except ValueError:
        raise TargetRefused(REFUSED_ADDRESS) from None
    literal = _literal_address(host)
    if literal is not None:
        addresses = [literal]
    else:
        try:
            addresses = resolve(host, port)
        except OSError, UnicodeError, ValueError:
            raise TargetRefused(UNRESOLVED_ADDRESS) from None
    if not addresses or any(is_forbidden_address(address) for address in addresses):
        raise TargetRefused(REFUSED_ADDRESS)
