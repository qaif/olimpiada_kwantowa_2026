"""Tokeny motywu: rejestr ``classic`` zgodny z ``app.css``, generator ``tokens.css``, lint arkuszy."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.themes.tokens import (
    CLASSIC_DARK_OTHER,
    CLASSIC_DARK_OVERRIDES,
    CLASSIC_LIGHT,
    CLASSIC_OTHER,
    TokenSet,
    accent_override_css,
    build_tokens_css,
    classic_tokens_json,
    complete_palette,
    contrast,
    parse_tokens,
)

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
APP_CSS = (CSS_DIR / "app.css").read_text(encoding="utf-8")


def _root_tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--t-([a-z0-9-]+):\s*([^;]+);", block))


def _blocks():
    start = APP_CSS.index("/* --- 2. Tokeny")
    end = APP_CSS.index("/* --- 3. Reset")
    section = APP_CSS[start:end]
    dark_at = section.index("@media (prefers-color-scheme: dark)")
    return _root_tokens(section[:dark_at]), _root_tokens(section[dark_at:])


def test_tokens_registry_matches_app_css():
    """Generator i arkusz mówią o tych samych wartościach ``classic`` – inaczej pochodne kłamią."""
    light, dark = _blocks()
    expected_light = {**CLASSIC_LIGHT, **CLASSIC_OTHER}
    for name, value in expected_light.items():
        assert light.get(name) == value, name
    for name, value in {**CLASSIC_DARK_OVERRIDES, **CLASSIC_DARK_OTHER}.items():
        assert dark.get(name) == value, name
    rgb_only = {name for name in light if name.endswith("-rgb")}
    assert set(light) - set(expected_light) == rgb_only


def test_roles_are_defined_once_through_theme_tokens():
    """Wariant ciemny przestawia tokeny motywu, a nie role – wtedy motyw jasny wygrywa kolejnością."""
    _light, _dark = _blocks()
    start = APP_CSS.index("@media (prefers-color-scheme: dark) {\n  :root {\n    --t-bg")
    dark_block = APP_CSS[start : APP_CSS.index("}\n}", start)]
    assert re.findall(r"^\s+--(?!t-)[a-z]", dark_block, re.M) == []


#: Literały marki ``classic`` (kolory i trójki RGB z tokenów), które nie mogą wrócić do reguł
#: arkuszy – motyw zmieniający tokeny zostawiłby wtedy granatowe/czerwone plamy w panelach.
BRAND_LITERALS = {v.lower() for k, v in CLASSIC_LIGHT.items() if not k.startswith(("on-", "logo"))} | {
    v.lower() for v in CLASSIC_DARK_OVERRIDES.values()
}
BRAND_RGB = ("244, 241, 232", "239, 138, 130", "18, 35, 63", "161, 15, 15", "212, 50, 40")
#: Bloki, w których literał jest świadomy: tokeny, tryb wysokiego kontrastu, plakaty (grafika do
#: druku), wydruk, kreator instalacji (osobna strona bez ``app.css``) i wartości oznaczone „stała”.
EXEMPT_FILES = {"setup.css"}


def _rule_lines():
    for path in sorted(CSS_DIR.glob("*.css")):
        if path.name in EXEMPT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == "app.css":
            text = text[text.index("/* --- 3. Reset") :]
        depth_exempt = 0
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if re.match(r'^(:root\[data-contrast="high"\]|@media print|\.hero-poster|.*--poster-)', stripped):
                depth_exempt = max(depth_exempt, 1)
            if depth_exempt:
                depth_exempt += line.count("{") - line.count("}")
                depth_exempt = max(depth_exempt, 0)
                continue
            if stripped.startswith(("/*", "*", "--")) or "stała" in line:
                continue
            yield path.name, number, line


def test_brand_colors_live_only_in_tokens():
    offenders = []
    for name, number, line in _rule_lines():
        lowered = line.lower()
        hexes = set(re.findall(r"#[0-9a-f]{3,8}\b", lowered))
        if hexes & BRAND_LITERALS or any(rgb in line for rgb in BRAND_RGB):
            offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == []


def test_classic_package_tokens_match_registry():
    path = Path(settings.BASE_DIR).parent / "themes" / "classic" / "tokens.json"
    if not path.exists():
        pytest.skip("Katalog themes/ poza obrazem (kontener montuje wyłącznie backend/).")
    assert json.loads(path.read_text(encoding="utf-8")) == classic_tokens_json()


def test_classic_tokens_json_parses_cleanly():
    parsed = parse_tokens(json.dumps(classic_tokens_json()).encode())
    assert parsed.errors == [] and parsed.warnings == []


def test_light_theme_emits_full_palette_and_light_scheme():
    tokens = parse_tokens(json.dumps({"colors": {"primary": "#3c4bff", "accent": "#c2185b"}}).encode())
    generated = build_tokens_css(tokens, "light")
    assert "color-scheme: light;" in generated.css
    assert "@media" not in generated.css
    # Każdy token koloru classic ma wartość – ciemny wariant app.css nie przebije motywu jasnego.
    for name in CLASSIC_LIGHT:
        assert f"--t-{name}:" in generated.css
    assert "--t-primary: #3c4bff;" in generated.css
    assert "--t-primary-rgb: 60, 75, 255;" in generated.css
    assert "--t-accent-rgb: 194, 24, 91;" in generated.css


def test_auto_theme_emits_dark_media_block():
    tokens = parse_tokens(json.dumps({"colors": {"bg": "#ffffff"}, "dark": {"bg": "#000010"}}).encode())
    generated = build_tokens_css(tokens, "auto")
    assert "@media (prefers-color-scheme: dark)" in generated.css
    assert "color-scheme: light dark;" in generated.css


def test_auto_without_dark_warns():
    tokens = parse_tokens(json.dumps({"colors": {"bg": "#ffffff"}}).encode())
    assert any("zawsze jasny" in w for w in build_tokens_css(tokens, "auto").warnings)


def test_dark_theme_uses_dark_set():
    tokens = parse_tokens(json.dumps({"colors": {"bg": "#ffffff"}, "dark": {"bg": "#0b0a24"}}).encode())
    generated = build_tokens_css(tokens, "dark")
    assert "--t-bg: #0b0a24;" in generated.css and "color-scheme: dark;" in generated.css


def test_derived_contrast_colors_are_readable():
    palette = complete_palette(
        {"primary": "#fafafa", "accent": "#ffd400", "bg": "#ffffff", "text": "#111111"}, dark=False
    )
    assert contrast(palette["primary-contrast"], palette["primary"]) >= 4.5
    assert contrast(palette["on-accent"], palette["accent"]) >= 4.5


def test_contrast_warning_not_error():
    tokens = parse_tokens(json.dumps({"colors": {"bg": "#ffffff", "text": "#eeeeee"}}).encode())
    generated = build_tokens_css(tokens, "light")
    assert any("tekst na tle strony" in w for w in generated.warnings)


def test_aliases_map_to_canonical_names():
    tokens = parse_tokens(
        json.dumps({"colors": {"accent-contrast": "#000000"}, "tokens": {"radius-md": "3px"}}).encode()
    )
    assert tokens.light["on-accent"] == "#000000"
    assert tokens.other["radius"] == "3px" and tokens.other["radius-md"] == "3px"


def test_short_group_keys():
    tokens = parse_tokens(json.dumps({"radius": {"sm": "1px", "md": "2px"}, "space": {"1": "3px"}}).encode())
    assert tokens.other == {"radius-sm": "1px", "radius": "2px", "space-1": "3px"}


def test_accent_override_rejects_non_hex():
    assert accent_override_css("red;}body{x", {}) == ""
    css = accent_override_css("#0055aa", {"surface": "#ffffff"})
    assert "--t-accent: #0055aa;" in css and "--t-accent-rgb: 0, 85, 170;" in css
    # Obwódka fokusu zostaje przy wartości motywu (kontrast sprawdzony przy wgraniu).
    assert "--t-focus" not in css


def test_token_set_roundtrip():
    tokens = parse_tokens(json.dumps({"colors": {"bg": "#fff"}, "tokens": {"x": "1px"}}).encode())
    again = TokenSet.from_json(tokens.as_json())
    assert again.light == tokens.light and again.other == tokens.other
