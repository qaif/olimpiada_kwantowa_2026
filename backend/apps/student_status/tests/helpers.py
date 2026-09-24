"""Wspólne klocki testów „statusu ucznia”: pliki o prawdziwych sygnaturach, flaga, gotowy wiersz."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.promo.validators import PNG_MAGIC
from apps.student_status.models import FLAG, CertificateStatus, ScanStatus, StudentStatusCertificate
from apps.student_status.services import build_object_key
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import JPEG_BYTES, PDF_BYTES

#: Najkrótszy „PNG”: sygnatura i kilka bajtów. Walidator czyta sygnaturę, obrazu nie dekoduje.
PNG_BYTES = PNG_MAGIC + b"\x00" * 64

#: Plik HTML ze skryptem – klasyczna próba podsunięcia koordynatorowi własnego kodu.
HTML_BYTES = b"<html><script>alert(document.cookie)</script></html>"

__all__ = ["HTML_BYTES", "JPEG_BYTES", "PDF_BYTES", "PNG_BYTES"]


def upload(name: str, content: bytes, content_type: str = "application/pdf") -> SimpleUploadedFile:
    """Plik z formularza. ``content_type`` jest celowo podawany przez test – serwer go ignoruje."""
    return SimpleUploadedFile(name, content, content_type=content_type)


def enable(competition):
    """Konkurs z **włączoną** flagą – zapis do bazy, bo konkurs żądania czyta ``CompetitionMiddleware``."""
    competition.feature_flags = {**(competition.feature_flags or {}), FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def make_certificate(
    participant,
    edition,
    *,
    status=CertificateStatus.PENDING,
    scan=ScanStatus.CLEAN,
    version=1,
    is_current=True,
    content: bytes = PDF_BYTES,
    mime: str = "application/pdf",
    ext: str = "pdf",
) -> StudentStatusCertificate:
    """Wiersz z plikiem w storage – stan „po wgraniu i skanie”, bez przechodzenia przez widok."""
    certificate = StudentStatusCertificate(
        participant=participant,
        edition=edition,
        version=version,
        is_current=is_current,
        status=status,
        sha256="b" * 64,
        mime=mime,
        size_bytes=len(content),
        scan_status=scan,
        uploaded_at=timezone.now(),
    )
    certificate.object_key = build_object_key(
        certificate, competition_id=edition.competition_id, participant_code=participant.public_code, ext=ext
    )
    get_submission_storage().put(certificate.object_key, BytesIO(content), mime)
    certificate.save()
    return certificate


def stored(key: str) -> bool:
    return get_submission_storage().exists(key)
