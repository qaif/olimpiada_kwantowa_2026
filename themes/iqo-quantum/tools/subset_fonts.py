"""Space Grotesk (variable, wght 300–700) + Space Mono 400 → woff2 subsets.

Zakresy jak w podziale Google Fonts: latin + latin-ext (polski, niemiecki, francuski,
hiszpański, portugalski, indonezyjski, turecki…) + wietnamski i interpunkcja ogólna.
Cyrylicy, arabskiego, dewanagari, bengalskiego i chińskiego te kroje nie mają – tam
działają kroje systemowe ze stosu w tokens.json.

Źródła (OFL): github.com/google/fonts – ofl/spacegrotesk/SpaceGrotesk[wght].ttf,
ofl/spacemono/SpaceMono-Regular.ttf. Pobierz je do jednego katalogu i uruchom:

    UV_SYSTEM_CERTS=1 uv run --no-project --with fonttools --with brotli         python themes/iqo-quantum/tools/subset_fonts.py themes/iqo-quantum/assets/fonts <katalog z TTF>
"""
import pathlib
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

OUT = pathlib.Path(sys.argv[1])
HERE = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).parent
OUT.mkdir(parents=True, exist_ok=True)

LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2190-2199,U+2212,U+2215,U+27E8-27E9,U+FEFF,U+FFFD"
)
LATIN_EXT = (
    "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1D00-1DBF,U+1E00-1E9F,"
    "U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
)
VIET = "U+0102-0103,U+0110-0111,U+0128-0129,U+0168-0169,U+01A0-01A1,U+01AF-01B0,U+0300-0301,U+0303-0304,U+0308-0309,U+0323,U+0329,U+1EA0-1EF9,U+20AB"


def ranges(spec):
    out = []
    for part in spec.split(","):
        part = part.strip().removeprefix("U+")
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a, 16), int(b, 16) + 1))
        else:
            out.append(int(part, 16))
    return out


UNICODES = sorted(set(ranges(LATIN) + ranges(LATIN_EXT) + ranges(VIET)))


def make(src, dst, keep_axes=True):
    font = TTFont(HERE / src)
    cmap = font.getBestCmap()
    have = [u for u in UNICODES if u in cmap]
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.hinting = False
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=have)
    sub.subset(font)
    font.flavor = "woff2"
    font.save(OUT / dst)
    print(dst, (OUT / dst).stat().st_size, "bytes,", len(have), "codepoints")
    for probe in (0x27E9, 0x27E8, 0x2223, 0x03C8, 0x0119, 0x0142):
        print("   U+%04X" % probe, "yes" if probe in cmap else "no")


make("SpaceGrotesk[wght].ttf" if (HERE / "SpaceGrotesk[wght].ttf").exists() else "SpaceGrotesk.ttf", "space-grotesk-var.woff2")
make("SpaceMono-Regular.ttf", "space-mono-400.woff2")
