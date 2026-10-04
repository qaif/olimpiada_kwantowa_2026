"""Skład jednej linii tekstu w dowolnym piśmie na stronie PDF (MED-01 § 2.1).

Dlaczego osobny moduł, a nie poprawka ``apps.results.certificates._text``. Tamten skład pisze
jednym krojem (DejaVu) i od lewej do prawej – i dla Olimpiady Kwantowej ma tak zostać co do bajtu.
Dyplom olimpiady międzynarodowej musi tymczasem złożyć **arabski** (od prawej do lewej, z łączeniem
liter), **dewanagari i bengalski** (ligatury spółgłoskowe, samogłoski przestawiane przed spółgłoskę)
oraz **chiński** (krój z tysiącami znaków) – a do tego nazwisko w innym piśmie niż język dokumentu
(Rosjanin z dyplomem po angielsku, Egipcjanin z nazwiskiem zapisanym łacinką na dyplomie arabskim).

Trzy kroki, każdy możliwie mały:

1. **Kierunek** – własny, mały algorytm dwukierunkowy (podzbiór UBA, Unicode Standard Annex #9) na
   poziomie jednej linii: klasy ``unicodedata.bidirectional``, liczby po piśmie arabskim jako
   osadzenie LTR, neutralne według reguł N1/N2 i odwracanie przebiegów według L2. Pełny UBA (z
   osadzeniami jawnymi i nawiasami parowanymi) nie jest tu potrzebny: linia dyplomu nie zawiera
   znaków sterujących kierunkiem. ReportLab ma własną obsługę kierunku, ale wymaga pakietu
   ``rlbidi``, którego nie ma w PyPI.
2. **Krój per znak** – pismo znaku wybiera krój (arabski, dewanagari, bengalski, CJK), a znaki
   wspólne (cyfry, spacje, interpunkcja) dziedziczą krój sąsiada, o ile ten ma glif. Znak bez
   żadnego kroju staje się ``?`` i trafia do ``missing`` – wołający decyduje, co z tym zrobić.
3. **Kształtowanie** – ``reportlab.pdfbase.ttfonts.shapeStr`` (HarfBuzz przez ``uharfbuzz``) dla
   pism, które go wymagają. Bez ``uharfbuzz`` te pisma są **niedostępne** (``language_support``),
   a nie składane po cichu w postaci, której nikt nie przeczyta.

Kroje są w repozytorium (``fonts/``, licencje i źródła w ``fonts/SOURCES.txt``) i osadzane w PDF-ie
jako podzbiory. Rejestracja jest leniwa i idempotentna – globalna na proces, jak w module dyplomów.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent / "fonts"
#: DejaVu jest już w repozytorium dla dotychczasowych dyplomów – nie kopiujemy go drugi raz.
DEJAVU_DIR = Path(settings.BASE_DIR) / "static" / "fonts"

LATIN = "latin"
ARABIC = "arabic"
DEVANAGARI = "devanagari"
BENGALI = "bengali"
CJK = "cjk"

#: Znak zastępczy dla znaku, którego nie ma w żadnym kroju. Zwykły pytajnik, a nie U+FFFD: ma się
#: dać przeczytać na wydruku i przepisać ręcznie, a romb z pytajnikiem bywa w krojach różny.
REPLACEMENT = "?"

#: Stopień obrysu przy symulowanym pogrubieniu (krój CJK nie ma odmiany bold) – ułamek stopnia pisma.
FAKE_BOLD_STROKE = 0.035


@dataclass(frozen=True)
class Face:
    """Jeden krój: nazwy rejestracji, pliki i to, czy pismo wymaga kształtowania."""

    script: str
    regular: str
    regular_path: Path
    bold: str = ""
    bold_path: Path | None = None
    needs_shaping: bool = False


#: Kolejność jest kolejnością odwrotu przy szukaniu glifu dla znaku wspólnego.
FACES: tuple[Face, ...] = (
    Face(
        LATIN,
        "MedDejaVuSans",
        DEJAVU_DIR / "DejaVuSans.ttf",
        "MedDejaVuSans-Bold",
        DEJAVU_DIR / "DejaVuSans-Bold.ttf",
    ),
    Face(
        ARABIC,
        "MedNotoSansArabic",
        FONT_DIR / "NotoSansArabic-Regular.ttf",
        "MedNotoSansArabic-Bold",
        FONT_DIR / "NotoSansArabic-Bold.ttf",
        needs_shaping=True,
    ),
    Face(
        DEVANAGARI,
        "MedNotoSansDevanagari",
        FONT_DIR / "NotoSansDevanagari-Regular.ttf",
        "MedNotoSansDevanagari-Bold",
        FONT_DIR / "NotoSansDevanagari-Bold.ttf",
        needs_shaping=True,
    ),
    Face(
        BENGALI,
        "MedNotoSansBengali",
        FONT_DIR / "NotoSansBengali-Regular.ttf",
        "MedNotoSansBengali-Bold",
        FONT_DIR / "NotoSansBengali-Bold.ttf",
        needs_shaping=True,
    ),
    # Bez odmiany bold – patrz ``fonts/SOURCES.txt``; pogrubienie symuluje obrys (``FAKE_BOLD_STROKE``).
    Face(CJK, "MedDroidSansFallback", FONT_DIR / "DroidSansFallbackFull.ttf"),
)
FACES_BY_SCRIPT = {face.script: face for face in FACES}

#: Pismo każdego języka interfejsu (``settings.LANGUAGES``). Rosyjski jest „łaciński” w sensie
#: kroju: cyrylicę składa ten sam DejaVu Sans.
LANGUAGE_SCRIPTS: dict[str, str] = {
    "pl": LATIN,
    "en": LATIN,
    "es": LATIN,
    "fr": LATIN,
    "pt": LATIN,
    "id": LATIN,
    "ru": LATIN,
    "ar": ARABIC,
    "hi": DEVANAGARI,
    "bn": BENGALI,
    "zh-hans": CJK,
}

#: Język odwrotu, gdy pisma języka ucznia nie da się złożyć: bazowy język olimpiady międzynarodowej.
FALLBACK_LANGUAGE = "en"

#: Zakresy kodowe pism, które mają własny krój. Wszystko spoza nich składa DejaVu (łacina, cyrylica,
#: greka, hebrajski) albo – dla znaków wspólnych – krój sąsiada.
_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0600, 0x06FF, ARABIC),
    (0x0750, 0x077F, ARABIC),
    (0x0870, 0x08FF, ARABIC),
    (0xFB50, 0xFDFF, ARABIC),
    (0xFE70, 0xFEFF, ARABIC),
    (0x0900, 0x097F, DEVANAGARI),
    (0xA8E0, 0xA8FF, DEVANAGARI),
    (0x0980, 0x09FF, BENGALI),
    (0x2E80, 0x2FDF, CJK),
    (0x3000, 0x9FFF, CJK),
    (0xAC00, 0xD7AF, CJK),
    (0xF900, 0xFAFF, CJK),
    (0xFF00, 0xFFEF, CJK),
    (0x20000, 0x2FFFF, CJK),
)

#: Lustrzane odbicia nawiasów w przebiegu od prawej do lewej, który odwracamy sami (bez HarfBuzza).
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def script_of(char: str) -> str | None:
    """Pismo znaku z własnym krojem albo ``None`` (łacina, cyrylica i znaki wspólne)."""
    code = ord(char)
    for low, high, script in _RANGES:
        if low <= code <= high:
            return script
    return None


# --- kroje -------------------------------------------------------------------------------------


def shaping_available() -> bool:
    """Czy ReportLab ma HarfBuzza. Bez niego arabski, dewanagari i bengalski są nieczytelne."""
    from reportlab.pdfbase import ttfonts

    return getattr(ttfonts, "uharfbuzz", None) is not None


@cache
def _register(name: str, path_text: str) -> bool:
    """Rejestruje jeden plik kroju w ReportLabie. ``False``, gdy pliku nie ma albo jest uszkodzony.

    Brak pliku nie wywraca dokumentu: pismo staje się „niedostępne”, dokument wychodzi w języku
    odwrotu, a ślad zostaje w logu (``language_support`` mówi o tym także na ekranie medali).
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    if name in pdfmetrics.getRegisteredFontNames():
        return True
    path = Path(path_text)
    if not path.exists():
        logger.warning("Brak kroju %s (%s) – pismo nie będzie składane.", name, path)
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, str(path), shapable=True))
    except TTFError, OSError, ValueError:
        logger.warning("Nie udało się zarejestrować kroju %s (%s).", name, path)
        return False
    return True


