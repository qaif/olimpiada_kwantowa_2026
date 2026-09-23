"""Pliki i plakaty do testów – wspólne dla ``apps/promo/tests`` i testów ekranów w ``apps/web/tests``.

Pliki są **prawdziwe** w tym sensie, na którym stoi walidacja: PDF zaczyna się od ``%PDF-``, a JPG
i PNG robi Pillow. Plik „udający” (HTML z rozszerzeniem ``.pdf``) jest tu osobno, bo właśnie jego
odrzucenie jest przedmiotem testu.
"""

from __future__ import annotations

from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.promo.models import PromoDownload, PromoMaterial

PDF_BYTES = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
FAKE_PDF_BYTES = b"<!doctype html><script>alert(1)</script>"


def image_bytes(fmt: str = "PNG", size=(300, 420), mode: str = "RGB") -> bytes:
    from PIL import Image

    buffer = BytesIO()
    color = (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name: str, content: bytes, content_type: str = "application/octet-stream") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=content_type)


def make_material(
    competition,
    *,
    title: str = "Plakat olimpiady",
    fmt: str = "pdf",
    published: bool = True,
    position: int = 0,
    content: bytes | None = None,
    **kwargs,
) -> PromoMaterial:
    """Plakat zapisany wprost (bez formularza) – do testów listy, pobrania i statystyk."""
    if content is None:
        content = PDF_BYTES if fmt == "pdf" else image_bytes("JPEG" if fmt == "jpg" else "PNG")
    material = PromoMaterial(
        competition=competition,
        title=title,
        file_format=fmt,
        file_size=len(content),
        is_published=published,
        position=position,
        **kwargs,
    )
    material.file.save(f"plik.{fmt}", ContentFile(content), save=False)
    material.save()
    return material


def make_download(material, *, ip_hash: str | None = "a" * 64, at=None) -> PromoDownload:
    return PromoDownload.objects.create(
        material=material,
        competition=material.competition,
        downloaded_at=at or timezone.now(),
        ip_hash=ip_hash,
    )
