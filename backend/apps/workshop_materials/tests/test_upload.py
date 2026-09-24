"""Wgrywanie filmu i pliku: start → podpisy części → zakończenie z weryfikacją – przez adresy panelu.

To, czego nie widać po samym ekranie:

- **plik nie przechodzi przez serwer** – serwer podpisuje wyłącznie numery części wynikające
  z zadeklarowanego rozmiaru, a o tym, co dotarło, pyta magazyn (``ListParts``), nie przeglądarkę,
- **zakończenie weryfikuje**: komplet i rozmiary części, rozmiar złożonego obiektu i format po
  bajtach; każda porażka kasuje obiekt i wiersz – materiału, którego nie da się pokazać, nie ma,
- film jest od razu gotowy, plik idzie do skanera (kolejka ``scan``),
- deklaracja ponad limit albo z rozszerzeniem spoza listy nie zakłada wgrywania wcale.
"""

from __future__ import annotations

import pytest

from apps.core.models import AuditLog
from apps.workshop_materials import services
from apps.workshop_materials.models import MaterialStatus, WorkshopMaterial
from apps.workshop_materials.storage import PART_SIZE

from .helpers import HTML_BYTES, MOV_HEADER, MP4_HEADER, PDF_BYTES, enable, key_of, workshops_page_for

pytestmark = pytest.mark.django_db

BASE = "/coordinator/workshops/materials/"


@pytest.fixture
def ready(competition):
    enable(competition)
    workshops_page_for(competition)
    return competition


def start(client, *, kind="video", filename="nagranie.mp4", size=None, content=MP4_HEADER, **fields):
    data = {
        "workshop": key_of(0),
        "kind": kind,
        "title": "Nagranie zajęć",
        "description": "",
        "filename": filename,
        "size": size if size is not None else len(content),
        **fields,
    }
    return client.post(f"{BASE}upload/start/", data)


def upload_and_complete(client, storage, content, **kwargs):
    response = start(client, content=content, **kwargs)
    assert response.status_code == 200, response.content
    payload = response.json()
    material = WorkshopMaterial.objects.get(pk=payload["id"])
    storage.put_file(material.upload_id, content)
    return client.post(payload["complete_url"]), material.pk


def test_start_creates_an_uploading_material_and_a_multipart_upload(coordinator_client, ready, storage):
    response = start(coordinator_client, is_published="on")

    assert response.status_code == 200
    payload = response.json()
    material = WorkshopMaterial.objects.get(pk=payload["id"])
    assert material.competition == ready
    assert material.status == MaterialStatus.UPLOADING
    assert material.is_published is True
    assert material.workshop_key == key_of(0)
    assert material.workshop_topic == "Kubity i bramki kwantowe"
    assert payload["part_size"] == PART_SIZE
    assert payload["parts"] == 1
    # Nazwa pliku od przesyłającego nie trafia do klucza obiektu.
    assert "nagranie" not in material.object_key
    assert material.object_key.startswith(f"workshop-materials/{ready.pk}/")
    assert material.upload_id in storage.uploads
    assert AuditLog.objects.filter(action="workshop_material.upload_started").count() == 1


def test_start_refuses_a_video_over_the_limit_without_touching_storage(
    coordinator_client, ready, storage, settings
):
    settings.WORKSHOP_VIDEO_MAX_MB = 10

    response = start(coordinator_client, size=11 * 1024 * 1024)

    assert response.status_code == 400
    assert "limit" in response.json()["error"]
    assert WorkshopMaterial.objects.count() == 0
    assert storage.uploads == {}


def test_start_refuses_an_extension_off_the_list(coordinator_client, ready, storage):
    response = start(coordinator_client, kind="file", filename="program.exe", content=b"MZ")

    assert response.status_code == 400
    assert "nie są przyjmowane" in response.json()["error"]
    assert WorkshopMaterial.objects.count() == 0


def test_start_refuses_a_workshop_outside_the_schedule(coordinator_client, ready):
    response = start(coordinator_client, workshop="2026-01-01-cudzy-warsztat")

    assert response.status_code == 400
    assert "workshop" in response.json()["errors"]


def test_start_reports_a_storage_outage_and_leaves_no_row(coordinator_client, ready, storage):
    storage.fail_create = True

    response = start(coordinator_client)

    assert response.status_code == 400
    assert "Magazyn" in response.json()["error"]
    assert WorkshopMaterial.objects.count() == 0


def test_sign_only_signs_part_numbers_within_the_declared_size(coordinator_client, ready):
    payload = start(coordinator_client, size=PART_SIZE * 2 + 1).json()

    ok = coordinator_client.post(payload["sign_url"], {"part": ["1", "3"]})
    too_far = coordinator_client.post(payload["sign_url"], {"part": ["4"]})
    zero = coordinator_client.post(payload["sign_url"], {"part": ["0"]})

    assert ok.status_code == 200
    assert set(ok.json()["urls"]) == {"1", "3"}
    assert "no-store" in ok["Cache-Control"]
    assert too_far.status_code == 400
    assert zero.status_code == 400