def face_available(face: Face) -> bool:
    return _register(face.regular, str(face.regular_path))


def _font_name(face: Face, bold: bool) -> tuple[str, bool]:
    """Nazwa zarejestrowanego kroju i to, czy pogrubienie trzeba symulować."""
    if bold and face.bold and face.bold_path is not None and _register(face.bold, str(face.bold_path)):
        return face.bold, False
    return face.regular, bold


@cache
def _coverage(name: str) -> frozenset[int]:
    from reportlab.pdfbase import pdfmetrics

    return frozenset(pdfmetrics.getFont(name).face.charToGlyph)


def _has_glyph(face: Face, char: str) -> bool:
    return face_available(face) and ord(char) in _coverage(face.regular)


@dataclass(frozen=True)
class LanguageSupport:
    """Czy dokument w tym języku da się złożyć – i jeśli nie, dlaczego."""

    language: str
    script: str
    ok: bool
    reason: str = ""


def language_support(language: str) -> LanguageSupport:
    """Stan potoku dla jednego języka: krój obecny, a dla pism złożonych – także HarfBuzz."""
    script = LANGUAGE_SCRIPTS.get(language, LATIN)
    face = FACES_BY_SCRIPT[script]
    if not face_available(face):
        return LanguageSupport(language, script, False, f"brak kroju {face.regular_path.name}")
    if face.needs_shaping and not shaping_available():
        return LanguageSupport(language, script, False, "brak modułu uharfbuzz (kształtowanie pisma)")
    return LanguageSupport(language, script, True)


