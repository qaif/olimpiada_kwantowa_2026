"""Zadania materiałów: werdykt ClamAV-a dla plików i sprzątanie porzuconych wgrywań oraz pseudonimów.

Skaner jest podmieniony (``scan_stream``) – przedmiotem jest to, co zadanie robi z werdyktem:
czysty plik staje się gotowy, zainfekowany znika z magazynu i przestaje być publikowalny, a przy
niedostępnym skanerze materiał zostaje niewidoczny (domyślnie zamknięte).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.submissions.antivirus import ClamAVUnavailable
from apps.workshop_materials import tasks
from apps.workshop_materials.models import MaterialStatus, WorkshopMaterial, WorkshopMaterialViewer

from .helpers import enable, make_material

pytestmark = pytest.mark.django_db


@pytest.fixture
def scanning(competition):
    enable(competition)
    return make_material(competition, kind="file", status=MaterialStatus.SCANNING, published=True)


def test_clean_file_becomes_ready(scanning, monkeypatch):
    monkeypatch.setattr(tasks, "scan_stream", lambda stream, **kwargs: ("CLEAN", ""))

    assert tasks.scan_material.delay(scanning.pk).get() == "CLEAN"

    scanning.refresh_from_db()
    assert scanning.status == MaterialStatus.READY
    assert scanning.is_visible


def test_infected_file_is_deleted_and_unpublished(scanning, monkeypatch, storage):
    monkeypatch.setattr(tasks, "scan_stream", lambda stream, **kwargs: ("INFECTED", "Eicar-Signature"))
    key = scanning.object_key

    assert tasks.scan_material.delay(scanning.pk).get() == "INFECTED"

    scanning.refresh_from_db()
    assert scanning.status == MaterialStatus.REJECTED
    assert not scanning.is_published
    assert scanning.object_key == ""
    assert "Eicar-Signature" in scanning.status_note
    assert key not in storage.objects


def test_unavailable_scanner_retries_and_finally_keeps_the_file_hidden(scanning, monkeypatch):
    from celery.exceptions import Retry

    def down(stream, **kwargs):
        raise ClamAVUnavailable("brak połączenia")

    monkeypatch.setattr(tasks, "scan_stream", down)

    with pytest.raises(Retry):
        tasks.scan_material.apply(args=[scanning.pk], throw=True)
    # Ostatnia próba nie ponawia – materiał zostaje w „sprawdzaniu”, niewidoczny dla widzów.
    last = tasks.scan_material.apply(args=[scanning.pk], retries=tasks.MAX_SCAN_RETRIES)

    assert last.result == "UNAVAILABLE"
    scanning.refresh_from_db()
    assert scanning.status == MaterialStatus.SCANNING
    assert not scanning.is_visible


def test_scan_skips_materials_not_waiting_for_it(competition, monkeypatch):
    material = make_material(competition, kind="file")
    monkeypatch.setattr(tasks, "scan_stream", lambda *a, **k: pytest.fail("skan nie powinien ruszyć"))

    assert tasks.scan_material.delay(material.pk).get() == "SKIPPED"


def test_cleanup_removes_stale_uploads_with_their_parts(competition, storage):
    stale = make_material(competition, status=MaterialStatus.UPLOADING, title="Porzucone")
    stale.upload_id = storage().create_upload(stale.object_key, "video/mp4")
    stale.save()
    WorkshopMaterial.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=25))
    fresh = make_material(competition, status=MaterialStatus.UPLOADING, title="W toku")

    result = tasks.cleanup()

    assert result["stale_uploads"] == 1
    assert not WorkshopMaterial.objects.filter(pk=stale.pk).exists()
    assert WorkshopMaterial.objects.filter(pk=fresh.pk).exists()
    assert storage.uploads == {}
    assert stale.object_key in storage.deleted


def test_cleanup_drops_viewer_pseudonyms_after_the_retention_period(competition):
    material = make_material(competition)
    old = WorkshopMaterialViewer.objects.create(
        material=material, viewer_hash="a" * 64, first_seen_at=timezone.now() - timedelta(days=400)
    )
    recent = WorkshopMaterialViewer.objects.create(material=material, viewer_hash="b" * 64)
    material.view_count = 5
    material.save()

    tasks.cleanup()

    assert not WorkshopMaterialViewer.objects.filter(pk=old.pk).exists()
    assert WorkshopMaterialViewer.objects.filter(pk=recent.pk).exists()
    material.refresh_from_db()
    assert material.view_count == 5
