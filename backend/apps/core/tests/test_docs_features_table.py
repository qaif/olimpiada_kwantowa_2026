"""Tabela „Funkcje i flagi” na początku ``docs/OPERACJE.md`` zgadza się z ``FEATURE_DEFAULTS``.

Operator szuka przełącznika w dokumentacji, a nie w ``apps/tenancy/models.py``. Tabela pisana ręcznie
rozjeżdża się z kodem przy pierwszej nowej fladze – więc test wymaga, żeby każda flaga z katalogu
miała wiersz z poprawną wartością domyślną, a każda flaga z tabeli istniała w kodzie. Przełączniki
``.env`` sprawdzamy słabiej: nazwa ma występować w ustawieniach, ``.env.example``, compose albo
skrypcie wdrożenia (``ENV_SOURCES``).

Jak ``test_docs_section_refs``: potrzebuje ``docs/`` obok ``backend/`` – w kontenerze, który montuje
tylko ``backend/``, jest pomijany; CI uruchamia go z pełnego checkoutu.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from apps.tenancy.models import FEATURE_DEFAULTS

REPO = Path(__file__).resolve().parents[4]
OPERACJE = REPO / "docs" / "OPERACJE.md"
START = "<!-- funkcje-i-flagi: początek"
END = "<!-- funkcje-i-flagi: koniec -->"
DEFAULT_WORDS = {True: "wł.", False: "wył."}

pytestmark = pytest.mark.skipif(not OPERACJE.is_file(), reason="brak docs/ – kontener montuje tylko backend/")


def table_rows() -> list[dict[str, str]]:
    """Wiersze tabeli między znacznikami jako słowniki ``nagłówek → komórka``."""
    text = OPERACJE.read_text(encoding="utf-8")
    assert START in text and END in text, "brak znaczników tabeli „Funkcje i flagi” w OPERACJE.md"
    block = text.split(START, 1)[1].split(END, 1)[0]
    lines = [line for line in block.splitlines() if line.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    return [
        dict(zip(header, (c.strip() for c in line.strip("|").split("|")), strict=True)) for line in lines[2:]
    ]


def names(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def test_every_feature_flag_has_a_row_with_its_default():
    rows = {
        n: row for row in table_rows() if row["Rodzaj"] == "flaga konkursu" for n in names(row["Przełącznik"])
    }
    missing = sorted(set(FEATURE_DEFAULTS) - set(rows))
    assert not missing, f"Flagi bez wiersza w tabeli „Funkcje i flagi” (docs/OPERACJE.md): {missing}"
    wrong = {
        flag: (rows[flag]["Domyślnie"], DEFAULT_WORDS[default])
        for flag, default in FEATURE_DEFAULTS.items()
        if rows[flag]["Domyślnie"] != DEFAULT_WORDS[default]
    }
    assert not wrong, f"Wartość domyślna w tabeli ≠ FEATURE_DEFAULTS (tabela, kod): {wrong}"


def test_table_lists_only_existing_flags():
    flags = [
        n for row in table_rows() if row["Rodzaj"] == "flaga konkursu" for n in names(row["Przełącznik"])
    ]
    unknown = sorted(set(flags) - set(FEATURE_DEFAULTS))
    assert not unknown, f"Tabela wymienia flagi, których nie ma w FEATURE_DEFAULTS: {unknown}"
    assert len(flags) == len(set(flags)), "flaga w dwóch wierszach tabeli"


#: Gdzie przełącznik instalacji może być czytany: ustawienia Django, wzór ``.env``, compose i skrypt
#: wdrożenia (np. ``WEB_MEM_LIMIT`` czyta tylko compose, ``DEPLOY_SMOKE`` – tylko ``deploy.sh``).
ENV_SOURCES = ("backend/config/settings/base.py", ".env.example", "docker-compose.yml", "scripts/deploy.sh")


def test_env_switches_exist_in_settings_env_example_or_deploy():
    known = "".join((REPO / rel).read_text(encoding="utf-8") for rel in ENV_SOURCES)
    env = [
        n
        for row in table_rows()
        if row["Rodzaj"] in ("`.env`", "zmienna `scripts/deploy.sh`")
        for n in names(row["Przełącznik"])
    ]
    assert env, "tabela bez przełączników .env"
    unknown = [n for n in env if not re.search(rf"\b{re.escape(n)}\b", known)]
    assert not unknown, f"Przełączniki nieznane w {', '.join(ENV_SOURCES)}: {unknown}"
