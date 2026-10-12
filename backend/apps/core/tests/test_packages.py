"""Katalog roboczy i limit paczek ZIP (``apps.core.packages``; audyt 10.10.2026, S15)."""

from __future__ import annotations

import io

import pytest

from apps.core.api import DomainError
from apps.core.packages import PackageTooLarge, ensure_package_fits, package_tempfile
from apps.submissions.models import AvStatus
from apps.submissions.packaging import build_zip
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFileFactory


def test_the_temporary_file_lands_in_the_package_directory(settings, tmp_path):
    directory = tmp_path / "paczki"
    settings.PACKAGE_TMP_DIR = str(directory)

    with package_tempfile() as stream:
        stream.write(b"x")
        # ``TemporaryFile`` na POSIX-ie nie ma nazwy w katalogu, ale katalog musi istnieć
        # i to w nim system zakłada plik – inaczej ``dir=`` rzuciłoby ``FileNotFoundError``.
        assert directory.is_dir()


def test_a_package_within_the_limit_passes(settings):
    settings.PACKAGE_MAX_BYTES = 100

    ensure_package_fits(100)


def test_a_package_above_the_limit_is_a_readable_domain_error(settings):
    settings.PACKAGE_MAX_BYTES = 200 * 1024 * 1024

    with pytest.raises(PackageTooLarge) as caught:
        ensure_package_fits(200 * 1024 * 1024 + 1, hint="Zawęź zakres.")

    error = caught.value
    assert isinstance(error, DomainError)
    assert error.status_code == 413
    assert "201 MB" in str(error.detail)
    assert "200 MB" in str(error.detail)
    assert "Zawęź zakres." in str(error.detail)


@pytest.mark.django_db
def test_build_zip_refuses_a_package_above_the_limit_before_reading_the_storage(settings, monkeypatch):
    settings.PACKAGE_MAX_BYTES = 10
    item = SubmissionFileFactory(av_status=AvStatus.CLEAN, size_bytes=11)
    opened = []
    storage_class = type(get_submission_storage())
    monkeypatch.setattr(storage_class, "open", lambda self, key: opened.append(key) or io.BytesIO(b""))

    with pytest.raises(PackageTooLarge):
        build_zip([item.submission])

    assert opened == []


@pytest.mark.django_db
def test_build_zip_counts_only_files_that_go_into_the_package(settings):
    """Plik w kwarantannie nie wchodzi do paczki, więc nie liczy się też do limitu."""
    settings.PACKAGE_MAX_BYTES = 10
    item = SubmissionFileFactory(av_status=AvStatus.INFECTED, size_bytes=10**9)

    package = build_zip([item.submission])
    package.stream.close()

    assert package.count == 0
