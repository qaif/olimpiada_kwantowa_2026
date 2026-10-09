"""Rozpoznawanie formatu materiału po treści – filmy, które przeglądarka odtworzy, i pliki z listy.

Przedmiotem jest odrzucenie tego, co **wygląda** poprawnie po nazwie: MOV i MKV przemianowane na
``.mp4``, HTML podpisany jako ``.pdf``, PDF podpisany jako ``.pptx``.
"""

from __future__ import annotations

import codecs

import pytest

from apps.workshop_materials import formats

from .helpers import (
    CFB_BYTES,
    ELF_BYTES,
    HTML_BYTES,
    MKV_HEADER,
    MOV_HEADER,
    MP4_HEADER,
    PDF_BYTES,
    PY_BYTES,
    RTF_BYTES,
    WEBM_HEADER,
    ZIP_BYTES,
)


@pytest.mark.parametrize(("header", "expected"), [(MP4_HEADER, "mp4"), (WEBM_HEADER, "webm")])
def test_browser_playable_videos_are_accepted(header, expected):
    assert formats.verify_video(header).key == expected


@pytest.mark.parametrize(
    ("header", "hint"),
    [
        (MOV_HEADER, "QuickTime"),
        (MKV_HEADER, "Matroska"),
        (PDF_BYTES, "nie jest filmem"),
        (HTML_BYTES, "nie jest filmem"),
    ],
)
def test_other_containers_are_refused_with_a_hint(header, hint):
    with pytest.raises(formats.FormatError, match=hint):
        formats.verify_video(header)


def test_zip_family_takes_its_name_from_the_extension():
    assert formats.verify_file(ZIP_BYTES, "pptx").content_type.endswith("presentationml.presentation")
    assert formats.verify_file(ZIP_BYTES, "zip").key == "zip"


@pytest.mark.parametrize(
    ("content", "extension"), [(HTML_BYTES, "pdf"), (PDF_BYTES, "pptx"), (ZIP_BYTES, "pdf")]
)
def test_extension_that_lies_about_the_content_is_refused(content, extension):
    with pytest.raises(formats.FormatError, match="treść nie jest"):
        formats.verify_file(content, extension)


def test_unknown_extension_is_refused():
    with pytest.raises(formats.FormatError, match="nie są przyjmowane"):
        formats.verify_file(PDF_BYTES, "exe")


def test_notebook_is_a_json_object_even_with_a_bom():
    assert formats.verify_file(b'\xef\xbb\xbf  {"cells": []}', "ipynb").key == "ipynb"


def test_file_limit_never_exceeds_the_scanner_limit(settings):
    settings.WORKSHOP_FILE_MAX_MB = 500
    settings.CLAMAV_STREAM_MAX_BYTES = 100 * formats.MEGABYTE

    assert formats.file_max_bytes() == 100 * formats.MEGABYTE


def test_jpeg_extension_is_an_alias():
    assert formats.normalise_extension("Slajd.JPEG") == "jpg"


# --- WM-FMT-01: pełny Office, RTF, tekst i kod ----------------------------------------------------


@pytest.mark.parametrize(
    ("extension", "content_type"),
    [
        ("doc", "application/msword"),
        ("xls", "application/vnd.ms-excel"),
        ("ppt", "application/vnd.ms-powerpoint"),
        ("pps", "application/vnd.ms-powerpoint"),
    ],
)
def test_legacy_office_is_one_cfb_family_named_by_the_extension(extension, content_type):
    fmt = formats.verify_file(CFB_BYTES, extension)

    assert fmt.family == "cfb"
    assert fmt.content_type == content_type
    assert fmt.inline is False


@pytest.mark.parametrize(
    "extension",
    ["docm", "xlsm", "pptm", "dotx", "xltx", "potx", "ppsx", "xlsb", "odg", "epub"],
)
def test_ooxml_variants_templates_and_epub_are_zip(extension):
    assert formats.verify_file(ZIP_BYTES, extension).key == extension


