"""Typ pliku w MinIO z rozszerzenia, nie od przeglądarki (apps/core/storage.py, audyt z 1.10.2026).

Przed zmianą ``S3Storage`` zapisywał ``ContentType`` z części formularza multipart – redaktor mógł
wgrać ``x.txt`` z ``Content-Type: text/html``, a anonimowo czytelny bucket ``public-media`` podawał
go potem jako HTML. Testy są **offline**: nie tworzą klienta boto3 ani nie łączą się z S3. Zapis
przechodzi prawdziwą ścieżkę ``Storage.save`` → ``S3Storage._save`` → ``_get_write_parameters``, ale
kończy się na atrapie bucketu, która zapamiętuje ``ExtraArgs`` przekazane do ``upload_fileobj``.

Że produkcja w ogóle używa tej klasy (oba aliasy na S3), sprawdza
``apps/core/tests/test_production_guards.py`` – w podprocesie, na module ustawień produkcyjnych.
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.storage import (
    FALLBACK_CONTENT_TYPE,
    ExtensionContentTypeS3Storage,
    content_type_for,
    object_parameters_for,
)


class _RecordingObject:
    def __init__(self, calls: list, name: str):
        self._calls = calls
        self._name = name

    def upload_fileobj(self, fileobj, ExtraArgs=None, Config=None):  # noqa: N803 - sygnatura boto3
        self._calls.append((self._name, dict(ExtraArgs or {}), fileobj.read()))


class _RecordingBucket:
    """Atrapa ``boto3.resource('s3').Bucket`` – jedyne, czego dotyka ``S3Storage._save``."""

    def __init__(self):
        self.calls: list[tuple[str, dict, bytes]] = []

    def Object(self, name):  # noqa: N802 - nazwa z API boto3
        return _RecordingObject(self.calls, name)


@pytest.fixture
def storage(monkeypatch):
    """Storage jak alias ``default`` w produkcji, bez sieci: bucket-atrapa, ``exists`` zawsze ``False``."""
    instance = ExtensionContentTypeS3Storage(
        bucket_name="public-media",
        endpoint_url="http://minio:9000",
        access_key="klucz-testowy",
        secret_key="sekret-testowy",
        file_overwrite=False,
        default_acl=None,
        querystring_auth=False,
    )
    instance._bucket = _RecordingBucket()
    monkeypatch.setattr(instance, "exists", lambda name: False)
    return instance


def _saved(storage, name, upload) -> dict:
    storage.save(name, upload)
    calls = storage._bucket.calls
    assert len(calls) == 1, calls
    return calls[0][1]


@pytest.mark.parametrize(
    ("name", "claimed", "expected_type"),
    [
        # Klient kłamie o typie – zapisany jest typ z rozszerzenia, a plik nie renderuje się w karcie.
        ("documents/notatka.txt", "text/html", "text/plain"),
        ("documents/strona.html", "image/png", "text/html"),
        (
            "documents/raport.docx",
            "text/html",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        # SVG to obraz, ale i dokument XML ze skryptami – typ prawdziwy, ale pobranie wymuszone.
        ("original_images/logo.svg", "image/svg+xml", "image/svg+xml"),
        # Bez rozszerzenia i z kodowaniem – bezpieczny typ binarny, bez Content-Encoding.
        ("documents/bez-rozszerzenia", "text/html", FALLBACK_CONTENT_TYPE),
        ("documents/archiwum.tar.gz", "text/html", FALLBACK_CONTENT_TYPE),
        ("original_images/logo.svgz", "image/svg+xml", FALLBACK_CONTENT_TYPE),
    ],
)
def test_attachment_types_ignore_the_client_content_type(storage, name, claimed, expected_type):
    params = _saved(
        storage,
        name,
        SimpleUploadedFile(name.rsplit("/", 1)[-1], b"<script>1</script>", content_type=claimed),
    )

    assert params["ContentType"] == expected_type
    assert params["ContentDisposition"] == "attachment"
    assert "ContentEncoding" not in params


@pytest.mark.parametrize(
    ("name", "claimed", "expected_type"),
    [
        ("original_images/zdjecie.JPG", "text/html", "image/jpeg"),
        ("images/zdjecie.2e16d0ba.fill-300x200.format-webp.webp", "text/html", "image/webp"),
        ("original_images/wykres.png", "application/octet-stream", "image/png"),
        ("documents/regulamin.PDF", "text/html", "application/pdf"),
        ("media/nagranie.mp4", "text/html", "video/mp4"),
        ("media/podcast.mp3", "text/html", "audio/mpeg"),
    ],
)
def test_inline_types_come_from_the_extension_without_disposition(storage, name, claimed, expected_type):
    params = _saved(
        storage, name, SimpleUploadedFile(name.rsplit("/", 1)[-1], b"bajty", content_type=claimed)
    )

    assert params["ContentType"] == expected_type
    assert "ContentDisposition" not in params
    assert "ContentEncoding" not in params


def test_content_without_declared_type_is_handled_the_same(storage):
    """``ContentFile`` (zapis z kodu, np. rendition Wagtaila) nie ma ``content_type`` – wynik ten sam."""
    params = _saved(storage, "documents/eksport.html", ContentFile(b"<p>x</p>"))

    assert params == {"ContentType": "text/html", "ContentDisposition": "attachment"}


def test_object_parameters_from_settings_cannot_reintroduce_a_type(storage):
    """``AWS_S3_OBJECT_PARAMETERS`` (``object_parameters``) nie przywróci typu ani kodowania z zewnątrz."""
    storage.object_parameters = {
        "ContentType": "text/html",
        "ContentEncoding": "gzip",
        "ContentDisposition": "inline",
        "CacheControl": "public, max-age=86400",
    }

    params = storage.get_object_parameters("documents/notatka.txt")

    assert params == {
        "ContentType": "text/plain",
        "ContentDisposition": "attachment",
        "CacheControl": "public, max-age=86400",
    }


def test_location_prefix_and_case_do_not_change_the_result():
    assert content_type_for("problem-statements/Zadanie.PDF") == "application/pdf"
    assert object_parameters_for("problem-statements/Zadanie.PDF") == {"ContentType": "application/pdf"}
    assert content_type_for("") == FALLBACK_CONTENT_TYPE
    assert content_type_for("katalog.pdf/plik") == FALLBACK_CONTENT_TYPE


def test_read_parameters_stay_free_of_write_metadata(storage):
    """Odczyt (``exists``/``open``) bierze z tej metody wyłącznie parametry SSE-C – nasze klucze odpadają."""
    from storages.backends.s3 import _filter_download_params

    assert _filter_download_params(storage.get_object_parameters("documents/notatka.txt")) == {}
