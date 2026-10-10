"""Własny model dokumentu Wagtaila: plik w **prywatnym** storage pod nieodgadywalnym kluczem.

**Dlaczego (audyt 10.10.2026, W2).** Domyślny ``wagtaildocs.Document`` zapisuje plik pod
``documents/<oryginalna nazwa>`` w storage ``default`` – w produkcji to anonimowo czytelny bucket
``public-media``. Ograniczenie widoczności kolekcji („tylko zalogowani”, „z hasłem”) sprawdzał
wyłącznie widok ``/documents/<id>/<nazwa>``, a ten sam plik leżał pod adresem bucketu bez żadnej
kontroli: bezterminowo, także po odebraniu dostępu, a nazwy w rodzaju ``regulamin.pdf`` dało się
zgadnąć. Ograniczenie kolekcji nie chroniło więc niczego.

Stąd dwie zmiany naraz, bo każda z osobna jest za słaba:

- **storage prywatny** (:func:`private_documents_storage`): w produkcji bucket ``submissions`` (ten
  sam, co treści zadań, bez anonimowego odczytu) pod własnym prefiksem ``documents/``; lokalnie
  i w testach ``MEDIA_ROOT/private/documents``. Plik oddaje wyłącznie widok aplikacji
  (``WAGTAILDOCS_SERVE_METHOD = "serve_view"``, ``apps.cms.views.serve``), który najpierw sprawdza
  ograniczenia kolekcji, a potem streamuje obiekt (``FileResponse`` ze strumienia storage – ten sam
  wzorzec, co ``ProblemStatementView``). ``document.url`` to zawsze adres tego widoku, nigdy bucketu,
- **klucz z ``uuid4``** (:func:`document_upload_to`): ``<32 znaki hex>/<oryginalna nazwa>``. Nawet
  gdyby obiekt kiedyś trafił do miejsca czytelnego z zewnątrz (pomyłka w polityce, kopia), jego
  adresu nie da się odgadnąć z nazwy pliku. Nazwa pliku zostaje na końcu klucza, bo Wagtail buduje
  z niej ``filename`` – a więc i adres ``/documents/<id>/<nazwa>``, który już krąży w treściach,
  listach i zakładkach.

Model nazywa się ``cms.Document`` (``WAGTAILDOCS_DOCUMENT_MODEL``). Istniejące wiersze
``wagtaildocs.Document`` przenosi migracja ``cms.0031``–``cms.0034`` z zachowaniem kluczy głównych,
a pliki z publicznego bucketu do prywatnego – polecenie ``migrate_documents_to_private``
(uruchamiane raz, zaraz po migracji – opis w docstringu polecenia).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.db import models
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from taggit.managers import TaggableManager
from wagtail.documents.models import AbstractDocument
from wagtail.documents.models import Document as WagtailDocument

from apps.competitions.storage import PRIVATE_MEDIA_ALIAS, PrivateMediaFileSystemStorage

#: Alias w ``settings.STORAGES``, którym produkcja **może** podać storage dokumentów wprost.
#: Bez niego storage jest wyprowadzany z ``private_media`` (patrz :func:`private_documents_storage`).
DOCUMENTS_STORAGE_ALIAS = "private_documents"

#: Prefiks obiektów w prywatnym buckecie. Osobny od ``problem-statements``: listing bucketu ma
#: mówić, co jest czym, a polecenia porządkowe treści zadań nie mogą sięgać po dokumenty.
DOCUMENTS_PREFIX = "documents"


class PrivateDocumentsFileSystemStorage(PrivateMediaFileSystemStorage):
    """Lokalny odpowiednik prefiksu ``documents/`` w prywatnym buckecie: ``MEDIA_ROOT/private/documents``.

    Podkatalog ``private/``, a nie ``MEDIA_ROOT`` wprost, z tego samego powodu, co przy treściach
    zadań: test „dokument nie leży w drzewie storage ``default``” ma sprawdzać skutek, a nie nazwę.
    """

    subdirectory = f"{PrivateMediaFileSystemStorage.subdirectory}/{DOCUMENTS_PREFIX}"


def private_documents_storage() -> Storage:
    """Storage plików dokumentów Wagtaila – zawsze prywatny.

    Kolejność:

    1. alias ``private_documents`` z ``settings.STORAGES``, jeśli istnieje (jawna konfiguracja
       produkcji wygrywa),
    2. w przeciwnym razie **ten sam backend i te same opcje**, co ``private_media`` (konto
       serwisowe i bucket prywatny), z podmienionym prefiksem na ``documents``,
    3. lokalnie (``private_media`` na dysku) – :class:`PrivateDocumentsFileSystemStorage`.

    Wyprowadzenie z ``private_media`` zamiast nowego aliasu w ``production.py`` jest celowe: konto
    serwisowe, bucket i adres MinIO są w jednym miejscu, a dokumenty nie mogą się z nimi rozjechać.

    Callable, nie instancja: migracja zapisuje referencję do funkcji, więc zmiana backendu
    w ustawieniach nie generuje nowej migracji.
    """
    if DOCUMENTS_STORAGE_ALIAS in settings.STORAGES:
        return storages[DOCUMENTS_STORAGE_ALIAS]
    return storage_from_private_media(settings.STORAGES[PRIVATE_MEDIA_ALIAS])


def storage_from_private_media(config: dict) -> Storage:
    """Storage dokumentów z konfiguracji aliasu ``private_media``: ten sam backend, prefiks ``documents``."""
    backend = import_string(config["BACKEND"])
    if issubclass(backend, FileSystemStorage):
        return PrivateDocumentsFileSystemStorage()
    return backend(**{**config.get("OPTIONS", {}), "location": DOCUMENTS_PREFIX})


def document_upload_to(instance, filename: str) -> str:
    """``<uuid4 hex>/<nazwa>`` – klucz, którego nie da się odgadnąć z nazwy pliku.

    Katalog z losowym identyfikatorem, a nie losowa nazwa pliku: Wagtail bierze ``filename``
    dokumentu z ostatniego członu klucza i z niego buduje adres ``/documents/<id>/<nazwa>``
    oraz nagłówek ``Content-Disposition``. Czytelnik ma dostać ``regulamin.pdf``, a nie
    ``3f2a…​.pdf``.
    """
    return f"{uuid.uuid4().hex}/{filename}"


class Document(AbstractDocument):
    """Dokument biblioteki Wagtaila z plikiem w prywatnym storage (docstring modułu)."""

    file = models.FileField(
        upload_to=document_upload_to,
        storage=private_documents_storage,
        # 255 zamiast domyślnych 100: 33 znaki prefiksu z ``uuid4`` zjadałyby długie nazwy plików,
        # a storage przycinałby je po cichu (``get_available_name``).
        max_length=255,
        verbose_name=_("file"),
    )
    # Dwa pola powtórzone z ``AbstractDocument`` wyłącznie dla ``related_name``: obok tego modelu
    # nadal istnieje ``wagtaildocs.Document`` (Wagtail rejestruje go zawsze), a oba nazywają się
    # „Document” – domyślne ``User.document_set`` i ``Tag.document_set`` kolidowałyby między nimi.
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("uploaded by user"),
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="cms_documents",
    )
    uploaded_by_user.wagtail_reference_index_ignore = True
    tags = TaggableManager(help_text=None, blank=True, verbose_name=_("tags"), related_name="cms_documents")

    admin_form_fields = WagtailDocument.admin_form_fields

    class Meta(AbstractDocument.Meta):
        permissions = [("choose_document", "Can choose document")]
