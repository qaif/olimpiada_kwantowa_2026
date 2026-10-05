#!/usr/bin/env python3
"""JavaScript spoza repozytorium: zgodność zapisu z plikami i porównanie z upstreamem (SEC-02).

Rejestr: ``.security/vendor.toml`` (opis pól tamże). Dwa tryby:

``check`` (offline, CI przy każdym PR, job ``supply-chain``)
    - wersja każdej zwendorowanej biblioteki da się odczytać z jej ``VERSION``,
    - **każdy** plik katalogu biblioteki ma skrót zapisany w ``VERSION``/``SHA384``/``SHA256SUMS``
      – podmiana pliku bez aktualizacji zapisu (albo plik dorzucony obok) wywraca job,
    - każdy katalog ``vendor/<nazwa>`` w drzewie jest w rejestrze, a każdy ``<script src="https://…">``
      w szablonach należy do któregoś wpisu ``[[cdn]]`` – nowa biblioteka nie wchodzi bokiem,
    - wersja z CDN jest wpisana wszędzie tak samo (pdf.js stoi w dwóch plikach).

``upstream`` (sieć, raz w miesiącu, ``.github/workflows/vendor-upstream.yml``)
    Najnowsze wydanie w rejestrze npm, znane podatności naszej wersji (OSV), suma paczki npm
    zapisana przy wendorowaniu i SRI plików z CDN. Wynik w Markdownie – do jednego zgłoszenia.
    Tylko raport: niczego nie aktualizuje i nie kończy się błędem z powodu nowszej wersji.

Biblioteka standardowa (``tomllib``, ``urllib``) – bez instalowania czegokolwiek.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / ".security" / "vendor.toml"
#: Pliki opisowe katalogu biblioteki – nie są „treścią”, więc nie muszą mieć własnego skrótu.
META_FILES = {"VERSION", "SHA384", "SHA256SUMS", "LICENSE", "LICENSE.txt", "LICENSE.md", "NOTICE"}
#: Gdzie szukać katalogów ``vendor/`` i odwołań do CDN. Zależności Pythona (``.venv``) pomijamy.
SCAN_ROOTS = ("backend", "djcms", "themes")
SKIP_PARTS = {".venv", "node_modules", "staticfiles", "__pycache__", "dist"}
SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc=[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)
SRI_PAIR = re.compile(
    r"src=[\"'](?P<url>https://[^\"']+)[\"'][^>]*?integrity=[\"'](?P<sri>sha(?:256|384|512)-[A-Za-z0-9+/=]+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
SHA256SUMS_LINE = re.compile(r"^(?P<hash>[0-9a-f]{64}) [ *](?P<path>.+)$")
USER_AGENT = "olimpiada-vendor-check (SEC-02)"


@dataclass
class Finding:
    component: str
    message: str

    def __str__(self) -> str:
        return f"{self.component}: {self.message}"


def load_registry(path: Path = REGISTRY) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _digests(data: bytes) -> set[str]:
    """Wszystkie zapisy skrótu, jakich używamy: sha256 hex (VERSION, SHA256SUMS) i SRI sha384."""
    return {
        hashlib.sha256(data).hexdigest(),
        "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode(),
    }


def _single_version(pattern: str, texts: dict[str, str], component: str) -> tuple[str | None, list[Finding]]:
    found: dict[str, set[str]] = {}
    for where, text in texts.items():
        versions = {m.group("version") for m in re.finditer(pattern, text)}
        if not versions:
            return None, [Finding(component, f"w {where} nie ma wersji (wzorzec {pattern!r})")]
        found[where] = versions
    every = set().union(*found.values())
    if len(every) != 1:
        detail = "; ".join(f"{where}: {', '.join(sorted(v))}" for where, v in found.items())
        return None, [Finding(component, f"różne wersje w różnych miejscach – {detail}")]
    return every.pop(), []


# --- check -----------------------------------------------------------------------------------


def check_vendored(entry: dict, root: Path = ROOT) -> tuple[str | None, list[Finding]]:
    name = entry["name"]
    directory = root / entry["dir"]
    if not directory.is_dir():
        return None, [Finding(name, f"brak katalogu {entry['dir']}")]
    version_file = directory / entry["version_file"]
    if not version_file.is_file():
        return None, [Finding(name, f"brak pliku {entry['version_file']}")]
    version, findings = _single_version(
        entry["version_pattern"], {entry["version_file"]: version_file.read_text(encoding="utf-8")}, name
    )

    recorded = ""
    for hash_file in entry["hash_files"]:
        path = directory / hash_file
        if not path.is_file():
            findings.append(Finding(name, f"brak pliku skrótów {hash_file}"))
            continue
        text = path.read_text(encoding="utf-8")
        recorded += text + "\n"
        # Plik w formacie ``sha256sum`` sprawdzamy także w drugą stronę: wpis bez pliku albo ze
        # starym skrótem to zapis, który przestał opisywać katalog.
        for line in text.splitlines():
            if match := SHA256SUMS_LINE.match(line.strip()):
                target = directory / match.group("path")
                if not target.is_file():
                    findings.append(
                        Finding(name, f"{hash_file} wymienia nieistniejący {match.group('path')}")
                    )
                elif hashlib.sha256(target.read_bytes()).hexdigest() != match.group("hash"):
                    findings.append(Finding(name, f"{hash_file}: skrót {match.group('path')} się nie zgadza"))

    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory).as_posix()
        if relative in META_FILES or relative in entry["hash_files"]:
            continue
        if not _digests(path.read_bytes()) & set(
            re.findall(r"[0-9a-f]{64}|sha384-[A-Za-z0-9+/=]+", recorded)
        ):
            findings.append(
                Finding(name, f"{relative}: skrót pliku nie występuje w {', '.join(entry['hash_files'])}")
            )
    return version, findings


def check_cdn(entry: dict, root: Path = ROOT) -> tuple[str | None, list[Finding]]:
    texts = {}
    for name in entry["files"]:
        path = root / name
        if not path.is_file():
            return None, [Finding(entry["name"], f"brak pliku {name}")]
        texts[name] = path.read_text(encoding="utf-8")
    return _single_version(entry["version_pattern"], texts, entry["name"])


def _scanned_files(root: Path, suffixes: tuple[str, ...]):
    for top in SCAN_ROOTS:
        for path in (root / top).rglob("*"):
            if path.suffix in suffixes and path.is_file() and not SKIP_PARTS & set(path.parts):
                yield path


def check_coverage(registry: dict, root: Path = ROOT) -> list[Finding]:
    """Nic spoza rejestru: katalogi ``vendor/<x>`` i zewnętrzne ``<script src>`` w szablonach."""
    findings = []
    known_dirs = {(root / entry["dir"]).resolve() for entry in registry.get("vendored", [])}
    for top in SCAN_ROOTS:
        for vendor in (root / top).rglob("vendor"):
            if not vendor.is_dir() or SKIP_PARTS & set(vendor.parts):
                continue
            for child in vendor.iterdir():
                if child.is_dir() and child.resolve() not in known_dirs:
                    rel = child.relative_to(root).as_posix()
                    findings.append(Finding(rel, "katalog nie jest opisany w .security/vendor.toml"))
    patterns = [entry["version_pattern"] for entry in registry.get("cdn", [])]
    patterns += [entry["url_pattern"] for entry in registry.get("external", [])]
    for path in _scanned_files(root, (".html",)):
        for url in SCRIPT_SRC.findall(path.read_text(encoding="utf-8", errors="replace")):
            if not any(re.search(pattern, url) for pattern in patterns):
                rel = path.relative_to(root).as_posix()
                findings.append(Finding(rel, f"skrypt z {url} nie jest opisany w .security/vendor.toml"))
    return findings


def run_check(registry: dict, root: Path = ROOT) -> tuple[list[str], list[Finding]]:
    lines, findings = [], []
    for entry in registry.get("vendored", []):
        version, problems = check_vendored(entry, root)
        lines.append(f"zwendorowany {entry['name']} {version or '?'}")
        findings += problems
    for entry in registry.get("cdn", []):
        version, problems = check_cdn(entry, root)
        lines.append(f"CDN {entry['name']} {version or '?'}")
        findings += problems
    findings += check_coverage(registry, root)
    return lines, findings


# --- upstream --------------------------------------------------------------------------------


def _get(url: str, *, data: bytes | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - adresy stałe, https
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def npm_manifest(package: str, version: str) -> dict:
    return json.loads(_get(f"https://registry.npmjs.org/{urllib.parse.quote(package, safe='@')}/{version}"))


def osv_vulns(package: str, version: str) -> list[str]:
    body = json.dumps({"package": {"name": package, "ecosystem": "npm"}, "version": version}).encode()
    vulns = json.loads(_get("https://api.osv.dev/v1/query", data=body)).get("vulns", [])
    return sorted({v["id"] for v in vulns})


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def upstream_row(entry: dict, version: str | None, recorded_integrity: str | None, sri_urls) -> dict:
    row = {"name": entry["name"], "npm": entry["npm"], "ours": version or "?", "notes": []}
    try:
        latest = npm_manifest(entry["npm"], "latest")["version"]
        row["latest"] = latest
        if version and latest != version:
            kind = "nowa wersja główna" if _major(latest) != _major(version) else "nowsza"
            row["notes"].append(f"{kind}: {latest}")
        if version and recorded_integrity:
            published = npm_manifest(entry["npm"], version)["dist"]["integrity"]
            if published != recorded_integrity:
                row["notes"].append("**SUMA PACZKI npm INNA NIŻ ZAPISANA PRZY WENDOROWANIU**")
        if version:
            if vulns := osv_vulns(entry["npm"], version):
                row["notes"].append("znane podatności tej wersji: " + ", ".join(vulns))
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        row.setdefault("latest", "?")
        row["notes"].append(f"błąd zapytania: {exc}")
    for url, sri in sri_urls:
        algorithm, expected = sri.split("-", 1)
        try:
            actual = base64.b64encode(hashlib.new(algorithm, _get(url, timeout=60)).digest()).decode()
        except (urllib.error.URLError, TimeoutError) as exc:
            row["notes"].append(f"SRI {url}: błąd pobrania ({exc})")
            continue
        if actual != expected:
            row["notes"].append(f"**SRI NIE ZGADZA SIĘ z treścią {url}** (przeglądarki odrzucą skrypt)")
    return row


def run_upstream(registry: dict, root: Path = ROOT) -> tuple[str, bool]:
    rows = []
    for entry in registry.get("vendored", []):
        version, _ = check_vendored(entry, root)
        text = (root / entry["dir"] / entry["version_file"]).read_text(encoding="utf-8")
        match = re.search(entry["integrity_pattern"], text) if entry.get("integrity_pattern") else None
        rows.append(upstream_row(entry, version, match.group("integrity") if match else None, []))
    for entry in registry.get("cdn", []):
        version, _ = check_cdn(entry, root)
        pairs = []
        if entry.get("sri"):
            for name in entry["files"]:
                for m in SRI_PAIR.finditer((root / name).read_text(encoding="utf-8")):
                    if re.search(entry["version_pattern"], m.group("url")):
                        pairs.append((m.group("url"), m.group("sri")))
        rows.append(upstream_row(entry, version, None, pairs))

    attention = any(row["notes"] for row in rows)
    out = [
        "| Biblioteka | Pakiet npm | Nasza wersja | Najnowsza | Uwagi |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        notes = "<br>".join(row["notes"]) or "aktualna"
        out.append(f"| {row['name']} | `{row['npm']}` | {row['ours']} | {row.get('latest', '?')} | {notes} |")
    return "\n".join(out) + "\n", attention


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("check", "upstream"))
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--attention-file", type=Path, help="upstream: zapisz 'yes'/'no' (czy są uwagi)")
    args = parser.parse_args(argv)
    # Konsola Windows (cp1250) nie zapisze polskich znaków z rejestru – wynik zawsze w UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    registry = load_registry(args.registry)

    if args.mode == "check":
        lines, findings = run_check(registry)
        print("\n".join(lines))
        for finding in findings:
            print(f"::error title=vendor_check::{finding}")
        print(f"{len(findings)} problemów" if findings else "OK – zapis wersji i skrótów zgodny z plikami")
        return 1 if findings else 0

    table, attention = run_upstream(registry)
    print(table)
    if args.attention_file:
        args.attention_file.write_text("yes" if attention else "no", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
