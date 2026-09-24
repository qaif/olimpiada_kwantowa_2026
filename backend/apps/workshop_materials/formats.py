"""Formaty materiałów z warsztatów: co wolno wgrać, jak to rozpoznać i ile to może ważyć.

O formacie decyduje **treść** pliku, a nie nazwa ani nagłówek ``Content-Type`` – ta sama reguła,
co przy plakatach (``apps.promo.validators``) i rozwiązaniach (``apps.submissions.validators``).
Różnica jest jedna i wynika z drogi pliku: materiał nie przechodzi przez serwer aplikacji (idzie
z przeglądarki prosto do MinIO, patrz ``apps.workshop_materials.storage``), więc sprawdzamy go
**po fakcie** – krok „zakończ wgrywanie” czyta z magazynu pierwsze ``HEADER_PROBE_BYTES`` bajtów
i dopiero wtedy materiał dostaje status inny niż „wgrywanie”.

Rozszerzenie z nazwy pliku ma tu jednak **drugą** rolę, której przy plakacie nie miało: kontener
ZIP jest wspólny dla prezentacji (``pptx``), dokumentu (``docx``), arkusza (``xlsx``), formatów
OpenDocument i zwykłego archiwum. Po samych bajtach nie odróżnimy ich bez rozpakowania – a treść
decyduje wyłącznie o tym, **czy to w ogóle jest ZIP**. Rozszerzenie wybiera, pod jaką nazwą
i z jakim ``Content-Type`` plik trafi do pobierającego; jeżeli nie zgadza się z rodziną rozpoznaną
po bajtach („prezentacja.pptx”, która jest PDF-em), plik jest odrzucany, a nie przemianowywany.

Filmy są osobną listą z dwóch powodów:

- przeglądarka ma je **odtworzyć** w ``<video>``, a nie zapisać – więc przyjmujemy wyłącznie
  kontenery, które odtwarzają wszystkie współczesne przeglądarki: MP4 (rodzina ISO BMFF) i WebM.
  QuickTime (``.mov``, marka ``qt  ``) i Matroska (``.mkv``) są odrzucane z komunikatem, jak je
  przepakować – odtwarzanie „u mnie działa, u ucznia czarny ekran” byłoby gorsze niż odmowa,
- filmy **nie idą przez ClamAV** (uzasadnienie w ``apps.workshop_materials.tasks``), więc
  sprawdzenie sygnatury jest dla nich jedyną bramką treści.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.submissions.validators import JPEG_MAGIC, PDF_MAGIC

MEGABYTE = 1024 * 1024

#: Ile bajtów czytamy z początku obiektu. Nagłówek EBML pliku WebM podaje ``DocType`` zwykle
#: w pierwszych kilkudziesięciu bajtach, a marka MP4 stoi na bajtach 8–12; cztery kilobajty to
#: zapas na nietypowe nagłówki, a nadal jedno małe żądanie ``Range`` do magazynu.
HEADER_PROBE_BYTES = 4096

#: Sygnatury kontenerów (pierwsze bajty pliku).
ZIP_MAGIC = b"PK\x03\x04"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EBML_MAGIC = b"\x1a\x45\xdf\xa3"

#: Marki MP4 (bajty 8–12, pole ``major_brand`` pudełka ``ftyp``), które przeglądarki odtwarzają.
#: ``qt  `` (QuickTime) celowo **nie** stoi na liście – patrz docstring modułu.
MP4_BRANDS = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"mp41",
        b"mp42",
        b"avc1",
        b"dash",
        b"M4V ",
        b"MSNV",
        b"mmp4",
        b"f4v ",
    }
)


@dataclass(frozen=True)
class Format:
    """Jeden przyjmowany format: klucz w bazie, rodzina rozpoznawana po bajtach i typ MIME."""

    key: str
    family: str
    content_type: str
    label: str
    #: Czy przeglądarka ma plik **otworzyć** (PDF, obraz), czy zapisać (reszta). Pliki leżą w innym
    #: originie niż aplikacja (MinIO pod osobnym portem/hostem), więc otwarcie nie daje treści
    #: dostępu do ciasteczek serwisu – a PDF slajdów otwarty w karcie to wygoda, nie ryzyko.
    inline: bool = False


#: Filmy – odtwarzane w ``<video>``. Klucz = rozszerzenie pliku u pobierającego.
VIDEO_FORMATS: dict[str, Format] = {
    "mp4": Format("mp4", "mp4", "video/mp4", "MP4", inline=True),
    "webm": Format("webm", "webm", "video/webm", "WebM", inline=True),
}

#: Pliki – pobierane albo otwierane w przeglądarce. ``family`` mówi, co musi stać w bajtach.
FILE_FORMATS: dict[str, Format] = {
    "pdf": Format("pdf", "pdf", "application/pdf", "PDF", inline=True),
    "pptx": Format(
        "pptx",
        "zip",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "PowerPoint (PPTX)",
    ),
    "docx": Format(
        "docx",
        "zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Word (DOCX)",
    ),
    "xlsx": Format(
        "xlsx", "zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel (XLSX)"
    ),
    "odp": Format("odp", "zip", "application/vnd.oasis.opendocument.presentation", "Prezentacja (ODP)"),
    "odt": Format("odt", "zip", "application/vnd.oasis.opendocument.text", "Dokument (ODT)"),
    "ods": Format("ods", "zip", "application/vnd.oasis.opendocument.spreadsheet", "Arkusz (ODS)"),
    "zip": Format("zip", "zip", "application/zip", "Archiwum ZIP"),
    "ipynb": Format("ipynb", "json", "application/x-ipynb+json", "Notatnik Jupyter (IPYNB)"),
    "png": Format("png", "png", "image/png", "Obraz PNG", inline=True),
    "jpg": Format("jpg", "jpg", "image/jpeg", "Obraz JPG", inline=True),
}

#: ``.jpeg`` i ``.jpg`` to ten sam format – nazwa u pobierającego ma jedną postać.
EXTENSION_ALIASES = {"jpeg": "jpg"}

ALL_FORMATS: dict[str, Format] = {**VIDEO_FORMATS, **FILE_FORMATS}

#: Domyślne limity rozmiaru (MB). Nadpisywalne ustawieniami o tej samej nazwie.
#:
#: - film: 4 GB to dwugodzinne nagranie 1080p z typowym bitrate'em platformy wideo (1–2 GB) razy
#:   dwa. Wyżej nie idziemy, bo każdy gigabajt to miejsce na dysku jedynego serwera,
#: - plik: 100 MB, bo tyle wynosi ``StreamMaxLength`` ClamAV-a (``CLAMAV_STREAM_MAX_BYTES``).
#:   Plik większy nie dałby się przeskanować i nigdy nie stałby się widoczny – odmowa na wejściu
#:   jest uczciwsza niż materiał wiszący w „sprawdzaniu” bez końca.
DEFAULT_VIDEO_MAX_MB = 4096
DEFAULT_FILE_MAX_MB = 100


def video_max_bytes() -> int:
    return int(getattr(settings, "WORKSHOP_VIDEO_MAX_MB", DEFAULT_VIDEO_MAX_MB)) * MEGABYTE


def file_max_bytes() -> int:
    """Limit pliku – nigdy powyżej limitu strumienia ClamAV (patrz ``DEFAULT_FILE_MAX_MB``)."""
    configured = int(getattr(settings, "WORKSHOP_FILE_MAX_MB", DEFAULT_FILE_MAX_MB)) * MEGABYTE
    clamav = int(getattr(settings, "CLAMAV_STREAM_MAX_BYTES", DEFAULT_FILE_MAX_MB * MEGABYTE))
    return min(configured, clamav)


def normalise_extension(filename: str) -> str:
    """Rozszerzenie z nazwy pliku: małe litery, bez kropki, z aliasami (``jpeg`` → ``jpg``)."""
    name = (filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EXTENSION_ALIASES.get(ext, ext)


def _looks_like_json_object(header: bytes) -> bool:
    """Notatnik Jupyter to obiekt JSON: po (opcjonalnym) BOM i białych znakach stoi ``{``."""
    body = header[3:] if header.startswith(b"\xef\xbb\xbf") else header
    return body.lstrip(b" \t\r\n").startswith(b"{")


def detect_family(header: bytes) -> str | None:
    """Rodzina pliku rozpoznana po bajtach: ``mp4``/``webm``/``pdf``/``zip``/``png``/``jpg``/``json``.

    Kolejne sprawdzenia są rozłączne (sygnatury nie nachodzą na siebie), więc kolejność ma
    znaczenie wyłącznie dla ``json`` – ten jest najsłabszy i sprawdzany na końcu.
    Dla kontenerów, które **rozpoznajemy, ale odrzucamy** (QuickTime, Matroska), oddajemy osobne
    nazwy – komunikat dla koordynatora ma powiedzieć, co zrobić z plikiem, a nie tylko „nie”.
    """
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand == b"qt  ":
            return "quicktime"
        return "mp4" if brand in MP4_BRANDS else "mp4-unknown"
    if header.startswith(EBML_MAGIC):
        if b"webm" in header:
            return "webm"
        return "matroska"
    if header.startswith(PDF_MAGIC):
        return "pdf"
    if header.startswith(ZIP_MAGIC):
        return "zip"
    if header.startswith(PNG_MAGIC):
        return "png"
    if header.startswith(JPEG_MAGIC):
        return "jpg"
    if _looks_like_json_object(header):
        return "json"
    return None


class FormatError(ValueError):
    """Plik nie przeszedł sprawdzenia treści. Komunikat jest gotowy dla koordynatora."""


def verify_video(header: bytes) -> Format:
    """Format filmu z treści albo ``FormatError`` z komunikatem, co zrobić z plikiem."""
    family = detect_family(header)
    if family in VIDEO_FORMATS:
        return VIDEO_FORMATS[family]
    if family == "quicktime":
        raise FormatError(
            "To jest plik QuickTime (MOV), którego część przeglądarek nie odtworzy. Zapisz nagranie "
            "ponownie jako MP4 (H.264 + AAC) – np. w programie HandBrake albo poleceniem "
            "„ffmpeg -i nagranie.mov -c copy -movflags +faststart nagranie.mp4”."
        )
    if family == "matroska":
        raise FormatError(
            "To jest plik Matroska (MKV), którego przeglądarki nie odtwarzają. Przepakuj go do MP4 "
            "(np. w OBS: Plik → Remiksuj nagrania) albo zapisz jako WebM."
        )
    raise FormatError(
        "Treść pliku nie jest filmem MP4 ani WebM. Rozpoznajemy format po zawartości, nie po "
        "rozszerzeniu – zapisz nagranie jako MP4 (H.264 + AAC)."
    )


def verify_file(header: bytes, extension: str) -> Format:
    """Format pliku: rozszerzenie z listy **i** zgodna z nim rodzina bajtów – patrz docstring modułu."""
    fmt = FILE_FORMATS.get(extension)
    if fmt is None:
        raise FormatError(f"Pliki „.{extension}” nie są przyjmowane. Dozwolone: {allowed_file_extensions()}.")
    family = detect_family(header)
    if family != fmt.family:
        raise FormatError(
            f"Plik ma rozszerzenie „.{extension}”, ale jego treść nie jest plikiem {fmt.label}. "
            "Rozpoznajemy format po zawartości – zapisz plik ponownie we właściwym formacie."
        )
    return fmt


def allowed_file_extensions() -> str:
    return ", ".join(sorted(FILE_FORMATS))


def allowed_video_extensions() -> str:
    return ", ".join(sorted(VIDEO_FORMATS))