def renderable_language(language: str) -> str:
    """Język, w którym dokument naprawdę wyjdzie: żądany albo – gdy pisma brak – język odwrotu."""
    support = language_support(language)
    if support.ok:
        return language
    logger.warning(
        "Dokument w języku %s składany po angielsku: %s.", language, support.reason or "pismo niedostępne"
    )
    return FALLBACK_LANGUAGE


# --- kierunek ----------------------------------------------------------------------------------


def _bidi_type(char: str) -> str:
    """Klasa znaku sprowadzona do kilku: ``L``, ``R``, ``N`` (liczba), ``S`` (separator liczb),
    ``T`` (znak przy liczbie: %, $, °), ``M`` (łączący) i ``O`` (pozostałe neutralne).

    LRM i RLM (niewidoczne znaki kierunku) mają w Unicode własne klasy, a działają jak silne L i R –
    i tak je traktujemy; dokument wstawia LRM za adresem URL w akapicie RTL.
    """
    if char == "‎":
        return "L"
    if char == "‏":
        return "R"
    kind = unicodedata.bidirectional(char)
    if kind in ("R", "AL"):
        return "R"
    if kind == "L":
        return "L"
    if kind in ("EN", "AN"):
        return "N"
    if kind in ("CS", "ES"):
        return "S"
    if kind == "ET":
        return "T"
    if kind == "NSM":
        return "M"
    return "O"


def base_is_rtl(text: str, default_rtl: bool = False) -> bool:
    """Kierunek akapitu (reguły P2/P3): pierwszy znak silny; bez silnego – podpowiedź wołającego."""
    for char in text:
        kind = _bidi_type(char)
        if kind == "R":
            return True
        if kind == "L":
            return False
    return default_rtl


