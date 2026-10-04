"""Magazyn nagrań webinarów – prywatny bucket MinIO, odczyt wyłącznie adresem podpisanym na krótko.

Plik MP4 zapisuje **serwer LiveKit Egress** własnym kontem serwisowym (konfiguracja
``deploy/livekit/egress.yaml``, uprawnienie zapisu tylko do prefiksu ``webinars/``). Platforma
nagrania nie przesyła – podpisuje adres odczytu dla uprawnionego widza (``RECORDING_URL_TTL_SECONDS``)
i kasuje obiekt na żądanie koordynatora. Klient S3 jest ten sam, co przy materiałach z warsztatów
(``apps.workshop_materials.storage.S3MaterialStorage``: konto ``S3_PRIVATE_*``, podpis adresem
publicznym), z bucketem z ``LIVEKIT_RECORDINGS_BUCKET``.

Klasa jest wymienialna ustawieniem (``WEBINARS_STORAGE_BACKEND``) – testy podstawiają magazyn w pamięci.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

#: Ważność adresu nagrania w odtwarzaczu. Dwie godziny = webinar obejrzany jednym ciągiem
#: (przewijanie wysyła kolejne żądania ``Range`` na ten sam adres); potem wystarczy odświeżyć stronę.
RECORDING_URL_TTL_SECONDS = 2 * 3600

#: Prefiks kluczy nagrań w buckecie – ten sam, na który egress ma prawo zapisu.
KEY_PREFIX = "webinars/"


class S3RecordingStorage:
    def __init__(self):
        from apps.workshop_materials.storage import S3MaterialStorage

        self._s3 = S3MaterialStorage(settings.LIVEKIT_RECORDINGS_BUCKET or None)

    def presigned_get(self, key: str, *, ttl: int, content_type: str, content_disposition: str) -> str:
        return self._s3.presigned_get(
            key, ttl=ttl, content_type=content_type, content_disposition=content_disposition
        )

    def delete(self, key: str) -> None:
        self._s3.delete(key)


def get_recording_storage():
    path = getattr(settings, "WEBINARS_STORAGE_BACKEND", "apps.webinars.storage.S3RecordingStorage")
    return import_string(path)()
