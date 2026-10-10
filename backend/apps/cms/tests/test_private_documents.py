"""Dokumenty Wagtaila w prywatnym storage pod nieodgadywalnym kluczem (audyt 10.10.2026, W2).

Przed zmianą plik leżał w ``public-media`` pod ``documents/<oryginalna nazwa>``: ograniczenie
widoczności kolekcji sprawdzał widok ``/documents/<id>/<nazwa>``, a ten sam plik dało się pobrać
anonimowo prosto z bucketu. Te testy pilnują trzech rzeczy:

- plik trafia do **prywatnego** storage (lokalnie ``MEDIA_ROOT/private/documents``), a nie do
  drzewa ``default``, i to pod kluczem z ``uuid4``,
- jedyną drogą do treści jest widok z kontrolą kolekcji, a ``document.url`` wskazuje na ten widok,
- polecenie ``migrate_documents_to_private`` przenosi pliki sprzed zmiany i jest idempotentne.
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from wagtail.documents import get_document_model
from wagtail.models import Collection, CollectionViewRestriction

from apps.accounts.tests.factories import DEFAULT_PASSWORD
from apps.cms.documents import (
    Document,
    PrivateDocumentsFileSystemStorage,
    storage_from_private_media,
)

pytestmark = pytest.mark.django_db

KEY = re.compile(r"^[0-9a-f]{32}/regulamin\.pdf$")


def _document(collection=None, name="regulamin.pdf", body=b"%PDF-1.4 regulamin"):
    return Document.objects.create(
        title="Regulamin",
        collection=collection or Collection.get_first_root_node(),
        file=SimpleUploadedFile(name, body, content_type="application/pdf"),
    )


def test_the_document_model_is_the_private_one(settings):
    assert settings.WAGTAILDOCS_DOCUMENT_MODEL == "cms.Document"
    assert get_document_model() is Document


def test_the_document_storage_is_private_and_separate_from_default():
    storage = Document._meta.get_field("file").storage

    assert isinstance(storage, PrivateDocumentsFileSystemStorage)
    assert storage is not storages["default"]


def test_an_uploaded_document_lands_in_the_private_storage_under_an_unguessable_key():
    document = _document()

    assert KEY.match(document.file.name), document.file.name
    private = Document._meta.get_field("file").storage
    assert private.exists(document.file.name)
    # Fizycznie poza drzewem ``default`` – nie wystarczy nazwa aliasu.
    assert not storages["default"].exists(document.file.name)
    assert not storages["default"].exists(f"documents/{document.filename}")
    assert "private" in private.path(document.file.name).replace("\\", "/").split("/")


def test_two_uploads_of_the_same_name_get_different_keys():
    first, second = _document(), _document()

    assert first.file.name != second.file.name
    assert first.filename == second.filename == "regulamin.pdf"


def test_the_document_url_is_the_serve_view_not_the_storage_address():
    document = _document()

    assert document.url == f"/documents/{document.pk}/regulamin.pdf"
    assert not document.url.startswith(("http://", "https://", "/media/"))


def test_a_login_restricted_document_redirects_anonymous_and_serves_a_logged_in_user(web_client, coordinator):
    collection = Collection.get_first_root_node().add_child(name="Tylko zalogowani")
    CollectionViewRestriction.objects.create(
        collection=collection, restriction_type=CollectionViewRestriction.LOGIN
    )
    document = _document(collection=collection, body=b"%PDF-1.4 tajne")
    url = f"/documents/{document.pk}/{document.filename}"

    anonymous = web_client.get(url)
    assert anonymous.status_code == 302

    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)
    allowed = web_client.get(url)

    assert allowed.status_code == 200
    assert b"".join(allowed.streaming_content) == b"%PDF-1.4 tajne"


def test_the_serve_view_streams_from_a_storage_without_a_local_path(web_client, monkeypatch):
    """Produkcja to S3: storage bez ``path()``. Widok ma oddać plik strumieniem, nie przekierowaniem."""
    from django.db.models.fields.files import FieldFile

    document = _document(body=b"%PDF-1.4 zdalny")

    def no_local_path(self):
        raise NotImplementedError("storage zdalny")

    # ``FieldFile.path`` – tak pyta Wagtail; storage pod spodem nadal czyta z dysku.
    monkeypatch.setattr(FieldFile, "path", property(no_local_path))

    response = web_client.get(f"/documents/{document.pk}/{document.filename}")

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF-1.4 zdalny"


def test_the_storage_for_s3_reuses_the_private_bucket_with_its_own_prefix():
    """Bez aliasu ``private_documents`` storage powstaje z ``private_media`` z prefiksem ``documents``.

    Konfiguracja podana wprost, a nie przez ``settings.STORAGES``: zmiana tego ustawienia w teście
    przebudowuje ``storages`` i rozjeżdża instancje przypięte do pól modeli w kolejnych testach.
    """
    storage = storage_from_private_media(
        {
            "BACKEND": "apps.core.storage.ExtensionContentTypeS3Storage",
            "OPTIONS": {"bucket_name": "submissions", "location": "problem-statements"},
        }
    )

    assert storage.bucket_name == "submissions"
    assert storage.location == "documents"


# --- przeniesienie plików sprzed zmiany ------------------------------------------------------


def _legacy_document(body=b"%PDF-1.4 stary"):
    """Wiersz jak po migracji ``cms.0032``: plik nadal w ``default`` pod ``documents/<nazwa>``."""
    public = storages["default"]
    name = public.save("documents/regulamin.pdf", ContentFile(body))
    document = _document(body=b"tymczasowy")
    temporary = document.file.name
    Document.objects.filter(pk=document.pk).update(file=name)
    Document._meta.get_field("file").storage.delete(temporary)
    document.refresh_from_db()
    return document, name


def test_the_move_command_dry_run_changes_nothing():
    document, name = _legacy_document()
    out = StringIO()

    call_command("migrate_documents_to_private", "--dry-run", stdout=out)

    document.refresh_from_db()
    assert document.file.name == name
    assert storages["default"].exists(name)
    assert "Do przeniesienia: 1" in out.getvalue()


def test_the_move_command_moves_the_file_to_the_private_storage_and_is_idempotent(web_client):
    document, name = _legacy_document()

    call_command("migrate_documents_to_private", stdout=StringIO())

    document.refresh_from_db()
    private = Document._meta.get_field("file").storage
    assert KEY.match(document.file.name), document.file.name
    assert private.exists(document.file.name)
    assert not storages["default"].exists(name)
    response = web_client.get(document.url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF-1.4 stary"

    out = StringIO()
    call_command("migrate_documents_to_private", stdout=out)

    document.refresh_from_db()
    assert private.exists(document.file.name)
    assert "Przeniesiono: 0, już prywatne: 1" in out.getvalue()


def test_the_move_command_can_keep_the_public_copy():
    document, name = _legacy_document()

    call_command("migrate_documents_to_private", "--keep-public", stdout=StringIO())

    document.refresh_from_db()
    assert KEY.match(document.file.name)
    assert storages["default"].exists(name)


def test_the_move_command_reports_a_missing_file():
    document, name = _legacy_document()
    storages["default"].delete(name)
    err = StringIO()

    call_command("migrate_documents_to_private", stdout=StringIO(), stderr=err)

    assert f"#{document.pk}" in err.getvalue()
