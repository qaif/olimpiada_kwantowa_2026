"""Budowanie paczek motywów w testach: fikstura ``package_example`` i paczki złośliwe."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

EXAMPLE_DIR = Path(__file__).parent / "package_example"
FIXTURES = Path(__file__).parent / "fixtures"
IQO_ZIP = FIXTURES / "iqo-quantum-1.0.0.zip"


def example_files() -> dict[str, bytes]:
    return {
        path.relative_to(EXAMPLE_DIR).as_posix(): path.read_bytes()
        for path in sorted(EXAMPLE_DIR.rglob("*"))
        if path.is_file()
    }


def build_zip(files: dict[str, bytes | str], *, symlinks: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name in symlinks:
                info.external_attr = 0o120777 << 16
            archive.writestr(info, content.encode("utf-8") if isinstance(content, str) else content)
    return buffer.getvalue()


def zip_with(changes: dict[str, bytes | str | None] | None = None) -> bytes:
    """Paczka ``example`` z podmienionymi plikami (``None`` = plik usunięty)."""
    files: dict[str, bytes | str] = dict(example_files())
    for name, content in (changes or {}).items():
        if content is None:
            files.pop(name, None)
        else:
            files[name] = content
    return build_zip(files)
