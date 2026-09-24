"""Rozpoznawanie formatu materiału po treści – filmy, które przeglądarka odtworzy, i pliki z listy.

Przedmiotem jest odrzucenie tego, co **wygląda** poprawnie po nazwie: MOV i MKV przemianowane na
``.mp4``, HTML podpisany jako ``.pdf``, PDF podpisany jako ``.pptx``.
"""

from __future__ import annotations

import pytest

from apps.workshop_materials import formats

from .helpers import HTML_BYTES, MKV_HEADER, MOV_HEADER, MP4_HEADER, PDF_BYTES, WEBM_HEADER, ZIP_BYTES


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
