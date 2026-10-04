"""Oczyszczanie plików SVG z paczki motywu – lista **dozwolona** elementów i atrybutów.

SVG jest dokumentem XML, w którym może siedzieć skrypt – osadzony przez ``<img>`` albo jako tło
w CSS nie wykona go, ale otwarty wprost z adresu bucketu (``Content-Disposition: attachment``
z ``apps.core.storage`` ogranicza i to) albo osadzony przez ``<object>`` już tak. Dlatego plik jest
**przepisywany**: zostają wyłącznie elementy grafiki z przestrzeni nazw SVG i ich atrybuty
prezentacyjne, a raport dostaje ostrzeżenie z tym, co usunięto (THEME-01 § 2).

Pierwsza wersja była listą zakazaną i przegląd (4.10.2026, L1) wskazał trzy obejścia: tekst po
elemencie-dziecku wewnątrz ``<style>``, elementy XHTML (``<meta>``, ``<form>``) w obcej przestrzeni
nazw i plik w UTF-16, na którym kontrola bajtowa ``<!DOCTYPE`` nie widziała niczego. Teraz:

- plik musi być UTF-8 (z BOM albo bez) – inne kodowanie to błąd, nie zgadywanie,
- DOCTYPE i ENTITY są **błędem** (bomba encji, XXE) – sprawdzane na tekście już zdekodowanym,
- element spoza listy albo spoza przestrzeni SVG wypada razem z poddrzewem,
- ``<style>`` z elementem w środku wypada w całości; treść stylu przechodzi przez parser CSS
  (``url()`` wyłącznie do ``#elementu`` tego dokumentu),
- atrybuty: wyłącznie z listy; ``href``/``xlink:href`` tylko ``#element``; ``style`` przez parser CSS.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .css import sanitize_css, sanitize_declarations

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "symbol",
        "use",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "textpath",
        "lineargradient",
        "radialgradient",
        "stop",
        "clippath",
        "mask",
        "pattern",
        "marker",
        "style",
        "filter",
        "feblend",
        "fecolormatrix",
        "fecomponenttransfer",
        "fecomposite",
        "feconvolvematrix",
        "fediffuselighting",
        "fedisplacementmap",
        "fedistantlight",
        "fedropshadow",
        "feflood",
        "fefunca",
        "fefuncb",
        "fefuncg",
        "fefuncr",
        "fegaussianblur",
        "femerge",
        "femergenode",
        "femorphology",
        "feoffset",
        "fepointlight",
        "fespecularlighting",
        "fespotlight",
        "fetile",
        "feturbulence",
    }
)

#: Atrybuty geometrii i prezentacji (bez przestrzeni nazw). Wszystko inne wypada.
ALLOWED_ATTRIBUTES = frozenset(
    """
    id class style transform viewbox preserveaspectratio width height x y x1 x2 y1 y2 cx cy r rx ry
    d points pathlength fill fill-opacity fill-rule stroke stroke-width stroke-linecap stroke-linejoin
    stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity opacity color clip-path
    clip-rule mask filter display visibility overflow vector-effect shape-rendering text-rendering
    image-rendering color-interpolation color-interpolation-filters paint-order font-family font-size
    font-weight font-style font-variant letter-spacing word-spacing text-anchor dominant-baseline
    alignment-baseline baseline-shift text-decoration writing-mode direction unicode-bidi dx dy rotate
    textlength lengthadjust startoffset method spacing side offset stop-color stop-opacity
    gradientunits gradienttransform spreadmethod fx fy fr patternunits patterncontentunits
    patterntransform clippathunits maskunits maskcontentunits markerwidth markerheight markerunits
    refx refy orient filterunits primitiveunits in in2 result mode type values tablevalues slope
    intercept amplitude exponent k1 k2 k3 k4 operator radius stddeviation edgemode kernelmatrix order
    divisor bias targetx targety preservealpha surfacescale diffuseconstant specularconstant
    specularexponent kernelunitlength azimuth elevation z pointsatx pointsaty pointsatz
    limitingconeangle scale xchannelselector ychannelselector basefrequency numoctaves seed
    stitchtiles flood-color flood-opacity lighting-color href version xmlns role aria-label
    aria-hidden aria-labelledby aria-describedby focusable lang
    """.split()
)
#: Atrybuty w przestrzeniach nazw, które zostają: ``xlink:href`` (``#element``) i ``xml:lang``/``space``.
ALLOWED_NS_ATTRIBUTES = {(XLINK_NS, "href"), (XML_NS, "lang"), (XML_NS, "space")}

DOCTYPE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
MAX_SVG_BYTES = 2 * 1024 * 1024


@dataclass
class SvgResult:
    data: bytes = b""
    errors: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _split(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local.lower()
    return "", tag.lower()


def sanitize_svg(data: bytes) -> SvgResult:
    result = SvgResult()
    if len(data) > MAX_SVG_BYTES:
        result.errors.append(f"plik SVG większy niż {MAX_SVG_BYTES // 1024 // 1024} MB")
        return result
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.errors.append("plik SVG musi być zapisany w UTF-8")
        return result
    if DOCTYPE.search(text):
        result.errors.append("SVG z deklaracją DOCTYPE/ENTITY jest niedozwolony")
        return result
    # Deklaracja XML mogłaby twierdzić inne kodowanie niż to, którym tekst już zdekodowaliśmy.
    text = XML_DECLARATION.sub("", text, count=1)
    try:
        # ``defusedxml`` nie jest zależnością projektu; jego dwie ochrony są tu wprost: DOCTYPE
        # i ENTITY odrzuca kontrola wyżej (bomba encji, XXE), a expat nie pobiera zasobów.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        result.errors.append(f"niepoprawny XML ({exc})")
        return result
    if _split(root.tag) != (SVG_NS, "svg"):
        result.errors.append("korzeniem pliku nie jest <svg> w przestrzeni nazw SVG")
        return result

    def clean_attributes(element: ET.Element) -> None:
        for attribute in list(element.attrib):
            namespace, local = _split(attribute)
            value = element.attrib[attribute]
            allowed = (
                (namespace, local) in ALLOWED_NS_ATTRIBUTES if namespace else local in ALLOWED_ATTRIBUTES
            )
            if not allowed:
                del element.attrib[attribute]
                result.removed.append(f"atrybut {local}")
            elif local == "href" and not value.strip().startswith("#"):
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

    def clean(element: ET.Element) -> None:
        for child in list(element):
            if not isinstance(child.tag, str):
                element.remove(child)
                continue
            namespace, name = _split(child.tag)
            if namespace != SVG_NS or name not in ALLOWED_ELEMENTS:
                element.remove(child)
                result.removed.append(f"<{name}>")
                continue
            if name == "style":
                checked = sanitize_css(child.text or "", svg_mode=True) if len(child) == 0 else None
                if checked is None or checked.errors:
                    element.remove(child)
                    result.removed.append("<style> z niedozwoloną treścią")
                    continue
                child.text = checked.css
                child.attrib.clear()
                continue
            clean(child)
        clean_attributes(element)

    clean(root)
    result.data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return result
