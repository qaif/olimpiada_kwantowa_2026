"""Minimalny klient DNS (RFC 1035) na bibliotece standardowej: rekordy TXT, A i MX.

Dlaczego własny, a nie ``dnspython``: sprawdzenie rekordów poczty (``check_mail_dns``) to jedyne
miejsce w aplikacji, które pyta DNS o coś więcej niż adres – a biblioteka standardowa umie tylko
``getaddrinfo`` (rekordy A/AAAA). Trzy typy rekordów, jedno pytanie na raz i odpowiedź rekurencyjnego
resolwera (``RD=1``) to kilkadziesiąt linii; nowa zależność obrazu dla jednej komendy operatora
byłaby droższa niż ten moduł (zasada zadania MAIL-01: bez nowych zależności).

Czego klient **nie** robi: nie jest resolwerem rekurencyjnym (pyta serwery z ``/etc/resolv.conf``
albo podane jawnie), nie sprawdza DNSSEC i nie ma pamięci podręcznej. Pyta UDP z EDNS0 (bufor 4096 B
– klucz DKIM 2048 bitów to ~420 bajtów samego TXT, więc klasyczne 512 B bywa za mało), a przy
odpowiedzi uciętej (bit TC) ponawia przez TCP.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from dataclasses import dataclass, field

TYPE_A = 1
TYPE_CNAME = 5
TYPE_MX = 15
TYPE_TXT = 16
TYPE_OPT = 41
CLASS_IN = 1

RCODE_NXDOMAIN = 3

#: Serwery zapasowe, gdy ``/etc/resolv.conf`` nie istnieje albo jest pusty (np. Windows w devie).
FALLBACK_NAMESERVERS = ("1.1.1.1", "8.8.8.8")

#: Ile skoków wskaźnika kompresji nazwy przyjmujemy. Poprawna odpowiedź ma ich kilka; pętla
#: wskaźników w spreparowanym pakiecie zawiesiłaby komendę na zawsze.
MAX_POINTER_JUMPS = 64


class DnsError(Exception):
    """Odpowiedź, z której nie da się nic powiedzieć: brak odpowiedzi, SERVFAIL, pakiet zepsuty."""


@dataclass
class Answer:
    """Rekordy jednej odpowiedzi. ``records`` to krotki ``(typ, wartość)``; NXDOMAIN = pusta lista."""

    records: list[tuple[int, object]] = field(default_factory=list)
    truncated: bool = False
    nxdomain: bool = False


def default_nameservers(path: str = "/etc/resolv.conf") -> list[str]:
    """Serwery z ``resolv.conf`` (w kontenerze: wbudowany DNS Dockera ``127.0.0.11``)."""
    servers: list[str] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    try:
                        ipaddress.ip_address(parts[1].split("%")[0])
                    except ValueError:
                        continue
                    servers.append(parts[1])
    except OSError:
        pass
    return servers or list(FALLBACK_NAMESERVERS)


def encode_name(name: str) -> bytes:
    """Nazwa domenowa w zapisie etykiet. IDN przez ``idna`` (biblioteka standardowa, IDNA 2003)."""
    name = name.strip().rstrip(".")
    if not name:
        return b"\x00"
    out = bytearray()
    for label in name.split("."):
        raw = label.encode("idna") if any(ord(ch) > 127 for ch in label) else label.encode("ascii")
        if not raw or len(raw) > 63:
            raise DnsError(f"nieprawidłowa etykieta nazwy: {label!r}")
        out.append(len(raw))
        out += raw
    out.append(0)
    if len(out) > 255:
        raise DnsError("nazwa domenowa dłuższa niż 255 bajtów")
    return bytes(out)


def build_query(qid: int, name: str, qtype: int) -> bytes:
    """Pytanie z bitem RD i rekordem OPT (EDNS0, bufor 4096 B)."""
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 1)
    question = encode_name(name) + struct.pack("!HH", qtype, CLASS_IN)
    opt = b"\x00" + struct.pack("!HHIH", TYPE_OPT, 4096, 0, 0)
    return header + question + opt


def read_name(message: bytes, offset: int) -> tuple[str, int]:
    """Nazwa od ``offset`` z obsługą kompresji (RFC 1035 § 4.1.4). Zwraca (nazwa, offset za nią)."""
    labels: list[str] = []
    jumps = 0
    end: int | None = None
    while True:
        if offset >= len(message):
            raise DnsError("nazwa wychodzi poza pakiet")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise DnsError("ucięty wskaźnik kompresji")
            jumps += 1
            if jumps > MAX_POINTER_JUMPS:
                raise DnsError("pętla wskaźników kompresji")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | message[offset + 1]
            continue
        if length & 0xC0:
            raise DnsError("nieznany typ etykiety")
        offset += 1
        if length == 0:
            break
        labels.append(message[offset : offset + length].decode("ascii", errors="replace"))
        offset += length
    return ".".join(labels), (end if end is not None else offset)


def _txt_value(rdata: bytes) -> str:
    """Wartość TXT: napisy znakowe sklejone **bez** separatora (RFC 7208 § 3.3, RFC 6376 § 3.6.2.2)."""
    parts: list[bytes] = []
    index = 0
    while index < len(rdata):
        length = rdata[index]
        parts.append(rdata[index + 1 : index + 1 + length])
        index += 1 + length
    return b"".join(parts).decode("utf-8", errors="replace")


def parse_response(message: bytes, qid: int) -> Answer:
    """Rekordy sekcji odpowiedzi. CNAME pomijamy – resolwer rekurencyjny dokłada rekord docelowy."""
    if len(message) < 12:
        raise DnsError("odpowiedź krótsza niż nagłówek")
    rid, flags, qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", message[:12])
    if rid != qid:
        raise DnsError("odpowiedź na inne pytanie (identyfikator się nie zgadza)")
    if not flags & 0x8000:
        raise DnsError("pakiet nie jest odpowiedzią")
    answer = Answer(truncated=bool(flags & 0x0200))
    rcode = flags & 0x000F
    if rcode == RCODE_NXDOMAIN:
        answer.nxdomain = True
        return answer
    if rcode != 0:
        raise DnsError(f"serwer DNS odpowiedział kodem błędu {rcode}")
    offset = 12
    for _ in range(qdcount):
        _name, offset = read_name(message, offset)
        offset += 4
    for _ in range(ancount):
        _name, offset = read_name(message, offset)
        if offset + 10 > len(message):
            raise DnsError("ucięty rekord odpowiedzi")
        rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", message[offset : offset + 10])
        offset += 10
        rdata = message[offset : offset + rdlength]
        if len(rdata) != rdlength:
            raise DnsError("ucięte dane rekordu")
        if rtype == TYPE_TXT:
            answer.records.append((TYPE_TXT, _txt_value(rdata)))
        elif rtype == TYPE_A and rdlength == 4:
            answer.records.append((TYPE_A, socket.inet_ntoa(rdata)))
        elif rtype == TYPE_MX and rdlength >= 3:
            preference = struct.unpack("!H", rdata[:2])[0]
            exchange, _ = read_name(message, offset + 2)
            answer.records.append((TYPE_MX, (preference, exchange.lower())))
        offset += rdlength
    return answer


class Resolver:
    """Pytania do resolwera rekurencyjnego. Interfejs (``txt``/``a``/``mx``) podmieniają testy atrapą."""

    def __init__(self, nameservers: list[str] | None = None, timeout: float = 3.0, tries: int = 2):
        self.nameservers = list(nameservers or default_nameservers())
        self.timeout = timeout
        self.tries = tries

    def txt(self, name: str) -> list[str]:
        return [value for rtype, value in self.query(name, TYPE_TXT) if rtype == TYPE_TXT]

    def a(self, name: str) -> list[str]:
        return [value for rtype, value in self.query(name, TYPE_A) if rtype == TYPE_A]

    def mx(self, name: str) -> list[tuple[int, str]]:
        return sorted(value for rtype, value in self.query(name, TYPE_MX) if rtype == TYPE_MX)

    def query(self, name: str, qtype: int) -> list[tuple[int, object]]:
        last_error: Exception | None = None
        for _attempt in range(self.tries):
            for server in self.nameservers:
                qid = int.from_bytes(os.urandom(2), "big")
                packet = build_query(qid, name, qtype)
                try:
                    answer = parse_response(self._udp(server, packet), qid)
                    if answer.truncated:
                        answer = parse_response(self._tcp(server, packet), qid)
                    return answer.records
                except (OSError, DnsError) as exc:
                    last_error = exc
        raise DnsError(f"brak odpowiedzi DNS dla {name}: {last_error}")

    def _family(self, server: str) -> int:
        return socket.AF_INET6 if ":" in server else socket.AF_INET

    def _udp(self, server: str, packet: bytes) -> bytes:
        with socket.socket(self._family(server), socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(65535)
            return data

    def _tcp(self, server: str, packet: bytes) -> bytes:
        with socket.create_connection((server, 53), timeout=self.timeout) as sock:
            sock.sendall(struct.pack("!H", len(packet)) + packet)
            length = struct.unpack("!H", _recv_exact(sock, 2))[0]
            return _recv_exact(sock, length)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise DnsError("połączenie TCP zamknięte w środku odpowiedzi")
        chunks += chunk
    return bytes(chunks)
