"""Katalogi tłumaczeń (``locale/*/LC_MESSAGES/django.po``) – kompilacja i zgodność placeholderów.

Tłumaczenia poza angielskim są maszynowe (I18N-01 § 8), więc dwie rzeczy sprawdzamy automatem,
zanim zobaczy je człowiek:

- **każdy katalog się kompiluje** (``msgfmt --check``: składnia, nagłówek, formy mnogie,
  zgodność formatów ``python-format``) – bez tego Django cicho oddaje polskie napisy,
- **placeholdery są te same** w ``msgid`` i w każdej formie ``msgstr``: ``%(name)s``, ``%s``,
  ``%d`` i ``{name}``. Zgubiony placeholder to zdanie bez liczby albo nazwy; obcy – ``KeyError``
  w środku renderowania listu albo strony. ``msgfmt`` sprawdza tylko wpisy oznaczone
  ``python-format``, a szablony (``{% blocktranslate %}``) takiej flagi nie dostają.

Parser jest celowo mały (bez ``polib``): katalog jest plikiem gettext w podstawowej postaci,
którą wypisuje ``makemessages``, a zależność tylko dla testu byłaby kolejną rzeczą do aktualizacji.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings
from django.utils.translation import to_locale

LOCALE_DIR = Path(settings.BASE_DIR) / "locale"
CATALOGS = sorted(LOCALE_DIR.glob("*/LC_MESSAGES/django.po"))
#: Katalogi **aplikacji** (``apps/<nazwa>/locale/``) – nowe funkcje trzymają tam swoje napisy, a Django
#: scala je ze wspólnym katalogiem. Te same sprawdzenia, co dla wspólnego; ``Dockerfile`` kompiluje oba.
APP_CATALOGS = sorted((Path(settings.BASE_DIR) / "apps").glob("*/locale/*/LC_MESSAGES/django.po"))
ALL_CATALOGS = [*CATALOGS, *APP_CATALOGS]


def catalog_id(path: Path) -> str:
    return str(path.relative_to(settings.BASE_DIR).parent.parent)


#: ``%(name)s``, ``%s``, ``%d``, ``%.2f`` … oraz ``{name}``. ``%%`` to znak procentu, nie placeholder.
PERCENT = re.compile(r"%(?:\([A-Za-z_][A-Za-z0-9_]*\))?[-#0 +]*\d*(?:\.\d+)?[sdifr]")
BRACE = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")


def parse_po(path: Path) -> list[dict]:
    """Wpisy katalogu: ``msgid``, ``msgid_plural``, ``msgstr`` (lista form), flagi."""
    entries: list[dict] = []
    current: dict = {}
    key: tuple | None = None

    def flush():
        if "msgid" in current:
            entries.append(dict(current))
        current.clear()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#,"):
            if "msgid" in current:
                flush()
            current["flags"] = {flag.strip() for flag in line[2:].split(",")}
        elif line.startswith("#"):
            if "msgid" in current and key is not None:
                flush()
                key = None
        elif line.startswith("msgctxt "):
            if "msgid" in current:
                flush()
            current["msgctxt"] = ast.literal_eval(line[8:])
            key = ("msgctxt",)
        elif line.startswith("msgid "):
            if "msgid" in current:
                flush()
            current["msgid"] = ast.literal_eval(line[6:])
            key = ("msgid",)
        elif line.startswith("msgid_plural "):
            current["msgid_plural"] = ast.literal_eval(line[13:])
            key = ("msgid_plural",)
        elif line.startswith("msgstr["):
            index = int(line[7 : line.index("]")])
            current.setdefault("msgstr", {})[index] = ast.literal_eval(line[line.index("]") + 2 :])
            key = ("msgstr", index)
        elif line.startswith("msgstr "):
            current["msgstr"] = {0: ast.literal_eval(line[7:])}
            key = ("msgstr", 0)
        elif line.startswith('"') and key is not None:
            value = ast.literal_eval(line)
            if key[0] == "msgstr":
                current["msgstr"][key[1]] += value
            else:
                current[key[0]] += value
    flush()
    return entries


def placeholders(text: str) -> list[str]:
    cleaned = text.replace("%%", "")
    return sorted(PERCENT.findall(cleaned) + BRACE.findall(cleaned))


def test_every_configured_language_has_a_catalog():
    """Język w ``LANGUAGES`` bez katalogu dałby polskie napisy pod obcym ``lang``."""
    present = {path.parts[-3] for path in CATALOGS}
    missing = [code for code, _label in settings.LANGUAGES if code != "pl" and to_locale(code) not in present]
    assert not missing


def test_every_app_catalog_covers_every_language():
    """Aplikacja z własnym katalogiem ma go w każdym języku – inaczej jej ekran byłby po polsku."""
    expected = {to_locale(code) for code, _label in settings.LANGUAGES if code != "pl"}
    by_app: dict[str, set[str]] = {}
    for path in APP_CATALOGS:
        by_app.setdefault(path.parts[-5], set()).add(path.parts[-3])
    missing = {app: sorted(expected - present) for app, present in by_app.items() if expected - present}
    assert not missing


@pytest.mark.parametrize("catalog", ALL_CATALOGS, ids=catalog_id)
def test_catalog_compiles(catalog, tmp_path):
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        pytest.skip("Brak msgfmt (gettext) – test biegnie w kontenerze i w CI.")
    result = subprocess.run(  # noqa: S603 - ścieżka z which, argumenty z repozytorium
        [msgfmt, "--check", "-o", str(tmp_path / "django.mo"), str(catalog)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("catalog", ALL_CATALOGS, ids=catalog_id)
def test_placeholders_match(catalog):
    problems = []
    for entry in parse_po(catalog):
        if not entry["msgid"]:
            continue  # nagłówek katalogu
        translations = entry.get("msgstr", {})
        for index, text in translations.items():
            if not text:
                continue  # brak tłumaczenia – Django pokaże tekst źródłowy, to nie jest błąd składni
            source = entry["msgid"] if index == 0 or "msgid_plural" not in entry else entry["msgid_plural"]
            expected = placeholders(source)
            got = placeholders(text)
            if "msgid_plural" in entry and index == 0:
                # Forma pierwsza bywa w językach bez liczby mnogiej (chiński) formą jedyną, więc
                # porównujemy z liczbą mnogą, a w pozostałych dopuszczamy oba warianty źródła.
                alternative = placeholders(entry["msgid_plural"])
                if got in (expected, alternative):
                    continue
            elif "msgid_plural" in entry:
                # Forma „jeden” w liczbie mnogiej (np. rosyjskie 21) może pominąć liczbę, jeśli
                # źródło pojedyncze też jej nie ma – ale nie może dołożyć obcej nazwy.
                if set(got) <= set(expected) | set(placeholders(entry["msgid"])) and got:
                    continue
            if got != expected:
                problems.append(f"{entry['msgid'][:60]!r} [{index}]: {expected} ≠ {got}")
    assert not problems, "\n".join(problems[:20])


@pytest.mark.parametrize("catalog", ALL_CATALOGS, ids=catalog_id)
def test_catalog_has_no_fuzzy_entries(catalog):
    """``fuzzy`` znaczy „Django tego nie użyje” – tłumaczenie uznane za dobre nie może nim być."""
    fuzzy = [
        entry["msgid"][:60]
        for entry in parse_po(catalog)
        if entry["msgid"] and "fuzzy" in entry.get("flags", set())
    ]
    assert not fuzzy, fuzzy[:10]


@pytest.mark.parametrize("catalog", ALL_CATALOGS, ids=catalog_id)
def test_catalog_is_complete(catalog):
    """Każdy napis źródłowy ma tłumaczenie – brak to polskie słowo w środku obcej strony."""
    missing = [
        entry["msgid"][:60]
        for entry in parse_po(catalog)
        if entry["msgid"] and not all(entry.get("msgstr", {}).values())
    ]
    assert not missing, f"{len(missing)} bez tłumaczenia, np. {missing[:5]}"
