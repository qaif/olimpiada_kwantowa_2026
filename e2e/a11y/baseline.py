"""Lista naruszeń przyjętych świadomie (``baseline.json``) i raport przebiegu.

Klucz naruszenia to ``<ekran>|<reguła axe>`` – bez selektora węzła. Selektory axe zawierają
``nth-child`` i zmieniają się z danymi (liczba wierszy tabeli, kolejność aktualności), więc odcisk po
węźle dawał „nowe” naruszenia przy każdym przebiegu na innych danych. Odcisk po ekranie i regule
mówi to, co ma mówić: „na tym ekranie ta reguła jest znanym długiem” – a nowa reguła albo ten sam
problem na nowym ekranie przewraca przebieg.

Każdy wpis ma **uzasadnienie** (``reason``) – pusty powód jest błędem przebiegu, bo baseline bez
powodów zamienia się w śmietnik, do którego dopisuje się wszystko, co przeszkadza.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from axe import BLOCKING_IMPACTS

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
ARTIFACTS = HERE.parent / "artifacts" / "a11y"


def load_baseline() -> dict[str, str]:
    if not BASELINE.exists():
        return {}
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    accepted = {}
    for entry in data.get("accepted", []):
        key = f"{entry['page']}|{entry['rule']}"
        reason = entry.get("reason", "").strip()
        # Wpis dopisany automatem (``A11Y_UPDATE_BASELINE=1``) ma powód „TODO…” i nie zwalnia
        # z niczego, dopóki człowiek go nie uzasadni – inaczej aktualizacja baseline byłaby
        # cichym wyłączeniem kontroli.
        if not reason or reason.upper().startswith("TODO"):
            raise ValueError(f"baseline.json: wpis {key} bez uzasadnienia (reason).")
        accepted[key] = entry["reason"]
    return accepted


def blocking(page_id: str, violations: list[dict], accepted: dict[str, str]) -> list[dict]:
    """Naruszenia, które przewracają przebieg: wpływ critical/serious i brak wpisu w baseline."""
    return [
        v for v in violations if v["impact"] in BLOCKING_IMPACTS and f"{page_id}|{v['id']}" not in accepted
    ]


def update_requested() -> bool:
    """``A11Y_UPDATE_BASELINE=1`` – dopisz bieżące naruszenia blokujące do baseline (z TODO w powodzie)."""
    return os.environ.get("A11Y_UPDATE_BASELINE", "0") == "1"


def write_report(results: dict[str, dict]) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "report.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Raport axe (WCAG 2.1 A/AA)", ""]
    for page_id, result in sorted(results.items()):
        violations = result.get("violations", [])
        if not violations:
            lines.append(f"- **{page_id}** – bez naruszeń")
            continue
        lines.append(f"- **{page_id}** ({result.get('path', '')})")
        for v in violations:
            lines.append(f"  - `{v['impact']}` {v['id']} ×{v['count']} – {v['help']}")
            for node in v["nodes"][:3]:
                lines.append(f"    - `{node['target']}`")
    (ARTIFACTS / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_to_baseline(entries: list[tuple[str, str]]) -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"accepted": []}
    known = {f"{e['page']}|{e['rule']}" for e in data["accepted"]}
    for page_id, rule in entries:
        if f"{page_id}|{rule}" not in known:
            data["accepted"].append({"page": page_id, "rule": rule, "reason": "TODO: uzasadnij albo napraw"})
    data["accepted"].sort(key=lambda e: (e["page"], e["rule"]))
    BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
