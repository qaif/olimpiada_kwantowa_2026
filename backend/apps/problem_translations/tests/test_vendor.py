"""Zwendorowany KaTeX (TR-01, ``static/problem_translations/vendor/katex/VERSION``).

Dwie rzeczy, które psują się po cichu przy aktualizacji: CSS wskazujący plik, którego nie ma
(``collectstatic`` z ``ManifestStaticFilesStorage`` przerywa wtedy wdrożenie), i JS podmieniony
bez aktualizacji zapisu wersji i skrótów.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "static" / "problem_translations" / "vendor" / "katex"


def test_css_references_only_existing_files():
    css = (VENDOR / "katex.min.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\(([^)]+)\)", css)

    assert urls, "CSS KaTeX-a bez krojów – zła kopia"
    missing = [url for url in urls if not (VENDOR / url.strip("'\"")).is_file()]
    assert not missing
    assert all(url.endswith(".woff2") for url in urls)


def test_version_file_matches_vendored_files():
    version = (VENDOR / "VERSION").read_text(encoding="utf-8")
    for name in ("katex.min.js", "katex.min.css"):
        digest = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert digest in version, name
    assert (VENDOR / "LICENSE").is_file()
