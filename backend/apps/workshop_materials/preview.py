"""Podgląd pliku tekstowego na stronie materiału: Markdown złożony bezpiecznie, kod i tekst w ``<pre>``.

**Kto go widzi.** Podgląd powstaje w tym samym widoku, co strona materiału
(``apps.web.views.workshop_materials``), czyli za tymi samymi bramkami, co pobranie: przełącznik,
logowanie, rola w konkursie (``access.can_view``) i materiał opublikowany oraz gotowy (po skanie
ClamAV-em). Koordynator ma swój podgląd także dla szkicu (``CoordinatorMaterialPreviewView``).
Treść czyta **serwer** z magazynu; przeglądarka nie dostaje do podglądu żadnego adresu pliku.

**Jak jest zabezpieczony.**

- Markdown składa ``apps.problem_translations.markup.render`` (TR-01) – konwerter, który **najpierw**
  ucieka cały tekst (``html.escape``), a dopiero potem dokłada znaczniki z zamkniętej listy. Surowy
  HTML z pliku (``<script>``, ``<img onerror=…>``) zostaje widocznym tekstem, a odnośników i obrazów
  konwerter nie tworzy wcale – więc ``javascript:`` nie ma atrybutu, do którego mógłby trafić.
  Formuły ``$…$`` rysuje KaTeX z plików statycznych TR-01 (``trust: false``). Biblioteki Markdown
  (``markdown``, ``markdown-it-py``) przepuszczają surowy HTML i wymagałyby drugiej biblioteki do
  czyszczenia – a ta sama treść ma tu tę samą naturę, co tłumaczenie zadania: tekst od jednej osoby
  pokazywany innym.
- Kod i zwykły tekst (także ``html`` i ``svg``) idą do szablonu jako **napis** – ``<pre><code>``
  z autoescape Django. Podświetlania składni nie ma: Pygments jest w środowisku tylko jako zależność
  pytesta (ekstra ``dev``), a podgląd nie jest wart nowej zależności produkcyjnej.

**Limit** (``preview_max_bytes``, domyślnie 1 MB): większy plik ma wyłącznie pobranie. Limit chroni
wątek gunicorna i pamięć – plik jest czytany w całości jednym żądaniem do magazynu, a strona
z kilkoma megabajtami ``<pre>`` jest nieużywalna także dla czytelnika.

**Budżet składania Markdowna** (``MARKDOWN_COST_BUDGET``). Wyrażenia konwertera TR-01 są leniwe
(``**(.+?)**``, bloki kodu i formuły ``\\[…\\]`` z ``DOTALL``), więc **niedomknięty** znacznik
skanuje tekst do końca wiersza albo pliku – a tysiące takich znaczników to koszt kwadratowy: wiersz
200 KB z „**a ” powtórzonym 50 000 razy składa się ponad dwie minuty (zmierzone 9.10.2026). Strona
materiału jest otwierana przez każdego uczestnika, więc taki plik zająłby wątek gunicorna przy każdym
wyświetleniu. ``markdown_cost`` szacuje z góry (liniowo) pracę tych wyrażeń; plik ponad budżet jest
pokazywany jako zwykły tekst w ``<pre>`` – nadal czytelny, tylko bez składu. Budżet 5·10⁷ to ok.
0,35 s najgorszego przypadku (ok. 7 ns na jednostkę na laptopie deweloperskim); zwykły plik Markdown
– także megabajtowy – ma koszt rzędu jego długości.

Treść poza próbką sprawdzoną przy wgrywaniu (``formats.HEADER_PROBE_BYTES``) nie musi być poprawnym
UTF-8 – niepoprawne bajty zamieniamy na „�” (``errors="replace"``), a NUL wycinamy.
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.utils.safestring import SafeString

from apps.problem_translations.markup import render as render_markdown

from . import formats
from .models import MaterialKind, WorkshopMaterial
from .storage import MaterialStorage

logger = logging.getLogger(__name__)

#: Domyślny limit podglądu (KB) – nadpisywalny ustawieniem ``WORKSHOP_PREVIEW_MAX_KB``.
DEFAULT_PREVIEW_MAX_KB = 1024

#: Stany podglądu w szablonie.
SHOWN_MARKDOWN = formats.PREVIEW_MARKDOWN
SHOWN_TEXT = formats.PREVIEW_TEXT
TOO_LARGE = "too_large"
UNAVAILABLE = "unavailable"


#: Górna granica szacowanej pracy konwertera Markdown – patrz docstring modułu.
MARKDOWN_COST_BUDGET = 50_000_000

#: Początki i końce bloków, które konwerter szuka leniwie przez cały tekst (``DOTALL``).
_FENCE_OPEN = re.compile(r"^```", re.MULTILINE)
_FENCE_CLOSE = re.compile(r"^```[ \t]*$", re.MULTILINE)


def _span_cost(text: str, openers: list[int], closers: list[int]) -> int:
    """Suma odległości od każdego otwarcia do najbliższego zamknięcia za nim (albo do końca tekstu)."""
    cost = 0
    for start in openers:
        index = bisect.bisect_right(closers, start)
        end = closers[index] if index < len(closers) else len(text)
        cost += end - start
    return cost


def _positions(text: str, token: str) -> list[int]:
    return [match.start() for match in re.finditer(re.escape(token), text)]


def markdown_cost(text: str) -> int:
    r"""Szacunek z góry pracy ``markup.render`` – liniowy w długości tekstu.

    - pogrubienie, kursywa i ``\(…\)`` działają w obrębie wiersza: każdy ``*`` i ``\(``
      może przeskanować resztę wiersza → liczba takich znaków razy długość wiersza,
    - bloki kodu, ``$$…$$`` i ``\[…\]`` działają na całym tekście: każde otwarcie skanuje do
      najbliższego zamknięcia za nim, a bez zamknięcia – do końca pliku.
    """
    cost = 0
    for line in text.split("\n"):
        risky = line.count("*") + line.count("\\(")
        if risky:
            cost += risky * len(line)
    fence_close = [match.start() for match in _FENCE_CLOSE.finditer(text)]
    cost += _span_cost(text, [match.start() for match in _FENCE_OPEN.finditer(text)], fence_close)
    dollars = _positions(text, "$$")
    cost += _span_cost(text, dollars, dollars)
    cost += _span_cost(text, _positions(text, "\\["), _positions(text, "\\]"))
    return cost


def preview_max_bytes() -> int:
    return int(getattr(settings, "WORKSHOP_PREVIEW_MAX_KB", DEFAULT_PREVIEW_MAX_KB)) * 1024


@dataclass(frozen=True)
class Preview:
    """Co pokazać pod przyciskiem „Pobierz”: złożony Markdown, tekst albo zdanie, czemu nic."""

    kind: str
    html: SafeString | str = ""
    text: str = ""
    limit_bytes: int = 0
    #: Markdown pokazany jako zwykły tekst, bo przekroczył ``MARKDOWN_COST_BUDGET``.
    plain_fallback: bool = False

    @property
    def shown(self) -> bool:
        """Czy czytelnik zobaczył treść – wtedy podgląd liczy się jako wyświetlenie."""
        return self.kind in (SHOWN_MARKDOWN, SHOWN_TEXT)


def previewable(material: WorkshopMaterial) -> bool:
    fmt = material.format
    return material.kind == MaterialKind.FILE and fmt is not None and bool(fmt.preview)


def decode(raw: bytes) -> str:
    """Bajty pliku → tekst: bez BOM-u, z „�” w miejscu niepoprawnego UTF-8 i bez NUL-i."""
    if raw.startswith(formats.UTF8_BOM):
        raw = raw[len(formats.UTF8_BOM) :]
    return raw.decode("utf-8", errors="replace").replace("\x00", "")


def build_preview(material: WorkshopMaterial, storage: MaterialStorage) -> Preview | None:
    """Podgląd materiału albo ``None``, gdy format go nie ma (PDF, Office, obraz, film, odnośnik)."""
    if not previewable(material):
        return None
    limit = preview_max_bytes()
    if material.size_bytes > limit:
        return Preview(TOO_LARGE, limit_bytes=limit)
    try:
        raw = storage.read_head(material.object_key, max(1, material.size_bytes))
    except Exception:  # noqa: BLE001 - awaria magazynu nie może wywrócić strony; zostaje „Pobierz”
        logger.warning("Nie udało się odczytać pliku materiału #%s do podglądu.", material.pk, exc_info=True)
        return Preview(UNAVAILABLE, limit_bytes=limit)
    text = decode(raw[:limit])
    if material.format.preview == formats.PREVIEW_MARKDOWN:
        if markdown_cost(text) <= MARKDOWN_COST_BUDGET:
            return Preview(SHOWN_MARKDOWN, html=render_markdown(text), limit_bytes=limit)
        return Preview(SHOWN_TEXT, text=text, limit_bytes=limit, plain_fallback=True)
    return Preview(SHOWN_TEXT, text=text, limit_bytes=limit)