def bidi_levels(text: str, *, rtl: bool) -> list[int]:
    """Poziom osadzenia każdego znaku linii (reguły W, N1/N2 i I1/I2 dla jednego akapitu).

    Liczba po piśmie od prawej do lewej (albo na początku akapitu RTL) jest osadzeniem LTR wewnątrz
    RTL – „٢٠٢٦” i „2026” w zdaniu arabskim czyta się od lewej. Liczba po łacinie jest po prostu
    łaciną (W7). Neutralny między dwoma silnymi tego samego kierunku przyjmuje ten kierunek (liczby
    liczą się przy tym jak ``R``, jeśli stoją w kontekście RTL), w pozostałych przypadkach – kierunek
    akapitu (N2).
    """
    base = 1 if rtl else 0
    kinds = [_bidi_type(char) for char in text]
    # W1: znak łączący przejmuje klasę poprzednika (na początku – kierunek akapitu).
    for index, kind in enumerate(kinds):
        if kind == "M":
            kinds[index] = kinds[index - 1] if index else ("R" if rtl else "L")
    # W4: pojedynczy separator między dwiema liczbami jest częścią liczby („04.10.2026”, „2,5”).
    for index in range(1, len(kinds) - 1):
        if kinds[index] == "S" and kinds[index - 1] == "N" and kinds[index + 1] == "N":
            kinds[index] = "N"
    # W5: znaki przy liczbie („50 %”, „$10”) przyklejają się do niej.
    for index, kind in enumerate(list(kinds)):
        if kind != "N":
            continue
        for step in (-1, 1):
            position = index + step
            while 0 <= position < len(kinds) and kinds[position] == "T":
                kinds[position] = "N"
                position += step
    # W6: pozostałe separatory i znaki przy liczbach są zwykłymi neutralnymi.
    kinds = ["O" if kind in ("S", "T") else kind for kind in kinds]
    # W2/W7: liczba w kontekście R zostaje liczbą („E” – osadzona), w kontekście L staje się L.
    previous_strong = "R" if rtl else "L"
    for index, kind in enumerate(kinds):
        if kind in ("L", "R"):
            previous_strong = kind
        elif kind == "N":
            kinds[index] = "E" if previous_strong == "R" else "L"
    _resolve_brackets(text, kinds, rtl=rtl)
    # N1/N2: ciągi neutralnych.
    resolved = list(kinds)
    index = 0
    while index < len(kinds):
        if kinds[index] != "O":
            index += 1
            continue
        end = index
        while end < len(kinds) and kinds[end] == "O":
            end += 1
        before = _strong_direction(kinds[index - 1]) if index > 0 else ("R" if rtl else "L")
        after = _strong_direction(kinds[end]) if end < len(kinds) else ("R" if rtl else "L")
        direction = before if before == after else ("R" if rtl else "L")
        for position in range(index, end):
            resolved[position] = direction
        index = end
    levels = []
    for kind in resolved:
        if kind == "R":
            levels.append(base if base % 2 else base + 1)
        elif kind == "E":
            levels.append(2)
        else:  # L
            levels.append(base + 1 if base % 2 else base)
    return levels


#: Pary nawiasów rozstrzygane regułą N0 (uproszczoną – bez kanonicznych odpowiedników).
_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _resolve_brackets(text: str, kinds: list[str], *, rtl: bool) -> None:
    """Reguła N0 w uproszczeniu: para nawiasów dostaje kierunek treści, którą obejmuje.

    Bez niej „الطالب: Jan Kowalski (Polska)” wychodzi jako „(Jan Kowalski (Polska :الطالب” –
    zamykający nawias na końcu akapitu RTL jest neutralnym przed końcem linii, więc N2 dałoby mu
    kierunek akapitu. Kraj w nawiasie przy nazwisku to dokładnie ten przypadek na dyplomie.
    """
    embedding = "R" if rtl else "L"
    opposite = "L" if rtl else "R"
    stack: list[tuple[str, int]] = []
    for index, char in enumerate(text):
        if char in _BRACKETS:
            stack.append((_BRACKETS[char], index))
        elif stack and char == stack[-1][0]:
            _closing, start = stack.pop()
            inside = {_strong_direction(kind) for kind in kinds[start + 1 : index] if kind in ("L", "R", "E")}
            if embedding in inside:
                direction = embedding
            elif opposite in inside:
                before = next(
                    (_strong_direction(kind) for kind in reversed(kinds[:start]) if kind in ("L", "R", "E")),
                    embedding,
                )
                direction = opposite if before == opposite else embedding
            else:
                continue
            kinds[start] = kinds[index] = direction


