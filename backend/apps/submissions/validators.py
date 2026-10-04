"""Walidacja uploadu rozwiązania. O akceptacji decyduje treść pliku, nie nazwa ani ``content_type``.

Model zagrożenia (PROJEKT.md 1.3): przesyłający kontroluje nazwę pliku, rozszerzenie i nagłówek
``Content-Type``. Rozszerzenie służy wyłącznie do wyboru walidatora treści; jeśli treść nie
potwierdza deklaracji, plik jest odrzucany. Nagłówek ``Content-Type`` jest ignorowany całkowicie.
"""

from __future__ import annotations

import json

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status

from apps.competitions.models import SUPPORTED_FILE_FORMATS
from apps.core.api import DomainError

MEGABYTE = 1024 * 1024
PDF_MAGIC = b"%PDF-"
#: Nagłówek JPEG: ``SOI`` (``FF D8``) plus pierwszy znacznik (``FF``). Wspólny dla JFIF i Exif,
#: więc rozpoznaje i zdjęcie z telefonu, i plik z edytora – a nie przepuszcza PNG ani PDF-a
#: przemianowanego na ``.jpg``.
JPEG_MAGIC = b"\xff\xd8\xff"
MAX_NOTEBOOK_OUTPUTS_BYTES = 2 * MEGABYTE
MIN_NOTEBOOK_FORMAT = 4
MAX_PYTHON_BYTES = 1 * MEGABYTE
HEADER_PROBE_BYTES = 8

MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "ipynb": "application/x-ipynb+json",
    "py": "text/x-python",
    "jpg": "image/jpeg",
}

#: Rozszerzenia sprowadzane do jednej nazwy formatu. Aparat zapisze ``.jpeg``, telefon ``.JPG``,
#: a zadanie ma w ``allowed_formats`` jedną wartość – bez tej normalizacji ten sam plik byłby raz
#: przyjmowany, raz odrzucany zależnie od tego, co wpisał producent sprzętu.
EXTENSION_ALIASES = {"jpeg": "jpg"}

#: Lista formatów w komunikacie odmowy. Trzymana obok ``MIME_BY_FORMAT``, żeby dopisanie formatu
#: nie zostawiało nieaktualnego zdania w jedynym miejscu, w którym uczestnik je przeczyta.
SUPPORTED_FORMATS_HINT = gettext_lazy("Rozpoznawane są wyłącznie pliki .pdf, .ipynb, .py i .jpg (.jpeg).")


def _too_large(detail: str) -> DomainError:
    return DomainError(detail, "FILE_TOO_LARGE", status.HTTP_400_BAD_REQUEST)


def _invalid_type(detail: str) -> DomainError:
    return DomainError(detail, "INVALID_FILE_TYPE", status.HTTP_400_BAD_REQUEST)


def _format_not_allowed(ext: str, allowed_formats) -> DomainError:
    return DomainError(
        _("Format .%(ext)s nie jest dopuszczony dla tego zadania (dozwolone: %(allowed)s).")
        % {"ext": ext, "allowed": ", ".join(allowed_formats)},
        "FORMAT_NOT_ALLOWED",
        status.HTTP_400_BAD_REQUEST,
    )


