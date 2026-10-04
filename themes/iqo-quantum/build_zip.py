"""Buduje paczkę motywu: ``dist/iqo-quantum-<wersja>.zip`` (wersja z manifest.json).

    python themes/iqo-quantum/build_zip.py

Do ZIP-a trafia wyłącznie to, co opisuje format paczki (THEME-01 § 1):
``manifest.json``, ``theme.css``, ``tokens.json``, ``screenshot.png``, ``assets/**``
(svg/png/jpg/webp/woff2 + licencje krojów ``.txt``) i ``templates/theme/*.html``.
Pomijane: ``_preview/``, ``tools/``, ``README.md``, ten skrypt, ``dist/``.

Przed spakowaniem skrypt sprawdza lokalnie te same reguły, które egzekwuje walidator
aplikacji (§ 2) – żeby błąd wyszedł tutaj, a nie dopiero przy wgraniu. Bez zależności
spoza biblioteki standardowej.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
DIST = ROOT / "dist"

ROOT_FILES = ("manifest.json", "theme.css", "tokens.json", "screenshot.png")
ASSET_EXT = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff2", ".txt"}
ALLOWED_SLOTS = {
    "theme/header.html", "theme/footer.html", "theme/home_hero.html", "theme/page_wrapper.html",
}
ALLOWED_LIBS = {"static", "i18n", "wagtailcore_tags", "wagtailimages_tags", "cms_extras", "web_extras"}
MAX_FILES, MAX_UNPACKED = 500, 60 * 1024 * 1024


def fail(msg: str) -> None:
    raise SystemExit(f"BŁĄD: {msg}")


def check_css(text: str) -> None:
    css = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    low = css.lower()
    for bad in ("@import", "expression(", "-moz-binding", "javascript:"):
        if bad in low:
            fail(f"theme.css zawiera {bad!r}")
    if re.search(r"(?<![\w-])behavior\s*:", low):
        fail("theme.css zawiera 'behavior:'")
    for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", css):
        if not url.startswith("assets/") or ".." in url:
            fail(f"theme.css: url() spoza assets/: {url}")
        if not (ROOT / url).is_file():
            fail(f"theme.css: brak pliku {url}")


def check_svg(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    if "<script" in text or "foreignobject" in text or "javascript:" in text:
        fail(f"{path.name}: niedozwolony element SVG")
    if re.search(r"\son[a-z]+\s*=", text):
        fail(f"{path.name}: atrybut on* w SVG")
    if re.search(r"href\s*=\s*['\"](?!#)", text):
        fail(f"{path.name}: zewnętrzny href w SVG")


def check_template(rel: str, text: str) -> None:
    if rel not in ALLOWED_SLOTS:
        fail(f"szablon spoza listy slotów: {rel}")
    if re.search(r"\|\s*safe\b", text) or re.search(r"{%\s*autoescape\s+off", text):
        fail(f"{rel}: |safe / autoescape off")
    if re.search(r"{%\s*extends\b", text):
        fail(f"{rel}: extends w slocie")
    for libs in re.findall(r"{%\s*load\s+([^%]+)%}", text):
        names = set(libs.split()) - {"from"}
        if names - ALLOWED_LIBS:
            fail(f"{rel}: niedozwolone biblioteki {sorted(names - ALLOWED_LIBS)}")
    for inc in re.findall(r"{%\s*include\s+['\"]([^'\"]+)", text):
        if ".." in inc or inc.startswith("/"):
            fail(f"{rel}: include {inc}")


def collect() -> list[pathlib.Path]:
    files = [ROOT / name for name in ROOT_FILES]
    files += sorted(p for p in (ROOT / "assets").rglob("*") if p.is_file())
    files += sorted(p for p in (ROOT / "templates").rglob("*") if p.is_file())
    return files


def main() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    json.loads((ROOT / "tokens.json").read_text(encoding="utf-8"))
    for key in ("schema", "slug", "name", "version", "layouts", "color_scheme"):
        if key not in manifest:
            fail(f"manifest.json: brak {key}")

    files = collect()
    for path in files:
        if not path.is_file():
            fail(f"brak pliku {path.relative_to(ROOT)}")
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("assets/"):
            if path.suffix.lower() not in ASSET_EXT:
                fail(f"niedozwolone rozszerzenie: {rel}")
            if path.suffix.lower() == ".svg":
                check_svg(path)
        elif rel.startswith("templates/"):
            check_template(rel.removeprefix("templates/"), path.read_text(encoding="utf-8"))
    check_css((ROOT / "theme.css").read_text(encoding="utf-8"))

    total = sum(p.stat().st_size for p in files)
    if len(files) > MAX_FILES or total > MAX_UNPACKED:
        fail("za dużo plików albo za duży rozmiar po rozpakowaniu")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"{manifest['slug']}-{manifest['version']}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(ROOT).as_posix())
    print(f"{out}  ({len(files)} plików, {total / 1024:.0f} KiB przed kompresją, "
          f"{out.stat().st_size / 1024:.0f} KiB ZIP)")


if __name__ == "__main__":
    sys.exit(main())