def _strong_direction(kind: str) -> str:
    """Kierunek, jakim znak wpływa na sąsiednie neutralne (liczby osadzone liczą się jak ``R``)."""
    return "R" if kind in ("R", "E") else "L"


def reorder(runs: list, levels: list[int]) -> list:
    """Reguła L2 na przebiegach: od najwyższego poziomu do najniższego nieparzystego odwracaj ciągi."""
    order = list(range(len(runs)))
    if not levels:
        return []
    highest = max(levels)
    lowest_odd = min((level for level in levels if level % 2), default=None)
    if lowest_odd is None:
        return list(runs)
    for level in range(highest, lowest_odd - 1, -1):
        index = 0
        while index < len(order):
            if levels[order[index]] >= level:
                end = index
                while end < len(order) and levels[order[end]] >= level:
                    end += 1
                order[index:end] = reversed(order[index:end])
                index = end
            else:
                index += 1
    return [runs[position] for position in order]


# --- linia -------------------------------------------------------------------------------------


@dataclass
class Run:
    """Kawałek linii jednym krojem i jednym kierunkiem – gotowy do narysowania."""

    font: str
    text: object  # ``str`` albo ``reportlab.pdfbase.ttfonts.ShapedStr``
    width: float
    fake_bold: bool = False


@dataclass
class Line:
    """Linia po składzie: przebiegi w kolejności **wizualnej**, szerokość i znaki bez kroju."""

    runs: list[Run] = field(default_factory=list)
    width: float = 0.0
    size: float = 12.0
    missing: tuple[str, ...] = ()


#: Znaki formatujące potrzebne kształtowaniu (ZWNJ, ZWJ) – tych nie usuwamy z linii.
_SHAPING_FORMAT_CHARS = frozenset({"‌", "‍"})


def _invisible(char: str) -> bool:
    return unicodedata.category(char) == "Cf" and char not in _SHAPING_FORMAT_CHARS


def _face_for(char: str, previous: Face | None) -> Face | None:
    """Krój dla znaku: własne pismo, potem krój sąsiada (znaki wspólne), potem pierwszy z glifem."""
    script = script_of(char)
    if script is not None:
        face = FACES_BY_SCRIPT[script]
        if _has_glyph(face, char):
            return face
    if previous is not None and _has_glyph(previous, char):
        return previous
    for face in FACES:
        if _has_glyph(face, char):
            return face
    return None


