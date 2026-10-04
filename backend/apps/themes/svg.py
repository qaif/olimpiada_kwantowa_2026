"""Oczyszczanie plików SVG z paczki motywu.

SVG jest dokumentem XML, w którym może siedzieć skrypt – osadzony przez ``<img>`` nie wykona go,
ale otwarty wprost z adresu bucketu (``Content-Disposition: attachment`` z
``apps.core.storage`` ogranicza i to) albo osadzony przez ``<object>`` już tak. Dlatego plik jest
**przepisywany**, a nie tylko sprawdzany: z drzewa wypadają elementy i atrybuty spoza listy
bezpiecznych, a raport dostaje ostrzeżenie z tym, co usunięto (THEME-01 § 2).

Usuwane: ``<script>``, ``<foreignObject>`` (wstawia HTML), elementy osadzające (``iframe``,
``embed``, ``object``, ``handler``, ``listener``), animacje SMIL (``set``/``animate*`` – motyw jest
statyczny, a ``<set attributeName="href" to="javascript:…">`` to klasyczna droga do skryptu),
atrybuty ``on*``, ``href``/``xlink:href`` inne niż ``#element``, ``style`` i ``<style>`` z ``url()``
spoza dokumentu. DOCTYPE i encje są **błędem** (bomba encji, XXE) – parser nawet ich nie zobaczy.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .css import sanitize_css, sanitize_declarations

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

REMOVED_ELEMENTS = frozenset(
    {
        "script",
        "foreignobject",
        "iframe",
        "embed",
        "object",
        "handler",
        "listener",
        "set",
        "animate",
        "animatemotion",
        "animatetransform",
        "animatecolor",
        "discard",
    }
)
DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
MAX_SVG_BYTES = 2 * 1024 * 1024


@dataclass
class SvgResult:
    data: bytes = b""
    errors: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def sanitize_svg(data: bytes) -> SvgResult:
    result = SvgResult()
    if len(data) > MAX_SVG_BYTES:
        result.errors.append(f"plik SVG większy niż {MAX_SVG_BYTES // 1024 // 1024} MB")
        return result
    if DOCTYPE.search(data):
        result.errors.append("SVG z deklaracją DOCTYPE/ENTITY jest niedozwolony")
        return result
    try:
        # ``defusedxml`` nie jest zależnością projektu; jego dwie ochrony są tu wprost: DOCTYPE
        # i ENTITY odrzuca kontrola wyżej (bomba encji, XXE), a expat nie pobiera zasobów.
        root = ET.fromstring(data)  # noqa: S314
    except ET.ParseError as exc:
        result.errors.append(f"niepoprawny XML ({exc})")
        return result
    if _local(root.tag) != "svg":
        result.errors.append("korzeniem pliku nie jest <svg>")
        return result

    def clean(element: ET.Element) -> None:
        for child in list(element):
            if not isinstance(child.tag, str):
                element.remove(child)
                continue
            name = _local(child.tag)
            if name in REMOVED_ELEMENTS:
                element.remove(child)
                result.removed.append(f"<{name}>")
                continue
            if name == "style":
                checked = sanitize_css(child.text or "", svg_mode=True)
                if checked.errors:
                    element.remove(child)
                    result.removed.append("<style> z niedozwoloną treścią")
                    continue
                child.text = checked.css
            clean(child)
        for attribute in list(element.attrib):
            local = _local(attribute)
            value = element.attrib[attribute]
            if local.startswith("on"):
                del element.attrib[attribute]
                result.removed.append(f"atrybut {local}")
            elif local == "href":
                if not value.strip().startswith("#"):
                    del element.attrib[attribute]
                    result.removed.append(f"odnośnik {value[:60]!r}")
            elif local == "style":
                checked = sanitize_declarations(value)
                if checked.errors:
                    del element.attrib[attribute]
                    result.removed.append("atrybut style z niedozwoloną treścią")
                else:
                    element.attrib[attribute] = checked.css
            elif "javascript:" in "".join(value.split()).lower():
                del element.attrib[attribute]
                result.removed.append(f"atrybut {local} ze skryptem")

    clean(root)
    result.data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return result
