"""Tekst redakcji bez ``style`` (audyt 2026-10-10): ``clean_html`` djangocms-text zdejmuje atrybut."""

from __future__ import annotations

from djangocms_text import html

from apps.blocks.sanitizer import FORBIDDEN_GLOBAL_ATTRIBUTES, tighten_text_sanitizer

OVERLAY = (
    '<p style="position:fixed;inset:0;z-index:99999;background:#fff" class="lead">'
    '<a href="/x/" style="display:block">Zaloguj się</a></p>'
)


def test_clean_html_drops_style_but_keeps_the_rest():
    cleaned = html.clean_html(OVERLAY)
    assert "style" not in cleaned and "position" not in cleaned
    assert 'class="lead"' in cleaned and 'href="/x/"' in cleaned and "Zaloguj się" in cleaned


def test_no_allowed_attribute_list_has_style():
    for allowed in (html.cms_additional_attributes, html.cms_parser.ALLOWED_ATTRIBUTES):
        for tag, attributes in allowed.items():
            assert not attributes & FORBIDDEN_GLOBAL_ATTRIBUTES, tag


def test_a_fresh_parser_has_no_style_either():
    assert "style" not in html.NH3Parser().ALLOWED_ATTRIBUTES.get("*", set())


def test_tightening_is_idempotent():
    before = {tag: set(attrs) for tag, attrs in html.cms_parser.ALLOWED_ATTRIBUTES.items()}
    tighten_text_sanitizer()
    assert html.cms_parser.ALLOWED_ATTRIBUTES == before
