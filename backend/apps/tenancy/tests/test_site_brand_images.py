"""Logotyp nagłówka, favikona i obraz udostępniania z grafik **konkursu** (IQO, 4.10.2026).

Konkurs bez wskazanych grafik – Konkurs #1 – ma renderować dokładnie te same pliki statyczne, co
przed polami ``site_logo`` / ``social_image`` / użyciem ``favicon``. Konkurs z grafikami dostaje
rendycje Wagtaila w tych samych trzech miejscach ramy serwisu.
"""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image as PILImage
from wagtail.images import get_image_model

pytestmark = pytest.mark.django_db


def _image(title: str, size: tuple[int, int]):
    buffer = BytesIO()
    PILImage.new("RGB", size, (20, 18, 58)).save(buffer, format="PNG")
    return get_image_model().objects.create(
        title=title, file=SimpleUploadedFile(f"{title}.png", buffer.getvalue())
    )


def test_competition_without_brand_images_keeps_the_static_files(client_for, competition):
    body = client_for(competition).get("/").content.decode()

    assert "img/logo-olimpiada-kwantowa.png" in body
    assert "img/favicon.svg" in body
    assert "img/og-image.png" in body


def test_competition_brand_images_replace_logo_favicon_and_social_image(client_for, competition):
    competition.site_logo = _image("iqo-logo", (1200, 586))
    competition.favicon = _image("iqo-icon", (512, 512))
    competition.social_image = _image("iqo-og", (1200, 630))
    competition.save(update_fields=["site_logo", "favicon", "social_image"])

    body = client_for(competition).get("/").content.decode()

    assert "img/logo-olimpiada-kwantowa.png" not in body
    assert "img/favicon.svg" not in body
    assert "img/og-image.png" not in body
    assert "iqo-logo" in body and 'class="brand__logo"' in body
    assert 'rel="apple-touch-icon"' in body and "iqo-icon" in body
    assert 'property="og:image"' in body and "iqo-og" in body
    og = body.split('property="og:image" content="', 1)[1].split('"', 1)[0]
    assert og.count("://") == 1, og


def test_absolute_rendition_url_is_not_prefixed_with_the_site_host(client_for, competition, monkeypatch):
    """S3 daje rendycji pełny adres – og:image ma go wziąć wprost (prod 4.10.2026)."""
    rendition_model = get_image_model().get_rendition_model()

    competition.social_image = _image("iqo-og", (1200, 630))
    competition.save(update_fields=["social_image"])
    monkeypatch.setattr(rendition_model, "url", property(lambda self: "https://cdn.example.test/og.png"))

    body = client_for(competition).get("/").content.decode()

    assert 'property="og:image" content="https://cdn.example.test/og.png"' in body


def test_organizer_logo_field_does_not_leak_into_the_header(client_for, competition):
    """``Competition.logo`` Konkursu #1 to znak organizatora (dyplomy) – nagłówek go nie bierze."""
    competition.logo = _image("fundacja", (400, 200))
    competition.save(update_fields=["logo"])

    body = client_for(competition).get("/").content.decode()

    assert "img/logo-olimpiada-kwantowa.png" in body
    assert "fundacja" not in body
