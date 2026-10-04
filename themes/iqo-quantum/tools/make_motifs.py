"""Generuje motywy graficzne i ikony IQO Quantum 1.1.0 (uruchamiane ręcznie, nie wchodzi do ZIP-a).

    uv run --no-project python themes/iqo-quantum/tools/make_motifs.py

Wyjście – statyczne SVG w ``assets/`` (bez skryptów, ``on*``, ``<style>`` i zewnętrznych ``href``;
wyłącznie atrybuty prezentacyjne z listy dozwolonej ``apps/themes/svg.py``):

- ``assets/motifs/bloch.svg`` – sfera Blocha (okrąg, równik i południk z tylną połową kreskowaną,
  osie, wektor stanu z rzutem na równik) – główny rysunek planszy,
- ``assets/motifs/orbital.svg`` – trzy orbity obrócone o 60° z jądrem i elektronami (stopka,
  narożniki kart),
- ``assets/motifs/fringes.svg`` – prążki interferencyjne dwóch szczelin (cos² pod obwiednią
  sinc²) – pasy nagłówków stron, karty aktualności, dół planszy,
- ``assets/icons/*.svg`` – ikony 24×24 (menu, zamknij, konto, strzałka, rozwiń).

Wszystkie rysunki są **czarne na przezroczystym** z różną nieprzezroczystością: ``theme.css``
używa ich jako masek (``mask-image``), a kolor nadaje tłem z tokenów (``--t-cta``, ``--t-accent``,
``--t-cover-text``…). Dzięki temu nadpisanie koloru przez koordynatora przemalowuje też grafiki,
a tryb wysokiego kontrastu zamienia je w żółć jednym przypisaniem koloru.
"""
from __future__ import annotations

import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
INK = "#000"


def write(rel: str, text: str) -> None:
    lowered = text.lower()
    for bad in ("<script", "foreignobject", "javascript:", "<style", " on", "href="):
        if bad == " on":
            assert not any(f" on{c}" in lowered for c in "abcdefghijklmnopqrstuvwxyz"), rel
            continue
        assert bad not in lowered, (rel, bad)
    path = ASSETS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"assets/{rel}  {path.stat().st_size} B")


def svg(view_box: str, body: str, *, width: int, height: int, extra: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" width="{width}" '
        f'height="{height}"{extra}>\n{body}\n</svg>\n'
    )


