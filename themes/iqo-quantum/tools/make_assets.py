"""Generuje grafiki motywu IQO Quantum do ``assets/`` (uruchamiane ręcznie, nie wchodzi do ZIP-a).

    uv run --no-project python themes/iqo-quantum/tools/make_assets.py <katalog z logo D-hybrid>

Wejście: pliki logo „hybryda D” (logo.svg, logo-white.svg, mark.svg, favicon.svg).
Wyjście (statyczne SVG – bez skryptów, bez ``on*``, bez zewnętrznych ``href``):

- ``assets/logo/*`` – kopie logo + wersje pochodne (biały znak, poziomy „lockup” do nagłówka),
- (do 1.0.0 także ``assets/hero/wave-field.svg``; od 1.1.0 rysunki planszy generuje
  ``tools/make_motifs.py`` – funkcja ``hero_field`` została jako wzór).
"""
from __future__ import annotations

import math
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

NAVY = "#14123A"
BLUE = "#3C4BFF"
BLUE_ON_DARK = "#7C88FF"
RASPBERRY = "#FF6B98"
WHITE = "#FFFFFF"


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{path.relative_to(ROOT)}  {path.stat().st_size} B")


def assert_clean(text: str, name: str) -> None:
    lowered = text.lower()
    for bad in ("<script", "foreignobject", "javascript:", "href=", " on"):
        if bad == " on":
            if re.search(r"\son[a-z]+\s*=", lowered):
                raise SystemExit(f"{name}: atrybut on* w SVG")
            continue
        if bad in lowered:
            raise SystemExit(f"{name}: niedozwolony fragment {bad!r}")


def lockup(src: str, ring: str, wave: str, word: str, ket: str) -> str:
    """Logo bez podpisu „INTERNATIONAL QUANTUM OLYMPIAD”, napis wyśrodkowany na pierścieniu.

    Do nagłówka (wysokość ~40 px): podpis miałby tam 4 px, więc zostaje tylko znak + |IQO⟩.
    """
    root = ET.fromstring(src)
    outer = root[0]  # <g transform="translate(24 24)">
    children = list(outer)
    ring_group, ket_bar, letters, chevron, subtitle = children
    outer.remove(subtitle)
    # Wiersz napisu (y 46–146) przesunięty do środka pierścienia (środek y = 125).
    shift = 125 - 96.3
    for el in (ket_bar, letters, chevron):
        outer.remove(el)
    word_group = ET.SubElement(outer, f"{{{SVG_NS}}}g", {"transform": f"translate(0 {shift:.1f})"})
    for el in (ket_bar, letters, chevron):
        word_group.append(el)
    # Kolory
    for el in ring_group.iter():
        if el.get("stroke") in (NAVY, WHITE):
            el.set("stroke", ring)
        elif el.get("stroke") in (BLUE, BLUE_ON_DARK):
            el.set("stroke", wave)
    letters.set("fill", word)
    ket_bar.set("fill", ket)
    chevron.set("stroke", ket)
    # Ciasny kadr: pierścień 0–250 (x, y) + napis do x≈530, w układzie przesuniętym o 24.
    root.set("viewBox", "20 20 540 258")
    root.set("width", "540")
    root.set("height", "258")
    return ET.tostring(root, encoding="unicode")


def recolor_mark(src: str, ring: str, wave: str) -> str:
    out = src.replace(f'stroke="{NAVY}"', f'stroke="{ring}"')
    return out.replace(f'stroke="{BLUE}"', f'stroke="{wave}"')


def wave_packet(x0: float, sigma: float, k: float, amp: float, y: float, x_from: float,
                x_to: float, phase: float = 0.0, step: float = 2.0) -> str:
    pts = []
    x = x_from
    while x <= x_to + 0.01:
        env = math.exp(-((x - x0) ** 2) / (2 * sigma**2))
        pts.append(f"{x:.0f} {y - amp * env * math.cos(k * (x - x0) + phase):.1f}")
        x += step
    return "M" + " L".join(pts)


def envelope(x0: float, sigma: float, amp: float, y: float, x_from: float, x_to: float,
             sign: int) -> str:
    pts = []
    x = x_from
    while x <= x_to + 0.01:
        env = math.exp(-((x - x0) ** 2) / (2 * sigma**2))
        pts.append(f"{x:.0f} {y - sign * amp * env:.1f}")
        x += 4
    return "M" + " L".join(pts)


