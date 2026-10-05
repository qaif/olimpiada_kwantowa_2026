#!/usr/bin/env python3
"""Bramka CI na wynik ``pip-audit`` (SEC-02, docs/OPERACJE.md § 47.1).

Sam ``pip-audit`` umie tylko „jest podatność → kod 1” i ``--ignore-vuln ID`` bez daty ani powodu.
Tu potrzebujemy trzech rzeczy, których on nie ma:

1. **Czerwono tylko wtedy, gdy jest co zrobić** – podatność z wydaną poprawką (``fix_versions``)
   blokuje; podatność bez poprawki zostaje ostrzeżeniem w podsumowaniu joba. Inaczej CI stałby
   czerwony tygodniami za coś, czego nikt w repozytorium nie może naprawić, i przestałby cokolwiek
   znaczyć.
2. **Wyjątek ma termin i uzasadnienie** – ``.security/pip-audit-ignore.toml``. Wpis bez powodu,
   z przeszłą datą albo z datą dalszą niż ``--max-days`` wywraca job: zaakceptowane ryzyko ma
   wracać do przeglądu, a nie wisieć w pliku na zawsze.
3. **Wpis, który niczego już nie dotyczy, jest widoczny** – ostrzeżenie „do usunięcia”, żeby lista
   wyjątków nie rosła o martwe pozycje po aktualizacji pakietu.

Wyłącznie biblioteka standardowa (``tomllib``) – skrypt biegnie przed instalacją czegokolwiek.

Użycie (CI, job ``pip-audit``)::

    pip-audit -r req.txt --no-deps --disable-pip -f json -o audit.json || true
    python3 scripts/security/pip_audit_gate.py --label backend audit.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOWLIST = Path(__file__).resolve().parents[2] / ".security" / "pip-audit-ignore.toml"
#: Najdalszy dopuszczalny termin wyjątku. Pół roku: dość, żeby przeczekać wydanie zależności,
#: za mało, żeby wpis przeżył kolejną edycję olimpiady bez ponownego spojrzenia.
DEFAULT_MAX_DAYS = 180
#: Uzasadnienie krótsze niż zdanie to zwykle „TODO” albo „fałszywy alarm” – czyli brak uzasadnienia.
MIN_REASON = 20


def _norm(name: str) -> str:
    """Nazwa pakietu wg PEP 503 – ``Django``, ``django`` i ``django_cms`` mają się zgadzać."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Waiver:
    id: str
    package: str
    reason: str
    expires: dt.date

    def matches(self, package: str, ids: set[str]) -> bool:
        return _norm(self.package) == _norm(package) and self.id in ids


@dataclass
class Verdict:
    blocking: list[str] = field(default_factory=list)
    no_fix: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_allowlist(path: Path, today: dt.date, max_days: int) -> tuple[list[Waiver], list[str]]:
    """Wczytuje wyjątki i zwraca (ważne wpisy, błędy). Błąd w pliku = czerwony job, nie cisza."""
    if not path.exists():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return [], [f"{path.name}: niepoprawny TOML ({exc})"]
    entries, errors = [], []
    for n, raw in enumerate(data.get("ignore", []), start=1):
        where = f"{path.name} wpis {n} ({raw.get('id', '?')})"
        missing = [key for key in ("id", "package", "reason", "expires") if not raw.get(key)]
        if missing:
            errors.append(f"{where}: brak pól {', '.join(missing)}")
            continue
        expires = raw["expires"]
        if isinstance(expires, dt.datetime):
            expires = expires.date()
        if not isinstance(expires, dt.date):
            errors.append(f"{where}: `expires` ma być datą TOML (RRRR-MM-DD), bez cudzysłowów")
            continue
        if len(str(raw["reason"]).strip()) < MIN_REASON:
            errors.append(f"{where}: uzasadnienie krótsze niż {MIN_REASON} znaków")
            continue
        if expires < today:
            errors.append(f"{where}: wyjątek wygasł {expires.isoformat()} – przejrzyj ryzyko ponownie")
            continue
        if expires > today + dt.timedelta(days=max_days):
            errors.append(f"{where}: termin {expires.isoformat()} dalej niż {max_days} dni od dziś")
            continue
        entries.append(Waiver(str(raw["id"]), str(raw["package"]), str(raw["reason"]), expires))
    return entries, errors


def evaluate(report: dict, allowlist: list[Waiver]) -> Verdict:
    verdict = Verdict()
    used: set[Waiver] = set()
    for dep in report.get("dependencies", []):
        name, version = dep.get("name", "?"), dep.get("version", "?")
        if dep.get("skip_reason"):
            verdict.skipped.append(f"{name}: {dep['skip_reason']}")
            continue
        for vuln in dep.get("vulns", []):
            ids = {vuln["id"], *vuln.get("aliases", [])}
            fixes = vuln.get("fix_versions") or []
            label = f"{name}=={version} {vuln['id']}"
            if aliases := sorted(set(vuln.get("aliases", [])) - {vuln["id"]}):
                label += f" ({', '.join(aliases)})"
            hit = next((entry for entry in allowlist if entry.matches(name, ids)), None)
            if hit:
                used.add(hit)
                verdict.accepted.append(f"{label} – do {hit.expires.isoformat()}: {hit.reason}")
            elif fixes:
                verdict.blocking.append(f"{label} → poprawka w {', '.join(fixes)}")
            else:
                verdict.no_fix.append(f"{label} – brak wydanej poprawki")
    verdict.stale = [
        f"{entry.package} {entry.id} (do {entry.expires.isoformat()})"
        for entry in allowlist
        if entry not in used
    ]
    return verdict


def render(label: str, verdict: Verdict) -> str:
    lines = [f"### pip-audit: {label}", ""]
    sections = (
        ("Błędy listy wyjątków", verdict.errors),
        ("Blokujące (jest poprawka – podnieś wersję albo dopisz wyjątek)", verdict.blocking),
        ("Bez poprawki (ostrzeżenie, nie blokuje)", verdict.no_fix),
        ("Zaakceptowane wyjątki", verdict.accepted),
        ("Wyjątki, które niczego nie dotyczą (usuń je)", verdict.stale),
        ("Pominięte przez pip-audit", verdict.skipped),
    )
    for title, items in sections:
        if items:
            lines += [f"**{title}:**", *[f"- {item}" for item in items], ""]
    if not any(items for _title, items in sections):
        lines.append("Brak znanych podatności.")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="wynik `pip-audit -f json`")
    parser.add_argument("--label", default="zależności", help="nazwa zbioru w podsumowaniu")
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)

    allowlist, errors = load_allowlist(args.allowlist, args.today, args.max_days)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Brak raportu to awaria narzędzia (sieć, zła blokada), a nie „zero podatności”.
        print(f"Nie da się odczytać raportu pip-audit {args.report}: {exc}", file=sys.stderr)
        return 2
    verdict = evaluate(report, allowlist)
    verdict.errors = errors

    text = render(args.label, verdict)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    for item in verdict.no_fix + verdict.stale:
        print(f"::warning title=pip-audit ({args.label})::{item}")
    for item in verdict.errors + verdict.blocking:
        print(f"::error title=pip-audit ({args.label})::{item}")
    return 1 if verdict.blocking or verdict.errors else 0


if __name__ == "__main__":
    sys.exit(main())
