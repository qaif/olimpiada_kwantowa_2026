"""Walidator paczki motywu (THEME-01 § 2): każda reguła ma przypadek złośliwy.

Testy wołają ``validate_package`` wprost – bez bazy i storage: reguła ma działać zanim cokolwiek
zostanie zapisane.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from apps.themes import package
from apps.themes.package import validate_package

from .helpers import IQO_ZIP, build_zip, example_files, zip_with


def errors_of(data: bytes) -> str:
    return "\n".join(validate_package(data).errors)


def test_example_package_is_valid():
    result = validate_package(zip_with())
    assert result.errors == []
    assert result.slug == "example" and result.version == "1.0.0"
    assert set(result.templates) == {
        "theme/footer.html",
        "theme/page_wrapper.html",
        "theme/partials/links.html",
    }
    published = {f.path for f in result.public_files}
    assert published == {"assets/fonts/demo.woff2", "assets/img/pixel.png", "screenshot.png"}
    # README.txt (licencja/opis) zostaje w paczce prywatnej – nie jest publikowany.
    assert "README.txt" not in published
    # url() przepisane na postać kanoniczną (względną do theme.css w storage).
    assert 'url("assets/fonts/demo.woff2")' in result.theme_css
    assert 'url("assets/img/pixel.png")' in result.theme_css
    assert any("1200×900" in w for w in result.warnings)


def test_iqo_package_validates():
    """Prawdziwa paczka IQO (gałąź feature/motyw-iqo) przechodzi walidację bez błędów."""
    result = validate_package(IQO_ZIP.read_bytes(), app_version="0.41.0")
    assert result.errors == []
    assert result.slug == "iqo-quantum"
    assert set(result.templates) == {"theme/header.html", "theme/footer.html", "theme/home_hero.html"}
    assert 'url("assets/fonts/space-grotesk-var.woff2")' in result.theme_css


# --- ZIP -----------------------------------------------------------------------------------------


def test_not_a_zip():
    assert "nie jest poprawnym archiwum ZIP" in errors_of(b"PK\x03\x04 to nie jest zip")


def test_package_size_limit(monkeypatch):
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", 100)
    assert "limit to" in errors_of(zip_with())


@pytest.mark.parametrize(
    "name", ["../evil.css", "assets/../../evil.png", "/etc/passwd", "C:/win.ini", "a\\..\\b"]
)
def test_zip_slip_paths_rejected(name):
    files = {**example_files(), name: b"x"}
    assert "Niebezpieczna ścieżka" in errors_of(build_zip(files))


def test_symlink_rejected():
    files = {**example_files(), "assets/img/link.png": b"/etc/passwd"}
    assert "Dowiązanie symboliczne" in errors_of(build_zip(files, symlinks=("assets/img/link.png",)))


def test_zip_bomb_rejected_by_bytes_actually_unpacked(monkeypatch):
    """Limit liczony na rozpakowanych bajtach – plik 1 MB zer (kompresja ~1000:1) przy limicie 64 KB."""
    monkeypatch.setattr(package, "MAX_UNPACKED_BYTES", 64 * 1024)
    files = {**example_files(), "assets/img/bomb.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024)}
    assert "bomba ZIP" in errors_of(build_zip(files))


def test_too_many_files(monkeypatch):
    monkeypatch.setattr(package, "MAX_FILES", 5)
    assert "pozycji – limit" in errors_of(zip_with())


@pytest.mark.parametrize(
    "name", ["assets/x.js", "assets/x.html", "assets/img/x.gif", "evil.php", "assets/x.svgz", "theme/../x"]
)
def test_disallowed_file_types(name):
    message = errors_of(build_zip({**example_files(), name: b"x"}))
    assert message


def test_extension_must_match_content():
    files = {**example_files(), "assets/img/fake.png": b"<html><script>alert(1)</script>"}
    assert "nie odpowiada rozszerzeniu" in errors_of(build_zip(files))


def test_wrapping_directory_is_stripped():
    files = {f"example/{name}": content for name, content in example_files().items()}
    assert validate_package(build_zip(files)).errors == []


def test_required_files():
    assert "Brak wymaganego pliku theme.css" in errors_of(zip_with({"theme.css": None}))


def test_encrypted_member_rejected():
    data = bytearray(zip_with())
    # Bit 0 „encrypted” w nagłówku centralnym pierwszego wpisu.
    central = data.find(b"PK\x01\x02")
    data[central + 8] |= 0x01
    assert "zaszyfrowany" in errors_of(bytes(data))


# --- manifest ------------------------------------------------------------------------------------


def _manifest(**changes) -> bytes:
    manifest = json.loads(example_files()["manifest.json"])
    manifest.update(changes)
    return zip_with({"manifest.json": json.dumps(manifest)})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"slug": "../x"}, "slug"),
        ({"slug": "classic"}, "zarezerwowany"),
        ({"version": "1.0"}, "version"),
        ({"schema": 2}, "schema"),
        ({"color_scheme": "neon"}, "color_scheme"),
        ({"layouts": {"header": "floating"}}, "nie zna wariantów"),
        ({"supports": ["admin"]}, "supports"),
        ({"min_app_version": "99.0.0"}, "wymaga wersji aplikacji"),
    ],
)
def test_manifest_rules(changes, message):
    result = validate_package(_manifest(**changes), app_version="0.41.0")
    assert message in "\n".join(result.errors)


def test_layouts_first_option_is_default():
    result = validate_package(zip_with())
    assert result.manifest["layouts"] == {"header": ["minimal", "split"], "cards": ["outline", "flat"]}


# --- CSS -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("css", "message"),
    [
        ('@import url("https://evil.example/x.css");', "@import"),
        ("@\\69mport 'x.css';", "@import"),
        ('a { background: url("https://evil.example/t.png") }', "url()"),
        ("a { background: url(//evil.example/t.png) }", "url()"),
        ("a { background: url(data:image/png;base64,AAAA) }", "url()"),
        ("a { background: url(javascript:alert(1)) }", "theme.css"),  # błąd składni – też odrzucenie
        ('a { background: url("assets/../manifest.json") }', "url()"),
        ('a { background: url("/media/x.png") }', "url()"),
        ('a { background: url("assets/img/missing.png") }', "nie ma w paczce"),
        ('a { background: image-set("https://evil.example/x.png" 1x) }', "url()"),
        ("a { width: expression(alert(1)) }", "expression"),
        ("a { width: exp\\72 ession(alert(1)) }", "expression"),
        ('a { behavior: url("assets/img/pixel.png") }', "behavior"),
        ('a { -moz-binding: url("assets/img/pixel.png") }', "-moz-binding"),
        ('a { content: "javascript:alert(1)" }', "schemat skryptu"),
        ("@-moz-document url-prefix() { a { color: red } }", "at-reguła"),
    ],
)
def test_css_rules(css, message):
    assert message in errors_of(zip_with({"theme.css": css}))


# --- SVG -----------------------------------------------------------------------------------------

EVIL_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     onload="alert(1)" viewBox="0 0 10 10">
  <script>alert(1)</script>
  <foreignObject><body xmlns="http://www.w3.org/1999/xhtml"><iframe src="https://evil.example"/></body></foreignObject>
  <a xlink:href="javascript:alert(1)"><rect width="5" height="5" onclick="alert(2)"/></a>
  <image href="https://evil.example/track.png"/>
  <set attributeName="href" to="javascript:alert(3)"/>
  <style>@import url(https://evil.example/x.css); rect { fill: red }</style>
  <rect style="fill: url(https://evil.example/p.svg)" width="1" height="1"/>
  <linearGradient id="g"/><rect fill="url(#g)" width="1" height="1"/>
</svg>"""