@pytest.mark.parametrize("extension", ["docm", "xlsm", "pptm", "doc", "xls", "ppt"])
def test_macro_capable_formats_are_accepted_and_flagged(extension):
    content = CFB_BYTES if extension in ("doc", "xls", "ppt") else ZIP_BYTES

    assert formats.verify_file(content, extension).macros is True
    assert formats.FILE_FORMATS["docx"].macros is False


def test_rtf_is_recognised_by_its_signature():
    fmt = formats.verify_file(RTF_BYTES, "rtf")

    assert fmt.content_type == "application/rtf"


@pytest.mark.parametrize(
    ("content", "extension"),
    [
        (CFB_BYTES, "docx"),  # stary Word przemianowany na nowy
        (ZIP_BYTES, "doc"),
        (PDF_BYTES, "xls"),
        (PDF_BYTES, "rtf"),
        (RTF_BYTES, "doc"),
        (CFB_BYTES, "zip"),
    ],
)
def test_office_extension_that_lies_about_the_family_is_refused(content, extension):
    with pytest.raises(formats.FormatError, match="treść nie jest"):
        formats.verify_file(content, extension)


@pytest.mark.parametrize("extension", ["py", "md", "txt", "csv", "tex", "json", "yaml", "qasm", "qs", "m"])
def test_utf8_text_without_bom(extension):
    fmt = formats.verify_file(PY_BYTES, extension)

    assert fmt.family == "text"
    assert fmt.content_type.endswith("charset=utf-8")
    assert fmt.inline is False


def test_utf8_text_with_bom():
    assert formats.verify_file(b"\xef\xbb\xbfa;b;\xc5\x82\n1;2;3\n", "csv").key == "csv"


def test_character_cut_at_the_probe_boundary_is_not_an_error():
    header = ("ł" * (formats.HEADER_PROBE_BYTES // 2)).encode()[: formats.HEADER_PROBE_BYTES - 1]

    assert formats.verify_file(header, "md").key == "md"


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (ELF_BYTES, "program"),
        (PDF_BYTES + b"\x00\x01\x02", "PDF z NUL"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "obraz"),
        (ZIP_BYTES, "archiwum"),
        ("x = 1  # zażółć\n".encode("utf-16"), "UTF-16"),
        ("# zażółć gęślą jaźń\n".encode("cp1250"), "Windows-1250"),
        (b"a" * 10 + b"\x01\x02\x03\x04\x05\x06\x07\x08" * 4, "znaki sterujące"),
    ],
)
def test_binary_content_with_a_code_extension_is_refused(content, why):
    with pytest.raises(formats.FormatError, match="UTF-8"):
        formats.verify_file(content, "py")


def test_text_family_tolerates_tabs_form_feeds_and_a_few_escapes():
    log = b"\tkolumna\r\n\x0cstrona 2\n" + b"\x1b[31mERROR\x1b[0m " + b"x" * 200 + b"\n"

    assert formats.verify_file(log, "txt").key == "txt"


@pytest.mark.parametrize("extension", ["html", "svg", "css", "js", "sh"])
def test_markup_and_scripts_are_plain_text_attachments(extension):
    fmt = formats.verify_file(b"<svg onload=alert(1)><script>alert(1)</script></svg>", extension)

    assert fmt.content_type == "text/plain; charset=utf-8"
    assert fmt.inline is False


def test_markdown_has_its_own_type_and_a_rendered_preview():
    fmt = formats.FILE_FORMATS["md"]

    assert fmt.content_type == "text/markdown; charset=utf-8"
    assert fmt.preview == formats.PREVIEW_MARKDOWN
    assert formats.FILE_FORMATS["py"].preview == formats.PREVIEW_TEXT
    assert formats.FILE_FORMATS["pdf"].preview == ""


@pytest.mark.parametrize(("filename", "key"), [("notatki.MARKDOWN", "md"), ("strona.htm", "html")])
def test_new_aliases(filename, key):
    assert formats.normalise_extension(filename) == key


# --- nic, co było przyjmowane, nie przestaje być przyjmowane ------------------------------------------


