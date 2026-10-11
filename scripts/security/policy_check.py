#!/usr/bin/env python3
"""Reguły łańcucha dostaw, których nie pilnuje żadne z narzędzi (SEC-02, docs/OPERACJE.md § 47.5).

1. **Akcje GitHuba przypięte pełnym SHA commita.** Tag (``@v4``) to wskaźnik, który właściciel
   repozytorium akcji może przestawić – i w marcu 2026 r. napastnik zrobił dokładnie to z tagami
   ``aquasecurity/trivy-action``: przebiegi CI „tej samej wersji” wykonały cudzy kod z dostępem do
   sekretów. SHA się nie przestawi. Wersja zostaje w komentarzu (``# v4.4.0``), a podbija ją
   Dependabot (``.github/dependabot.yml``, ekosystem ``github-actions``).
2. **Wyjątki Trivy z terminem i uzasadnieniem.** Trivy honoruje ``expired_at``, ale nie wymaga go
   ani ``statement``; tu brak któregoś (albo termin dalszy niż 180 dni) wywraca job.

Wyłącznie biblioteka standardowa – plik ``.security/trivyignore.yaml`` czytamy prostym parserem
wierszowym (podzbiór YAML-a opisany w nagłówku tego pliku), żeby nie instalować PyYAML-a.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*[\"']?(?P<ref>[^\"'\s#]+)")
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@]+)?@[0-9a-f]{40}$")
MAX_DAYS = 180


def check_workflows(root: Path = ROOT) -> list[str]:
    problems = []
    files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    files += sorted((root / ".github" / "actions").rglob("action.y*ml"))
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not (match := USES.match(line)):
                continue
            ref = match.group("ref")
            if ref.startswith("./") or re.match(r"^docker://.+@sha256:[0-9a-f]{64}$", ref):
                continue
            if not PINNED.match(ref):
                rel = path.relative_to(root).as_posix()
                problems.append(
                    f"{rel}:{number}: `{ref}` – przypnij pełnym SHA commita (# vX.Y.Z w komentarzu)"
                )
    return problems


def parse_trivyignore(text: str) -> list[dict]:
    """Wpisy ``- id: …`` z kluczami w kolejnych wierszach (aż do następnego wpisu albo sekcji)."""
    entries: list[dict] = []
    for line in text.splitlines():
        stripped = line.split(" #", 1)[0].rstrip() if not line.lstrip().startswith("#") else ""
        if not stripped:
            continue
        if re.match(r"^[A-Za-z_]+:", stripped):  # nowa sekcja najwyższego poziomu
            continue
        if match := re.match(r"^\s*-\s+id:\s*[\"']?([^\"']+)[\"']?$", stripped):
            entries.append({"id": match.group(1).strip()})
        elif entries and (match := re.match(r"^\s+(statement|expired_at):\s*(.*)$", stripped)):
            entries[-1][match.group(1)] = match.group(2).strip().strip("\"'")
    return entries


def check_trivyignore(path: Path, today: dt.date, max_days: int = MAX_DAYS) -> list[str]:
    if not path.exists():
        return []
    problems = []
    for entry in parse_trivyignore(path.read_text(encoding="utf-8")):
        where = f"{path.name} {entry['id']}"
        if len(entry.get("statement", "")) < 20:
            problems.append(f"{where}: brak uzasadnienia (`statement`, co najmniej jedno zdanie)")
        try:
            expires = dt.date.fromisoformat(entry.get("expired_at", "")[:10])
        except ValueError:
            problems.append(f"{where}: brak albo zły `expired_at` (RRRR-MM-DD)")
            continue
        if expires < today:
            problems.append(
                f"{where}: wyjątek wygasł {expires.isoformat()} – przejrzyj ryzyko albo usuń wpis"
            )
        elif expires > today + dt.timedelta(days=max_days):
            problems.append(f"{where}: termin {expires.isoformat()} dalej niż {max_days} dni od dziś")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    problems = check_workflows() + check_trivyignore(ROOT / ".security" / "trivyignore.yaml", args.today)
    for problem in problems:
        print(f"::error title=policy_check::{problem}")
    print(f"{len(problems)} problemów" if problems else "OK – akcje przypięte SHA, wyjątki Trivy z terminem")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
