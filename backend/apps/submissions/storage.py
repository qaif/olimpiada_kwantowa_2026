"""Prywatny storage rozwiązań: interfejs + backend S3/MinIO + backend lokalny (testy, offline dev).

Klucz obiektu powstaje wyłącznie z identyfikatorów technicznych i sumy sha256. Nazwa pliku
podana przez użytkownika nie bierze udziału w budowie ścieżki (PROJEKT.md 1.3) – dzięki temu
``../../evil.pdf`` jest tylko metadaną w bazie i nie ma wpływu na to, gdzie ląduje treść.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.utils.module_loading import import_string

# Segment ścieżki: tylko znaki bezpieczne w kluczu S3 i w ścieżce lokalnej. Brak kropek → brak "..".
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]")
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,10}$")


def sanitize_segment(value) -> str:
    """Sprowadza dowolną wartość do bezpiecznego segmentu klucza (bez ``/``, bez ``.``)."""
    cleaned = _SAFE_SEGMENT.sub("-", str(value))
    return cleaned or "x"


def build_object_key(
    *, edition_id, stage_id, participant_code, submission_uuid, sha256: str, ext: str
) -> str:
    """``{edition}/{stage}/{kod uczestnika}/{uuid zgłoszenia}/{sha256}.{ext}``.

    Wszystkie segmenty pochodzą z bazy albo z treści pliku – nigdy z ``upload.name``.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ValueError("sha256 musi być 64 znakami szesnastkowymi.")
    safe_ext = ext.lower()
    if not _SAFE_EXT.fullmatch(safe_ext):
        raise ValueError("Rozszerzenie musi być krótkim ciągiem alfanumerycznym.")
    parts = [
        sanitize_segment(edition_id),
        sanitize_segment(stage_id),
        sanitize_segment(participant_code),
        sanitize_segment(submission_uuid),
    ]
    return "/".join(parts) + f"/{sha256}.{safe_ext}"


class SubmissionStorage(ABC):
    """Kontrakt storage rozwiązań. Implementacje nie znają domeny – dostają gotowy klucz."""

    #: Czy backend potrafi wystawić presigned URL (wtedy pobranie kończy się 302).
    supports_presigned_url = False

    @abstractmethod
    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        """Zapisuje strumień pod kluczem. Nadpisanie tego samego klucza jest idempotentne."""

    @abstractmethod
    def presigned_get_url(
        self, key: str, ttl: int | None = None, *, content_disposition: str | None = None
    ) -> str | None:
        """URL czasowy do pobrania albo ``None``, gdy backend nie wspiera presigned URL-i.

        ``content_disposition`` narzuca nagłówek ``Content-Disposition`` odpowiedzi obiektowej –
        dzięki temu nazwa pliku widziana przez pobierającego jest ustalana przez aplikację, a nie
        przez metadane obiektu (ocenianie ślepe: recenzent nie może dostać nazwy od uczestnika).
        """

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Otwiera obiekt do odczytu binarnego (używane przez skan antywirusowy i download)."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Czy obiekt istnieje. Używane w smoke testach i w adminie."""


@lru_cache(maxsize=4)
def _s3_client(endpoint_url: str, access_key: str, secret_key: str, region: str):
    """Klient boto3 jest drogi w budowie i bezpieczny wątkowo do odczytu – trzymamy go w cache."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class S3SubmissionStorage(SubmissionStorage):
    """MinIO / S3. Bucket jest prywatny – dostęp wyłącznie przez presigned URL o krótkim TTL.

    Dwa klienty, bo mamy dwa różne adresy tego samego MinIO:
    - ``client`` (``S3_ENDPOINT_URL``) – ruch serwer→MinIO wewnątrz sieci compose (upload, odczyt),
    - ``presign_client`` (``S3_PUBLIC_ENDPOINT_URL``) – tylko do podpisania URL-a, którym pobierze
      plik przeglądarka uczestnika. Podpis SigV4 obejmuje nagłówek ``Host``, więc URL podpisany
      hostem ``minio`` jest bezużyteczny poza siecią compose i nie da się go poprawić po fakcie.
    """

    supports_presigned_url = True

    def __init__(self, bucket: str | None = None):
        self.bucket = bucket or settings.S3_SUBMISSIONS_BUCKET

    @property
    def client(self):
        return _s3_client(
            settings.S3_ENDPOINT_URL,
            settings.S3_ACCESS_KEY,
            settings.S3_SECRET_KEY,
            getattr(settings, "S3_REGION", "us-east-1"),
        )

    @property
    def presign_client(self):
        """Klient podpisujący. Bez ``S3_PUBLIC_ENDPOINT_URL`` degraduje się do endpointu wewnętrznego."""
        public = getattr(settings, "S3_PUBLIC_ENDPOINT_URL", "") or settings.S3_ENDPOINT_URL
        if public == settings.S3_ENDPOINT_URL:
            return self.client
        return _s3_client(
            public,
            settings.S3_ACCESS_KEY,
            settings.S3_SECRET_KEY,
            getattr(settings, "S3_REGION", "us-east-1"),
        )

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        self.client.upload_fileobj(
            fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type or "application/octet-stream"}
        )

    def presigned_get_url(
        self, key: str, ttl: int | None = None, *, content_disposition: str | None = None
    ) -> str:
        # generate_presigned_url liczy podpis lokalnie – nie wykonuje żadnego żądania sieciowego.
        params = {"Bucket": self.bucket, "Key": key}
        if content_disposition:
            # Parametr wchodzi do podpisu SigV4, więc pobierający nie podmieni nazwy pliku w URL-u.
            params["ResponseContentDisposition"] = content_disposition
        return self.presign_client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=int(ttl or settings.S3_PRESIGNED_TTL_SECONDS),
        )

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return False
        return True


class LocalSubmissionStorage(SubmissionStorage):
    """Backend plikowy pod ``MEDIA_ROOT/submissions`` – testy i dev bez MinIO.

    Nie wystawia presigned URL-i; widok pobierania wysyła plik bezpośrednio. ``MEDIA_ROOT``
    czytany jest przy każdej operacji, bo testy podmieniają go per test (``conftest.py``).
    """

    supports_presigned_url = False
    prefix = "submissions"

    @property
    def root(self) -> Path:
        return Path(settings.MEDIA_ROOT) / self.prefix

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        candidate = (root / key).resolve()
        # Ostatnia linia obrony: klucz jest generowany, ale ścieżka nigdy nie może uciec z katalogu.
        if not candidate.is_relative_to(root):
            raise ValueError("Klucz obiektu wychodzi poza katalog storage.")
        return candidate

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as target:
            for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
                target.write(chunk)

    def presigned_get_url(
        self, key: str, ttl: int | None = None, *, content_disposition: str | None = None
    ) -> None:
        return None

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def get_submission_storage() -> SubmissionStorage:
    """Fabryka storage wybierana ustawieniem ``SUBMISSION_STORAGE_BACKEND``."""
    return import_string(settings.SUBMISSION_STORAGE_BACKEND)()