def f(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


# --- motywy -------------------------------------------------------------------------------------


def bloch() -> str:
    cx, cy, r = 200.0, 200.0, 150.0
    ry_eq = 40.0  # pochylenie równika
    rx_mer = 58.0  # południk widziany z ukosa
    theta, phi = math.radians(48), math.radians(-28)
    # Rzut punktu sfery (x w prawo, y w głąb, z w górę) na płaszczyznę rysunku.
    px = cx + r * math.sin(theta) * math.cos(phi)
    py_plane = cy - ry_eq * math.sin(theta) * math.sin(phi)  # punkt na równiku pod wektorem
    pz = py_plane - r * math.cos(theta)
    body = [
        f'<g fill="none" stroke="{INK}" stroke-linecap="round">',
        # sfera
        f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" stroke-width="2.2"/>',
        # równik: przód ciągły, tył kreskowany
        f'<path d="M{f(cx - r)} {f(cy)} A{f(r)} {f(ry_eq)} 0 0 0 {f(cx + r)} {f(cy)}" stroke-width="1.6"/>',
        f'<path d="M{f(cx - r)} {f(cy)} A{f(r)} {f(ry_eq)} 0 0 1 {f(cx + r)} {f(cy)}" stroke-width="1.2" '
        f'stroke-dasharray="3 7" stroke-opacity="0.6"/>',
        # południk: przód ciągły (prawa połowa), tył kreskowany
        f'<path d="M{f(cx)} {f(cy - r)} A{f(rx_mer)} {f(r)} 0 0 1 {f(cx)} {f(cy + r)}" stroke-width="1.4"/>',
        f'<path d="M{f(cx)} {f(cy - r)} A{f(rx_mer)} {f(r)} 0 0 0 {f(cx)} {f(cy + r)}" stroke-width="1.1" '
        f'stroke-dasharray="3 7" stroke-opacity="0.55"/>',
        # osie
        f'<path d="M{f(cx)} {f(cy - r - 22)} V{f(cy + r + 22)}" stroke-width="1" stroke-opacity="0.5" '
        f'stroke-dasharray="1 5"/>',
        f'<path d="M{f(cx - r - 22)} {f(cy)} H{f(cx + r + 22)}" stroke-width="1" stroke-opacity="0.5" '
        f'stroke-dasharray="1 5"/>',
        f'<path d="M{f(cx - 70)} {f(cy + 74)} L{f(cx + 70)} {f(cy - 74)}" stroke-width="1" '
        f'stroke-opacity="0.35" stroke-dasharray="1 5"/>',
        # rzut wektora na równik i kąt θ
        f'<path d="M{f(cx)} {f(cy)} L{f(px)} {f(py_plane)} L{f(px)} {f(pz)}" stroke-width="1.2" '
        f'stroke-dasharray="4 4" stroke-opacity="0.75"/>',
        f'<path d="M{f(cx)} {f(cy - 46)} A46 46 0 0 1 {f(cx + 46 * math.sin(theta) * 0.95)} '
        f'{f(cy - 46 * math.cos(theta) * 1.02)}" stroke-width="1.4"/>',
        # wektor stanu
        f'<path d="M{f(cx)} {f(cy)} L{f(px)} {f(pz)}" stroke-width="4"/>',
        "</g>",
        f'<g fill="{INK}">',
        f'<circle cx="{f(px)}" cy="{f(pz)}" r="9"/>',
        f'<circle cx="{f(px)}" cy="{f(pz)}" r="18" fill-opacity="0.18"/>',
        f'<circle cx="{f(cx)}" cy="{f(cy)}" r="4.5"/>',
        f'<circle cx="{f(cx)}" cy="{f(cy - r)}" r="5"/>',
        f'<circle cx="{f(cx)}" cy="{f(cy + r)}" r="5" fill-opacity="0.6"/>',
        f'<circle cx="{f(px)}" cy="{f(py_plane)}" r="3" fill-opacity="0.7"/>',
        "</g>",
    ]
    return svg("0 0 400 400", "\n".join(body), width=400, height=400)


def orbital() -> str:
    cx = cy = 200.0
    rx, ry = 180.0, 58.0
    parts = [f'<g fill="none" stroke="{INK}" stroke-width="1.6">']
    for angle in (0, 60, 120):
        parts.append(
            f'<ellipse cx="{f(cx)}" cy="{f(cy)}" rx="{f(rx)}" ry="{f(ry)}" '
            f'transform="rotate({angle} {f(cx)} {f(cy)})"/>'
        )
    parts.append(f'<circle cx="{f(cx)}" cy="{f(cy)}" r="26" stroke-opacity="0.45" stroke-dasharray="2 4"/>')
    parts.append("</g>")
    parts.append(f'<g fill="{INK}">')
    parts.append(f'<circle cx="{f(cx)}" cy="{f(cy)}" r="11"/>')
    # elektrony: po jednym na orbicie, w różnych fazach
    for angle, t in ((0, 0.62), (60, 2.4), (120, 4.3)):
        a = math.radians(angle)
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        x = cx + ex * math.cos(a) - ey * math.sin(a)
        y = cy + ex * math.sin(a) + ey * math.cos(a)
        parts.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="6.5"/>')
    parts.append("</g>")
    return svg("0 0 400 400", "\n".join(parts), width=400, height=400)


def fringes() -> str:
    # Wzór dwóch szczelin: I(x) = cos²(π d x / λL) · sinc²(π a x / λL). Paski co 4 jednostki.
    width, height, step = 1200, 120, 4
    rects = []
    for i in range(0, width, step):
        x = (i + step / 2 - width / 2) / (width / 2)  # −1 … 1
        interference = math.cos(math.pi * 15 * x) ** 2
        u = math.pi * 2.2 * x
        envelope = 1.0 if abs(u) < 1e-9 else (math.sin(u) / u) ** 2
        value = interference * (0.15 + 0.85 * envelope)
        if value < 0.04:
            continue
        rects.append(
            f'<rect x="{i}" y="0" width="{step}" height="{height}" fill-opacity="{value:.2f}"/>'
        )
    body = f'<g fill="{INK}">\n' + "\n".join(rects) + "\n</g>"
    return svg(f"0 0 {width} {height}", body, width=width, height=height,
               extra=' preserveAspectRatio="none"')



# --- ikony 24×24 ----------------------------------------------------------------------------------

ICON_ATTRS = f'fill="none" stroke="{INK}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'


def icon(body: str) -> str:
    return svg("0 0 24 24", f"<g {ICON_ATTRS}>{body}</g>", width=24, height=24)


ICONS = {
    # Trzy kreski, środkowa jako paczka falowa – „menu” w języku motywu.
    "menu": icon('<path d="M4 6.5h16"/><path d="M4 12c1.4-2.4 2.6-2.4 4 0s2.6 2.4 4 0 2.6-2.4 4 0 2.6 2.4 4 0"/>'
                 '<path d="M4 17.5h16"/>'),
    "close": icon('<path d="M6 6l12 12M18 6 6 18"/>'),
    # Osoba na orbicie.
    "account": icon('<circle cx="12" cy="9" r="3.4"/><path d="M5.5 19.5c1.2-3.2 3.6-4.8 6.5-4.8s5.3 1.6 6.5 4.8"/>'
                    '<path d="M18.6 5.2c1.6.5 2.5 1.2 2.5 2" stroke-width="1.3"/>'),
    "arrow": icon('<path d="M4 12h15"/><path d="M13.5 6.5 19 12l-5.5 5.5"/>'),
    "chevron": icon('<path d="M6.5 9.5 12 15l5.5-5.5"/>'),
}


def main() -> None:
    write("motifs/bloch.svg", bloch())
    write("motifs/orbital.svg", orbital())
    write("motifs/fringes.svg", fringes())
    for name, text in ICONS.items():
        write(f"icons/{name}.svg", text)


if __name__ == "__main__":
    main()
