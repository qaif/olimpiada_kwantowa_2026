"""Walidacja plików plakatów: o formacie decyduje **treść** pliku, nie nazwa ani ``Content-Type``.

Ta sama reguła, co przy rozwiązaniach uczestników (``apps.submissions.validators``) i treści zadań
(``apps.web.forms.ProblemForm``): rozszerzenie i nagłówek typu podaje przesyłający, więc jedynym
świadectwem formatu jest kilka pierwszych bajtów. Sygnatury bierzemy stamtąd (``PDF_MAGIC``,
``JPEG_MAGIC``), żeby „co to jest PDF” miało w serwisie jedną definicję.

Dlaczego to ma znaczenie przy plakacie, skoro wgrywa go koordynator, a nie anonim: plik trafia do
**każdego** nauczyciela, który kliknie „Pobierz”. Przemianowany ``.html`` albo ``.svg`` ze skryptem
podany jako „plakat.pdf” byłby drogą, którą konto koordynatora (przejęte albo po prostu pomylone)
rozsyła cudzą treść pod marką organizatora. Zamknięta lista trzech formatów, sprawdzana po
bajtach, tę drogę zamyka.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.submissions.validators import JPEG_MAGIC, PDF_MAGIC

MEGABYTE = 1024 * 1024

#: Sygnatura PNG (RFC 2083, § 3.1): osiem bajtów, z których dwa (CR LF) wykrywają plik
#: przepuszczony przez konwersję końców linii – czyli uszkodzony po drodze, a nie tylko podpisany.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: Ile bajtów czytamy z początku pliku. Najdłuższa sygnatura (PNG) ma osiem.
HEADER_PROBE_BYTES = 8

#: Limit pliku plakatu. Plakat A2 w 300 dpi zapisany jako PNG potrafi mieć kilkadziesiąt
#: megabajtów, a plik do drukarni ma być wierny – stąd limit wyższy niż przy rozwiązaniach.
#: Wyżej nie idziemy: plik jest oddawany przez aplikację (``apps.web.views.posters``), więc każdy
#: megabajt to czas, przez który jeden wątek serwera jest zajęty jednym pobraniem.
MAX_FILE_MB = 50

#: Limit własnego podglądu (miniatury). Podgląd jest obrazkiem na karcie, nie materiałem do druku.
MAX_PREVIEW_MB = 5

#: Formaty plakatu: klucz w bazie → (sygnatura, typ MIME, etykieta na karcie).
FORMATS = {
    "pdf": (PDF_MAGIC, "application/pdf", "PDF"),
    "jpg": (JPEG_MAGIC, "image/jpeg", "JPG"),
    "png": (PNG_MAGIC, "image/png", "PNG"),
}

#: Formaty, z których da się automatycznie zrobić miniaturę (``apps.promo.previews``).
IMAGE_FORMATS = frozenset({"jpg", "png"})

FORMAT_CHOICES = [(key, label) for key, (_magic, _mime, label) in FORMATS.items()]


def _header(upload) -> bytes:
    upload.seek(0)
    header = upload.read(HEADER_PROBE_BYTES)
    upload.seek(0)
    return header


def _size(upload) -> int:
    size = getattr(upload, "size", None)
    if size is None:
        upload.seek(0, 2)
        size = upload.tell()
        upload.seek(0)
    return int(size)


def detect_format(upload) -> str | None:
    """Format pliku rozpoznany po sygnaturze albo ``None``, gdy żadna z trzech nie pasuje."""
    header = _header(upload)
    for key, (magic, _mime, _label) in FORMATS.items():
        if header.startswith(magic):
            return key
    return None


def validate_material_file(upload) -> str:
    """Sprawdza plik plakatu i zwraca jego format (``pdf``/``jpg``/``png``).

    Kolejność jest ważna: rozmiar najpierw, bo odmowa pliku 300 MB nie powinna zależeć od tego,
    co ma na początku. Pusty plik jest osobnym komunikatem – „to nie jest PDF” byłoby o pustym
    pliku zdaniem prawdziwym, ale bezużytecznym.
    """
    size = _size(upload)
    if size == 0:
        raise ValidationError("Plik jest pusty.", code="empty")
    if size > MAX_FILE_MB * MEGABYTE:
        raise ValidationError(
            f"Plik ma {size / MEGABYTE:.1f} MB – limit to {MAX_FILE_MB} MB.",
            code="too_large",
        )
    fmt = detect_format(upload)
    if fmt is None:
        raise ValidationError(
            "Treść pliku nie jest ani PDF-em, ani obrazem JPG/PNG. Rozpoznajemy format po "
            "zawartości, nie po rozszerzeniu – zapisz plik ponownie jako PDF, JPG albo PNG.",
            code="invalid_type",
        )
    return fmt


def validate_preview_file(upload) -> str:
    """Sprawdza własny podgląd: wyłącznie JPG albo PNG, najwyżej ``MAX_PREVIEW_MB``.

    Treść potwierdza jeszcze Pillow (``apps.promo.previews.verify_image``) – sygnatura mówi „to
    miało być zdjęcie”, a dopiero dekoder mówi, czy da się je pokazać.
    """
    size = _size(upload)
    if size == 0:
        raise ValidationError("Plik podglądu jest pusty.", code="empty")
    if size > MAX_PREVIEW_MB * MEGABYTE:
        raise ValidationError(f"Podgląd może ważyć najwyżej {MAX_PREVIEW_MB} MB.", code="too_large")
    fmt = detect_format(upload)
    if fmt not in IMAGE_FORMATS:
        raise ValidationError("Podgląd musi być obrazem JPG albo PNG.", code="invalid_type")
    return fmt
