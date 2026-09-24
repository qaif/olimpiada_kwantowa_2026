"""Walidacja skanu zaświadczenia: o formacie decyduje **treść** pliku, nie nazwa ani ``Content-Type``.

Ta sama reguła, co przy rozwiązaniach (``apps.submissions.validators``) i plakatach
(``apps.promo.validators``): nazwę, rozszerzenie i nagłówek typu podaje przesyłający, więc jedynym
świadectwem formatu jest kilka pierwszych bajtów. Sygnatury bierzemy stamtąd, żeby „co to jest
PDF” miało w serwisie jedną definicję.

Tu reguła ma jeszcze jeden powód. Skan otwiera **koordynator** – w przeglądarce, pod domeną panelu,
na ekranie z uprawnieniami do danych wszystkich uczestników. Plik ``.html`` albo ``.svg`` ze
skryptem przemianowany na „zaswiadczenie.pdf” byłby drogą, którą uczestnik podsuwa koordynatorowi
własny kod. Zamknięta lista trzech formatów sprawdzana po bajtach, typ odpowiedzi ustawiany przez
serwer z tej listy i nagłówek ``Content-Security-Policy: sandbox`` przy podglądzie zamykają tę drogę
z trzech stron naraz (``apps.web.views.coordinator_student_status``).
"""

from __future__ import annotations

from rest_framework import status

from apps.core.api import DomainError
from apps.promo.validators import PNG_MAGIC
from apps.submissions.validators import JPEG_MAGIC, PDF_MAGIC

MEGABYTE = 1024 * 1024

#: Limit pliku. Zdjęcie kartki A4 z telefonu to 2–6 MB, skan w 300 dpi jako PDF – poniżej 2 MB.
#: Dziesięć megabajtów mieści oba z zapasem, a jednocześnie trzyma skan kilkaset razy poniżej
#: ``StreamMaxLength`` clamd (100 MB), więc odmowa skanera „za duży plik” nie ma tu jak wystąpić.
MAX_FILE_MB = 10

#: Ile bajtów czytamy z początku pliku. Najdłuższa sygnatura (PNG) ma osiem.
HEADER_PROBE_BYTES = 8

#: Formaty skanu: klucz → (sygnatura, typ MIME). Kolejność jest kolejnością w komunikacie odmowy.
FORMATS: dict[str, tuple[bytes, str]] = {
    "pdf": (PDF_MAGIC, "application/pdf"),
    "jpg": (JPEG_MAGIC, "image/jpeg"),
    "png": (PNG_MAGIC, "image/png"),
}

#: Odwrotna mapa – rozszerzenie w nazwie pobieranego pliku bierze się z typu zapisanego przez serwer.
EXTENSION_BY_MIME: dict[str, str] = {mime: ext for ext, (_magic, mime) in FORMATS.items()}

#: Atrybut ``accept`` pola pliku w formularzu. Podpowiedź dla przeglądarki (okno wyboru pliku
#: i aparat w telefonie), a nie zabezpieczenie – to robi :func:`validate_scan`.
ACCEPT_ATTRIBUTE = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"


def _invalid(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_400_BAD_REQUEST)


def _size(upload) -> int:
    size = getattr(upload, "size", None)
    if size is None:
        upload.seek(0, 2)
        size = upload.tell()
        upload.seek(0)
    return int(size)


def detect_format(upload) -> str | None:
    """Format rozpoznany po sygnaturze albo ``None``. Strumień wraca przewinięty na początek."""
    upload.seek(0)
    header = upload.read(HEADER_PROBE_BYTES)
    upload.seek(0)
    for key, (magic, _mime) in FORMATS.items():
        if header.startswith(magic):
            return key
    return None


def validate_scan(upload) -> tuple[str, str]:
    """Sprawdza plik skanu i zwraca ``(rozszerzenie, typ MIME)`` wyliczone po stronie serwera.

    Kolejność: pusty plik, rozmiar, treść. Rozmiar przed treścią, bo odmowa pliku 300 MB nie może
    zależeć od tego, co ma na początku; pusty plik osobno, bo „to nie jest PDF” byłoby o nim zdaniem
    prawdziwym, ale bezużytecznym. ``DomainError``, a nie ``ValidationError``: tę samą regułę
    wykonuje serwis, a formularz panelu jest tylko jednym z wołających.
    """
    size = _size(upload)
    if size == 0:
        raise _invalid("Plik jest pusty.", "EMPTY_FILE")
    if size > MAX_FILE_MB * MEGABYTE:
        raise DomainError(
            f"Plik ma {size / MEGABYTE:.1f} MB – limit to {MAX_FILE_MB} MB. Zrób zdjęcie w niższej "
            "rozdzielczości albo zapisz skan jako PDF.",
            "FILE_TOO_LARGE",
            status.HTTP_400_BAD_REQUEST,
        )
    fmt = detect_format(upload)
    if fmt is None:
        raise _invalid(
            "Treść pliku nie jest ani PDF-em, ani zdjęciem JPG/PNG. Rozpoznajemy format po "
            "zawartości, nie po rozszerzeniu – zapisz skan ponownie jako PDF, JPG albo PNG.",
            "INVALID_FILE_TYPE",
        )
    return fmt, FORMATS[fmt][1]
