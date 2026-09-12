#!/usr/bin/env python
"""Składa PDF-y zadań treningowych z ``backend/apps/competitions/fixtures/training/zadania.md``.

Cienka nakładka: cała logika (parser markdowna, rezerwa znakowa DejaVu, skład A4) mieszka
w ``apps.competitions.training_pdf``, żeby testy mogły ją zaimportować zamiast uruchamiać skrypt.
Tutaj zostaje to, co jest właściwe uruchomieniu z konsoli: argumenty i raport z rozmiarów plików.

PDF-y są artefaktem wersjonowanym razem ze źródłem, więc ten skrypt uruchamia się **ręcznie**, po
zmianie ``zadania.md`` – wynik idzie do repozytorium, a ``manage.py seed_training_problems`` tylko
go wgrywa. Dzięki temu ``reportlab`` jest zależnością dev, a obraz produkcyjny nie musi umieć
składać dokumentów.

Uruchomienie (z katalogu repozytorium):

    backend/.venv/Scripts/python.exe scripts/build_training_problem_pdfs.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Skrypt jest uruchamiany wprost (bez ``manage.py``), więc katalog z pakietem ``apps`` trzeba
# wskazać ręcznie – tak samo robi ``scripts/build_school_fixture.py``.
BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from apps.competitions import training_pdf  # noqa: E402 - po dopisaniu backendu do sys.path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", type=Path, default=training_pdf.SOURCE, help="plik markdown z zadaniami")
    parser.add_argument("--out", type=Path, default=training_pdf.TRAINING_DIR, help="katalog docelowy PDF-ów")
    args = parser.parse_args(argv)

    try:
        written = training_pdf.build_all(args.source, args.out)
    except training_pdf.BuildError as exc:
        raise SystemExit(str(exc)) from exc
    for path in written:
        print(f"{path.name}: {path.stat().st_size} B")
    return 0


if __name__ == "__main__":  # pragma: no cover - punkt wejścia skryptu
    sys.exit(main())
