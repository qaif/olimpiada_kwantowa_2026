"""Kontrakt tras z aplikacją główną: ``app_routes.json`` (docs/tasks/DJ-02.md § 6, D3).

Plik generuje ``manage.py djcms_routes --write`` w ``backend/`` (``backend/djcms_contract/``), obraz
djcms kopiuje go do ``/opt/djcms_contract`` (``djcms/Dockerfile``), dev montuje katalog na żywo.
Z niego djcms wie, które ścieżki należą do **aplikacji** (Caddy kieruje je do ``web``), więc
strona djcms pod takim adresem byłaby niewidoczna albo – gorzej – przesłaniałaby aplikację (S5).

Moduł **bez importów Django**: czytają go ustawienia (``config/settings/base.py``) w chwili importu.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

APP_ROUTES_FILE = "app_routes.json"
SUPPORTED_VERSION = 1
REQUIRED_KEYS = (
    "first_segments",
    "nested_paths",
    "root_regexes",
    "private_prefixes",
    "app_re",
    "app_re_prefixed",
)


class ContractError(Exception):
    pass


def default_contract_dir(base_dir: Path) -> Path:
    """``/opt/djcms_contract`` (obraz, dev compose), a poza kontenerem – katalog z repozytorium."""
    image = Path("/opt/djcms_contract")
    if (image / APP_ROUTES_FILE).is_file():
        return image
    return base_dir.parent / "backend" / "djcms_contract"


def load_app_routes(directory: str | Path) -> dict:
    """Wczytany i sprawdzony ``app_routes.json``. Błąd = ``ContractError`` z nazwą pliku."""
    path = Path(directory) / APP_ROUTES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"{path}: {exc.__class__.__name__} – brak albo zły plik kontraktu tras") from None
    if not isinstance(data, dict) or data.get("version") != SUPPORTED_VERSION:
        raise ContractError(f"{path}: nieobsługiwana wersja kontraktu (oczekiwana {SUPPORTED_VERSION})")
    for key in REQUIRED_KEYS:
        if key not in data:
            raise ContractError(f"{path}: brak klucza {key!r}")
    for key in ("first_segments", "nested_paths", "root_regexes", "private_prefixes"):
        if not isinstance(data[key], list) or not all(isinstance(item, str) for item in data[key]):
            raise ContractError(f"{path}: {key!r} nie jest listą napisów")
    for key in ("app_re", "app_re_prefixed"):
        try:
            re.compile(data[key])
        except TypeError, re.error:
            raise ContractError(f"{path}: {key!r} nie jest poprawnym wyrażeniem regularnym") from None
    return data
