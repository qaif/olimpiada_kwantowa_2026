"""Minimalny klient clamd (protokół INSTREAM po TCP). Bez dodatkowych zależności.

Protokół (clamd, tryb ``z``-terminowany):
1. klient wysyła ``zINSTREAM\\0``,
2. następnie kolejne porcje danych, każda poprzedzona 4-bajtową długością (big-endian, unsigned),
3. porcja o długości 0 kończy strumień,
4. serwer odpowiada jedną linią zakończoną ``\\0``: ``stream: OK`` albo
   ``stream: <sygnatura> FOUND`` albo ``... ERROR``.

``StreamMaxLength`` clamd (w obrazie clamav 1.4 to 100 MB, ustawienie ``CLAMAV_STREAM_MAX_BYTES``)
ogranicza *cały* strumień, nie pojedynczą porcję. Po przekroczeniu clamd zamyka gniazdo w trakcie
wysyłki – klient widzi wtedy „Broken pipe”, czyli objaw nieodróżnialny od awarii usługi. Dlatego
limit sprawdzamy sami, przed wysłaniem, i zgłaszamy go jako błąd trwały (``ClamAVStreamTooLarge``),
którego nie ma sensu ponawiać.
"""

from __future__ import annotations

import logging
import socket
import struct
from typing import BinaryIO

from django.conf import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT = 60
#: Musi odpowiadać ``StreamMaxLength`` w konfiguracji clamd (obraz clamav 1.4 → 100 MB).
DEFAULT_STREAM_MAX_BYTES = 100 * 1024 * 1024

VERDICT_CLEAN = "CLEAN"
VERDICT_INFECTED = "INFECTED"


class ClamAVError(RuntimeError):
    """Błąd skanowania zgłoszony przez clamd (odpowiedź ERROR albo niezrozumiała)."""


class ClamAVUnavailable(ClamAVError):
    """Nie udało się porozmawiać z clamd (brak połączenia, timeout, zerwany socket) – do retry."""


class ClamAVStreamTooLarge(ClamAVError):
    """Plik przekracza ``StreamMaxLength`` clamd. Błąd trwały – ponowienie da dokładnie to samo."""


def stream_max_bytes() -> int:
    return int(getattr(settings, "CLAMAV_STREAM_MAX_BYTES", DEFAULT_STREAM_MAX_BYTES))


def _read_response(sock: socket.socket) -> str:
    chunks: list[bytes] = []
    while True:
        data = sock.recv(4096)
        if not data:
            break
        chunks.append(data)
        if b"\0" in data or data.endswith(b"\n"):
            break
    return b"".join(chunks).replace(b"\0", b"").decode("utf-8", "replace").strip()


def parse_response(response: str) -> tuple[str, str]:
    """Zamienia odpowiedź clamd na ``(werdykt, sygnatura)``. Nieznana odpowiedź → ``ClamAVError``."""
    text = (response or "").strip()
    if not text:
        raise ClamAVUnavailable("clamd nie odpowiedział.")
    if text.endswith("ERROR"):
        raise ClamAVError(f"clamd zwrócił błąd: {text}")
    if text.endswith("FOUND"):
        # "stream: Eicar-Signature FOUND" → sygnatura to część między dwukropkiem a "FOUND".
        signature = text.rsplit(" ", 1)[0].split(":", 1)[-1].strip()
        return VERDICT_INFECTED, signature
    if text.endswith("OK"):
        return VERDICT_CLEAN, ""
    raise ClamAVError(f"Nieznana odpowiedź clamd: {text}")


def scan_stream(
    fileobj: BinaryIO,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    chunk_size: int = CHUNK_SIZE,
    size: int | None = None,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """Skanuje strumień przez INSTREAM. Zwraca ``("CLEAN"|"INFECTED", sygnatura)``.

    ``size`` to znany z góry rozmiar (``SubmissionFile.size_bytes``) – pozwala odrzucić plik ponad
    ``StreamMaxLength`` bez otwierania gniazda. Licznik w pętli jest zabezpieczeniem na wypadek,
    gdyby deklarowany rozmiar rozjechał się z faktyczną treścią w storage.
    """
    host = host or settings.CLAMAV_HOST
    port = int(port or settings.CLAMAV_PORT)
    limit = int(max_bytes) if max_bytes is not None else stream_max_bytes()
    if size is not None and int(size) > limit:
        raise ClamAVStreamTooLarge(f"Plik ma {int(size)} B, limit strumienia clamd to {limit} B.")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\0")
            sent = 0
            while True:
                chunk = fileobj.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                if sent > limit:
                    raise ClamAVStreamTooLarge(f"Strumień przekroczył limit clamd ({limit} B) po {sent} B.")
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            response = _read_response(sock)
    except OSError as exc:  # obejmuje TimeoutError, ConnectionRefusedError i błędy DNS
        raise ClamAVUnavailable(f"Brak łączności z clamd {host}:{port}: {exc}") from exc
    return parse_response(response)


def ping(host: str | None = None, port: int | None = None, timeout: int = 5) -> bool:
    """Sprawdza żywotność clamd komendą ``zPING``. Używane w smoke testach i healthcheckach."""
    host = host or settings.CLAMAV_HOST
    port = int(port or settings.CLAMAV_PORT)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zPING\0")
            return _read_response(sock).upper().startswith("PONG")
    except OSError as exc:
        logger.warning("clamd niedostępny na %s:%s (%s)", host, port, exc)
        return False
