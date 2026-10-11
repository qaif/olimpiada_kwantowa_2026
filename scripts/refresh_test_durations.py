#!/usr/bin/env python3
"""Nowe ``backend/.test_durations`` z czasów zmierzonych w CI (docs/TESTY.md § 5).

Każdy shard joba ``tests`` (``.github/workflows/ci.yml``) zapisuje czasy swoich testów
(``backend/_durations_plugin.py``) i wystawia je jako artefakt ``test-durations-<shard>``. Ten skrypt
pobiera artefakty jednego przebiegu i składa z nich plik, według którego pytest-split dzieli testy
na shardy. Pomiar z CI, a nie ze stacji deweloperskiej, bo proporcje są inne: testy CMS-u
z seedami są na runnerze kilkukrotnie droższe względem reszty niż na 32 rdzeniach (8.10.2026).

Użycie (z korzenia repozytorium; ``gh`` zalogowane):

    python scripts/refresh_test_durations.py 37768175493     # przebieg CI z kompletem shardów
    python scripts/refresh_test_durations.py --dir ŚCIEŻKA   # artefakty pobrane wcześniej

Plik jest zastępowany w całości (testy usunięte znikają). Brak artefaktu któregoś sharda (shard
czerwony, przerwany) przerywa skrypt – połowiczny plik dałby podział gorszy od starego. Biblioteka
standardowa, bez Django.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend" / ".test_durations"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ARTIFACT = re.compile(r"test-durations-(\d+)\.json$")


def expected_shards() -> int:
    """Liczba shardów z ``matrix.shard`` joba ``tests`` – tyle artefaktów musi być."""
    match = re.search(r"^\s+shard:\s*\[([^\]]*)\]", WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise SystemExit(f"Nie znalazłem `matrix.shard` w {WORKFLOW}.")
    return len([part for part in match.group(1).split(",") if part.strip()])


def merge(directory: Path, shards: int) -> dict[str, float]:
    files = {int(m.group(1)): path for path in directory.rglob("*.json") if (m := ARTIFACT.search(path.name))}
    missing = sorted(set(range(1, shards + 1)) - set(files))
    if missing:
        raise SystemExit(f"Brak czasów shardów {missing} – weź przebieg, w którym wszystkie shardy przeszły.")
    merged: dict[str, float] = {}
    for shard in sorted(files):
        durations = json.loads(files[shard].read_text(encoding="utf-8"))
        total = sum(durations.values())
        print(f"shard {shard}: {len(durations):5d} testów, {total:8.0f} s (suma czasów workerów)")
        for nodeid, seconds in durations.items():
            merged[nodeid] = max(seconds, merged.get(nodeid, 0.0))
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "run_id", nargs="?", help="identyfikator przebiegu CI (gh run list --workflow ci.yml)"
    )
    source.add_argument("--dir", type=Path, help="katalog z pobranymi artefaktami test-durations-*")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        directory = args.dir
        if directory is None:
            directory = Path(tmp)
            command = [
                "gh",
                "run",
                "download",
                args.run_id,
                "--pattern",
                "test-durations-*",
                "--dir",
                str(directory),
            ]
            # ``gh`` z PATH, jak każde polecenie operatora w scripts/; identyfikator jako osobny argument.
            subprocess.run(command, check=True)  # noqa: S603
        merged = merge(directory, expected_shards())

    old = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}
    with TARGET.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(sorted(merged.items())), fh, indent=0, ensure_ascii=False)
    print(
        f"{TARGET.relative_to(ROOT).as_posix()}: {len(merged)} testów (było {len(old)}; "
        f"nowych {len(merged.keys() - old.keys())}, usuniętych {len(old.keys() - merged.keys())})."
    )


if __name__ == "__main__":
    sys.exit(main())
