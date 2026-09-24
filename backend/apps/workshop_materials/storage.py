"""Magazyn materiałów: wgrywanie wieloczęściowe z przeglądarki prosto do MinIO i podpisane odczyty.

**Dlaczego plik nie idzie przez serwer aplikacji.** Nagranie warsztatu waży setki megabajtów, bywa
kilka gigabajtów. Gunicorn ma cztery procesy po cztery wątki w limicie 2 GB pamięci, a przed nim
Caddy z limitem treści żądania ``MAX_UPLOAD_MB``. Plik przepuszczony przez widok zająłby wątek na
kilkadziesiąt minut i dysk tymczasowy kontenera (``/tmp`` to 256 MB tmpfs) – czyli nie zmieściłby
się wcale. Dlatego przeglądarka koordynatora wysyła plik **prosto do MinIO**, częściami, na adresy
podpisane przez serwer (``upload_part``), a serwer:

1. zakłada wgrywanie (``CreateMultipartUpload``) i zapamiętuje jego identyfikator,
2. podpisuje adresy kolejnych części – wyłącznie numerów od 1 do liczby części wynikającej
   z zadeklarowanego rozmiaru, każdą na ``UPLOAD_URL_TTL_SECONDS``,
3. w kroku „zakończ” sam pyta MinIO o listę części (``ListParts``) – nie wierzy liście od
   przeglądarki – i sprawdza, że są wszystkie i mają właściwe rozmiary, składa plik
   (``CompleteMultipartUpload``), czyta jego rozmiar (``HeadObject``) i pierwsze bajty (``Range``).

Przeglądarka nie musi przy tym czytać nagłówka ``ETag`` z odpowiedzi MinIO (to wymagałoby
``Access-Control-Expose-Headers``) – ETagi bierze serwer z ``ListParts``.

**Rozmiar części** (``PART_SIZE``) to 16 MB: MinIO wymaga co najmniej 5 MB na część (poza ostatnią)
i najwyżej 10 000 części, a Caddy przed MinIO przepuszcza żądania do ``MAX_UPLOAD_MB`` (domyślnie
25 MB) – część musi się zmieścić **pod** tym limitem. 16 MB × 10 000 = 160 GB, więc limit części
nigdy nie jest tu ograniczeniem.

**Konto i bucket** są te same, co przy rozwiązaniach (``S3SubmissionStorage``): prywatne konto
serwisowe ``S3_PRIVATE_*`` z polityką wyłącznie na bucket ``submissions``. Wgrywanie wieloczęściowe
wymaga w tej polityce trzech dodatkowych uprawnień (``s3:AbortMultipartUpload``,
``s3:ListMultipartUploadParts``, ``s3:ListBucketMultipartUploads``) – dopisanych w
``deploy/minio/policy-submissions.json`` (``docs/OPERACJE.md``).

Klasa jest **wymienialna ustawieniem** (``WORKSHOP_MATERIALS_STORAGE_BACKEND``), bo testy nie mają
MinIO – podstawiają magazyn w pamięci (``apps/workshop_materials/tests/helpers.py``).
"""

from __future__ import annotations

import logging
from typing import BinaryIO

from django.conf import settings
from django.utils.module_loading import import_string

from apps.submissions.storage import S3SubmissionStorage

logger = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024

#: Rozmiar części wgrywania – patrz docstring modułu. Musi być < ``MAX_UPLOAD_MB`` Caddy'ego.
PART_SIZE = 16 * MEGABYTE

#: Twardy limit liczby części w S3/MinIO.
MAX_PARTS = 10_000

#: Ważność adresu jednej części. Godzina wystarcza na 16 MB nawet przy łączu 0,1 Mb/s, a adresy są
#: podpisywane partiami w miarę postępu (``PARTS_PER_SIGN``), więc długie wgrywanie nie potrzebuje
#: dłuższego życia jednego adresu.
UPLOAD_URL_TTL_SECONDS = 3600

#: Ile adresów części podpisujemy na jedno żądanie przeglądarki.
PARTS_PER_SIGN = 20

#: Ważność adresu filmu w odtwarzaczu. Dwie godziny = typowy warsztat obejrzany jednym ciągiem
#: (przewijanie wysyła kolejne żądania ``Range`` na **ten sam** adres). Po wygaśnięciu wystarczy
#: odświeżyć stronę – nowy widok to nowy podpis.
VIDEO_URL_TTL_SECONDS = 2 * 3600

#: Ważność adresu pobrania pliku. Pięć minut: adres jest używany natychmiast po przekierowaniu,
#: a krótki czas życia ogranicza pożytek z adresu przekazanego dalej.
FILE_URL_TTL_SECONDS = 300


