"""Plik plakatu: format po treści, limity, miniatura i nazwa pliku u pobierającego.

Przedmiotem jest to, czego nie widać po formularzu: rozszerzenie i ``Content-Type`` podaje
przesyłający, więc o formacie decydują bajty. HTML przemianowany na ``.pdf`` ma odpaść, a PNG
nazwany ``.jpg`` – przejść jako PNG, bo tym właśnie jest.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.promo import validators
from apps.promo.forms import PromoMaterialForm
from apps.promo.models import PromoMaterial
from apps.promo.previews import make_thumbnail, verify_image
from apps.promo.tests.helpers import FAKE_PDF_BYTES, PDF_BYTES, image_bytes, make_material, upload

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (PDF_BYTES, "pdf"),
        (image_bytes("JPEG"), "jpg"),
        (image_bytes("PNG"), "png"),
    ],
)
def test_format_is_recognised_by_content(content, expected):
    assert validators.validate_material_file(upload("cokolwiek.bin", content)) == expected


def test_png_named_jpg_is_accepted_as_png():
    """Nazwa kłamie, treść nie – zapisujemy to, czym plik naprawdę jest."""
    assert validators.validate_material_file(upload("plakat.jpg", image_bytes("PNG"))) == "png"


@pytest.mark.parametrize(
    "content",
    [FAKE_PDF_BYTES, b"GIF89a....", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", b"PK\x03\x04zip"],
)
def test_other_content_is_rejected_whatever_the_name(content):
    with pytest.raises(ValidationError) as caught:
        validators.validate_material_file(upload("plakat.pdf", content, "application/pdf"))

    assert caught.value.code == "invalid_type"


def test_empty_file_is_rejected():
    with pytest.raises(ValidationError) as caught:
        validators.validate_material_file(upload("plakat.pdf", b""))

    assert caught.value.code == "empty"


def test_file_over_the_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(validators, "MAX_FILE_MB", 0.001)  # ok. 1 kB

    with pytest.raises(ValidationError) as caught:
        validators.validate_material_file(upload("plakat.pdf", PDF_BYTES + b"0" * 2048))

    assert caught.value.code == "too_large"


def test_default_limit_is_fifty_megabytes():
    assert validators.MAX_FILE_MB == 50


def test_preview_must_be_an_image():
    with pytest.raises(ValidationError):
        validators.validate_preview_file(upload("podglad.png", PDF_BYTES))


def test_verify_image_rejects_a_truncated_png():
    broken = image_bytes("PNG")[:60]

    assert verify_image(upload("x.png", image_bytes("PNG"))) is True
    assert verify_image(upload("x.png", broken)) is False


def test_thumbnail_is_a_small_jpeg_even_from_a_transparent_png():
    thumb = make_thumbnail(upload("x.png", image_bytes("PNG", size=(2000, 2800), mode="RGBA")))

    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(thumb.read()))
    assert image.format == "JPEG"
    assert max(image.size) <= 640


def test_thumbnail_of_garbage_is_none_not_an_exception():
    assert make_thumbnail(upload("x.png", b"\x89PNG\r\n\x1a\n-garbage")) is None


def test_download_name_is_ascii_and_keeps_the_polish_l(competition):
    material = make_material(competition, title="Plakat dla szkoły – Łódź 2026", fmt="png")

    assert material.download_name == "plakat-dla-szkoly-lodz-2026.png"


def test_form_rejects_html_disguised_as_pdf(competition):
    form = PromoMaterialForm(
        data={"title": "Plakat", "description": "", "is_published": "on"},
        files={"file": upload("plakat.pdf", FAKE_PDF_BYTES, "application/pdf")},
        instance=PromoMaterial(competition=competition),
    )

    assert not form.is_valid()
    assert "file" in form.errors


def test_form_requires_a_file_only_when_creating(competition):
    existing = make_material(competition)
    form = PromoMaterialForm(data={"title": "Nowy tytuł", "description": ""}, instance=existing)

    assert form.is_valid(), form.errors
    assert "clear_preview" not in form.fields
