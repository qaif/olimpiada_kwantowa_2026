"""Odbicia: klasyfikacja, parser zawiadomień o niedoręczeniu (DSN) i skrzynka Maildir relaya (MAIL-02 § 2).

Skąd przychodzą (decyzja w ``docs/tasks/MAIL-02.md`` § 2.1):

- **odmowa relaya w trakcie wysyłki** – ``backends.TrackingSMTPBackend`` dostaje ją od ``smtplib``
  (``SMTPRecipientsRefused``) i woła :func:`classify` z kodem i tekstem odpowiedzi,
- **DSN po przyjęciu listu** – relay doręcza zawiadomienia na ``noreply@<domena>`` agentem
  ``virtual(8)`` do Maildira na wolumenie ``mail_bounces`` (``deploy/mail/docker-init.d/50-bounces.sh``,
  ``MAIL_BOUNCE_TARGET=capture``); :func:`process_maildir` czyta ``new/``, zapisuje odbicia i kasuje
  pliki – zawiadomienie niesie kopię wysłanego listu (z linkiem aktywacyjnym), więc nie leży dłużej,
  niż trzeba go przeczytać.

Klasyfikacja wyłącznie po **kodzie rozszerzonym** (RFC 3463) z tablicy, bez zgadywania po treści
odpowiedzi (przegląd PR #98, L4): Microsoft odrzuca listy z powodu polityki kodem ``5.0.350`` i treścią
„mailbox unavailable”, a takie odbicie mówi o **naszej** reputacji, nie o adresie odbiorcy.

- twarde (:data:`HARD_STATUSES`) – adres albo domena nie istnieje: wstrzymujemy listy nieobowiązkowe,
  pokazujemy baner,
- miękkie (:func:`recipient_related`: klasa ``x.1.x`` adresu i ``x.2.x`` skrzynki) – liczone, bez skutków,
- pozostałe (polityka/spam ``x.7.x``, system, protokół, sieć, opóźnienie ``4.4.x``) – w ogóle nie są
  przypisywane adresowi: zostaje linia w logu workera z domeną i kodem.
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

#: Kody twarde: adres nie istnieje (5.1.1), domena nie istnieje (5.1.2), zła składnia adresu (5.1.3),
#: adres przeniesiony bez nowego (5.1.6), null MX (5.1.10, RFC 7505), skrzynka wyłączona (5.2.1 –
#: Gmail „account … is disabled”) i domena bez MX/A po stronie relaya (5.4.4 – Postfix „Host or domain
#: name not found”). Nic spoza tej tablicy – także ``5.0.0``, ``5.5.0``, ``5.0.350`` – nie jest twarde.
HARD_STATUSES = frozenset({"5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.1.10", "5.2.1", "5.4.4"})

#: Z klasy adresu – kody nadawcy, nie odbiorcy (adres koperty nadawcy zły / nadawca odrzucony).
SENDER_STATUSES = frozenset({"4.1.7", "4.1.8", "5.1.7", "5.1.8"})

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

    @property
    def recipient_related(self) -> bool:
        return self.hard or recipient_related(self.status)


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


def classify(status: str, diagnostic: str = "", *, failed: bool = True) -> bool:
    """Czy to twarde odbicie – wyłącznie tablica :data:`HARD_STATUSES` (``failed=False`` – nigdy)."""
    if not failed:
        return False
    return (status or status_from(diagnostic)) in HARD_STATUSES


def recipient_related(status: str) -> bool:
    """Czy kod mówi o adresie albo skrzynce odbiorcy (klasa ``x.1.x`` poza nadawcą, ``x.2.x``)."""
    parts = (status or "").split(".")
    if len(parts) != 3 or status in SENDER_STATUSES:
        return False
    return parts[1] in {"1", "2"}


def _recipient(value: str) -> str:
    """``rfc822; Jan@Example.org`` → ``jan@example.org``."""
    address = (value or "").rpartition(";")[2].strip().strip("<>").strip()
    return address.lower() if "@" in address else ""


def _mta(value: str) -> str:
    """``dns; mail.platforma.test`` → ``mail.platforma.test``."""
    return (value or "").rpartition(";")[2].strip().rstrip(".").lower()


def parse_dsn(raw: bytes, *, reporting_mta: str | None = None) -> list[Bounce] | None:
    """Odbicia z jednego zawiadomienia albo ``None``, gdy to nie jest DSN **naszego** relaya.

    Trzy warunki naraz (przegląd PR #98, M2 – sfałszowane zawiadomienie z wnętrza sieci compose):

    - ``multipart/report; report-type=delivery-status`` (RFC 3464) i **pusty** ``Return-Path``
      (RFC 5321 § 4.5.5; ``virtual(8)`` dopisuje go z koperty). Relay odrzuca pusty nadawcę od każdego
      klienta SMTP (``check_sender_access inline:{ <>=REJECT }``), więc pusty ``Return-Path`` ma
      wyłącznie zawiadomienie wygenerowane przez sam relay,
    - ``Reporting-MTA`` = nazwa naszego relaya (``reporting_mta``; ``None`` – bez tego warunku),
    - czytamy wyłącznie części **najwyższego poziomu**: ``message/delivery-status`` schowany
      w załączonym liście pierwotnym (``message/rfc822``) nie jest raportem relaya.
    """
    message = BytesParser(policy=policy.compat32).parsebytes(raw)
    if (message.get("Return-Path") or "").strip() != "<>":
        return None
    if message.get_content_type() != "multipart/report":
        return None
    if (message.get_param("report-type") or "").lower() != "delivery-status":
        return None
    parts = message.get_payload()
    if not isinstance(parts, list):
        return None
    bounces: list[Bounce] = []
    for part in parts:
        if part.get_content_type() != "message/delivery-status":
            continue
        blocks = part.get_payload()
        if not isinstance(blocks, list) or not blocks:
            continue
        # Pierwszy blok opisuje list (Reporting-MTA, Arrival-Date), kolejne – po jednym odbiorcy.
        if (
            reporting_mta
            and _mta(str(blocks[0].get("Reporting-MTA") or "")) != reporting_mta.rstrip(".").lower()
        ):
            return None
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


def process_maildir(
    path: str | Path, *, record, reporting_mta: str | None = None, now: float | None = None
) -> dict[str, int]:
    """Jeden przebieg skrzynki: ``new/`` → zapis odbić przez ``record(bounce)`` → skasowanie pliku.

    ``record`` wstrzykiwane (w zadaniu – ``services.record_bounce``), żeby parser i obsługa plików
    dały się sprawdzić bez bazy. Do ``record`` trafiają wyłącznie odbicia twarde i miękkie dotyczące
    adresu; pozostałe (polityka, system) – linia w logu z domeną i kodem. Plik, przy którym coś się
    wywróciło, przechodzi do ``cur/`` (zostaje do wglądu, nie wraca w następnym przebiegu) i znika po
    :data:`UNREADABLE_RETENTION_DAYS` dniach.
    """
    stats = {"files": 0, "hard": 0, "soft": 0, "other": 0, "ignored": 0, "errors": 0}
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
            bounces = parse_dsn(item.read_bytes(), reporting_mta=reporting_mta)
            if bounces is None:
                stats["ignored"] += 1
            else:
                for bounce in bounces:
                    if not bounce.recipient_related:
                        stats["other"] += 1
                        logger.info(
                            "Odbicie niedotyczące adresu (%s) w domenie %s – bez zapisu.",
                            bounce.status or "bez kodu",
                            bounce.email.rpartition("@")[2],
                        )
                        continue
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