def test_svg_is_sanitized_not_rejected():
    files = {**example_files(), "assets/img/evil.svg": EVIL_SVG}
    result = validate_package(build_zip(files))
    assert result.errors == []
    svg = next(f.data for f in result.public_files if f.path == "assets/img/evil.svg").decode()
    for forbidden in ("<script", "onload", "onclick", "foreignObject", "javascript:", "evil.example", "<set"):
        assert forbidden not in svg, forbidden
    assert 'fill="url(#g)"' in svg  # odwołania wewnętrzne zostają
    assert any("evil.svg: usunięto" in w for w in result.warnings)


@pytest.mark.parametrize(
    "svg",
    [
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b"<svg xmlns='http://www.w3.org/2000/svg'><unclosed></svg>",
        b"<html xmlns='http://www.w3.org/1999/xhtml'><script>alert(1)</script></html>",
    ],
)
def test_svg_with_entities_or_broken_xml_rejected(svg):
    assert "evil.svg" in errors_of(build_zip({**example_files(), "assets/img/evil.svg": svg}))


# --- szablony ------------------------------------------------------------------------------------

FOOTER = "templates/theme/footer.html"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{% load admin_list %}<footer></footer>", "spoza listy dozwolonej"),
        ("{% load cms_extras admin_urls %}<footer></footer>", "admin_urls"),
        ("{% load static from staticfiles_evil %}<footer></footer>", "spoza listy"),
        ("<footer>{{ settings.cms.SiteSettings.site_name|safe }}</footer>", "safe"),
        ("<footer>{% filter safe %}{{ x }}{% endfilter %}</footer>", "filter safe"),
        ("{% autoescape off %}{{ x }}{% endautoescape %}", "autoescape off"),
        ('{% extends "base.html" %}', "extends"),
        ("<footer>{% debug %}</footer>", "debug"),
        ('<footer>{% include "web/coordinator/base.html" %}</footer>', "spoza szablonów"),
        ("<footer>{% include template_name %}</footer>", "w cudzysłowie"),
        ('<footer>{% include "../../etc/passwd" %}</footer>', "niedozwolona nazwa"),
        ('<footer>{% include "theme/partials/missing.html" %}</footer>', "brak takiego szablonu"),
        ("<footer>{{ request.session.items }}</footer>", "niedostępnych"),
        ("<footer>{{ user.password }}</footer>", "niedostępnych"),
        ("<footer>{{ request.csp_nonce }}</footer>", "niedostępnych"),
        ("<footer><script>alert(1)</script></footer>", "<script"),
        ('<footer onclick="alert(1)"></footer>', "onclick"),
        ('<footer><a href="javascript:alert(1)">x</a></footer>', "javascript"),
        ('<footer style="background:url(https://evil.example)"></footer>', "style"),
        ('<footer><link rel="stylesheet" href="https://evil.example/x.css"></footer>', "<link"),
        ('<footer><iframe src="https://evil.example"></iframe></footer>', "<iframe"),
        ('<footer><img src="data:image/svg+xml;base64,AAAA"></footer>', "data"),
        ("<footer>{% if %}</footer>", "błąd składni"),
    ],
)
def test_template_rules(source, message):
    assert message in errors_of(zip_with({FOOTER: source}))


