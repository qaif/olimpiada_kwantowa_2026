"""Odbicia: klasyfikacja, parser zawiadomień o niedoręczeniu (DSN) i skrzynka Maildir relaya (MAIL-02 § 2).

Skąd przychodzą (decyzja w ``docs/tasks/MAIL-02.md`` § 2.1):

- **odmowa relaya w trakcie wysyłki** – ``backends.TrackingSMTPBackend`` dostaje ją od ``smtplib``
  (``SMTPRecipientsRefused``) i woła :func:`classify` z kodem i tekstem odpowiedzi,
- **DSN po przyjęciu listu** – relay doręcza zawiadomienia na ``noreply@<domena>`` agentem
  ``virtual(8)`` do Maildira na wolumenie ``mail_bounces`` (``deploy/mail/docker-init.d/50-bounces.sh``,
  ``MAIL_BOUNCE_TARGET=capture``); :func:`process_maildir` czyta ``new/``, zapisuje odbicia i kasuje
  pliki – zawiadomienie niesie kopię wysłanego listu (z linkiem aktywacyjnym), więc nie leży dłużej,
  niż trzeba go przeczytać.

Twarde = adres albo domena nie istnieje (wstrzymujemy listy nieobowiązkowe, pokazujemy baner).
Miękkie = wszystko inne (skrzynka pełna, odmowa z powodu polityki/spamu, opóźnienie) – liczone,
bez skutków: 5.7.x od Google'a mówi o **naszej** reputacji, nie o adresie odbiorcy.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path

logger = logging.getLogger(__name__)

#: Kod stanu RFC 3463 w tekście odpowiedzi serwera („550-5.1.1 The email account…”).
ENHANCED_STATUS = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
#: Trzycyfrowy kod odpowiedzi SMTP na początku diagnozy.
REPLY_CODE = re.compile(r"^\s*([245])\d\d\b")

#: Kody twarde poza całą klasą 5.1.x: skrzynka wyłączona (Gmail „account … is disabled”) i domena
#: bez MX/A (Postfix „Host or domain name not found”).
HARD_STATUSES = frozenset({"5.2.1", "5.4.4"})
#: Z klasy 5.1.x – kody nadawcy, nie odbiorcy (adres koperty nadawcy zły / nadawca odrzucony).
SENDER_STATUSES = frozenset({"5.1.7", "5.1.8"})
#: Zdania, po których rozpoznajemy nieistniejący adres, gdy serwer nie podał kodu rozszerzonego
#: (albo podał ogólny 5.0.0 / 5.5.0 – Microsoft: „mailbox unavailable”).
HARD_PHRASES = (
    "user unknown",
    "unknown user",
    "no such user",
    "user not found",
    "recipient not found",
    "no such recipient",
    "invalid recipient",
    "does not exist",
    "doesn't exist",
    "mailbox not found",
    "mailbox unavailable",
    "domain not found",
    "host or domain name not found",
    "need fully-qualified address",
)

#: Najdłuższy powód zapisany w bazie (kolumna ``reason``).
REASON_LENGTH = 500
#: Plik większy od tego nie jest DSN-em relaya (Postfix ucina zwracany list do ``bounce_size_limit``
#: = 50 kB) – nie czytamy go do pamięci, tylko odkładamy do ``cur/``.
MAX_FILE_BYTES = 1024 * 1024
#: Ile plików jeden przebieg zadania przetwarza – reszta czeka na następny (co 5 minut).
MAX_FILES_PER_RUN = 500
#: Plik, którego nie dało się przetworzyć, leży w ``cur/`` tyle dni (do wglądu operatora), potem znika.
UNREADABLE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class Bounce:
    email: str
    hard: bool
    status: str
    reason: str


def status_from(text: str) -> str:
    """Pierwszy kod RFC 3463 w tekście (``5.1.1``) albo pusty napis."""
    match = ENHANCED_STATUS.search(text or "")
    return ".".join(match.groups()) if match else ""


def clean_reason(text: str) -> str:
    """Diagnoza bez typu (``smtp;``), w jednej linii, przycięta do kolumny."""
    text = re.sub(r"\s+", " ", text or "").strip()
    head, sep, tail = text.partition(";")
    if sep and head.strip().lower() in {"smtp", "x-postfix", "x-unix"}:
        text = tail.strip()
    return text[:REASON_LENGTH]


def classify(status: str, diagnostic: str, *, failed: bool = True) -> bool:
    """Czy to twarde odbicie. ``failed=False`` (``Action: delayed``, kod 4xx) – zawsze miękkie."""
    if not failed:
        return False
    status = status or status_from(diagnostic)
    text = (diagnostic or "").lower()
    if status:
        if not status.startswith("5."):
            return False
        subject = status.split(".")[1]
        if subject == "7":  # polityka, spam, DMARC – mówi o nadawcy, nie o adresie odbiorcy
            return False
        if subject == "1":
            return status not in SENDER_STATUSES
        if status in HARD_STATUSES:
            return True
        return any(phrase in text for phrase in HARD_PHRASES)
    code = REPLY_CODE.match(diagnostic or "")
    if code is not None and code.group(1) != "5":
        return False
    return any(phrase in text for phrase in HARD_PHRASES)


def _recipient(value: str) -> str:
    """``rfc822; Jan@Example.org`` → ``jan@example.org``."""
    address = (value or "").rpartition(";")[2].strip().strip("<>").strip()
    return address.lower() if "@" in address else ""


def parse_dsn(raw: bytes) -> list[Bounce] | None:
    """Odbicia z jednego zawiadomienia albo ``None``, gdy to nie jest DSN wygenerowany przez MTA.

    DSN rozpoznajemy po dwóch rzeczach naraz: ``multipart/report; report-type=delivery-status``
    (RFC 3464) i **pusty** ``Return-Path`` (RFC 5321 § 4.5.5 – zawiadomienie ma pustego nadawcę
    koperty; ``virtual(8)`` dopisuje nagłówek przy doręczeniu). List wysłany przez aplikację na
    ``noreply@`` (np. ktoś zarejestrował się tym adresem) ma niepusty ``Return-Path`` i jest pomijany –
    treść listu od użytkownika nie może udawać odbicia cudzego adresu.
    """
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    if (message.get("Return-Path") or "").strip() != "<>":
        return None
    if message.get_content_type() != "multipart/report":
        return None
    if (message.get_param("report-type") or "").lower() != "delivery-status":
        return None
    bounces: list[Bounce] = []
    for part in message.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        blocks = part.get_payload()
        if not isinstance(blocks, list):
            continue
        # Pierwszy blok opisuje list (Reporting-MTA, Arrival-Date), kolejne – po jednym odbiorcy.
        for block in blocks[1:]:
            email = _recipient(str(block.get("Final-Recipient") or block.get("Original-Recipient") or ""))
            if not email:
                continue
            action = str(block.get("Action") or "").strip().lower()
            if action not in {"failed", "delayed"}:
                continue  # delivered / relayed / expanded – to nie jest odbicie
            diagnostic = clean_reason(str(block.get("Diagnostic-Code") or ""))
            status = status_from(str(block.get("Status") or "")) or status_from(diagnostic)
            bounces.append(
                Bounce(
                    email=email,
                    hard=classify(status, diagnostic, failed=action == "failed"),
                    status=status,
                    reason=diagnostic or status,
                )
            )
    return bounces


def process_maildir(path: str | Path, *, record, now: float | None = None) -> dict[str, int]:
    """Jeden przebieg skrzynki: ``new/`` → zapis odbić przez ``record(bounce)`` → skasowanie pliku.

    ``record`` wstrzykiwane (w zadaniu – ``services.record_bounce``), żeby parser i obsługa plików
    dały się sprawdzić bez bazy. Plik, przy którym coś się wywróciło, przechodzi do ``cur/`` (zostaje
    do wglądu, nie wraca w następnym przebiegu) i znika po :data:`UNREADABLE_RETENTION_DAYS` dniach.
    """
    stats = {"files": 0, "hard": 0, "soft": 0, "ignored": 0, "errors": 0}
    root = Path(path)
    new, cur = root / "new", root / "cur"
    if not new.is_dir():
        return stats
    for item in sorted(new.iterdir())[:MAX_FILES_PER_RUN]:
        if not item.is_file():
            continue
        stats["files"] += 1
        try:
            if item.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("plik większy niż zawiadomienie relaya")
            bounces = parse_dsn(item.read_bytes())
            if bounces is None:
                stats["ignored"] += 1
            else:
                for bounce in bounces:
                    record(bounce)
                    stats["hard" if bounce.hard else "soft"] += 1
            item.unlink()
        except Exception:  # noqa: BLE001 - jeden zły plik nie może zatrzymać skrzynki
            stats["errors"] += 1
            logger.exception("Nie udało się przetworzyć zawiadomienia o niedoręczeniu %s", item.name)
            try:
                cur.mkdir(exist_ok=True)
                item.rename(cur / (item.name + ":2,"))
            except OSError:
                logger.exception("Nie udało się odłożyć pliku %s do cur/", item.name)
    _purge_unreadable(cur, now=now)
    return stats


def _purge_unreadable(cur: Path, *, now: float | None = None) -> None:
    if not cur.is_dir():
        return
    limit = (now if now is not None else time.time()) - UNREADABLE_RETENTION_DAYS * 86400
    for item in cur.iterdir():
        try:
            if item.is_file() and item.stat().st_mtime < limit:
                item.unlink()
        except OSError:
            logger.exception("Nie udało się skasować starego pliku %s", item.name)
