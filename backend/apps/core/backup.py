"""Ślad po kopiach zapasowych widziany od strony aplikacji.

Same kopie robi host (``scripts/backup.sh`` z crona) i aplikacja nie ma jak ich sprawdzić: nie
widzi ani katalogu ``/opt/olimpiada-backups``, ani kubełka u dostawcy zewnętrznego – kontener
``web`` jest ``read_only`` i siedzi w sieci bez wyjścia na świat. To jest podział zamierzony:
proces WWW, który umiałby czytać kopie zapasowe, umiałby je też skasować po przejęciu.

Zostaje więc droga odwrotna: skrypty **meldują** wynik przez ``manage.py record_backup_status``,
a aplikacja pamięta wyłącznie datę ostatniego meldunku. Dzięki temu ``/status.json`` i watchdog
alertów (``apps.core.alerts``) odpowiadają na jedyne pytanie, jakie da się tu uczciwie zadać:
„czy kopia w ogóle powstała i czy ktoś sprawdził, że da się ją odtworzyć”.

Dwa znaczniki, nie jeden, bo to dwa różne fakty i mylenie ich jest klasyczną pułapką:

- ``backup:last_ok_at`` – kopia **powstała** (pg_dump + lustro kubełków + wysyłka). Mówi, że
  proces działa,
- ``backup:last_verified_at`` – kopię **odtworzono** do tymczasowego Postgresa i policzono w niej
  wiersze (``scripts/backup_verify.sh``). Mówi, że kopia jest coś warta. Kopia, której nikt nigdy
  nie odtworzył, jest hipotezą, a nie kopią.

Trzeci znacznik, ``backup:last_offsite_at``, jest doprecyzowaniem pierwszego: kopia powstała
**i wyjechała poza serwer** (S3 albo Dysk Google), a suma kontrolna po tamtej stronie się zgadza.
Instalacja bez skonfigurowanej kopii zdalnej melduje samo ``--ok`` i tego znacznika nie ma wcale –
to jest stan „kopia wyłącznie lokalna”, widoczny w ``/status.json`` jako ``backup_offsite: false``.

Stan trzymamy w cache'u, a nie w bazie, i to jest świadome: w scenariuszu, w którym te znaczniki
są naprawdę potrzebne (baza padła), wiersz w bazie byłby nieczytelny razem z nią. Cena jest znana
i wpisana w progi niżej: wyczyszczenie Redisa kasuje znaczniki, a stan „nie wiem” jest wtedy
traktowany tak samo jak „dawno”. To jest właściwa strona ostrożności – „nie wiem, czy mamy kopię”
wymaga reakcji człowieka dokładnie tak samo jak „kopii nie ma”.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Klucze meldunków. Nazwy są częścią umowy ze skryptami (docs/OPERACJE.md) i z watchdogiem alertów.
LAST_OK_KEY = "backup:last_ok_at"
LAST_VERIFIED_KEY = "backup:last_verified_at"
LAST_NOTE_KEY = "backup:last_note"
LAST_OFFSITE_KEY = "backup:last_offsite_at"

#: Ile żyje meldunek. Sto dwadzieścia dni, czyli znacznie dłużej niż którykolwiek próg niżej:
#: wpis ma **przeżyć** moment, w którym staje się nieświeży, bo inaczej „kopia sprzed pół roku”
#: i „kopia, której nigdy nie było” byłyby w cache'u nie do odróżnienia, a różnią się wszystkim.
STATE_TTL_SECONDS = 120 * 24 * 3600

#: Po ilu godzinach brak nowej kopii jest awarią. Kopia jest nocna, więc 36 godzin znaczy „jedna
#: przespana noc”. Dwie doby byłyby wygodniejsze dla dyżurnego i gorsze dla olimpiady: awaria crona
#: wykryta po 36 godzinach kosztuje jeden dzień prac uczestników, wykryta po trzech dniach – trzy.
MAX_BACKUP_AGE_HOURS = 36

#: Po ilu dniach brak testu odtwarzania jest awarią. Test jest cotygodniowy; dziesięć dni zostawia
#: zapas na jeden przestawiony termin i nie zamienia alarmu w cotygodniowy szum.
MAX_VERIFY_AGE_DAYS = 10


@dataclass(frozen=True)
class BackupState:
    """Stan kopii zapasowych „na teraz”. Dwa fakty i dwa werdykty policzone z tych samych progów."""

    last_ok: datetime | None
    last_verified: datetime | None
    note: str = ""
    last_offsite: datetime | None = None

    @property
    def backup_fresh(self) -> bool:
        """Czy ostatnia udana kopia mieści się w progu. Brak meldunku = nie."""
        return _within(self.last_ok, timedelta(hours=MAX_BACKUP_AGE_HOURS))

    @property
    def verify_fresh(self) -> bool:
        """Czy ostatni **udany test odtwarzania** mieści się w progu. Brak meldunku = nie."""
        return _within(self.last_verified, timedelta(days=MAX_VERIFY_AGE_DAYS))

    @property
    def offsite_fresh(self) -> bool:
        """Czy ostatnia kopia, która **wyjechała poza serwer**, mieści się w progu kopii (36 h).

        Próg ten sam, co dla ``backup_fresh``: kopia poza serwerem jest tą samą kopią nocną, tylko
        potwierdzoną po drugiej stronie. Brak meldunku = nie.
        """
        return _within(self.last_offsite, timedelta(hours=MAX_BACKUP_AGE_HOURS))

    @property
    def offsite_lost(self) -> bool:
        """Kopia poza serwerem **kiedyś działała**, a teraz nie jest świeża.

        To jest powód do alarmu osobnego od „brak kopii”: nocna kopia może się udawać (``--ok``)
        i jednocześnie przestać wyjeżdżać z serwera – np. ktoś wykomentował konfigurację albo
        skasował token. Instalacja, która nigdy kopii zdalnej nie miała, nie dostaje tego alarmu
        co godzinę; jej stan widać w ``/status.json`` (``backup_offsite: false``).
        """
        return self.last_offsite is not None and not self.offsite_fresh


def _within(moment: datetime | None, window: timedelta) -> bool:
    return moment is not None and (timezone.now() - moment) <= window


def _read(key: str) -> datetime | None:
    """Znacznik z cache'u jako ``datetime`` ze strefą. Śmieci i brak wpisu dają ``None``."""
    try:
        raw = cache.get(key)
    except Exception:  # noqa: BLE001 - brak cache'u nie może wywrócić strony statusu
        logger.warning("Kopie zapasowe: nie udało się odczytać %s.", key, exc_info=True)
        return None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:  # pragma: no cover - wpis zapisuje wyłącznie nasza komenda
        return None
    # Znacznik zapisujemy w ISO ze strefą; wartość bez strefy mogłaby przyjść wyłącznie z ręcznego
    # zapisu do Redisa i lepiej ją domknąć, niż pozwolić na porównanie naive z aware (TypeError).
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def record(
    *,
    ok: bool = False,
    verified: bool = False,
    offsite: bool = False,
    note: str = "",
    at: datetime | None = None,
) -> None:
    """Zapisuje meldunek skryptu kopii zapasowych.

    ``ok`` i ``verified`` są rozłączne w praktyce (melduje je inny skrypt), ale nie wykluczają się
    w podpisie: udany test odtwarzania **nie** jest dowodem na to, że dzisiejsza kopia powstała,
    i odwrotnie. Obu znaczników nie wolno więc odświeżać jednym zdarzeniem.

    ``offsite`` znaczy „ta kopia jest też poza serwerem, sprawdzona sumą kontrolną” i ma sens
    wyłącznie razem z ``ok`` – kopia zdalna bez kopii nie istnieje.
    """
    if offsite and not ok:
        raise ValueError("offsite=True wymaga ok=True: kopia poza serwerem jest tą samą kopią nocną")
    moment = (at or timezone.now()).isoformat()
    if ok:
        cache.set(LAST_OK_KEY, moment, STATE_TTL_SECONDS)
    if verified:
        cache.set(LAST_VERIFIED_KEY, moment, STATE_TTL_SECONDS)
    if offsite:
        cache.set(LAST_OFFSITE_KEY, moment, STATE_TTL_SECONDS)
    if note:
        cache.set(LAST_NOTE_KEY, note[:500], STATE_TTL_SECONDS)


def state() -> BackupState:
    """Odczyt obu znaczników jednym wywołaniem – dla ``/status.json`` i dla watchdoga alertów."""
    try:
        note = cache.get(LAST_NOTE_KEY) or ""
    except Exception:  # noqa: BLE001 - jak wyżej
        note = ""
    return BackupState(
        last_ok=_read(LAST_OK_KEY),
        last_verified=_read(LAST_VERIFIED_KEY),
        note=str(note),
        last_offsite=_read(LAST_OFFSITE_KEY),
    )
