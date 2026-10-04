"""Kroki budowy JupyterLite. Funkcje czyste (``closure``, ``prune_lock``, ``build_wheel``,
``externalize_inline_scripts``) mają testy w ``apps/notebooks/tests/test_labbuild.py``; ``main``
woła narzędzia zewnętrzne (``jupyter lite build``, sieć) i jest sprawdzany budową obrazu.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
QCLAB_DIR = BACKEND / "qclab"
CONFIG = json.loads((HERE / "pyodide.json").read_text(encoding="utf-8"))
#: Pliki dystrybucji Pyodide, których przeglądarka nie potrzebuje (CLI Node'a, typy TypeScript).
PYODIDE_DROP = ("python", "python.bat", "python.exe", "python_cli_entry.mjs", "ffi.d.ts", "pyodide.d.ts")
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
INLINE_SCRIPT_RE = re.compile(r"<script(?P<attrs>(?![^>]*\bsrc=)[^>]*)>(?P<body>.*?)</script>", re.S | re.I)


def normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def closure(packages: dict, roots) -> set[str]:
    """Pakiety z ``pyodide-lock.json`` potrzebne dla ``roots`` (z zależnościami)."""
    index = {normalize(key): key for key in packages}
    seen: set[str] = set()
    stack = [normalize(r) for r in roots]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        if name not in index:
            raise KeyError(f"package {name!r} is not in pyodide-lock.json")
        seen.add(name)
        stack.extend(normalize(dep) for dep in packages[index[name]].get("depends", []))
    return {index[name] for name in seen}


def prune_lock(lock: dict, keep: set[str]) -> dict:
    """Plik blokady tylko z pakietami, które naprawdę leżą obok – import czegoś spoza listy daje
    czytelne „nie ma takiego pakietu”, a nie 404 w środku ładowania."""
    pruned = dict(lock)
    pruned["packages"] = {name: data for name, data in lock["packages"].items() if name in keep}
    return pruned


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def wheel_files(source: Path = QCLAB_DIR) -> dict[str, bytes]:
    """Zawartość koła ``qclab``: pakiet + moduły zgodności ``qiskit``/``qiskit_aer`` na najwyższym
    poziomie. Bez atrap serwerowych (``_server_stubs`` – w przeglądarce działa prawdziwy piplite)."""
    files: dict[str, bytes] = {}
    for path in sorted(source.rglob("*.py")):
        rel = path.relative_to(source).as_posix()
        if "__pycache__" in rel or rel.startswith("_server_stubs/"):
            continue
        if rel.startswith("_compat/"):
            files[rel[len("_compat/") :]] = path.read_bytes()
        else:
            files[f"qclab/{rel}"] = path.read_bytes()
    return files


def build_wheel(out_dir: Path, version: str, files: dict[str, bytes] | None = None) -> Path:
    """Deterministyczne koło ``qclab-<wersja>-py3-none-any.whl`` (stałe daty, posortowane wpisy)."""
    files = dict(files or wheel_files())
    dist = f"qclab-{version}.dist-info"
    files[f"{dist}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: qclab\nVersion: {version}\n"
        "Summary: Qiskit-compatible statevector simulator for in-browser notebooks\n"
        "License: AGPL-3.0-or-later\nRequires-Dist: numpy\n"
    ).encode()
    files[f"{dist}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: olimpiada-labbuild\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    files[f"{dist}/top_level.txt"] = b"qclab\nqiskit\nqiskit_aer\n"
    record_lines = [f"{name},{_record_hash(data)},{len(data)}" for name, data in sorted(files.items())]
    record_lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record_lines) + "\n").encode()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"qclab-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return target


def lock_entry(wheel: Path, version: str) -> dict:
    return {
        "name": "qclab",
        "version": version,
        "file_name": wheel.name,
        "install_dir": "site",
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "package_type": "package",
        "imports": ["qclab", "qiskit", "qiskit_aer"],
        "depends": ["numpy"],
        "unvendored_tests": False,
    }


def externalize_inline_scripts(html: str, prefix: str = "boot") -> tuple[str, dict[str, str]]:
    """Skrypty inline → pliki obok (``script-src 'self'`` bez ``'unsafe-inline'``).

    Bloki ``type="application/json"`` (konfiguracja JupyterLite) zostają: to dane, nie kod –
    CSP ich nie wykonuje. ``import('../config-utils.js')`` w przeniesionym skrypcie rozwiązuje się
    względem pliku skryptu, który leży w tym samym katalogu co ``index.html`` – wynik ten sam.
    """
    scripts: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        attrs = match.group("attrs")
        if "application/json" in attrs.lower():
            return match.group(0)
        body = match.group("body")
        if not body.strip():
            return match.group(0)
        name = f"{prefix}-{hashlib.sha256(body.encode()).hexdigest()[:12]}.js"
        scripts[name] = body
        return f'<script src="./{name}"></script>'

    return INLINE_SCRIPT_RE.sub(replace, html), scripts


def _download(url: str, sha256: str, target: Path) -> Path:
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
        return target
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - stały adres z pyodide.json
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise RuntimeError(f"SHA-256 mismatch for {url}: {digest} != {sha256}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def prepare_pyodide(work: Path, cache: Path, wheel_version: str) -> Path:
    """Rdzeń Pyodide + wybrane koła + qclab, z przyciętym ``pyodide-lock.json``."""
    tarball = _download(CONFIG["core_url"], CONFIG["core_sha256"], cache / Path(CONFIG["core_url"]).name)
    with tarfile.open(tarball, "r:bz2") as archive:
        archive.extractall(work, filter="data")
    pyodide = work / "pyodide"
    lock = json.loads((pyodide / "pyodide-lock.json").read_text(encoding="utf-8"))
    keep = closure(lock["packages"], CONFIG["packages"])
    for name in sorted(keep):
        entry = lock["packages"][name]
        _download(
            CONFIG["packages_base_url"] + entry["file_name"], entry["sha256"], cache / entry["file_name"]
        )
        shutil.copy2(cache / entry["file_name"], pyodide / entry["file_name"])
    wheel = build_wheel(pyodide, wheel_version)
    pruned = prune_lock(lock, keep)
    pruned["packages"]["qclab"] = lock_entry(wheel, wheel_version)
    (pyodide / "pyodide-lock.json").write_text(json.dumps(pruned, indent=1, sort_keys=True), encoding="utf-8")
    for name in PYODIDE_DROP:
        (pyodide / name).unlink(missing_ok=True)
    return pyodide


def postprocess(site: Path) -> None:
    for index in site.rglob("index.html"):
        html, scripts = externalize_inline_scripts(index.read_text(encoding="utf-8"))
        for name, body in scripts.items():
            (index.parent / name).write_text(body, encoding="utf-8")
        index.write_text(html, encoding="utf-8")
    for name in ("rspack.config.js", "rspack.config.analyze.js", "rspack.config.watch.js"):
        (site / name).unlink(missing_ok=True)


def qclab_version_from_source() -> str:
    """Wersja z ``qclab/__init__.py`` bez importu – etap budowy obrazu nie ma NumPy."""
    match = re.search(
        r'^__version__ = "([^"]+)"', (QCLAB_DIR / "__init__.py").read_text(encoding="utf-8"), re.M
    )
    if not match:
        raise RuntimeError("qclab/__init__.py has no __version__")
    return match.group(1)


def inputs_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted([*HERE.rglob("*"), *QCLAB_DIR.rglob("*.py")]):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(BACKEND).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def wheel_licence(wheel: Path) -> str:
    """Licencja z ``METADATA`` koła: ``License-Expression``, ``License`` (pierwsza linia) albo
    klasyfikatory ``License ::``. Pusty napis, gdy koło nie deklaruje licencji wcale."""
    with zipfile.ZipFile(wheel) as archive:
        name = next((n for n in archive.namelist() if n.endswith(".dist-info/METADATA")), None)
        if name is None:
            return ""
        text = archive.read(name).decode("utf-8", errors="replace")
    headers, _sep, _body = text.partition("\n\n")
    fields: dict[str, list[str]] = {}
    for line in headers.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            fields.setdefault(key.strip(), []).append(value.strip())
    if fields.get("License-Expression"):
        return fields["License-Expression"][0]
    licence = (fields.get("License") or [""])[0].splitlines()[0].strip() if fields.get("License") else ""
    if licence and len(licence) < 80 and licence.upper() not in ("UNKNOWN", "NONE"):
        return licence
    classifiers = [
        c.split("::")[-1].strip() for c in fields.get("Classifier", []) if c.startswith("License ::")
    ]
    return ", ".join(classifiers)


def licences_of(site: Path) -> dict[str, str]:
    """Licencja każdego koła w budowie (Pyodide, piplite, qclab) – ``RuntimeError`` przy braku.

    Lista w ``manifest.json`` ma być **kompletna**: koło bez zadeklarowanej licencji zatrzymuje
    budowę, zamiast trafić do przeglądarek uczniów bez informacji, na jakich warunkach.
    Licencje kodu JavaScript (JupyterLab i rozszerzenia) leżą obok, w ``third-party-licenses.json``
    budowy – też w manifeście (``js_licence_files``).
    """
    result: dict[str, str] = {}
    missing = []
    for wheel in sorted(site.rglob("*.whl")):
        package = wheel.name.split("-")[0].lower().replace("_", "-")
        licence = wheel_licence(wheel)
        if not licence:
            licence = CONFIG.get("licence_overrides", {}).get(package, "")
        if not licence:
            missing.append(wheel.name)
        result[package] = licence
    if missing:
        raise RuntimeError(f"wheels without a declared licence: {', '.join(missing)}")
    return result


def site_sizes(site: Path) -> tuple[int, int, int]:
    files = [p for p in site.rglob("*") if p.is_file()]
    raw = sum(p.stat().st_size for p in files)
    compressed = sum(len(gzip.compress(p.read_bytes(), compresslevel=6)) for p in files)
    return len(files), raw, compressed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the self-hosted JupyterLite for QC-01.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "notebook-lab-cache")
    args = parser.parse_args(argv)
    qclab_version = qclab_version_from_source()

    build_id = f"{CONFIG['version']}-{inputs_digest()}"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        lite = work / "lite"
        shutil.copytree(HERE / "lite", lite)
        wheels = work / "piplite"
        subprocess.run(  # noqa: S603 - stałe polecenie budowy
            [sys.executable, "-m", "pip", "download", "--no-deps", "--require-hashes", "--only-binary=:all:",
             "-r", str(HERE / "piplite-requirements.txt"), "-d", str(wheels)],
            check=True,
        )  # fmt: skip
        pyodide = prepare_pyodide(work / "pyodide-src", args.cache, qclab_version)
        site = work / "site"
        command = [
            sys.executable, "-m", "jupyter", "lite", "build",
            "--lite-dir", str(lite), "--output-dir", str(site),
            "--no-sourcemaps", "--no-unused-shared-packages", "--apps", "lab", "--apps", "notebooks",
        ]  # fmt: skip
        for wheel in sorted(wheels.glob("*.whl")):
            command += ["--piplite-wheels", str(wheel)]
        subprocess.run(command, check=True, cwd=lite)  # noqa: S603 - stałe polecenie budowy
        target_pyodide = site / "static" / "pyodide"
        if target_pyodide.exists():
            shutil.rmtree(target_pyodide)
        shutil.copytree(pyodide, target_pyodide)
        postprocess(site)
        count, raw, compressed = site_sizes(site)
        manifest = {
            "build_id": build_id,
            "pyodide": CONFIG["version"],
            "qclab": qclab_version,
            "tools": (HERE / "requirements.in").read_text(encoding="utf-8").strip().splitlines()[-3:],
            "packages": sorted(json.loads((target_pyodide / "pyodide-lock.json").read_text())["packages"]),
            "licences": {**CONFIG["licences"], **licences_of(site)},
            "js_licence_files": sorted(
                path.relative_to(site).as_posix() for path in site.rglob("third-party-licenses.json")
            ),
            "files": count,
            "size_bytes": raw,
            "transfer_bytes": compressed,
        }
        (site / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        args.out.mkdir(parents=True, exist_ok=True)
        for old in args.out.iterdir():
            if old.is_dir():
                shutil.rmtree(old)
            else:
                old.unlink()
        shutil.copytree(site, args.out / build_id)
        (args.out / "current.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
