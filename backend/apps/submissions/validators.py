"""Walidacja uploadu rozwiązania. O akceptacji decyduje treść pliku, nie nazwa ani ``content_type``.

Model zagrożenia (PROJEKT.md 1.3): przesyłający kontroluje nazwę pliku, rozszerzenie i nagłówek
``Content-Type``. Rozszerzenie służy wyłącznie do wyboru walidatora treści; jeśli treść nie
potwierdza deklaracji, plik jest odrzucany. Nagłówek ``Content-Type`` jest ignorowany całkowicie.
"""

from __future__ import annotations

import json

from rest_framework import status

from apps.competitions.models import SUPPORTED_FILE_FORMATS
from apps.core.api import DomainError

MEGABYTE = 1024 * 1024
PDF_MAGIC = b"%PDF-"
MAX_NOTEBOOK_OUTPUTS_BYTES = 2 * MEGABYTE
MIN_NOTEBOOK_FORMAT = 4
MAX_PYTHON_BYTES = 1 * MEGABYTE
HEADER_PROBE_BYTES = 8

MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "ipynb": "application/x-ipynb+json",
    "py": "text/x-python",
}


def _too_large(detail: str) -> DomainError:
    return DomainError(detail, "FILE_TOO_LARGE", status.HTTP_400_BAD_REQUEST)


def _invalid_type(detail: str) -> DomainError:
    return DomainError(detail, "INVALID_FILE_TYPE", status.HTTP_400_BAD_REQUEST)


def _format_not_allowed(ext: str, allowed_formats) -> DomainError:
    return DomainError(
        f"Format .{ext} nie jest dopuszczony dla tego zadania (dozwolone: {', '.join(allowed_formats)}).",
        "FORMAT_NOT_ALLOWED",
        status.HTTP_400_BAD_REQUEST,
    )


def declared_extension(name: str | None) -> str:
    """Rozszerzenie z nazwy pliku – wyłącznie jako wskazówka, który walidator treści uruchomić."""
    if not name or "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip().lower()


def _read_all(upload) -> bytes:
    upload.seek(0)
    data = upload.read()
    upload.seek(0)
    return data


def validate_pdf(upload) -> None:
    """Nagłówek ``%PDF-`` na początku strumienia. Publiczna, bo tej samej reguły używa panel
    koordynatora dla treści zadania (``apps.web.forms.ProblemForm``): rozszerzenie ``.pdf`` i typ
    MIME podaje przesyłający, więc o akceptacji decyduje wyłącznie treść pliku.
    """
    upload.seek(0)
    header = upload.read(HEADER_PROBE_BYTES)
    upload.seek(0)
    if not header.startswith(PDF_MAGIC):
        raise _invalid_type("Treść pliku nie jest dokumentem PDF (brak nagłówka %PDF-).")


def _notebook_outputs_bytes(notebook) -> int:
    """Sumaryczna długość outputów w notatniku. Chroni recenzenta przed „bombą” w przeglądarce.

    Liczymy na obiekcie po konwersji do nbformat 4 (``notebook.cells``), nie na surowym JSON:
    w nbformat 3 komórki siedzą w ``worksheets[].cells[]``, więc licznik czytający ``cells``
    z surowego dokumentu widziałby zero i przepuszczał dowolnie duże outputy.
    """
    total = 0
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        return 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        outputs = cell.get("outputs")
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            total += len(json.dumps(output, ensure_ascii=False).encode("utf-8"))
    return total


def _validate_notebook(upload) -> None:
    import nbformat

    raw = _read_all(upload)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid_type("Notatnik musi być tekstem UTF-8.") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _invalid_type("Notatnik nie jest poprawnym dokumentem JSON.") from exc
    if not isinstance(document, dict):
        raise _invalid_type("Notatnik musi być obiektem JSON.")
    # Formaty archiwalne (nbformat ≤ 3) mają inny układ komórek i nikt ich dziś nie tworzy –
    # przyjmowanie ich to tylko dodatkowa powierzchnia ataku na konwerter i na liczniki limitów.
    version = document.get("nbformat")
    if not isinstance(version, int) or isinstance(version, bool) or version < MIN_NOTEBOOK_FORMAT:
        raise _invalid_type(
            f"Obsługiwane są wyłącznie notatniki w formacie nbformat {MIN_NOTEBOOK_FORMAT} lub nowszym."
        )
    try:
        notebook = nbformat.reads(text, as_version=4)
        nbformat.validate(notebook)
    except Exception as exc:  # nbformat rzuca kilka różnych klas wyjątków
        raise _invalid_type("Notatnik nie przechodzi walidacji nbformat.") from exc
    if _notebook_outputs_bytes(notebook) > MAX_NOTEBOOK_OUTPUTS_BYTES:
        raise _invalid_type("Sumaryczny rozmiar outputów w notatniku przekracza 2 MB.")


def _validate_python(upload) -> None:
    raw = _read_all(upload)
    if len(raw) > MAX_PYTHON_BYTES:
        raise _invalid_type("Plik .py nie może przekraczać 1 MB.")
    if b"\x00" in raw:
        raise _invalid_type("Plik .py zawiera bajty NUL – to nie jest kod źródłowy.")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid_type("Plik .py musi być tekstem UTF-8.") from exc


_CONTENT_VALIDATORS = {
    "pdf": validate_pdf,
    "ipynb": _validate_notebook,
    "py": _validate_python,
}


def validate_upload(upload, allowed_formats, max_file_mb: int) -> tuple[str, str]:
    """Sprawdza rozmiar, dopuszczalność formatu i zgodność treści z deklaracją.

    Zwraca ``(ext, mime)`` wyliczone po stronie serwera. Po powrocie strumień jest przewinięty
    na początek, więc serwis może policzyć sha256 i wysłać plik do storage.
    """
    size = getattr(upload, "size", None)
    if size is None:
        upload.seek(0, 2)
        size = upload.tell()
        upload.seek(0)
    limit = int(max_file_mb) * MEGABYTE
    if size > limit:
        raise _too_large(f"Plik ma {size} B, limit dla tego zadania to {max_file_mb} MB.")
    if size == 0:
        raise _invalid_type("Plik jest pusty.")

    ext = declared_extension(getattr(upload, "name", ""))
    if ext not in SUPPORTED_FILE_FORMATS:
        raise _invalid_type("Rozpoznawane są wyłącznie pliki .pdf, .ipynb i .py.")
    allowed = list(allowed_formats or [])
    if ext not in allowed:
        raise _format_not_allowed(ext, allowed)

    _CONTENT_VALIDATORS[ext](upload)
    upload.seek(0)
    return ext, MIME_BY_FORMAT[ext]