def test_video_is_ready_after_complete(coordinator_client, ready, storage):
    response, pk = upload_and_complete(coordinator_client, storage, MP4_HEADER)

    assert response.status_code == 200
    material = WorkshopMaterial.objects.get(pk=pk)
    assert material.status == MaterialStatus.READY
    assert material.file_format == "mp4"
    assert material.upload_id == ""
    assert material.ready_at is not None
    assert storage.objects[material.object_key] == MP4_HEADER
    assert AuditLog.objects.filter(action="workshop_material.uploaded").count() == 1


def test_multi_part_video_is_assembled_from_the_parts_minio_reports(coordinator_client, ready, storage):
    content = MP4_HEADER + b"\x00" * (PART_SIZE * 2)
    response, pk = upload_and_complete(coordinator_client, storage, content)

    assert response.status_code == 200
    material = WorkshopMaterial.objects.get(pk=pk)
    assert storage.objects[material.object_key] == content


def test_file_goes_to_the_scanner_after_complete(
    coordinator_client, ready, storage, django_capture_on_commit_callbacks, monkeypatch
):
    queued = []
    monkeypatch.setattr("apps.workshop_materials.tasks.scan_material.delay", queued.append)

    with django_capture_on_commit_callbacks(execute=True):
        response, pk = upload_and_complete(
            coordinator_client, storage, PDF_BYTES, kind="file", filename="slajdy.pdf"
        )

    assert response.status_code == 200
    assert WorkshopMaterial.objects.get(pk=pk).status == MaterialStatus.SCANNING
    assert queued == [pk]


@pytest.mark.parametrize(
    ("content", "fields", "message"),
    [
        (MOV_HEADER, {}, "QuickTime"),
        (HTML_BYTES, {"kind": "file", "filename": "slajdy.pdf"}, "treść nie jest"),
    ],
)
def test_complete_rejects_the_wrong_content_and_cleans_up(
    coordinator_client, ready, storage, content, fields, message
):
    response, pk = upload_and_complete(coordinator_client, storage, content, **fields)

    assert response.status_code == 400
    assert message in response.json()["error"]
    assert not WorkshopMaterial.objects.filter(pk=pk).exists()
    assert storage.objects == {}
    assert AuditLog.objects.filter(action="workshop_material.upload_rejected").count() == 1


def test_complete_rejects_a_size_that_differs_from_the_declaration(coordinator_client, ready, storage):
    payload = start(coordinator_client, size=len(MP4_HEADER) + 10).json()
    material = WorkshopMaterial.objects.get(pk=payload["id"])
    storage.put_file(material.upload_id, MP4_HEADER)

    response = coordinator_client.post(payload["complete_url"])

    assert response.status_code == 400
    assert "komplet" in response.json()["error"]
    assert not WorkshopMaterial.objects.exists()
    assert storage.uploads == {}


def test_complete_rejects_missing_parts(coordinator_client, ready, storage):
    payload = start(coordinator_client, size=PART_SIZE + 100).json()
    material = WorkshopMaterial.objects.get(pk=payload["id"])
    storage.put_part(material.upload_id, 2, b"\x00" * 100)

    response = coordinator_client.post(payload["complete_url"])

    assert response.status_code == 400
    assert not WorkshopMaterial.objects.exists()


def test_complete_twice_is_refused(coordinator_client, ready, storage):
    response, pk = upload_and_complete(coordinator_client, storage, MP4_HEADER)

    again = coordinator_client.post(f"{BASE}{pk}/upload/complete/")

    assert again.status_code == 400
    material = WorkshopMaterial.objects.get(pk=pk)
    assert material.status == MaterialStatus.READY
    # Drugie „zakończ” (ponowione żądanie, druga karta) nie kasuje poprawnie złożonego pliku.
    assert storage.objects[material.object_key] == MP4_HEADER


def test_abort_removes_parts_and_row(coordinator_client, ready, storage):
    payload = start(coordinator_client).json()

    response = coordinator_client.post(payload["abort_url"])

    assert response.status_code == 200
    assert not WorkshopMaterial.objects.exists()
    assert storage.uploads == {}


def test_upload_endpoints_of_another_competition_are_404(
    coordinator_client, ready, other_competition, storage
):
    enable(other_competition)
    foreign = WorkshopMaterial.objects.create(
        competition=other_competition,
        workshop_key=key_of(0),
        kind="video",
        title="Cudzy",
        size_bytes=10,
        upload_id="upload-x",
        object_key="workshop-materials/x/y.mp4",
    )

    for action in ("sign", "complete", "abort"):
        response = coordinator_client.post(f"{BASE}{foreign.pk}/upload/{action}/", {"part": ["1"]})
        assert response.status_code == 404, action
    assert WorkshopMaterial.objects.filter(pk=foreign.pk).exists()


def test_upload_start_is_404_with_the_flag_off(coordinator_client, competition):
    workshops_page_for(competition)

    assert start(coordinator_client).status_code == 404


def test_upload_start_needs_the_coordinator_role(participant_client, ready):
    assert start(participant_client).status_code == 403


def test_part_count_rounds_up():
    assert services.part_count(1) == 1
    assert services.part_count(PART_SIZE) == 1
    assert services.part_count(PART_SIZE + 1) == 2
