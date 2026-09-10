#!/usr/bin/env python
"""Generator słownika szkół ponadpodstawowych z wykazu SIO (dane.gov.pl).

Wejściem jest arkusz „Wykaz szkół i placówek oświatowych” pobrany z dane.gov.pl (zbiór 839),
wyjściem – kompaktowy JSON wgrywany do bazy komendą ``manage.py seed_schools``:

    backend/.venv/Scripts/python.exe scripts/build_school_fixture.py Wykaz_szkol.xlsx

Skrypt uruchamia się ręcznie, raz na rok szkolny (README § „Słownik szkół (SIO/RSPO)”), i jest
wyłącznie **interfejsem wiersza poleceń**: cała robota – reguła doboru wierszy, mapa typów szkół
i format zapisu – siedzi w ``apps.schools.sio``. Tam też jest testowana; gdyby ta logika mieszkała
w skrypcie spoza katalogu ``backend/``, nie widziałby jej ani pytest w kontenerze, ani ruff.

Django nie jest potrzebne: ``apps.schools.sio`` importuje tylko ``openpyxl`` (zależność Wagtaila,
więc jest w każdym środowisku projektu) i czysto tekstowy ``apps.core.text``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from apps.schools.sio import DEFAULT_SHEET, read_schools, write_fixture  # noqa: E402

#: Domyślne wyjście: fixture aplikacji ``apps.schools`` (plik trafia do repozytorium).
DEFAULT_OUTPUT = REPO_ROOT / "backend" / "apps" / "schools" / "fixtures" / "szkoly-srednie-sio-2025.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buduje fixture słownika szkół z wykazu SIO (.xlsx).")
    parser.add_argument("xlsx", type=Path, help="Wykaz szkół i placówek oświatowych (.xlsx) z dane.gov.pl")
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help=f"domyślnie {DEFAULT_OUTPUT}"
    )
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help=f"arkusz (domyślnie {DEFAULT_SHEET})")
    args = parser.parse_args(argv)

    if not args.xlsx.is_file():
        raise SystemExit(f"Nie ma pliku: {args.xlsx}")
    count = write_fixture(read_schools(args.xlsx, args.sheet), args.output)
    print(f"{args.output}: {count} szkół")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
