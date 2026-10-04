"""Komenda ``competition_brand_images``: wgrywa grafiki marki do kolekcji konkursu i je wskazuje."""

import io
import zipfile
from io import BytesIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image as PILImage

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _png(size) -> bytes:
    buffer = BytesIO()
    PILImage.new("RGB", size, (20, 18, 58)).save(buffer, format="PNG")
    return buffer.getvalue()


def _zip(**files) -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return raw.getvalue()


def test_zip_sets_all_three_images_in_the_competition_collection(competition, tmp_path, monkeypatch):
    from apps.cms.permissions import collection_name

    path = tmp_path / "marka.zip"
    path.write_bytes(
        _zip(
            **{
                "site_logo.png": _png((1200, 586)),
                "favicon.png": _png((512, 512)),
                "social_image.png": _png((1200, 630)),
            }
        )
    )

    call_command("competition_brand_images", competition.slug, "--zip", str(path), stdout=io.StringIO())

    competition.refresh_from_db()
    assert competition.site_logo.width == 1200
    assert competition.favicon.width == 512
    assert competition.social_image.height == 630
    assert competition.site_logo.collection.name == collection_name(competition)
    assert AuditLog.objects.filter(action="competition.brand_images_changed").exists()


def test_dry_run_changes_nothing(competition, tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(_png((1200, 586)))

    call_command(
        "competition_brand_images",
        competition.slug,
        "--site-logo",
        str(path),
        "--dry-run",
        stdout=io.StringIO(),
    )

    competition.refresh_from_db()
    assert competition.site_logo_id is None


def test_unknown_competition_and_no_files_are_errors(competition):
    with pytest.raises(CommandError):
        call_command("competition_brand_images", "nie-ma-takiego", "--site-logo", "x.png")
    with pytest.raises(CommandError):
        call_command("competition_brand_images", competition.slug)
