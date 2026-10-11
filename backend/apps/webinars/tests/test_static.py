"""Pliki statyczne pokoju – collectstatic z manifestem nie może się wywrócić na dostarczonym SDK.

Produkcja przepuszcza statyki przez ``CompressedManifestStaticFilesStorage`` (WhiteNoise), a ta przez
``HashedFilesMixin.post_process``: każde odwołanie w JS/CSS (``url(…)``, ``sourceMappingURL``) musi
wskazywać istniejący plik, inaczej ``ValueError`` i kontener ``web`` nie wstaje. Paczka
``livekit-client`` kończy się odwołaniem do mapy źródeł, której nie dostarczamy – stąd test na
prawdziwym ``post_process`` i sprawdzenie sumy z ``SHA384``.
"""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.core.files.storage import FileSystemStorage

STATIC = Path(settings.BASE_DIR) / "static"
VENDOR = STATIC / "vendor" / "livekit-client"
#: Pliki, które dokłada WEB-01 – razem z SDK przechodzą przez ``post_process`` w teście.
OWN = ("js/webinar-room.js", "js/webinars.js", "css/webinar-room.css")


def _sources() -> list[str]:
    vendored = [
        str(path.relative_to(STATIC)).replace("\\", "/")
        for path in (STATIC / "vendor").rglob("*")
        if path.is_file()
    ]
    return [*vendored, *OWN]


def test_vendored_and_own_static_files_survive_manifest_post_process(tmp_path):
    source = FileSystemStorage(location=STATIC)
    target = ManifestStaticFilesStorage(location=tmp_path, base_url="/static/")
    paths = {}
    for name in _sources():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STATIC / name, tmp_path / name)
        paths[name] = (source, name)

    errors = [
        (name, processed)
        for name, _hashed, processed in target.post_process(paths)
        if isinstance(processed, Exception)
    ]

    assert errors == []


def test_no_vendored_file_points_to_a_missing_source_map():
    for path in (STATIC / "vendor").rglob("*.js"):
        for match in re.finditer(rb"sourceMappingURL=([^\s*]+)", path.read_bytes()):
            assert (path.parent / match.group(1).decode()).exists(), (path, match.group(1))


def test_sha384_matches_the_committed_sdk():
    if not (VENDOR / "livekit-client.umd.js").exists():  # pragma: no cover - SDK jeszcze niewgrany
        return
    digest = (
        "sha384-"
        + base64.b64encode(hashlib.sha384((VENDOR / "livekit-client.umd.js").read_bytes()).digest()).decode()
    )
    assert (VENDOR / "SHA384").read_text(encoding="utf-8").strip() == digest
    assert "modified:" in (VENDOR / "VERSION").read_text(encoding="utf-8")
