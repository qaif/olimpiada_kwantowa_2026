"""Strażnik „bomby ZIP” przed otwarciem pliku XLSX przez openpyxl (audyt 10.10.2026, S14).

XLSX to archiwum ZIP z plikami XML. Limit rozmiaru wgranego pliku liczy bajty **skompresowane**,
a openpyxl – nawet w trybie ``read_only`` – rozpakowuje do pamięci cały ``sharedStrings.xml``
i czyta arkusz strumieniem po rozpakowaniu. Deflate potrafi ścisnąć powtarzalny XML w stosunku
około 1000 : 1, więc dwumegabajtowy plik z otwartego importu opiekuna to ~2 GB XML-a w procesie
``web`` (``mem_limit: 2g``, kilka procesów) – OOM, powtarzalnie i bez logowania się jako ktokolwiek
ważny.

Dlatego **przed** ``load_workbook`` czytamy wyłącznie katalog archiwum (``ZipFile.infolist``), który
podaje deklarowany rozmiar każdego pliku po rozpakowaniu, i odmawiamy, gdy:

- suma rozmiarów po rozpakowaniu przekracza ``max_uncompressed`` – lista klasowa z pięciuset
  wierszami to kilkaset kilobajtów XML-a, a 20 MB zostawia zapas na formatowanie i style,
- stosunek „po rozpakowaniu / skompresowane” całego archiwum przekracza ``max_ratio`` – zwykły
  arkusz ściska się kilka–kilkanaście razy, sto razy to już plik spreparowany.

Deklaracji w katalogu nie trzeba wierzyć na słowo: ``zipfile`` (którego używa openpyxl) obcina
rozpakowywaną treść do zadeklarowanego ``file_size`` i sprawdza CRC na końcu, więc plik, który
kłamie (deklaruje mniej, niż zawiera), kończy się błędem ``Bad CRC-32``, a nie urośnie ponad to, co
zadeklarował. Strażnik pilnuje deklaracji, a ``zipfile`` – żeby treść jej nie przekroczyła.

Stosunek kompresji sprawdzamy dopiero powyżej ``RATIO_FLOOR``: mały arkusz z powtarzalnym XML-em
(pięćset pustych wierszy z tym samym stylem) potrafi ścisnąć się mocno, a pamięci i tak nie
zagraża – odmowa byłaby tam fałszywym alarmem.

Moduł świadomie **nie importuje Django**: używa go też ``apps.schools.sio`` (narzędzie operatora
do wykazu szkół), które Django nie zna. Wołający zamienia ``XlsxBombError`` na własną odmowę.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

#: Domyślna granica sumy rozmiarów po rozpakowaniu – dla plików od użytkowników (import uczniów).
DEFAULT_MAX_UNCOMPRESSED = 20 * 1024 * 1024

#: Domyślny najwyższy dopuszczalny stopień kompresji całego archiwum.
DEFAULT_MAX_RATIO = 100

#: Poniżej tej sumy po rozpakowaniu stopnia kompresji nie sprawdzamy (docstring modułu).
RATIO_FLOOR = 1024 * 1024


class XlsxBombError(ValueError):
    """Archiwum odrzucone przed otwarciem – za duże po rozpakowaniu albo podejrzanie ściśnięte."""


def check_xlsx_bomb(
    source: bytes | str | Path,
    *,
    max_uncompressed: int = DEFAULT_MAX_UNCOMPRESSED,
    max_ratio: float = DEFAULT_MAX_RATIO,
) -> None:
    """Podnosi ``XlsxBombError``, gdy archiwum XLSX jest bombą. Plik zepsuty też jest odmową.

    ``source`` to treść pliku (``bytes``) albo ścieżka. Czytany jest wyłącznie katalog archiwum –
    koszt nie zależy od tego, ile plik deklaruje po rozpakowaniu.
    """
    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    try:
        with zipfile.ZipFile(handle) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise XlsxBombError("Plik nie jest poprawnym archiwum XLSX.") from exc
    total = sum(entry.file_size for entry in entries)
    compressed = sum(entry.compress_size for entry in entries)
    if total > max_uncompressed:
        raise XlsxBombError(
            f"Arkusz po rozpakowaniu ma {total} B, a dopuszczamy najwyżej {max_uncompressed} B."
        )
    # ``max(…, 1)``: archiwum z pustymi plikami nie dzieli przez zero.
    if total > RATIO_FLOOR and total / max(compressed, 1) > max_ratio:
        raise XlsxBombError(f"Arkusz jest ściśnięty ponad {max_ratio}-krotnie – to nie jest zwykły arkusz.")
