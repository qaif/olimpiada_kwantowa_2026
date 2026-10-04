#!/usr/bin/env python3
"""Cotygodniowy skan obrazów kontenerów spoza repozytorium (SEC-02, docs/OPERACJE.md § 47.3).

Obrazy budowane z naszego kodu (``web``, ``djcms``) skanuje CI przy każdym PR (job ``image-scan``).
Tu chodzi o resztę: Caddy, Postgres, Redis, MinIO, ClamAV, Postfix, LiveKit, Jitsi… – to, co
stoi na produkcji obok aplikacji i czego nikt z nas nie buduje. Podatność w takim obrazie pojawia
się **bez żadnej zmiany w repozytorium** (nowe CVE w starym tagu), więc skan musi chodzić z zegara,
a nie z pushy.

Trzy kroki (workflow ``.github/workflows/security-scan.yml``)::

    third_party_images.py list                 # obrazy z plików compose (domyślne wartości ${VAR:-…})
    third_party_images.py scan --out raport/   # trivy image … --format json, jeden plik na obraz
    third_party_images.py report --out raport/ # Markdown do zgłoszenia (issue) + skrót wyników

Wyłącznie raport: kod wyjścia 0 także przy znalezionych podatnościach. Aktualizacja obrazu to
świadoma zmiana w compose z wpisem w OPERACJE (np. § 19 dla Postgresa) – nie automat.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: Pliki compose z usługami, które mogą stać na produkcji. ``docker-compose.dev.yml`` (Playwright)
#: i ``docker-compose.e2e-djcms.yml`` to narzędzia dewelopera – ich obrazy nie dotykają danych.
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.operator.yml",
    "docker-compose.djcms.yml",
    "deploy/livekit/docker-compose.livekit.yml",
    "deploy/jitsi/docker-compose.jitsi.yml",
)
#: Nasze obrazy – skanowane w CI przy każdej zmianie (job ``image-scan``), tu byłyby powtórzeniem.
OWN_PREFIXES = ("olimpiada/",)
IMAGE_LINE = re.compile(r"^\s*image:\s*[\"']?(?P<ref>[^\"'#\s]+)[\"']?\s*(?:#.*)?$")
#: ``${VAR:-domyślna}`` i ``${VAR-domyślna}`` – zagnieżdżone też (``${A:-x/${B:-y}}``).
VAR_DEFAULT = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-(?P<default>[^${}]*)\}")
SEVERITIES = ("CRITICAL", "HIGH")
DETAIL_LIMIT = 15


def resolve_defaults(ref: str) -> str | None:
    """Podstawia domyślne wartości zmiennych; zmienna bez domyślnej = obrazu nie da się nazwać."""
    previous = None
    while previous != ref:
        previous, ref = ref, VAR_DEFAULT.sub(lambda m: m.group("default"), ref)
    return None if "$" in ref else ref


def list_images(root: Path = ROOT, files=COMPOSE_FILES) -> dict[str, list[str]]:
    """Obraz → pliki compose, w których występuje (kolejność stała: wynik idzie do diffu w issue)."""
    images: dict[str, list[str]] = {}
    for name in files:
        path = root / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not (match := IMAGE_LINE.match(line)):
                continue
            ref = resolve_defaults(match.group("ref"))
            if ref and not ref.startswith(OWN_PREFIXES):
                images.setdefault(ref, [])
                if name not in images[ref]:
                    images[ref].append(name)
    return dict(sorted(images.items()))


def slug(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", ref)


def scan(out: Path, extra: list[str], trivy: str = "trivy") -> None:
    out.mkdir(parents=True, exist_ok=True)
    images = list_images()
    for ref in extra:
        images.setdefault(ref, ["(obraz dodatkowy)"])
    (out / "index.json").write_text(json.dumps(images, indent=2), encoding="utf-8")
    for ref in images:
        target = out / f"{slug(ref)}.json"
        print(f"== {ref}", flush=True)
        # Bez ``--ignore-unfixed``: raport ma pokazać pełny obraz ryzyka, także to, czego dostawca
        # jeszcze nie załatał – kolumna „z poprawką” mówi, co da się zrobić od ręki.
        result = subprocess.run(  # noqa: S603 - lista argumentów, bez powłoki
            [
                trivy,
                "image",
                "--quiet",
                "--scanners",
                "vuln",
                "--severity",
                ",".join(SEVERITIES),
                "--format",
                "json",
                "--output",
                str(target),
                "--timeout",
                "20m",
                ref,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            (out / f"{slug(ref)}.error").write_text(result.stderr[-4000:], encoding="utf-8")
            print(f"   błąd skanu (kod {result.returncode})", flush=True)


def summarize(report: dict) -> dict:
    vulns = []
    for result in report.get("Results", []) or []:
        for vuln in result.get("Vulnerabilities", []) or []:
            if vuln.get("Severity") in SEVERITIES:
                vulns.append(vuln)
    unique = {(v["VulnerabilityID"], v["PkgName"]): v for v in vulns}.values()
    digests = (report.get("Metadata") or {}).get("RepoDigests") or []
    return {
        "digest": digests[0].split("@", 1)[-1] if digests else "?",
        "counts": {s: sum(1 for v in unique if v["Severity"] == s) for s in SEVERITIES},
        "fixable": sorted(
            (v for v in unique if v.get("FixedVersion")),
            key=lambda v: (SEVERITIES.index(v["Severity"]), v["VulnerabilityID"]),
        ),
    }


def report(out: Path) -> tuple[str, str, bool]:
    """Markdown do zgłoszenia, skrót wyników (bez dat – zmiana skrótu = coś się zmieniło)
    i odpowiedź, czy jest o czym mówić (cokolwiek CRITICAL/HIGH albo nieudany skan)."""
    images = json.loads((out / "index.json").read_text(encoding="utf-8"))
    rows, details, fingerprint = [], [], []
    attention = False
    for ref, sources in images.items():
        error = out / f"{slug(ref)}.error"
        data = out / f"{slug(ref)}.json"
        where = ", ".join(f"`{s}`" for s in sources)
        if error.exists() or not data.exists():
            rows.append(f"| `{ref}` | {where} | – | **błąd skanu** | | |")
            fingerprint.append(f"{ref} error")
            attention = True
            continue
        info = summarize(json.loads(data.read_text(encoding="utf-8")))
        c = info["counts"]
        attention = attention or any(c.values())
        fixable = info["fixable"]
        digest = info["digest"][:19]
        rows.append(f"| `{ref}` | {where} | `{digest}` | {c['CRITICAL']} | {c['HIGH']} | {len(fixable)} |")
        fingerprint.append(f"{ref} {info['digest']} " + " ".join(v["VulnerabilityID"] for v in fixable))
        if fixable:
            items = [
                f"- {v['Severity']} {v['VulnerabilityID']} – `{v['PkgName']}` "
                f"{v['InstalledVersion']} → {v['FixedVersion']}"
                for v in fixable[:DETAIL_LIMIT]
            ]
            if len(fixable) > DETAIL_LIMIT:
                rest = len(fixable) - DETAIL_LIMIT
                items.append(f"- … i {rest} kolejnych (pełny JSON w artefakcie przebiegu)")
            details.append(
                f"<details><summary><code>{ref}</code> – z poprawką</summary>\n\n"
                + "\n".join(items)
                + "\n\n</details>"
            )
    body = [
        "Cotygodniowy skan obrazów spoza repozytorium (SEC-02, `docs/OPERACJE.md` § 47.3). "
        "Liczby: unikalne podatności CRITICAL/HIGH; „z poprawką” – dostawca wydał już wersję bez nich "
        "(zwykle nowszy tag obrazu). Raport, nie blokada: decyzja o podbiciu tagu należy do operatora.",
        "",
        "| Obraz | Plik compose | Digest (sha256) | CRITICAL | HIGH | z poprawką |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        *details,
    ]
    digest = hashlib.sha256("\n".join(fingerprint).encode()).hexdigest()[:16]
    return "\n".join(body) + "\n", digest, attention


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("list", "scan", "report"))
    parser.add_argument("--out", type=Path, default=Path("trivy-third-party"))
    parser.add_argument(
        "--extra", action="append", default=[], help="scan: dodatkowy obraz (np. nasz z GHCR)"
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.mode == "list":
        for ref, sources in list_images().items():
            print(f"{ref}\t{', '.join(sources)}")
    elif args.mode == "scan":
        scan(args.out, args.extra)
    else:
        body, digest, attention = report(args.out)
        (args.out / "report.md").write_text(body, encoding="utf-8")
        (args.out / "fingerprint").write_text(digest, encoding="utf-8")
        (args.out / "attention").write_text("yes" if attention else "no", encoding="utf-8")
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
