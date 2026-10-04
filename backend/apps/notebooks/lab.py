"""Zbudowane JupyterLite: identyfikator wersji, adres i rozmiar pobrania (QC-01 § 3).

``current.json`` zapisuje budowa (``apps.notebooks.labbuild``) obok katalogu ``<BUILD_ID>/``. Brak
pliku znaczy „laboratorium nie zbudowane” (dev bez budowy, obraz bez etapu ``notebook-lab``) –
ekrany mówią to wprost, zamiast pokazywać pustą ramkę.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

BUILD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_cache: dict[str, tuple[float, dict | None]] = {}


def build_info() -> dict | None:
    """Zawartość ``current.json`` (``build_id``, ``transfer_bytes``…) albo ``None``."""
    path = Path(settings.NOTEBOOK_LAB_DIR) / "current.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        info = None
    if not isinstance(info, dict) or not BUILD_ID_RE.match(str(info.get("build_id", ""))):
        info = None
    _cache[str(path)] = (mtime, info)
    return info


def base_url() -> str | None:
    info = build_info()
    if info is None:
        return None
    return f"{settings.STATIC_URL}notebook-lab/{info['build_id']}/"


def lab_url(starter_path: str) -> str | None:
    """Adres JupyterLab otwierającego notatnik startowy (rozszerzenie ``fromURL``)."""
    base = base_url()
    if base is None:
        return None
    return f"{base}lab/index.html?fromURL={quote(starter_path, safe='/')}"


def transfer_megabytes() -> float | None:
    info = build_info()
    if not info or not info.get("transfer_bytes"):
        return None
    return round(int(info["transfer_bytes"]) / (1024 * 1024), 1)
