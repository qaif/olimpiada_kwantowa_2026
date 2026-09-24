"""Ekran „Materiały z warsztatów” w panelu koordynatora: lista, odnośnik, edycja, akcje, sieroty.

Przedmiotem jest to, czego nie widać po samym formularzu:

- **usunięcie kasuje obiekt w magazynie** (i porzuca wgrywanie, jeśli trwało),
- **zmiana tematu albo daty w harmonogramie nie gubi materiału**: materiał „osierocony” zostaje
  u widzów pod migawką, a koordynator widzi go w osobnej sekcji z podpowiedzią i przepina go,
- kolejność jest per warsztat; publikacja materiału w skanie jest zapamiętana do chwili werdyktu,
- ślad w audycie bez tytułu i opisu; ekran tylko dla koordynatora i tylko przy włączonej fladze.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.cms.workshops import workshop_key
from apps.core.models import AuditLog
from apps.web.coordinator_nav import groups
from apps.workshop_materials.models import MaterialKind, MaterialStatus, WorkshopMaterial

from .helpers import ROWS, enable, key_of, make_material, rewrite_rows, workshops_page_for

pytestmark = pytest.mark.django_db

URL = "/coordinator/workshops/materials/"


@pytest.fixture
def page(competition):
    enable(competition)
    return workshops_page_for(competition)


def actions() -> list[str]:
    return list(
        AuditLog.objects.filter(action__startswith="workshop_material.")
        .order_by("id")
        .values_list("action", flat=True)
    )


# --- dostęp i menu ----------------------------------------------------------------------------------


def test_screen_is_404_with_the_flag_off(coordinator_client, competition):
    assert coordinator_client.get(URL).status_code == 404
    assert coordinator_client.get(f"{URL}new/").status_code == 404


def test_screen_needs_the_coordinator_role(participant_client, page):
    assert participant_client.get(URL).status_code == 403


def test_anonymous_goes_to_login(client_for, page, competition):
    response = client_for(competition).get(URL)

    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


def test_menu_item_appears_only_with_the_flag(competition):
    def labels():
        return {item.label for group in groups([], competition) for item in group.items}

    assert "Materiały z warsztatów" not in labels()
    competition.feature_flags = {**(competition.feature_flags or {}), "workshop_materials": True}
    assert "Materiały z warsztatów" in labels()


def test_attendance_screen_links_to_materials_only_with_the_flag(coordinator_client, competition):
    workshops_page_for(competition)
    assert URL not in coordinator_client.get("/coordinator/workshops/attendance/").content.decode()

    enable(competition)
    assert URL in coordinator_client.get("/coordinator/workshops/attendance/").content.decode()


# --- lista i odnośnik ---------------------------------------------------------------------------------


def test_list_shows_every_workshop_even_without_materials(coordinator_client, page, competition):
    make_material(competition, title="Nagranie listopadowe")

    content = coordinator_client.get(URL).content.decode()

    assert "Kubity i bramki kwantowe" in content
    assert "Splątanie i nierówności Bella" in content
    assert "Nagranie listopadowe" in content
    assert f"new/?workshop={key_of(1)}" in content


def test_new_link_is_saved_ready(coordinator_client, page, competition):
    response = coordinator_client.post(
        f"{URL}new/",
        {
            "workshop": key_of(1),
            "kind": MaterialKind.LINK,
            "title": "Nagranie w serwisie wideo",
            "description": "",
            "url": "https://video.example/abc",
            "is_published": "on",
        },
    )

    assert response.status_code == 302
    material = WorkshopMaterial.objects.get()
    assert material.competition == competition
    assert material.status == MaterialStatus.READY
    assert material.is_visible
    assert material.workshop_key == key_of(1)
    assert actions() == ["workshop_material.created"]
    assert "Nagranie" not in str(AuditLog.objects.get().diff)


def test_link_must_be_https(coordinator_client, page):
    response = coordinator_client.post(
        f"{URL}new/",
        {"workshop": key_of(0), "kind": MaterialKind.LINK, "title": "X", "url": "http://video.example/abc"},
    )

    assert response.status_code == 400
    assert "https://" in response.content.decode()
    assert not WorkshopMaterial.objects.exists()


def test_video_cannot_be_posted_through_the_plain_form(coordinator_client, page):
    response = coordinator_client.post(
        f"{URL}new/", {"workshop": key_of(0), "kind": MaterialKind.VIDEO, "title": "X"}
    )

    assert response.status_code == 400
    assert "JavaScript" in response.content.decode()
    assert not WorkshopMaterial.objects.exists()


def test_new_form_preselects_the_workshop_from_the_address(coordinator_client, page):
    content = coordinator_client.get(f"{URL}new/?workshop={key_of(1)}").content.decode()

    assert f'<option value="{key_of(1)}" selected>' in content
    assert "data-start-url=" in content


# --- akcje ------------------------------------------------------------------------------------------


def test_publish_and_unpublish(coordinator_client, page, competition):
    material = make_material(competition, published=False)

    coordinator_client.post(URL, {"action": "publish", "pk": material.pk})
    material.refresh_from_db()
    assert material.is_published

    coordinator_client.post(URL, {"action": "unpublish", "pk": material.pk})
    material.refresh_from_db()
    assert not material.is_published
    assert actions() == ["workshop_material.published", "workshop_material.unpublished"]


def test_rejected_material_cannot_be_published(coordinator_client, page, competition):
    material = make_material(competition, published=False, status=MaterialStatus.REJECTED)

    coordinator_client.post(URL, {"action": "publish", "pk": material.pk})

    material.refresh_from_db()
    assert not material.is_published


def test_move_stays_within_the_workshop(coordinator_client, page, competition):
    first = make_material(competition, title="Pierwszy", position=0)
    second = make_material(competition, title="Drugi", position=1)
    other = make_material(competition, title="Inny warsztat", position=0, index=1)

    coordinator_client.post(URL, {"action": "down", "pk": first.pk})

    ordered = list(WorkshopMaterial.objects.filter(workshop_key=key_of(0)).order_by("position"))
    assert ordered == [second, first]
    other.refresh_from_db()
    assert other.position == 0


def test_delete_removes_the_object_from_storage(coordinator_client, page, competition, storage):
    material = make_material(competition)
    key = material.object_key

    response = coordinator_client.post(URL, {"action": "delete", "pk": material.pk})

    assert response.status_code == 302
    assert not WorkshopMaterial.objects.exists()
    assert key in storage.deleted
    assert key not in storage.objects
    assert actions() == ["workshop_material.deleted"]


def test_delete_of_an_unfinished_upload_aborts_it(coordinator_client, page, competition, storage):
    material = make_material(competition, status=MaterialStatus.UPLOADING)
    material.upload_id = storage().create_upload(material.object_key, "video/mp4")
    material.save()

    coordinator_client.post(URL, {"action": "delete", "pk": material.pk})

    assert storage.uploads == {}
    assert material.object_key in storage.aborted


def test_actions_on_another_competitions_material_are_404(coordinator_client, page, other_competition):
    foreign = make_material(other_competition)

    for action in ("publish", "delete", "up"):
        assert coordinator_client.post(URL, {"action": action, "pk": foreign.pk}).status_code == 404
    assert WorkshopMaterial.objects.filter(pk=foreign.pk).exists()
    assert coordinator_client.get(f"{URL}{foreign.pk}/edit/").status_code == 404
    assert coordinator_client.get(f"{URL}{foreign.pk}/preview/").status_code == 404


def test_rescan_requeues_a_file_waiting_for_the_scanner(
    coordinator_client, page, competition, monkeypatch, django_capture_on_commit_callbacks
):
    queued = []
    monkeypatch.setattr("apps.workshop_materials.tasks.scan_material.delay", queued.append)
    material = make_material(competition, kind="file", status=MaterialStatus.SCANNING)

    with django_capture_on_commit_callbacks(execute=True):
        coordinator_client.post(URL, {"action": "rescan", "pk": material.pk})

    assert queued == [material.pk]


def test_edit_changes_description_and_keeps_the_file(coordinator_client, page, competition):
    material = make_material(competition)
    key = material.object_key

    response = coordinator_client.post(
        f"{URL}{material.pk}/edit/",
        {
            "workshop": key_of(1),
            "title": "Nowy tytuł",
            "description": "Od 12. minuty zadanie 3.",
            "is_published": "on",
        },
    )

    assert response.status_code == 302
    material.refresh_from_db()
    assert material.title == "Nowy tytuł"
    assert material.object_key == key
    assert material.workshop_key == key_of(1)
    assert material.workshop_topic == "Splątanie i nierówności Bella"
    assert AuditLog.objects.get(action="workshop_material.updated").diff["fields"] == [
        "description",
        "title",
        "workshop",
    ]


def test_preview_redirects_the_coordinator_to_a_signed_url_even_for_a_draft(
    coordinator_client, page, competition
):
    material = make_material(competition, published=False)

    response = coordinator_client.get(f"{URL}{material.pk}/preview/")

    assert response.status_code == 302
    assert response["Location"].startswith("https://s3.test/")
    assert "no-store" in response["Cache-Control"]


def test_preview_is_404_before_the_scanner_verdict(coordinator_client, page, competition):
    material = make_material(competition, kind="file", status=MaterialStatus.SCANNING)

    assert coordinator_client.get(f"{URL}{material.pk}/preview/").status_code == 404


# --- materiały osierocone ---------------------------------------------------------------------------


RENAMED = [("Kubity, bramki i obwody", ROWS[0][1], ROWS[0][2]), ROWS[1]]


def test_renamed_workshop_leaves_an_orphan_that_viewers_still_see(
    coordinator_client, participant_client, page, competition
):
    material = make_material(competition, title="Nagranie listopadowe")
    rewrite_rows(page, RENAMED)

    coordinator_content = coordinator_client.get(URL).content.decode()
    viewer_content = participant_client.get("/warsztaty/materialy/").content.decode()

    assert "Materiały bez warsztatu w harmonogramie" in coordinator_content
    # Podpowiedź: jedyny wiersz z tą samą datą jest wybrany na liście.
    new_key = workshop_key(RENAMED[0][0], RENAMED[0][1])
    assert f'<option value="{new_key}" selected>' in coordinator_content
    # Widz nadal widzi materiał – pod tematem z migawki.
    assert "Nagranie listopadowe" in viewer_content
    assert "Kubity i bramki kwantowe" in viewer_content
    assert material.workshop_key == key_of(0)


def test_orphans_are_reattached_in_one_go(coordinator_client, page, competition):
    first = make_material(competition, title="Nagranie")
    second = make_material(competition, kind="file", title="Slajdy")
    rewrite_rows(page, RENAMED)
    new_key = workshop_key(RENAMED[0][0], RENAMED[0][1])

    response = coordinator_client.post(
        URL, {"action": "attach", "pk": [first.pk, second.pk], "workshop": new_key}
    )

    assert response.status_code == 302
    for material in (first, second):
        material.refresh_from_db()
        assert material.workshop_key == new_key
        assert material.workshop_topic == "Kubity, bramki i obwody"
    assert "Materiały bez warsztatu" not in coordinator_client.get(URL).content.decode()


def test_attach_refuses_a_key_outside_the_schedule(coordinator_client, page, competition):
    material = make_material(competition)

    coordinator_client.post(URL, {"action": "attach", "pk": material.pk, "workshop": "2020-01-01-nie-ma"})

    material.refresh_from_db()
    assert material.workshop_key == key_of(0)


def test_orphan_can_be_edited_without_reattaching(coordinator_client, page, competition):
    material = make_material(competition)
    rewrite_rows(page, RENAMED)

    response = coordinator_client.post(
        f"{URL}{material.pk}/edit/", {"workshop": key_of(0), "title": "Poprawiony tytuł", "description": ""}
    )

    assert response.status_code == 302
    material.refresh_from_db()
    assert material.title == "Poprawiony tytuł"
    assert material.workshop_key == key_of(0)
    assert material.workshop_date == date(2026, 11, 12)


def test_multiple_ids_are_only_accepted_for_attach(coordinator_client, page, competition):
    first = make_material(competition, title="A")
    second = make_material(competition, title="B")

    response = coordinator_client.post(URL, {"action": "delete", "pk": [first.pk, second.pk]})

    assert response.status_code == 404
    assert WorkshopMaterial.objects.count() == 2


def test_link_cannot_lose_its_address_on_edit(coordinator_client, page, competition):
    material = make_material(competition, kind="link")

    response = coordinator_client.post(
        f"{URL}{material.pk}/edit/", {"workshop": key_of(0), "title": "Odnośnik", "url": ""}
    )

    assert response.status_code == 400
    material.refresh_from_db()
    assert material.url == "https://example.com/nagranie"


def test_rejected_material_cannot_be_published_through_edit(coordinator_client, page, competition):
    material = make_material(competition, kind="file", published=False, status=MaterialStatus.REJECTED)

    response = coordinator_client.post(
        f"{URL}{material.pk}/edit/", {"workshop": key_of(0), "title": "Plik", "is_published": "on"}
    )

    assert response.status_code == 400
    material.refresh_from_db()
    assert not material.is_published