@pytest.mark.parametrize(
    ("content", "extension"),
    [
        (PDF_BYTES, "pdf"),
        (ZIP_BYTES, "pptx"),
        (ZIP_BYTES, "docx"),
        (ZIP_BYTES, "xlsx"),
        (ZIP_BYTES, "odp"),
        (ZIP_BYTES, "odt"),
        (ZIP_BYTES, "ods"),
        (ZIP_BYTES, "zip"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpg"),
        (b'{"cells": [], "metadata": {}}', "ipynb"),
        # Notatnik to „obiekt JSON po BOM i białych znakach” – także gdy przypadkiem zaczyna się
        # jak RTF albo zawiera bajty spoza UTF-8. Nowe rodziny tej reguły nie ruszają.
        (rb"{\rtf1}", "ipynb"),
        (b"{\x00\xff}", "ipynb"),
    ],
)
def test_formats_accepted_before_wm_fmt_01_are_still_accepted(content, extension):
    assert formats.verify_file(content, extension).key == extension


def test_accept_attribute_lists_every_extension_and_alias():
    accept = formats.file_accept().split(",")

    for extension in [*formats.FILE_FORMATS, "jpeg", "markdown", "htm"]:
        assert f".{extension}" in accept


def test_allowed_list_is_grouped_and_complete():
    listed = formats.allowed_file_extensions()

    assert listed.startswith("dokumenty, prezentacje i arkusze: pdf, docx")
    for extension in formats.FILE_FORMATS:
        assert f" {extension}" in listed


# --- CSV/TSV/TXT także w UTF-16 z BOM-em i w kodowaniu 8-bitowym ("przy wątpliwości przyjmij") -----

CSV_ROWS = "miasto;liczba\nŁódź;3\nŚwiętochłowice;1\n"


@pytest.mark.parametrize("extension", ["csv", "tsv", "txt"])
def test_excel_csv_in_windows_1250_is_accepted(extension):
    header = CSV_ROWS.encode("cp1250")

    assert formats.verify_file(header, extension).key == extension
    assert formats.detect_text_encoding(header, extension) == formats.CHARSET_WINDOWS_1250


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_with_a_bom_is_accepted_for_spreadsheet_data(codec):
    bom = codecs.BOM_UTF16_LE if codec == "utf-16-le" else codecs.BOM_UTF16_BE
    header = bom + CSV_ROWS.encode(codec)

    assert formats.verify_file(header, "csv").key == "csv"
    assert formats.detect_text_encoding(header, "csv") == formats.CHARSET_UTF16


def test_utf8_stays_utf8_for_spreadsheet_data():
    assert formats.detect_text_encoding(CSV_ROWS.encode(), "csv") == formats.CHARSET_UTF8
    assert formats.detect_text_encoding(b"\xef\xbb\xbf" + CSV_ROWS.encode(), "csv") == formats.CHARSET_UTF8


@pytest.mark.parametrize(
    "content",
    [
        b"a;b\n1;2\n\x00\x00\x00\x00binarne",  # NUL bez BOM-u UTF-16
        ELF_BYTES,
        ZIP_BYTES,
        b"a;b\n" + b"\x01\x02\x03\x04\x05\x06\x07\x08" * 8,  # znaki sterujące
    ],
)
def test_binary_csv_is_refused(content):
    with pytest.raises(formats.FormatError, match="nie jest plikiem tekstowym"):
        formats.verify_file(content, "csv")


@pytest.mark.parametrize("extension", ["py", "md", "json", "html"])
def test_code_and_markdown_stay_utf8_only(extension):
    for header in (CSV_ROWS.encode("cp1250"), codecs.BOM_UTF16_LE + CSV_ROWS.encode("utf-16-le")):
        with pytest.raises(formats.FormatError, match="UTF-8"):
            formats.verify_file(header, extension)


@pytest.mark.parametrize(
    ("charset", "expected"),
    [
        ("", "text/plain; charset=utf-8"),
        ("utf-8", "text/plain; charset=utf-8"),
        ("utf-16", "text/plain; charset=utf-16"),
        ("windows-1250", "text/plain; charset=windows-1250"),
    ],
)
def test_content_type_carries_the_detected_charset(charset, expected):
    assert formats.with_charset(formats.FILE_FORMATS["csv"].content_type, charset) == expected
    assert formats.with_charset("application/pdf", charset) == "application/pdf"