def test_template_outside_allowlist_rejected():
    assert "Szablon spoza listy dozwolonej" in errors_of(zip_with({"templates/web/login.html": "<p>x</p>"}))
    assert "Szablon spoza listy dozwolonej" in errors_of(zip_with({"templates/base.html": "<p>x</p>"}))


def test_include_cycle_rejected():
    changes = {
        "templates/theme/partials/a.html": '{% include "theme/partials/b.html" %}',
        "templates/theme/partials/b.html": '{% include "theme/partials/a.html" %}',
    }
    assert "cykl include" in errors_of(zip_with(changes))


def test_comment_block_text_is_not_linted():
    source = "{% comment %}<script> w komentarzu nie jest renderowany{% endcomment %}<footer></footer>"
    assert errors_of(zip_with({FOOTER: source})) == ""


def test_forms_are_allowed_with_csrf_token():
    source = (
        '<footer><form method="post" action="/logout/">{% csrf_token %}<button>x</button></form></footer>'
    )
    assert errors_of(zip_with({FOOTER: source})) == ""


# --- tokeny i skan -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [
        {"colors": {"bg": "red; } body { background: url(https://evil.example) "}},
        {"colors": {"bg": "url(https://evil.example/x.png)"}},
        {"typography": {"font-body": "x; } @import 'y'"}},
        {"typography": {"font-body": "expression(alert(1))"}},
        {"colors": {"Bad Name": "#fff"}},
    ],
)
def test_tokens_rules(tokens):
    assert "tokens.json" in errors_of(zip_with({"tokens.json": json.dumps(tokens)}))


def test_scan_infected_rejects_package():
    result = validate_package(zip_with(), scan=lambda data: ("INFECTED", "Eicar-Signature"))
    assert any("antywirusowy" in e and "Eicar" in e for e in result.errors)


def test_scan_receives_whole_package():
    seen = {}

    def scan(data):
        seen["data"] = data
        return "CLEAN", ""

    data = zip_with()
    assert validate_package(data, scan=scan).errors == []
    assert seen["data"] == data
    assert zipfile.is_zipfile(io.BytesIO(seen["data"]))