def _shape(text: str, font: str, size: float, *, rtl: bool, needs_shaping: bool):
    """Tekst przebiegu gotowy do ``textOut`` i jego szerokość w punktach."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import ShapedStr, shapeStr

    hb_handles_direction = rtl and any(script_of(char) == ARABIC for char in text)
    if rtl and not hb_handles_direction:
        # Przebieg od prawej do lewej bez pisma arabskiego (hebrajski w DejaVu, sama interpunkcja):
        # HarfBuzz zgadłby kierunek LTR, więc odwracamy sami i odbijamy nawiasy.
        text = text[::-1].translate(_MIRROR)
    if needs_shaping and shaping_available():
        shaped = shapeStr(text, font, size)
        if isinstance(shaped, ShapedStr):
            return shaped, sum(item.x_advance for item in shaped.__shapeData__) * size / 1000
        text = str(shaped)
    return text, pdfmetrics.stringWidth(text, font, size)


def layout_line(text: str, *, size: float, bold: bool = False, rtl_hint: bool = False) -> Line:
    """Składa jedną linię: kierunek, krój per znak, kształtowanie. Nie rysuje niczego."""
    text = unicodedata.normalize("NFC", (text or "").replace("\n", " ").strip())
    line = Line(size=size)
    if not text:
        return line
    rtl = base_is_rtl(text, rtl_hint)
    levels = bidi_levels(text, rtl=rtl)
    # Znaki formatujące (LRM, RLM…) rozstrzygają kierunek, ale nie są rysowane – żaden krój nie ma
    # dla nich glifu i bez tego stałyby się pytajnikami. ZWJ/ZWNJ zostają: sterują ligaturami
    # dewanagari i bengalskimi, a kroje Noto je mają.
    kept = [index for index, char in enumerate(text) if not _invisible(char)]
    if len(kept) != len(text):
        text = "".join(text[index] for index in kept)
        levels = [levels[index] for index in kept]
    missing: list[str] = []
    chars: list[tuple[str, Face]] = []
    previous: Face | None = None
    latin = FACES_BY_SCRIPT[LATIN]
    for char in text:
        face = _face_for(char, previous)
        if face is None:
            missing.append(char)
            char, face = REPLACEMENT, latin
        chars.append((char, face))
        previous = face
    # Przebiegi: ciągi znaków o tym samym poziomie i tym samym kroju.
    pieces: list[tuple[str, Face, int]] = []
    for (char, face), level in zip(chars, levels, strict=True):
        if pieces and pieces[-1][1] is face and pieces[-1][2] == level:
            pieces[-1] = (pieces[-1][0] + char, face, level)
        else:
            pieces.append((char, face, level))
    ordered = reorder(pieces, [piece[2] for piece in pieces])
    for chunk, face, level in ordered:
        font, fake_bold = _font_name(face, bold)
        shaped, width = _shape(chunk, font, size, rtl=bool(level % 2), needs_shaping=face.needs_shaping)
        line.runs.append(Run(font=font, text=shaped, width=width, fake_bold=fake_bold))
        line.width += width
    line.missing = tuple(missing)
    if missing:
        logger.warning(
            "Znaki bez kroju w dokumencie: %s – zastąpione znakiem „%s”.", "".join(missing), REPLACEMENT
        )
    return line


def fitted_line(
    text: str, *, size: float, max_width: float, bold: bool = False, rtl_hint: bool = False
) -> Line:
    """Linia zmniejszana do ``max_width`` (nie poniżej 60 % stopnia) – długie nazwisko się mieści."""
    line = layout_line(text, size=size, bold=bold, rtl_hint=rtl_hint)
    if line.width <= max_width or line.width == 0:
        return line
    smaller = max(size * 0.6, size * max_width / line.width)
    return layout_line(text, size=smaller, bold=bold, rtl_hint=rtl_hint)


def draw_line(canvas, line: Line, *, x: float, y: float, align: str = "center") -> None:
    """Rysuje złożoną linię. ``align``: ``center`` (``x`` = środek), ``left`` albo ``right``."""
    if not line.runs:
        return
    if align == "center":
        cursor = x - line.width / 2
    elif align == "right":
        cursor = x - line.width
    else:
        cursor = x
    for run in line.runs:
        text_object = canvas.beginText(cursor, y)
        text_object.setFont(run.font, line.size)
        if run.fake_bold:
            canvas.saveState()
            canvas.setLineWidth(line.size * FAKE_BOLD_STROKE)
            text_object.setTextRenderMode(2)
        text_object.textOut(run.text)
        canvas.drawText(text_object)
        if run.fake_bold:
            canvas.restoreState()
        cursor += run.width


def draw_text(
    canvas,
    text: str,
    *,
    x: float,
    y: float,
    size: float,
    bold: bool = False,
    align: str = "center",
    rtl_hint: bool = False,
    max_width: float | None = None,
) -> Line:
    """Skład i rysowanie w jednym kroku. Zwraca linię – wołający czyta z niej ``missing``."""
    if max_width:
        line = fitted_line(text, size=size, max_width=max_width, bold=bold, rtl_hint=rtl_hint)
    else:
        line = layout_line(text, size=size, bold=bold, rtl_hint=rtl_hint)
    draw_line(canvas, line, x=x, y=y, align=align)
    return line
