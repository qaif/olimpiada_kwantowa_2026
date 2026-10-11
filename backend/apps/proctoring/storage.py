"""Magazyn nośników nadzoru – prywatny bucket, odczyt wyłącznie adresem podpisanym na krótko.

Dwa rodzaje obiektów, oba pod prefiksem :data:`KEY_PREFIX`:

- **nagrania kamer** – zapisuje je serwer LiveKit Egress własnym kontem (prawo zapisu wyłącznie do
  ``webinars/`` i ``proctoring/``, ``deploy/livekit/policy-egress.json``); platforma ich nie przesyła,
- **zdjęcia dokumentu** – JPEG z konsoli ucznia, zapisywany przez platformę (``put``).

Bucket ten sam, co nagrań webinarów (``LIVEKIT_RECORDINGS_BUCKET``, pusty = bucket prac), klient
S3 ten sam, co materiałów z warsztatów. Wymienialne ustawieniem ``PROCTORING_STORAGE_BACKEND``
(testy – pamięć).
"""

from __future__ import annotations

import io
import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

KEY_PREFIX = "proctoring/"
#: Adres nagrania/zdjęcia dla komisji: kwadrans – obejrzenie fragmentu, nie archiwum w zakładkach.
MEDIA_URL_TTL_SECONDS = 15 * 60


class S3ProctoringStorage:
    def __init__(self):
        from apps.workshop_materials.storage import S3MaterialStorage

        self._s3 = S3MaterialStorage(settings.LIVEKIT_RECORDINGS_BUCKET or None)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.client.put_object(
            Bucket=self._s3.bucket, Key=key, Body=io.BytesIO(data), ContentType=content_type
        )

    def presigned_get(self, key: str, *, ttl: int, content_type: str, content_disposition: str) -> str:
        return self._s3.presigned_get(
            key, ttl=ttl, content_type=content_type, content_disposition=content_disposition
        )

    def delete(self, key: str) -> None:
        self._s3.delete(key)


def get_storage():
    path = getattr(settings, "PROCTORING_STORAGE_BACKEND", "apps.proctoring.storage.S3ProctoringStorage")
    return import_string(path)()


def delete_quietly(key: str) -> bool:
    """Kasuje obiekt nadzoru; awaria magazynu zostaje w logu (klucz bez danych osoby), nie w wyjątku."""
    if not key or not key.startswith(KEY_PREFIX):
        return True
    try:
        get_storage().delete(key)
    except Exception:  # noqa: BLE001 - sierota w buckecie kosztuje miejsce, a nie zawody
        logger.warning("Nie udało się skasować obiektu nadzoru %s.", key, exc_info=True)
        return False
    return True
