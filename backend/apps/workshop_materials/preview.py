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

**Budżet składania Markdowna** jest jeden dla wszystkich wywołujących konwerter TR-01 – mieszka
w ``markup.render_within_budget`` (szacunek ``markup.markdown_cost``, ustawienie
``MARKDOWN_COST_BUDGET``, TR-01b). Konwerter jest liniowy, a bramka zostaje bezpiecznikiem: plik
o kształcie „drogim” dla wyrażeń leniwych (np. 50 000 niedomkniętych „**” w jednym wierszu) jest
pokazywany jako zwykły tekst w ``<pre>`` z adnotacją (``plain_fallback``) – nadal czytelny, tylko
bez składu. Strona materiału jest otwierana przez każdego uczestnika, więc nie może zależeć od tego,
czy konwerter zawsze będzie tani.

Tekst jest dekodowany w kodowaniu rozpoznanym przy wgrywaniu (``WorkshopMaterial.charset``: UTF-8,
a dla CSV/TSV/TXT także UTF-16 i Windows-1250). Treść poza sprawdzoną wtedy próbką
(``formats.HEADER_PROBE_BYTES``) nie musi być poprawna – nieprawidłowe bajty zamieniamy na „�”
(``errors="replace"``), a NUL wycinamy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils.safestring import SafeString

from apps.problem_translations.markup import render_within_budget

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


def preview_max_bytes() -> int:
    return int(getattr(settings, "WORKSHOP_PREVIEW_MAX_KB", DEFAULT_PREVIEW_MAX_KB)) * 1024


@dataclass(frozen=True)
class Preview:
    """Co pokazać pod przyciskiem „Pobierz”: złożony Markdown, tekst albo zdanie, czemu nic."""

    kind: str
    html: SafeString | str = ""
    text: str = ""
    limit_bytes: int = 0
    #: Markdown pokazany jako zwykły tekst, bo przekroczył budżet kosztu (``markup.render_within_budget``).
    plain_fallback: bool = False

    @property
    def shown(self) -> bool:
        """Czy czytelnik zobaczył treść – wtedy podgląd liczy się jako wyświetlenie."""
        return self.kind in (SHOWN_MARKDOWN, SHOWN_TEXT)


def previewable(material: WorkshopMaterial) -> bool:
    fmt = material.format
    return material.kind == MaterialKind.FILE and fmt is not None and bool(fmt.preview)


#: Kodowanie zapisane przy wgrywaniu → dekoder Pythona. ``utf-16`` sam czyta BOM i kolejność bajtów.
_DECODERS = {
    formats.CHARSET_UTF8: "utf-8",
    formats.CHARSET_UTF16: "utf-16",
    formats.CHARSET_WINDOWS_1250: "cp1250",
}


def decode(raw: bytes, charset: str = "") -> str:
    """Bajty pliku → tekst w kodowaniu rozpoznanym przy wgrywaniu (pusty = UTF-8).

    Bez BOM-u, z „�” w miejscu bajtów nieprawidłowych dla kodowania i bez NUL-i.
    """
    codec = _DECODERS.get(charset, "utf-8")
    if codec == "utf-8" and raw.startswith(formats.UTF8_BOM):
        raw = raw[len(formats.UTF8_BOM) :]
    return raw.decode(codec, errors="replace").replace("\x00", "")


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
    text = decode(raw[:limit], material.charset)
    if material.format.preview == formats.PREVIEW_MARKDOWN:
        rendered = render_within_budget(text)
        if rendered is not None:
            return Preview(SHOWN_MARKDOWN, html=rendered, limit_bytes=limit)
        return Preview(SHOWN_TEXT, text=text, limit_bytes=limit, plain_fallback=True)
    return Preview(SHOWN_TEXT, text=text, limit_bytes=limit)
