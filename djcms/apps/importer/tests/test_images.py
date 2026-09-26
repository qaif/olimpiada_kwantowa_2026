"""Obrazy paczki: strumieniowo (bez trzymania bajtów w pamięci), bomba dekompresyjna, format według
Pillow; limit pikseli w ustawieniach i w walidatorze wgrywania filera (przegląd krytyka DJ-01)."""

from __future__ import annotations

import hashlib
import io
import tracemalloc

import pytest
from PIL import Image as PILImage

from apps.importer import services
from apps.importer.tests import bundles

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _png(size, mode="RGB", noise=False) -> bytes:
    if noise:
        import os

        image = PILImage.frombytes(mode, size, os.urandom(size[0] * size[1] * len(mode)))
    else:
        image = PILImage.new(mode, size)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(size=(40, 20)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, "red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _with_image(key: str, data: bytes, path: str | None = None) -> tuple[dict, dict]:
    """Manifest v2 z jednym obrazem podmienionym na ``data`` (ten sam klucz, nowe SHA-1)."""
    manifest = bundles.v2_manifest()
    images = bundles.fixture_images()
    meta = manifest["images"][key]
    old_path = meta["path"]
    meta["sha1"] = hashlib.sha1(data).hexdigest()
    if path is not None:
        del images[old_path]
        meta["path"] = path
    images[meta["path"]] = data
    return manifest, images


def test_bundle_keeps_no_image_bytes():
    bundle = services.open_bundle(io.BytesIO(bundles.build_zip(bundles.v2_manifest())))
    assert not hasattr(bundle, "images")
    assert set(bundle.image_formats.values()) == {"PNG"}


def test_images_are_streamed_one_at_a_time(superuser, monkeypatch):
    """Dwa obrazy po ~3 MB: szczyt pamięci walidacji i zapisu obrazów poniżej wielkości **jednego**
    – idą strumieniem z paczki przez plik tymczasowy, a nie jako ``bytes`` całej paczki."""
    monkeypatch.setattr(services, "IMAGE_SPOOL_BYTES", 64 * 1024)
    monkeypatch.setattr(services, "READ_CHUNK_BYTES", 64 * 1024)
    # Rozgrzewka: moduły filera/Pillow i bufory Django ładują się przy pierwszym imporcie obrazu –
    # to nie jest koszt obrazu, a zaciemniłoby pomiar.
    warm = services.open_bundle(io.BytesIO(bundles.build_zip(bundles.v2_manifest())))
    services.import_images(warm, None, services.ImportReport())
    big = _png((1000, 1000), noise=True)
    manifest, images = _with_image("17", big)
    other = _png((1000, 1001), noise=True)
    manifest["images"]["18"]["sha1"] = hashlib.sha1(other).hexdigest()
    images[manifest["images"]["18"]["path"]] = other
    source = io.BytesIO(bundles.build_zip(manifest, images=images))
    size = len(big)
    del images, big, other
    from filer.models import Image

    Image.objects.all().delete()
    tracemalloc.start()
    try:
        bundle = services.open_bundle(source)
        report = services.ImportReport()
        services.import_images(bundle, None, report)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert report.images_created == 2
    assert peak < size // 2, (peak, size)


def test_image_above_pixel_limit_is_rejected():
    manifest, images = _with_image("18", _png((8000, 6000), mode="1"))  # 48 Mpx, kilka kB
    with pytest.raises(services.BundleError, match="40 Mpx"):
        services.open_bundle(io.BytesIO(bundles.build_zip(manifest, images=images)))


def test_extension_follows_the_detected_format(superuser):
    from filer.models import Image

    manifest, images = _with_image("18", _jpeg())  # plik ``18-foto.png`` jest w istocie JPEG-iem
    bundle = services.open_bundle(io.BytesIO(bundles.build_zip(manifest, images=images)))
    assert bundle.image_formats["18"] == "JPEG"
    services.run_import(bundle, user=superuser)
    image = Image.objects.get(sha1=manifest["images"]["18"]["sha1"])
    assert image.original_filename.endswith(".jpg") and image.file.name.endswith(".jpg")
    assert not image.file.name.endswith(".png.jpg")


def test_format_outside_the_allowed_set_is_rejected():
    buffer = io.BytesIO()
    PILImage.new("RGB", (4, 4)).save(buffer, format="BMP")
    manifest, images = _with_image("18", buffer.getvalue())
    with pytest.raises(services.BundleError, match="format BMP"):
        services.open_bundle(io.BytesIO(bundles.build_zip(manifest, images=images)))


def test_bundle_changed_between_validation_and_import_is_refused(superuser, tmp_path):
    path = tmp_path / "paczka.zip"
    path.write_bytes(bundles.build_zip(bundles.v2_manifest()))
    bundle = services.open_bundle(path)
    manifest, images = _with_image("17", _png((3, 3)))
    manifest["images"]["17"]["sha1"] = bundles.v2_manifest()["images"]["17"]["sha1"]  # stare SHA-1
    path.write_bytes(bundles.build_zip(manifest, images=images))
    with pytest.raises(services.BundleError, match="zmieniła się"):
        services.run_import(bundle, user=superuser)
    from cms.models import Page

    assert not Page.objects.exists()


# --- ustawienia Pillow i walidator filera ---------------------------------------------------------


def test_pillow_limit_and_bomb_warning_is_an_error(settings):
    """Ustawienia: limit Pillow = limit importu, a ostrzeżenie o bombie – błędem.

    Filtr ostrzeżeń sprawdzamy w osobnym procesie: pytest przywraca filtry ostrzeżeń po fazie
    konfiguracji (w której ładują się ustawienia), więc w samym teście go nie widać – w gunicornie
    i w komendach działa od importu ustawień.
    """
    import os
    import subprocess
    import sys

    assert PILImage.MAX_IMAGE_PIXELS == settings.DJ_MAX_IMAGE_PIXELS == services.MAX_IMAGE_PIXELS
    code = "\n".join(
        [
            "import django, io",
            "django.setup()",
            "from PIL import Image",
            "b = io.BytesIO()",
            "Image.new('1', (8000, 6000)).save(b, format='PNG')",
            "b.seek(0)",
            "try:",
            "    Image.open(b)",
            "except Image.DecompressionBombWarning:",
            "    print('BLAD')",
        ]
    )
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )  # noqa: S603
    assert result.stdout.strip() == "BLAD", result.stderr[-2000:]


def test_filer_validator_rejects_huge_images():
    from filer.validation import FileValidationError

    from apps.blocks.uploads import validate_image_pixels

    huge = io.BytesIO(_png((8000, 6000), mode="1"))
    with pytest.raises(FileValidationError, match="40 Mpx"):
        validate_image_pixels("duzy.png", huge, None, "image/png")
    small = io.BytesIO(_png((10, 10)))
    small.seek(3)
    assert validate_image_pixels("maly.png", small, None, "image/png") is None
    assert small.tell() == 3  # pozycja pliku przywrócona dla kolejnych walidatorów i zapisu
    assert validate_image_pixels("nie-obraz.png", io.BytesIO(b"<svg/>"), None, "image/png") is None


def test_filer_validator_is_configured_for_image_uploads(settings):
    from filer import settings as filer_settings

    for mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        assert "apps.blocks.uploads.validate_image_pixels" in filer_settings.FILE_VALIDATORS[mime]
