"""Parser i zapis katalogów ``.po`` – kontekst wpisów i zapis „w miejscu” (L10N-01 § 4, § 7)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from apps.translation_review import catalogs
from apps.translation_review.models import string_key

SAMPLE = """\
# Komentarz pliku.
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

#. Napis na przycisku.
#: templates/a.html:3 apps/x.py:10
msgid "Zapisz"
msgstr "Save"

#: apps/x.py:12
#, python-format
msgid "Witaj, %(name)s!"
msgstr ""
"Hello, "
"%(name)s!"

#: apps/x.py:14
msgctxt "czasownik"
msgid "Zgłoś"
msgstr "Report"

#: apps/x.py:20
#, python-format
msgid "%(count)s punkt"
msgid_plural "%(count)s punktów"
msgstr[0] "%(count)s point"
msgstr[1] "%(count)s points"

#~ msgid "Stary"
#~ msgstr "Old"
"""


def test_parse_reads_entries_with_context():
    entries = {(entry.msgctxt, entry.msgid): entry for entry in catalogs.parse(SAMPLE)}
    assert set(entries) == {
        (None, "Zapisz"),
        (None, "Witaj, %(name)s!"),
        ("czasownik", "Zgłoś"),
        (None, "%(count)s punkt"),
    }
    save = entries[(None, "Zapisz")]
    assert save.msgstr == {0: "Save"}
    assert save.locations == ["templates/a.html:3", "apps/x.py:10"]
    assert save.extracted == ["Napis na przycisku."]
    assert entries[(None, "Witaj, %(name)s!")].msgstr == {0: "Hello, %(name)s!"}
    assert "python-format" in entries[(None, "Witaj, %(name)s!")].flags
    plural = entries[(None, "%(count)s punkt")]
    assert plural.msgid_plural == "%(count)s punktów"
    assert plural.msgstr == {0: "%(count)s point", 1: "%(count)s points"}


def test_rewrite_changes_only_the_target_entries():
    updates = {
        (None, "Witaj, %(name)s!"): {0: 'Hi there, %(name)s! "quoted"\\ok'},
        (None, "%(count)s punkt"): {1: "%(count)s pts"},
    }
    content, changed = catalogs.rewrite(SAMPLE, updates)
    assert changed == 2
    entries = {(entry.msgctxt, entry.msgid): entry for entry in catalogs.parse(content)}
    assert entries[(None, "Witaj, %(name)s!")].msgstr == {0: 'Hi there, %(name)s! "quoted"\\ok'}
    assert entries[(None, "%(count)s punkt")].msgstr == {0: "%(count)s point", 1: "%(count)s pts"}
    assert entries[(None, "Witaj, %(name)s!")].reviewed
    assert not entries[(None, "Zapisz")].reviewed
    # Wpisy bez zmian zostają co do bajtu, łącznie z wpisem przestarzałym na końcu pliku.
    assert '#: templates/a.html:3 apps/x.py:10\nmsgid "Zapisz"\nmsgstr "Save"\n' in content
    assert content.endswith('#~ msgid "Stary"\n#~ msgstr "Old"\n')
    # Drugi zapis tej samej poprawki nie dokłada drugiego znacznika.
    again, _changed = catalogs.rewrite(content, updates)
    assert again == content


def test_rewrite_wraps_long_and_multiline_text():
    text = "Line one\n" + "word " * 30 + "end"
    content, _changed = catalogs.rewrite(SAMPLE, {(None, "Zapisz"): {0: text}})
    entry = next(entry for entry in catalogs.parse(content) if entry.msgid == "Zapisz")
    assert entry.msgstr[0] == text
    assert 'msgstr ""\n"Line one\\n"\n' in content


def test_rewrite_keeps_crlf_line_endings():
    crlf = SAMPLE.replace("\n", "\r\n")
    content, changed = catalogs.rewrite(crlf, {(None, "Zapisz"): {0: "Store"}})
    assert changed == 1
    assert "\n" not in content.replace("\r\n", "")


def test_rewritten_catalog_compiles(tmp_path):
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        pytest.skip("Brak msgfmt (gettext) – test biegnie w kontenerze i w CI.")
    content, _changed = catalogs.rewrite(
        SAMPLE,
        {(None, "Witaj, %(name)s!"): {0: "¡Hola, %(name)s!\ttab"}, ("czasownik", "Zgłoś"): {0: "Informar"}},
    )
    source = tmp_path / "django.po"
    source.write_text(content, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - ścieżka z which
        [msgfmt, "--check", "-o", str(tmp_path / "django.mo"), str(source)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_index_covers_project_and_app_catalogs():
    """Indeks języka zna napisy katalogu projektu **i** katalogów aplikacji (tu: tej aplikacji)."""
    index = catalogs.index("es")
    paths = {name for row in index.rows for name in row.catalogs}
    assert "locale/es/LC_MESSAGES/django.po" in paths
    assert "apps/translation_review/locale/es/LC_MESSAGES/django.po" in paths
    row = index.by_key[string_key(None, "Zgłoś tłumaczenie")]
    assert row.translation
    assert row.locations


def test_review_languages_exclude_the_source_language():
    languages = catalogs.review_languages()
    assert "pl" not in languages
    assert {"en", "zh-hans", "ar"} <= set(languages)
