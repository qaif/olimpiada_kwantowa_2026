"""Skan ClamAV mediów redakcyjnych (dokumenty i obrazy wgrywane w ``/cms/``).

**Dlaczego (audyt 10.10.2026, niskie).** Prace uczestników, zaświadczenia, materiały z warsztatów
i paczki motywów przechodzą przez clamd; dokumenty Wagtaila (``zip``, ``doc``, ``xls``…) i obrazy
– nie. A to właśnie one są linkowane z publicznych stron serwisu i pobierane przez szkoły.

**Dlaczego formularz, a nie sygnał ``pre_save``.** Odrzucenie ma być **czytelnym błędem walidacji
przy polu pliku** – redaktor widzi komunikat w formularzu (także przy wgrywaniu wielu plików
naraz), a nie stronę błędu 500. Wagtail pozwala podmienić bazę formularza ustawieniami
``WAGTAILDOCS_DOCUMENT_FORM_BASE`` / ``WAGTAILIMAGES_IMAGE_FORM_BASE`` i z tej bazy budują się
formularze dodawania, edycji i wgrywania wielu plików. Wyjątek rzucony z ``pre_save`` przerwałby
zapis bez komunikatu. Pliki tworzone z kodu (``apps.cms.attachments``, ``apps.cms.images``) to
materiały z repozytorium, nie od użytkownika, i skanu nie potrzebują.

**Zachowanie przy braku skanera.** Plik z wykrytym zagrożeniem jest odrzucany zawsze. Gdy clamd
nie odpowiada, plik **przechodzi** z ostrzeżeniem w logu – tak jak w reszcie serwisu praca
uczestnika jest wtedy przyjmowana i czeka na skan, a nie odrzucana; instalacja bez usługi
``clamav`` (``docker-compose.operator.yml``) nie może zablokować redakcji. Całość wyłącza
``CMS_MEDIA_AV_SCAN = False``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from wagtail.documents.forms import BaseDocumentForm
from wagtail.images.forms import BaseImageForm

logger = logging.getLogger(__name__)

#: Krócej niż domyślne 60 s klienta clamd: redaktor czeka na odpowiedź formularza, a plik, którego
#: nie dało się sprawdzić, i tak przechodzi (docstring modułu).
SCAN_TIMEOUT_SECONDS = 20

INFECTED_MESSAGE = "Plik odrzucony: skaner antywirusowy wykrył zagrożenie (%(signature)s)."
TOO_LARGE_MESSAGE = (
    "Plik jest za duży, żeby sprawdzić go antywirusem (limit %(limit)s MB). Zmniejsz go albo podziel."
)


def scan_uploaded_file(upload) -> None:
    """Skanuje świeżo wgrany plik; przy zagrożeniu rzuca ``ValidationError`` z komunikatem dla redaktora.

    Skanujemy wyłącznie ``UploadedFile`` – przy edycji bez podmiany pliku formularz oddaje
    istniejący ``FieldFile``, który był sprawdzony przy wgraniu.
    """
    if not getattr(settings, "CMS_MEDIA_AV_SCAN", True) or not isinstance(upload, UploadedFile):
        return
    from apps.submissions.antivirus import (
        VERDICT_INFECTED,
        ClamAVError,
        ClamAVStreamTooLarge,
        ClamAVUnavailable,
        scan_stream,
        stream_max_bytes,
    )

    try:
        upload.seek(0)
        verdict, signature = scan_stream(upload, size=upload.size, timeout=SCAN_TIMEOUT_SECONDS)
    except ClamAVStreamTooLarge as exc:
        raise ValidationError(
            TOO_LARGE_MESSAGE, code="av_too_large", params={"limit": stream_max_bytes() // (1024 * 1024)}
        ) from exc
    except ClamAVUnavailable, ClamAVError:
        logger.warning(
            "Skan ClamAV pliku %r z /cms/ nieudany – plik przyjęty bez skanu.", upload.name, exc_info=True
        )
        return
    finally:
        # Po skanie Wagtail czyta plik jeszcze raz (Pillow, skrót SHA-1, zapis do storage).
        upload.seek(0)
    if verdict == VERDICT_INFECTED:
        logger.warning("ClamAV odrzucił plik %r wgrywany w /cms/: %s", upload.name, signature)
        raise ValidationError(INFECTED_MESSAGE, code="av_infected", params={"signature": signature})


class _ScannedFileMixin:
    """``clean_file`` ze skanem – wspólne dla dokumentów i obrazów."""

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        scan_uploaded_file(upload)
        return upload


class ScannedDocumentForm(_ScannedFileMixin, BaseDocumentForm):
    """Baza formularzy dokumentów Wagtaila (``WAGTAILDOCS_DOCUMENT_FORM_BASE``)."""


class ScannedImageForm(_ScannedFileMixin, BaseImageForm):
    """Baza formularzy obrazów Wagtaila (``WAGTAILIMAGES_IMAGE_FORM_BASE``)."""
