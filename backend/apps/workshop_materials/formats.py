"""Formaty materiałów z warsztatów: co wolno wgrać, jak to rozpoznać i ile to może ważyć.

O formacie decyduje **treść** pliku, a nie nazwa ani nagłówek ``Content-Type`` – ta sama reguła,
co przy plakatach (``apps.promo.validators``) i rozwiązaniach (``apps.submissions.validators``).
Różnica jest jedna i wynika z drogi pliku: materiał nie przechodzi przez serwer aplikacji (idzie
z przeglądarki prosto do MinIO, patrz ``apps.workshop_materials.storage``), więc sprawdzamy go
**po fakcie** – krok „zakończ wgrywanie” czyta z magazynu pierwsze ``HEADER_PROBE_BYTES`` bajtów
i dopiero wtedy materiał dostaje status inny niż „wgrywanie”.

Rozszerzenie z nazwy pliku ma tu jednak **drugą** rolę, której przy plakacie nie miało: kontener
ZIP jest wspólny dla prezentacji (``pptx``), dokumentu (``docx``), arkusza (``xlsx``), formatów
OpenDocument, EPUB-a i zwykłego archiwum. Po samych bajtach nie odróżnimy ich bez rozpakowania –
a treść decyduje wyłącznie o tym, **czy to w ogóle jest ZIP**. Rozszerzenie wybiera, pod jaką nazwą
i z jakim ``Content-Type`` plik trafi do pobierającego; jeżeli nie zgadza się z rodziną rozpoznaną
po bajtach („prezentacja.pptx”, która jest PDF-em), plik jest odrzucany, a nie przemianowywany.
Ta sama reguła obejmuje dwie rodziny dodane w WM-FMT-01 (``docs/tasks/WM-FMT-01.md``):

- ``cfb`` – kontener OLE (Compound File Binary) starego Office'a: ``doc``, ``xls``, ``ppt`` i ich
  szablony mają wspólną sygnaturę, a różnią się dopiero strumieniami w środku,
- ``text`` – Markdown, dane tekstowe i kod. Tekst **nie ma** sygnatury, więc „rozpoznanie po treści”
  znaczy tu: próbka jest poprawnym UTF-8 bez bajtów NUL i bez nadmiaru znaków sterujących
  (``looks_like_text``). To odsiewa to, przed czym ta bramka ma chronić: program, obraz albo archiwum
  przemianowane na ``.py`` (w pierwszych 4 KB pliku binarnego prawie zawsze stoi NUL, a losowe bajty
  nie są poprawnym UTF-8). Dla CSV, TSV i TXT przyjmujemy też UTF-16 z BOM-em i tekst 8-bitowy
  (Windows-1250 z polskiego Excela – ``detect_text_encoding``); kodowanie trafia do
  ``WorkshopMaterial.charset``. Wszystkie teksty – także ``html`` i ``svg`` – są serwowane jako
  ``text/plain`` w załączniku (``inline=False``), nigdy jako strona albo obraz.

**Makra** (``docm``, ``xlsm``, ``pptm``…, a także stare ``doc``/``xls``/``ppt``) są przyjmowane –
decyzja organizatora z 9.10.2026 („nie zawężaj listy, co najwyżej ją rozszerz”). Plik i tak idzie
przez ClamAV, a program biurowy nie uruchamia makr bez zgody; strona materiału mówi o tym neutralnie
(``Format.macros``).

Filmy są osobną listą z dwóch powodów:

- przeglądarka ma je **odtworzyć** w ``<video>``, a nie zapisać – więc przyjmujemy wyłącznie
  kontenery, które odtwarzają wszystkie współczesne przeglądarki: MP4 (rodzina ISO BMFF) i WebM.
  QuickTime (``.mov``, marka ``qt  ``) i Matroska (``.mkv``) są odrzucane z komunikatem, jak je
  przepakować – odtwarzanie „u mnie działa, u ucznia czarny ekran” byłoby gorsze niż odmowa,
- filmy **nie idą przez ClamAV** (uzasadnienie w ``apps.workshop_materials.tasks``), więc
  sprawdzenie sygnatury jest dla nich jedyną bramką treści.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

from django.conf import settings

from apps.submissions.validators import JPEG_MAGIC, PDF_MAGIC

MEGABYTE = 1024 * 1024

#: Ile bajtów czytamy z początku obiektu. Nagłówek EBML pliku WebM podaje ``DocType`` zwykle
#: w pierwszych kilkudziesięciu bajtach, a marka MP4 stoi na bajtach 8–12; cztery kilobajty to
#: zapas na nietypowe nagłówki, a nadal jedno małe żądanie ``Range`` do magazynu. Dla tekstu to
#: próbka, na której sprawdzamy UTF-8 – kilkadziesiąt wierszy kodu.
HEADER_PROBE_BYTES = 4096

#: Sygnatury kontenerów (pierwsze bajty pliku).
ZIP_MAGIC = b"PK\x03\x04"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EBML_MAGIC = b"\x1a\x45\xdf\xa3"
#: Compound File Binary (OLE2) – ``doc``/``xls``/``ppt`` sprzed Office 2007.
CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RTF_MAGIC = b"{\\rtf"
UTF8_BOM = b"\xef\xbb\xbf"

#: Najwyższy odsetek znaków sterujących w próbce tekstu. Kod i Markdown nie mają ich wcale (poza
#: tabulacją i końcami wierszy, których nie liczymy); 5 % zostawia miejsce na log z sekwencjami
#: kolorów terminala (ESC). Bajty losowe (plik binarny, który przypadkiem jest poprawnym UTF-8 bez
#: NUL) mają ich ok. 11 % – a to i tak przypadek skrajny, bo zwykle odpadają już na UTF-8.
TEXT_CONTROL_MAX_RATIO = 0.05
#: Znaki sterujące, które w tekście są normalne: tabulacja, nowy wiersz, tabulacja pionowa, wysuw
#: strony (stary kod C/Fortranu dzieli nim strony), powrót karetki.
TEXT_ALLOWED_CONTROLS = frozenset("\t\n\v\f\r")

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

#: Grupy formatów w podpowiedzi formularza i w komunikatach odrzucenia – sama lista ~70 rozszerzeń
#: w kolejności alfabetycznej byłaby nieczytelna.
GROUP_DOCUMENTS = "dokumenty, prezentacje i arkusze"
GROUP_TEXT = "tekst i dane"
GROUP_CODE = "kod"
GROUP_IMAGES = "obrazy"
GROUP_ARCHIVES = "archiwa"
GROUP_ORDER = (GROUP_DOCUMENTS, GROUP_TEXT, GROUP_CODE, GROUP_IMAGES, GROUP_ARCHIVES)

#: Typy MIME tekstu. Jeden dla całego kodu (także ``html``/``svg``/``js``), bo plik ma zostać
#: **zapisany**, a nie zinterpretowany – ``text/plain`` nie jest typem, który przeglądarka wykonuje.
TEXT_PLAIN = "text/plain; charset=utf-8"
TEXT_MARKDOWN = "text/markdown; charset=utf-8"

#: Kodowania tekstu zapisywane w ``WorkshopMaterial.charset`` i podawane w ``Content-Type``.
CHARSET_UTF8 = "utf-8"
CHARSET_UTF16 = "utf-16"
CHARSET_WINDOWS_1250 = "windows-1250"
#: Rozszerzenia, dla których poza UTF-8 przyjmujemy UTF-16 z BOM-em i tekst 8-bitowy – dane
#: eksportowane z arkusza i notatki (``detect_text_encoding``). Kod i Markdown: tylko UTF-8.
LEGACY_TEXT_EXTENSIONS = frozenset({"csv", "tsv", "txt"})


def with_charset(content_type: str, charset: str) -> str:
    """``text/plain; charset=utf-8`` → ``text/plain; charset=<charset>`` (pusty = bez zmian)."""
    if not charset or "; charset=" not in content_type:
        return content_type
    return f"{content_type.split('; charset=', 1)[0]}; charset={charset}"


#: Rodzaje podglądu na stronie materiału (``apps.workshop_materials.preview``).
PREVIEW_MARKDOWN = "markdown"
PREVIEW_TEXT = "text"


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
    #: Grupa w podpowiedzi formularza (``GROUP_*``); filmy jej nie mają.
    group: str = ""
    #: Podgląd na stronie materiału: ``PREVIEW_MARKDOWN``, ``PREVIEW_TEXT`` albo brak.
    preview: str = ""
    #: Format, który **może** nieść makra – strona materiału mówi o tym neutralnie (bez blokady).
    macros: bool = False


#: Filmy – odtwarzane w ``<video>``. Klucz = rozszerzenie pliku u pobierającego.
VIDEO_FORMATS: dict[str, Format] = {
    "mp4": Format("mp4", "mp4", "video/mp4", "MP4", inline=True),
    "webm": Format("webm", "webm", "video/webm", "WebM", inline=True),
}

_OOXML = "application/vnd.openxmlformats-officedocument"


def _office(key: str, family: str, content_type: str, label: str, *, macros: bool = False) -> Format:
    return Format(key, family, content_type, label, group=GROUP_DOCUMENTS, macros=macros)


def _text(
    key: str, label: str, group: str, *, content_type: str = TEXT_PLAIN, preview=PREVIEW_TEXT
) -> Format:
    return Format(key, "text", content_type, label, group=group, preview=preview)


_DOCUMENTS = [
    Format("pdf", "pdf", "application/pdf", "PDF", inline=True, group=GROUP_DOCUMENTS),
    # Office 2007+ (ZIP).
    _office("docx", "zip", f"{_OOXML}.wordprocessingml.document", "Word (DOCX)"),
    _office("dotx", "zip", f"{_OOXML}.wordprocessingml.template", "Szablon Worda (DOTX)"),
    _office(
        "docm",
        "zip",
        "application/vnd.ms-word.document.macroEnabled.12",
        "Word z makrami (DOCM)",
        macros=True,
    ),
    _office(
        "dotm",
        "zip",
        "application/vnd.ms-word.template.macroEnabled.12",
        "Szablon Worda z makrami (DOTM)",
        macros=True,
    ),
    _office("xlsx", "zip", f"{_OOXML}.spreadsheetml.sheet", "Excel (XLSX)"),
    _office("xltx", "zip", f"{_OOXML}.spreadsheetml.template", "Szablon Excela (XLTX)"),
    _office(
        "xlsm", "zip", "application/vnd.ms-excel.sheet.macroEnabled.12", "Excel z makrami (XLSM)", macros=True
    ),
    _office(
        "xltm",
        "zip",
        "application/vnd.ms-excel.template.macroEnabled.12",
        "Szablon Excela z makrami (XLTM)",
        macros=True,
    ),
    _office(
        "xlsb",
        "zip",
        "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
        "Excel binarny (XLSB)",
        macros=True,
    ),
    _office("pptx", "zip", f"{_OOXML}.presentationml.presentation", "PowerPoint (PPTX)"),
    _office("ppsx", "zip", f"{_OOXML}.presentationml.slideshow", "Pokaz PowerPointa (PPSX)"),
    _office("potx", "zip", f"{_OOXML}.presentationml.template", "Szablon PowerPointa (POTX)"),
    _office(
        "pptm",
        "zip",
        "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
        "PowerPoint z makrami (PPTM)",
        macros=True,
    ),
    _office(
        "ppsm",
        "zip",
        "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",
        "Pokaz PowerPointa z makrami (PPSM)",
        macros=True,
    ),
    _office(
        "potm",
        "zip",
        "application/vnd.ms-powerpoint.template.macroEnabled.12",
        "Szablon PowerPointa z makrami (POTM)",
        macros=True,
    ),
    # Office 97–2003 (CFB). Ten format też może nieść makra VBA – stąd ``macros``.
    _office("doc", "cfb", "application/msword", "Word 97–2003 (DOC)", macros=True),
    _office("dot", "cfb", "application/msword", "Szablon Worda 97–2003 (DOT)", macros=True),
    _office("xls", "cfb", "application/vnd.ms-excel", "Excel 97–2003 (XLS)", macros=True),
    _office("xlt", "cfb", "application/vnd.ms-excel", "Szablon Excela 97–2003 (XLT)", macros=True),
    _office("ppt", "cfb", "application/vnd.ms-powerpoint", "PowerPoint 97–2003 (PPT)", macros=True),
    _office("pps", "cfb", "application/vnd.ms-powerpoint", "Pokaz PowerPointa 97–2003 (PPS)", macros=True),
    _office("pot", "cfb", "application/vnd.ms-powerpoint", "Szablon PowerPointa 97–2003 (POT)", macros=True),
    # OpenDocument (ZIP).
    _office("odt", "zip", "application/vnd.oasis.opendocument.text", "Dokument (ODT)"),
    _office("ods", "zip", "application/vnd.oasis.opendocument.spreadsheet", "Arkusz (ODS)"),
    _office("odp", "zip", "application/vnd.oasis.opendocument.presentation", "Prezentacja (ODP)"),
    _office("odg", "zip", "application/vnd.oasis.opendocument.graphics", "Rysunek (ODG)"),
    _office("rtf", "rtf", "application/rtf", "Dokument RTF"),
    _office("epub", "zip", "application/epub+zip", "E-book (EPUB)"),
]

_TEXT = [
    _text("md", "Markdown (MD)", GROUP_TEXT, content_type=TEXT_MARKDOWN, preview=PREVIEW_MARKDOWN),
    _text("txt", "Tekst (TXT)", GROUP_TEXT),
    _text("csv", "Dane CSV", GROUP_TEXT),
    _text("tsv", "Dane TSV", GROUP_TEXT),
    _text("tex", "LaTeX (TEX)", GROUP_TEXT),
    _text("bib", "BibTeX (BIB)", GROUP_TEXT),
    _text("json", "JSON", GROUP_TEXT),
    _text("yaml", "YAML", GROUP_TEXT),
    _text("yml", "YAML", GROUP_TEXT),
    _text("toml", "TOML", GROUP_TEXT),
    _text("xml", "XML", GROUP_TEXT),
    _text("rst", "reStructuredText (RST)", GROUP_TEXT),
]

#: Kod. ``html``, ``css`` i ``svg`` są tu **kodem do pobrania** – nigdy stroną ani obrazem
#: (``text/plain`` + ``attachment``, patrz docstring modułu). Notatnik ``ipynb`` – osobna rodzina, niżej.
_CODE = [
    _text("py", "Python (PY)", GROUP_CODE),
    _text("c", "C", GROUP_CODE),
    _text("h", "Nagłówek C (H)", GROUP_CODE),
    _text("cpp", "C++ (CPP)", GROUP_CODE),
    _text("hpp", "Nagłówek C++ (HPP)", GROUP_CODE),
    _text("cc", "C++ (CC)", GROUP_CODE),
    _text("cs", "C#", GROUP_CODE),
    _text("java", "Java", GROUP_CODE),
    _text("kt", "Kotlin", GROUP_CODE),
    _text("scala", "Scala", GROUP_CODE),
    _text("js", "JavaScript (JS)", GROUP_CODE),
    _text("ts", "TypeScript (TS)", GROUP_CODE),
    _text("rs", "Rust", GROUP_CODE),
    _text("go", "Go", GROUP_CODE),
    _text("jl", "Julia", GROUP_CODE),
    _text("r", "R", GROUP_CODE),
    _text("m", "MATLAB/Octave (M)", GROUP_CODE),
    _text("f90", "Fortran (F90)", GROUP_CODE),
    _text("hs", "Haskell", GROUP_CODE),
    _text("rb", "Ruby", GROUP_CODE),
    _text("php", "PHP", GROUP_CODE),
    _text("lua", "Lua", GROUP_CODE),
    _text("swift", "Swift", GROUP_CODE),
    _text("qasm", "OpenQASM", GROUP_CODE),
    _text("qs", "Q#", GROUP_CODE),
    _text("sh", "Skrypt powłoki (SH)", GROUP_CODE),
    _text("sql", "SQL", GROUP_CODE),
    _text("html", "HTML jako kod", GROUP_CODE),
    _text("css", "CSS jako kod", GROUP_CODE),
    _text("svg", "SVG jako kod", GROUP_CODE),
]

#: Pliki – pobierane albo otwierane w przeglądarce. ``family`` mówi, co musi stać w bajtach.
FILE_FORMATS: dict[str, Format] = {
    **{fmt.key: fmt for fmt in _DOCUMENTS},
    **{fmt.key: fmt for fmt in _TEXT},
    **{fmt.key: fmt for fmt in _CODE},
    "ipynb": Format(
        "ipynb", "json", "application/x-ipynb+json", "Notatnik Jupyter (IPYNB)", group=GROUP_CODE
    ),
    "png": Format("png", "png", "image/png", "Obraz PNG", inline=True, group=GROUP_IMAGES),
    "jpg": Format("jpg", "jpg", "image/jpeg", "Obraz JPG", inline=True, group=GROUP_IMAGES),
    "zip": Format("zip", "zip", "application/zip", "Archiwum ZIP", group=GROUP_ARCHIVES),
}

#: Różne nazwy tego samego formatu – nazwa u pobierającego ma jedną postać.
EXTENSION_ALIASES = {"jpeg": "jpg", "markdown": "md", "htm": "html"}

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


def _strip_bom(header: bytes) -> bytes:
    return header[len(UTF8_BOM) :] if header.startswith(UTF8_BOM) else header


def _looks_like_json_object(header: bytes) -> bool:
    """Notatnik Jupyter to obiekt JSON: po (opcjonalnym) BOM i białych znakach stoi ``{``."""
    return _strip_bom(header).lstrip(b" \t\r\n").startswith(b"{")


def _few_controls(text: str, *, c1: bool = True) -> bool:
    """Najwyżej ``TEXT_CONTROL_MAX_RATIO`` znaków sterujących (C0 poza dozwolonymi, DEL i – dla
    Unicode – C1). W Windows-1250 bajty 0x80–0x9F to litery (Ś, Ź, „…”), więc tam C1 nie liczymy."""
    if not text:
        return True
    controls = sum(
        1
        for char in text
        if (ord(char) < 0x20 and char not in TEXT_ALLOWED_CONTROLS)
        or ord(char) == 0x7F
        or (c1 and 0x80 <= ord(char) < 0xA0)
    )
    return controls <= len(text) * TEXT_CONTROL_MAX_RATIO


def looks_like_text(header: bytes) -> bool:
    """Czy próbka to tekst UTF-8: bez NUL, poprawny UTF-8, mało znaków sterujących.

    Próbka jest **początkiem** pliku, więc ostatni znak bywa urwany w pół sekwencji – dekoder
    przyrostowy (``final=False``) zostawia taki ogon w buforze, zamiast zgłaszać błąd. BOM UTF-8
    (Notatnik Windows, Excel „CSV UTF-8”) jest dozwolony. Inne kodowania przyjmujemy wyłącznie dla
    danych z arkusza i notatek (``LEGACY_TEXT_EXTENSIONS``, ``detect_text_encoding``).
    """
    body = _strip_bom(header)
    if b"\x00" in body:
        return False
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(body, final=False)
    except UnicodeDecodeError:
        return False
    return _few_controls(text)


def detect_text_encoding(header: bytes, extension: str) -> str | None:
    """Kodowanie tekstu (``CHARSET_*``) albo ``None``, gdy próbka nie jest tekstem.

    Kod i Markdown – wyłącznie UTF-8. CSV, TSV i TXT (``LEGACY_TEXT_EXTENSIONS``) dodatkowo – decyzja
    organizatora z 9.10.2026 „przy wątpliwości przyjmij”: polski Excel zapisuje „CSV (rozdzielany
    przecinkami)” w Windows-1250, a Notatnik „Unicode” to UTF-16 z BOM-em. Kolejność:

    1. UTF-16 z BOM-em (``FF FE``/``FE FF``) – NUL-e są tu częścią znaków, więc sprawdzamy dopiero
       odkodowany tekst (poprawne pary zastępcze, mało znaków sterujących),
    2. UTF-8 (``looks_like_text``),
    3. tekst 8-bitowy: bez NUL i z najwyżej 5 % bajtów sterujących. Nazywamy go ``windows-1250`` –
       najczęstszy przypadek w Polsce; ISO-8859-2 różni się kilkoma literami i podgląd pokaże je
       niedokładnie, ale plik pobierze się bajt w bajt.
    """
    legacy = extension in LEGACY_TEXT_EXTENSIONS
    if legacy and header[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        try:
            text = codecs.getincrementaldecoder("utf-16")().decode(header, final=False)
        except UnicodeDecodeError:
            return None
        return CHARSET_UTF16 if _few_controls(text) else None
    if looks_like_text(header):
        return CHARSET_UTF8
    if legacy and b"\x00" not in header and _few_controls(header.decode("latin-1"), c1=False):
        return CHARSET_WINDOWS_1250
    return None


def detect_family(header: bytes) -> str | None:
    """Rodzina pliku po **sygnaturze**: ``mp4``/``webm``/``pdf``/``zip``/``cfb``/``png``/``jpg``/``rtf``/…

    Kolejne sprawdzenia są rozłączne (sygnatury nie nachodzą na siebie), więc kolejność ma
    znaczenie wyłącznie dla ``rtf`` i ``json`` – oba zaczynają się od ``{``, a ``json`` jest
    najsłabszy i sprawdzany na końcu. Rodziny ``text`` tu nie ma: tekst nie ma sygnatury i jest
    sprawdzany wyłącznie wtedy, gdy rozszerzenie go zapowiada (``content_matches``).
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
    if header.startswith(CFB_MAGIC):
        return "cfb"
    if header.startswith(PNG_MAGIC):
        return "png"
    if header.startswith(JPEG_MAGIC):
        return "jpg"
    if header.startswith(RTF_MAGIC):
        return "rtf"
    if _looks_like_json_object(header):
        return "json"
    return None


def content_matches(family: str, header: bytes, extension: str = "") -> bool:
    """Czy treść pasuje do rodziny zapowiedzianej rozszerzeniem.

    ``json`` (notatnik) i ``text`` mają własne, słabsze reguły i sprawdzamy je **wprost** – tak
    notatnik zaczynający się przypadkiem od ``{\\rtf`` przechodzi dokładnie jak przed WM-FMT-01,
    a nowe rodziny niczego, co dotąd było przyjmowane, nie zawężają.
    """
    if family == "json":
        return _looks_like_json_object(header)
    if family == "text":
        return detect_text_encoding(header, extension) is not None
    return detect_family(header) == family


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
    if content_matches(fmt.family, header, extension):
        return fmt
    if fmt.family == "text" and extension in LEGACY_TEXT_EXTENSIONS:
        raise FormatError(
            f"Plik ma rozszerzenie „.{extension}”, ale nie jest plikiem tekstowym (zawiera bajty binarne "
            "albo zbyt wiele znaków sterujących). Wyeksportuj dane ponownie jako tekst, np. w Excelu "
            "jako „CSV UTF-8 (rozdzielany przecinkami)”."
        )
    if fmt.family == "text":
        raise FormatError(
            f"Plik ma rozszerzenie „.{extension}”, ale nie jest plikiem tekstowym w kodowaniu UTF-8 "
            "(zawiera bajty binarne albo znaki w innym kodowaniu, np. Windows-1250 lub UTF-16). "
            "Zapisz go ponownie jako tekst UTF-8 – w edytorze kodu „Zapisz z kodowaniem → UTF-8”, "
            "w Excelu typ pliku „CSV UTF-8 (rozdzielany przecinkami)”."
        )
    raise FormatError(
        f"Plik ma rozszerzenie „.{extension}”, ale jego treść nie jest plikiem {fmt.label}. "
        "Rozpoznajemy format po zawartości – zapisz plik ponownie we właściwym formacie."
    )


def allowed_file_extensions() -> str:
    """Rozszerzenia plików pogrupowane: „dokumenty…: pdf, docx, …; tekst i dane: md, …; …”."""
    groups: dict[str, list[str]] = {group: [] for group in GROUP_ORDER}
    for key, fmt in FILE_FORMATS.items():
        groups.setdefault(fmt.group, []).append(key)
    return "; ".join(f"{group}: {', '.join(keys)}" for group, keys in groups.items() if keys)


def file_accept() -> str:
    """Wartość ``accept`` okna wyboru pliku: każde rozszerzenie z listy i każdy alias."""
    extensions = list(FILE_FORMATS) + [
        alias for alias, key in EXTENSION_ALIASES.items() if key in FILE_FORMATS
    ]
    return ",".join(f".{ext}" for ext in extensions)


def allowed_video_extensions() -> str:
    return ", ".join(sorted(VIDEO_FORMATS))
