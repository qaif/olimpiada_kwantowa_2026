"""Walidacja uploadu na poziomie jednostkowym: decyduje treść, nie nazwa i nie ``content_type``."""

import pytest

from apps.core.api import DomainError
from apps.submissions.tests.factories import PDF_BYTES, ZIP_BYTES, notebook_bytes, upload
from apps.submissions.validators import validate_upload


def assert_code(excinfo, code: str) -> None:
    assert excinfo.value.machine_code == code
    assert excinfo.value.status_code == 400


def test_pdf_is_recognised_by_content_not_by_content_type():
    # Nagłówek żądania kłamie ("text/plain"), treść jest prawdziwym PDF-em – liczy się treść.
    ext, mime = validate_upload(upload("a.pdf", PDF_BYTES, "text/plain"), ["pdf"], 20)

    assert (ext, mime) == ("pdf", "application/pdf")


def test_zip_content_with_pdf_extension_is_invalid():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.pdf", ZIP_BYTES, "application/pdf"), ["pdf"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_unknown_extension_is_invalid():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.exe", PDF_BYTES, "application/pdf"), ["pdf"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_size_over_limit_is_rejected_before_content_parsing():
    payload = b"%PDF-1.7\n" + b"0" * (1024 * 1024)

    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.pdf", payload, "application/pdf"), ["pdf"], 1)

    assert_code(excinfo, "FILE_TOO_LARGE")


def test_empty_file_is_invalid():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.pdf", b"", "application/pdf"), ["pdf"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_notebook_must_pass_nbformat_validation():
    broken = b'{"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [{"cell_type": "wat"}]}'

    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.ipynb", broken, "application/json"), ["ipynb"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_notebook_must_be_json():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.ipynb", b"to nie jest JSON", "application/json"), ["ipynb"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_valid_notebook_returns_notebook_mime():
    ext, mime = validate_upload(upload("a.ipynb", notebook_bytes(), "text/plain"), ["ipynb"], 20)

    assert (ext, mime) == ("ipynb", "application/x-ipynb+json")


def test_python_with_nul_bytes_is_invalid():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.py", b"print(1)\x00\x00", "text/x-python"), ["py"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_python_over_one_megabyte_is_invalid():
    with pytest.raises(DomainError) as excinfo:
        validate_upload(upload("a.py", b"#" * (1024 * 1024 + 1), "text/x-python"), ["py"], 20)

    assert_code(excinfo, "INVALID_FILE_TYPE")


def test_valid_python_is_accepted():
    ext, mime = validate_upload(upload("a.py", "print('zażółć')\n".encode(), "text/plain"), ["py"], 20)

    assert (ext, mime) == ("py", "text/x-python")


def test_stream_is_rewound_for_the_caller():
    file_obj = upload("a.pdf", PDF_BYTES, "application/pdf")

    validate_upload(file_obj, ["pdf"], 20)

    assert file_obj.read() == PDF_BYTES