class MaterialStorage:
    """Kontrakt magazynu materiałów. Implementacja S3 niżej, w testach – słownik w pamięci."""

    def create_upload(self, key: str, content_type: str) -> str:  # pragma: no cover - kontrakt
        raise NotImplementedError

    def presign_part(self, key: str, upload_id: str, part_number: int) -> str:  # pragma: no cover
        raise NotImplementedError

    def list_parts(self, key: str, upload_id: str) -> list[dict]:  # pragma: no cover
        """``[{"PartNumber": n, "ETag": "...", "Size": b}, …]`` – od MinIO, nie od przeglądarki."""
        raise NotImplementedError

    def complete_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:  # pragma: no cover
        raise NotImplementedError

    def abort_upload(self, key: str, upload_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def size(self, key: str) -> int | None:  # pragma: no cover
        """Rozmiar obiektu albo ``None``, gdy obiektu nie ma."""
        raise NotImplementedError

    def read_head(self, key: str, length: int) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def open(self, key: str) -> BinaryIO:  # pragma: no cover
        raise NotImplementedError

    def presigned_get(
        self, key: str, *, ttl: int, content_type: str, content_disposition: str
    ) -> str:  # pragma: no cover
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError


class S3MaterialStorage(MaterialStorage):
    """MinIO przez boto3 – te same dwa klienty, co ``S3SubmissionStorage`` (wewnętrzny i podpisujący).

    Podpisy części i odczytów **muszą** iść klientem podpisującym (``S3_PUBLIC_ENDPOINT_URL``):
    SigV4 obejmuje nagłówek ``Host``, a przeglądarka łączy się z adresem publicznym. Pozostałe
    wywołania (``ListParts``, ``Complete…``, ``HeadObject``, odczyt nagłówka) idą siecią compose.
    """

    def __init__(self, bucket: str | None = None):
        self._s3 = S3SubmissionStorage(bucket)
        self.bucket = self._s3.bucket

    @property
    def client(self):
        return self._s3.client

    @property
    def presign_client(self):
        return self._s3.presign_client

    def create_upload(self, key: str, content_type: str) -> str:
        response = self.client.create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=content_type)
        return response["UploadId"]

    def presign_part(self, key: str, upload_id: str, part_number: int) -> str:
        # Podpis liczony lokalnie – bez żądania sieciowego (tak samo jak ``presigned_get_url``).
        return self.presign_client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": part_number},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        parts: list[dict] = []
        marker = 0
        while True:
            response = self.client.list_parts(
                Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker, MaxParts=1000
            )
            parts.extend(
                {"PartNumber": part["PartNumber"], "ETag": part["ETag"], "Size": part["Size"]}
                for part in response.get("Parts", [])
            )
            if not response.get("IsTruncated"):
                return parts
            marker = response["NextPartNumberMarker"]

    def complete_upload(self, key: str, upload_id: str, parts: list[dict]) -> None:
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]},
        )

    def abort_upload(self, key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError:
            return None

    def read_head(self, key: str, length: int) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{length - 1}")["Body"]
        try:
            return body.read(length)
        finally:
            body.close()

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def presigned_get(self, key: str, *, ttl: int, content_type: str, content_disposition: str) -> str:
        # Oba nagłówki odpowiedzi wchodzą do podpisu – widz nie podmieni ani typu, ani nazwy pliku.
        # ``Range`` do podpisu nie wchodzi, więc przewijanie filmu działa na tym samym adresie.
        return self.presign_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": content_disposition,
            },
            ExpiresIn=int(ttl),
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_material_storage() -> MaterialStorage:
    """Fabryka wybierana ustawieniem ``WORKSHOP_MATERIALS_STORAGE_BACKEND`` (domyślnie S3)."""
    path = getattr(
        settings, "WORKSHOP_MATERIALS_STORAGE_BACKEND", "apps.workshop_materials.storage.S3MaterialStorage"
    )
    return import_string(path)()


def delete_quietly(storage: MaterialStorage, key: str) -> bool:
    """Kasuje obiekt, nie wywracając wołającego. ``False`` = obiekt został (log dla operatora).

    Obiekt, którego nie udało się skasować, jest sierotą w buckecie – kosztem miejsca, a nie błędem,
    który koordynator naprawiłby z formularza. Log niesie **klucz** (nie ma w nim nic o człowieku),
    żeby operator mógł go sprzątnąć ręcznie: ``mc rm local/submissions/<klucz>``.
    """
    if not key:
        return True
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001 - patrz docstring
        logger.warning("Nie udało się skasować obiektu %s z magazynu materiałów.", key, exc_info=True)
        return False
    return True


def abort_quietly(storage: MaterialStorage, key: str, upload_id: str) -> None:
    """Porzuca wgrywanie wieloczęściowe; brak wgrywania (już złożone/porzucone) nie jest błędem."""
    if not (key and upload_id):
        return
    try:
        storage.abort_upload(key, upload_id)
    except Exception:  # noqa: BLE001 - MinIO sam sprząta porzucone części po dobie (docs/OPERACJE.md)
        logger.info("Porzucenie wgrywania %s nie powiodło się (zapewne już zamknięte).", key, exc_info=True)