def hero_field() -> str:
    """Tło hero 1600×800: interferencja dwóch źródeł + medal z paczką falową (po stronie „end”).

    Kolory są stałe (obraz w CSS nie dziedziczy zmiennych), dobrane pod ciemne tło hero
    (#0B0A24–#14123A) – hero jest ciemne w obu wariantach kolorystycznych motywu.
    Lewa krawędź obrazu wygasa (maska + gradient obrysu), więc przy ``background-size: auto 100%``
    na szerokim ekranie nie widać szwu między obrazem a tłem sekcji. Siatkę rysuje CSS.
    """
    ring, wave, imag, fringe = WHITE, BLUE_ON_DARK, RASPBERRY, BLUE_ON_DARK
    w, h = 1600, 800
    cx, cy, r = 1170, 400, 250
    parts = [
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'preserveAspectRatio="xMaxYMid slice">',
        "<defs>"
        f'<radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">'
        f'<stop offset="0" stop-color="{wave}" stop-opacity="0.32"/>'
        f'<stop offset="1" stop-color="{wave}" stop-opacity="0"/></radialGradient>'
        '<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0.3" stop-color="#fff" stop-opacity="0"/>'
        '<stop offset="0.62" stop-color="#fff" stop-opacity="1"/></linearGradient>'
        f'<linearGradient id="waveFade" gradientUnits="userSpaceOnUse" x1="600" y1="0" x2="1000" y2="0">'
        f'<stop offset="0" stop-color="{wave}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{wave}" stop-opacity="1"/></linearGradient>'
        f'<linearGradient id="imagFade" gradientUnits="userSpaceOnUse" x1="600" y1="0" x2="1000" y2="0">'
        f'<stop offset="0" stop-color="{imag}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{imag}" stop-opacity="0.75"/></linearGradient>'
        f'<mask id="edge"><rect width="{w}" height="{h}" fill="url(#fade)"/></mask>'
        "</defs>",
    ]
    # Prążki: okręgi wokół dwóch źródeł (szczeliny) – ich przecięcia rysują moiré interferencji.
    s1, s2 = (cx - 40, cy - 70), (cx - 40, cy + 70)
    rings = []
    for sx, sy in (s1, s2):
        for rr in range(30, 1500, 26):
            rings.append(f'<circle cx="{sx}" cy="{sy}" r="{rr}"/>')
    parts.append(
        f'<g mask="url(#edge)" fill="none" stroke="{fringe}" stroke-opacity="0.16" '
        f'stroke-width="1.2">' + "".join(rings) + "</g>"
    )
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r + 140}" fill="url(#glow)"/>')
    # Medal: gruby i cienki pierścień (proporcje z logo 80/66, 8/2).
    parts.append(
        f'<g fill="none" stroke="{ring}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" stroke-width="{r * 0.09:.1f}" stroke-opacity="0.85"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r * 66 / 80:.1f}" stroke-width="{r * 0.025:.1f}" '
        f'stroke-opacity="0.7"/></g>'
    )
    # Paczka falowa przecinająca medal: obwiednia (przerywana), część urojona, część rzeczywista.
    x0, sigma, k, amp = cx, 120, 2 * math.pi / 64, 170
    x_from, x_to = 200, w
    parts.append(
        f'<g mask="url(#edge)" fill="none" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{envelope(x0, sigma, amp, cy, x_from, x_to, 1)}" stroke="{ring}" '
        f'stroke-opacity="0.35" stroke-width="1.5" stroke-dasharray="2 8"/>'
        f'<path d="{envelope(x0, sigma, amp, cy, x_from, x_to, -1)}" stroke="{ring}" '
        f'stroke-opacity="0.35" stroke-width="1.5" stroke-dasharray="2 8"/>'
        f'<path d="{wave_packet(x0, sigma, k, amp * 0.92, cy, x_from, x_to, math.pi / 2)}" '
        f'stroke="url(#imagFade)" stroke-width="3"/>'
        f'<path d="{wave_packet(x0, sigma, k, amp, cy, x_from, x_to)}" stroke="url(#waveFade)" '
        f'stroke-width="9"/>'
        f"</g>"
    )
    # Znaczniki źródeł (dwie szczeliny)
    parts.append(
        f'<g fill="{imag}" fill-opacity="0.9">'
        f'<circle cx="{s1[0]}" cy="{s1[1]}" r="4"/><circle cx="{s2[0]}" cy="{s2[1]}" r="4"/></g>'
    )
    parts.append("</svg>")
    return "".join(parts)



def main(src_dir: pathlib.Path) -> None:
    logo = (src_dir / "logo.svg").read_text(encoding="utf-8")
    logo_white = (src_dir / "logo-white.svg").read_text(encoding="utf-8")
    mark = (src_dir / "mark.svg").read_text(encoding="utf-8")
    favicon = (src_dir / "favicon.svg").read_text(encoding="utf-8")

    out = {
        "logo/logo.svg": logo,
        "logo/logo-white.svg": logo_white,
        "logo/mark.svg": mark,
        "logo/mark-white.svg": recolor_mark(mark, WHITE, BLUE_ON_DARK),
        "logo/favicon.svg": favicon,
        "logo/lockup-white.svg": lockup(logo_white, WHITE, BLUE_ON_DARK, WHITE, BLUE_ON_DARK),
        "logo/lockup.svg": lockup(logo_white, NAVY, BLUE, NAVY, BLUE),
    }
    for rel, text in out.items():
        assert_clean(text, rel)
        write(ASSETS / rel, text)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