def declared_extension(name: str | None) -> str:
    """Rozszerzenie z nazwy pliku – wyłącznie jako wskazówka, który walidator treści uruchomić.

    Wynik jest **znormalizowany** (``EXTENSION_ALIASES``): ``zdjecie.JPEG`` i ``zdjecie.jpg`` dają
    tę samą nazwę formatu, więc dalej – w dopuszczalnych formatach zadania, w kluczu obiektu
    i w nazwie pliku w paczce ZIP – istnieje już tylko jedna.
    """
    if not name or "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].strip().lower()
    return EXTENSION_ALIASES.get(ext, ext)


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
        raise _invalid_type(_("Treść pliku nie jest dokumentem PDF (brak nagłówka %PDF-)."))


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
        raise _invalid_type(_("Notatnik musi być tekstem UTF-8.")) from exc
    try:
        document = json.loads(text)
    except (ValueError, RecursionError) as exc:
        # ``RecursionError`` (pakiet 5): kilka tysięcy zagnieżdżonych ``[`` to kilkanaście
        # kilobajtów, a parser JSON schodzi rekurencyjnie – bez tej gałęzi taki plik kończył
        # upload pięćsetką zamiast odmową ``INVALID_FILE_TYPE``.
        raise _invalid_type(_("Notatnik nie jest poprawnym dokumentem JSON.")) from exc
    if not isinstance(document, dict):
        raise _invalid_type(_("Notatnik musi być obiektem JSON."))
    # Formaty archiwalne (nbformat ≤ 3) mają inny układ komórek i nikt ich dziś nie tworzy –
    # przyjmowanie ich to tylko dodatkowa powierzchnia ataku na konwerter i na liczniki limitów.
    version = document.get("nbformat")
    if not isinstance(version, int) or isinstance(version, bool) or version < MIN_NOTEBOOK_FORMAT:
        raise _invalid_type(
            _("Obsługiwane są wyłącznie notatniki w formacie nbformat %(version)s lub nowszym.")
            % {"version": MIN_NOTEBOOK_FORMAT}
        )
    try:
        notebook = nbformat.reads(text, as_version=4)
        nbformat.validate(notebook)
    except Exception as exc:  # nbformat rzuca kilka różnych klas wyjątków
        raise _invalid_type(_("Notatnik nie przechodzi walidacji nbformat.")) from exc
    try:
        outputs_bytes = _notebook_outputs_bytes(notebook)
    except RecursionError as exc:
        # Ten sam powód, co przy ``json.loads`` wyżej: serializacja zagnieżdżonego outputu.
        raise _invalid_type(_("Notatnik nie jest poprawnym dokumentem JSON.")) from exc
    if outputs_bytes > MAX_NOTEBOOK_OUTPUTS_BYTES:
        raise _invalid_type(_("Sumaryczny rozmiar outputów w notatniku przekracza 2 MB."))


def _validate_jpeg(upload) -> None:
    """Sygnatura ``FF D8 FF`` na początku strumienia.

    Rozmiaru nie sprawdzamy osobno: zdjęcie mieści się w limicie zadania (``max_file_mb``), tak
    samo jak PDF, a drugi limit tylko po to, żeby był, byłby regułą nie do wytłumaczenia
    uczestnikowi stojącemu nad telefonem.
    """
    upload.seek(0)
    header = upload.read(HEADER_PROBE_BYTES)
    upload.seek(0)
    if not header.startswith(JPEG_MAGIC):
        raise _invalid_type(_("Treść pliku nie jest zdjęciem JPEG (brak sygnatury FF D8 FF)."))


def _validate_python(upload) -> None:
    raw = _read_all(upload)
    if len(raw) > MAX_PYTHON_BYTES:
        raise _invalid_type(_("Plik .py nie może przekraczać 1 MB."))
    if b"\x00" in raw:
        raise _invalid_type(_("Plik .py zawiera bajty NUL – to nie jest kod źródłowy."))
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid_type(_("Plik .py musi być tekstem UTF-8.")) from exc


_CONTENT_VALIDATORS = {
    "pdf": validate_pdf,
    "ipynb": _validate_notebook,
    "py": _validate_python,
    "jpg": _validate_jpeg,
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
        raise _too_large(
            _("Plik ma %(size)s B, limit dla tego zadania to %(limit)s MB.")
            % {"size": size, "limit": max_file_mb}
        )
    if size == 0:
        raise _invalid_type(_("Plik jest pusty."))

    ext = declared_extension(getattr(upload, "name", ""))
    if ext not in SUPPORTED_FILE_FORMATS:
        raise _invalid_type(SUPPORTED_FORMATS_HINT)
    allowed = list(allowed_formats or [])
    if ext not in allowed:
        raise _format_not_allowed(ext, allowed)

    _CONTENT_VALIDATORS[ext](upload)
    upload.seek(0)
    return ext, MIME_BY_FORMAT[ext]
