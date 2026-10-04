"""Reguły poprawki tłumacza (L10N-01 § 5) – placeholdery, znaczniki, cudzysłowy, znaki sterujące."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.translation_review.validation import clean_translation


def ok(text, msgid, **kwargs):
    return clean_translation(text, msgid=msgid, **kwargs)


def rejected(text, msgid, **kwargs) -> str:
    with pytest.raises(ValidationError) as caught:
        clean_translation(text, msgid=msgid, **kwargs)
    return " ".join(caught.value.messages)


def test_plain_text_passes_and_is_trimmed():
    assert ok("  Hola mundo \n", "Witaj świecie") == "Hola mundo"


def test_whitespace_on_edges_follows_the_source():
    """``msgfmt`` wymaga zgodnych ``\\n`` na brzegach – dopasowujemy je, zamiast odrzucać."""
    assert ok("Hola", "\nWitaj\n") == "\nHola\n"


def test_empty_is_rejected():
    assert "puste" in rejected("   ", "Witaj")


@pytest.mark.parametrize(
    ("msgid", "good", "bad"),
    [
        ("Witaj, %(name)s!", "¡Hola, %(name)s!", "¡Hola!"),
        ("Witaj, %(name)s!", "¡Hola, %(name)s!", "¡Hola, %(nombre)s!"),
        ("Masz %d punktów", "Tienes %d puntos", "Tienes %s puntos"),
        ("Plik {name} gotowy", "Archivo {name} listo", "Archivo {nombre} listo"),
        ("Plik {name} gotowy", "Archivo {name} listo", "Archivo {name} {name} listo"),
    ],
)
def test_placeholders_must_match(msgid, good, bad):
    assert ok(good, msgid) == good
    assert "zmienne" in rejected(bad, msgid)


def test_lone_percent_and_braces_must_match_the_source():
    """Samotny ``%`` w napisie formatowanym to ``ValueError`` w ``blocktranslate`` – nie wpuszczamy."""
    assert ok("%(n)s%% hecho", "%(n)s%% gotowe") == "%(n)s%% hecho"
    rejected("%(n)s % hecho", "%(n)s%% gotowe")
    rejected("Archivo {name} listo {", "Plik {name} gotowy")
    assert ok("100 % gratis", "100% za darmo") == "100 % gratis"


@pytest.mark.parametrize(
    "payload",
    [
        "Hola <script>alert(1)</script>",
        "Hola <img src=x onerror=alert(1)>",
        "<b>Hola</b>",
        "Hola <a href='//evil.example'>aquí</a>",
        "Hola <img src=x onerror=alert(1) ",  # niedomknięty znacznik sklejony z HTML-em szablonu
        "Hola > adiós",
    ],
)
def test_new_html_is_rejected(payload):
    rejected(payload, "Witaj świecie")


def test_tags_must_be_exactly_those_of_the_source():
    msgid = 'Przeczytaj <a href="/regulamin/">regulamin</a> i <strong>zaakceptuj</strong>.'
    good = 'Lee <strong>y acepta</strong> el <a href="/regulamin/">reglamento</a>.'
    assert ok(good, msgid) == good
    # Zmieniony atrybut to inny znacznik – przekierowanie odnośnika poza serwis.
    rejected('Lee el <a href="https://evil.example/">reglamento</a> y <strong>acepta</strong>.', msgid)
    # Znacznik z dopisanym atrybutem zdarzenia.
    rejected('Lee el <a href="/regulamin/" onclick="x()">reglamento</a> y <strong>acepta</strong>.', msgid)
    rejected("Lee el reglamento y acepta.", msgid)


def test_attribute_breakout_quotes_are_rejected():
    """Napis bywa w ``title="…"`` – prosty cudzysłów zamknąłby atrybut i otworzył nowy."""
    message = rejected('Buscar" onmouseover="alert(1)', "Szukaj")
    assert "cudzysłowów" in message
    rejected("Buscar` x", "Szukaj")


def test_ascii_apostrophe_becomes_typographic_when_source_has_none():
    assert ok("L'école d'été", "Szkoła letnia") == "L’école d’été"


def test_apostrophe_kept_when_the_source_has_one():
    assert ok("Don't", "Nie rób 'tego'") == "Don't"


def test_apostrophe_inside_copied_tag_is_kept():
    msgid = "Zobacz <a href='/x/'>tutaj</a>"
    assert ok("Mira <a href='/x/'>aquí</a> l'été", msgid) == "Mira <a href='/x/'>aquí</a> l’été"


def test_control_characters():
    rejected("Hola‮mundo", "Witaj")
    rejected("Hola\x00", "Witaj")
    rejected("Hola\nmundo", "Witaj świecie")
    assert ok("Hola\nmundo", "Witaj\nświecie") == "Hola\nmundo"


def test_length_limit():
    rejected("x" * 500, "Krótki napis")


def test_plural_forms():
    msgid, plural = "%(count)s punkt", "%(count)s punktów"
    assert ok("%(count)s punto", msgid, msgid_plural=plural, plural_index=0)
    assert ok("%(count)s puntos", msgid, msgid_plural=plural, plural_index=1)
    rejected("%(n)s puntos", msgid, msgid_plural=plural, plural_index=1)
