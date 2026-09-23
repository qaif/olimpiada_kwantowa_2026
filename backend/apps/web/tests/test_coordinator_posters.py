"""Ekran „Plakaty do pobrania” ``/coordinator/posters/``: redakcja, statystyki, eksport, uprawnienia.

Przedmiotem jest to, czego nie widać po samym widoku:

- **pliki**: format rozpoznany po treści, miniatura JPG/PNG robiona sama, PDF bez podglądu dostaje
  ikonę; podmiana pliku sprząta stary plik ze storage,
- **usunięcie plakatu z pobraniami archiwizuje** go, a nie kasuje – statystyki zostają; plakat bez
  pobrań znika naprawdę, razem z plikiem,
- **statystyki są podwójne** (pobrania / unikalne IP) w trzech oknach, w tabeli, w sumie i w CSV,
- **ślad w audycie** przy każdej zmianie, bez tytułu i nazwy pliku w treści wpisu,
- **tylko koordynator tego konkursu** – uczestnik dostaje 403, anonim logowanie, a plakat sąsiada
  pod naszą domeną nie istnieje (404).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.promo.models import PromoDownload, PromoMaterial
from apps.promo.tests.helpers import (
    FAKE_PDF_BYTES,
    PDF_BYTES,
    image_bytes,
    make_download,
    make_material,
    upload,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/coordinator/posters/"


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def actions() -> list[str]:
    return list(AuditLog.objects.filter(action__startswith="promo.").values_list("action", flat=True))


def post_form(client, url, **fields):
    data = {"title": "Plakat olimpiady", "description": "A4 pionowy", **fields}
    return client.post(url, data)


# --- dodanie i edycja -------------------------------------------------------------------------------


def test_create_pdf_as_draft_without_preview(coordinator_client, competition):
    response = post_form(coordinator_client, f"{URL}new/", file=upload("plakat.pdf", PDF_BYTES))

    assert response.status_code == 302
    material = PromoMaterial.objects.get()
    assert material.competition == competition
    assert material.file_format == "pdf"
    assert material.file_size == len(PDF_BYTES)
    assert material.is_published is False
    assert not material.preview
    assert material.created_by is not None
    # Plik w storage **prywatnym**, pod kluczem bez nazwy od przesyłającego.
    assert Path(material.file.path).parts[-4] == "private"
    assert "plakat" not in material.file.name
    assert actions() == ["promo.created"]
    entry = AuditLog.objects.get(action="promo.created")
    assert "Plakat olimpiady" not in str(entry.diff)


def test_create_png_generates_the_thumbnail(coordinator_client):
    post_form(
        coordinator_client,
        f"{URL}new/",
        file=upload("plakat.png", image_bytes("PNG", size=(1200, 1700))),
        is_published="on",
    )

    material = PromoMaterial.objects.get()
    assert material.file_format == "png"
    assert material.preview_is_generated is True
    assert material.preview.name.endswith(".jpg")
    assert material.is_published is True


def test_create_pdf_with_own_preview(coordinator_client):
    post_form(
        coordinator_client,
        f"{URL}new/",
        file=upload("plakat.pdf", PDF_BYTES),
        preview=upload("podglad.jpg", image_bytes("JPEG")),
    )

    material = PromoMaterial.objects.get()
    assert material.preview
    assert material.preview_is_generated is False


def test_fake_pdf_is_refused_with_a_message(coordinator_client):
    response = post_form(coordinator_client, f"{URL}new/", file=upload("plakat.pdf", FAKE_PDF_BYTES))

    assert response.status_code == 400
    assert "Treść pliku nie jest" in response.content.decode()
    assert PromoMaterial.objects.count() == 0


def test_new_material_goes_to_the_end_of_the_list(coordinator_client, competition):
    make_material(competition, position=5)

    post_form(coordinator_client, f"{URL}new/", file=upload("plakat.pdf", PDF_BYTES))

    assert PromoMaterial.objects.order_by("-id").first().position == 6


def test_edit_keeps_the_file_when_none_is_uploaded(coordinator_client, competition):
    material = make_material(competition)
    old_name = material.file.name

    response = post_form(
        coordinator_client, f"{URL}{material.pk}/edit/", title="Nowy tytuł", is_published="on"
    )

    assert response.status_code == 302
    material.refresh_from_db()
    assert material.title == "Nowy tytuł"
    assert material.file.name == old_name
    assert AuditLog.objects.get(action="promo.updated").diff["fields"] == ["description", "title"]


def test_replacing_the_file_removes_the_old_one_and_keeps_the_stats(coordinator_client, competition):
    material = make_material(competition)
    make_download(material)
    old_path = Path(material.file.path)

    post_form(
        coordinator_client,
        f"{URL}{material.pk}/edit/",
        file=upload("nowy.png", image_bytes("PNG")),
        is_published="on",
    )

    material.refresh_from_db()
    assert material.file_format == "png"
    assert material.preview_is_generated is True
    assert not old_path.exists()
    assert material.downloads.count() == 1


def test_archived_material_cannot_be_edited(coordinator_client, competition):
    from django.utils import timezone

    material = make_material(competition, archived_at=timezone.now())

    assert coordinator_client.get(f"{URL}{material.pk}/edit/").status_code == 404


# --- akcje na liście ----------------------------------------------------------------------------------


def test_publish_and_unpublish(coordinator_client, competition):
    material = make_material(competition, published=False)

    coordinator_client.post(URL, {"action": "publish", "pk": material.pk})
    material.refresh_from_db()
    assert material.is_published is True

    coordinator_client.post(URL, {"action": "unpublish", "pk": material.pk})
    material.refresh_from_db()
    assert material.is_published is False
    assert actions() == ["promo.unpublished", "promo.published"]


def test_reorder_moves_one_place(coordinator_client, competition):
    first = make_material(competition, title="A", position=0)
    make_material(competition, title="B", position=0)
    third = make_material(competition, title="C", position=0)

    coordinator_client.post(URL, {"action": "up", "pk": third.pk})

    order = list(PromoMaterial.objects.order_by("position", "id").values_list("title", flat=True))
    assert order == ["A", "C", "B"]
    assert actions() == ["promo.reordered"]
    # Na brzegu listy nic się nie dzieje i nie zostaje ślad.
    coordinator_client.post(URL, {"action": "up", "pk": first.pk})
    assert actions() == ["promo.reordered"]


def test_delete_without_downloads_removes_row_and_files(coordinator_client, competition):
    material = make_material(competition)
    path = Path(material.file.path)

    coordinator_client.post(URL, {"action": "delete", "pk": material.pk})

    assert PromoMaterial.objects.count() == 0
    assert not path.exists()
    assert actions() == ["promo.deleted"]


def test_delete_with_downloads_archives_and_keeps_stats(coordinator_client, competition):
    material = make_material(competition)
    make_download(material)

    coordinator_client.post(URL, {"action": "delete", "pk": material.pk})

    material.refresh_from_db()
    assert material.is_archived
    assert material.is_published is False
    assert PromoDownload.objects.count() == 1
    assert actions() == ["promo.archived"]
    content = coordinator_client.get(URL).content.decode()
    assert "Archiwum" in content

    coordinator_client.post(URL, {"action": "restore", "pk": material.pk})
    material.refresh_from_db()
    assert not material.is_archived
    assert material.is_published is False


def test_unknown_action_and_bad_pk_are_404(coordinator_client, competition):
    material = make_material(competition)

    assert coordinator_client.post(URL, {"action": "drop", "pk": material.pk}).status_code == 404
    assert coordinator_client.post(URL, {"action": "publish", "pk": "abc"}).status_code == 404


def test_coordinator_file_download_is_not_counted(coordinator_client, competition):
    material = make_material(competition, published=False)

    response = coordinator_client.get(f"{URL}{material.pk}/file/")

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES
    assert PromoDownload.objects.count() == 0


# --- statystyki i eksport ---------------------------------------------------------------------------


def test_list_shows_downloads_and_unique_ips(coordinator_client, competition):
    material = make_material(competition, title="Plakat A")
    make_download(material, ip_hash="a" * 64)
    make_download(material, ip_hash="a" * 64)
    make_download(material, ip_hash="b" * 64)

    response = coordinator_client.get(URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Plakat A" in content
    assert "unik. IP" in content
    rows = response.context["active_rows"]
    assert (rows[0].counts.total, rows[0].counts.unique) == (3, 2)
    assert (response.context["totals"].total_30, response.context["totals"].unique_30) == (3, 2)
    assert response.context["series"][0]["count"] == 3


def test_chart_can_be_narrowed_to_one_material(coordinator_client, competition, other_competition):
    first = make_material(competition, title="A")
    second = make_material(competition, title="B")
    theirs = make_material(other_competition, title="C")
    make_download(first)
    make_download(second)
    make_download(second)

    assert coordinator_client.get(f"{URL}?material={second.pk}").context["series"][0]["count"] == 2
    # Plakat cudzego konkursu w parametrze nie zawęża niczego – wykres zostaje „wszystkie”.
    response = coordinator_client.get(f"{URL}?material={theirs.pk}")
    assert response.context["chart_material"] is None
    assert response.context["series"][0]["count"] == 3


def test_csv_export_has_both_numbers_and_a_total_row(coordinator_client, competition):
    first = make_material(competition, title="Plakat A")
    second = make_material(competition, title="Plakat B")
    make_download(first, ip_hash="a" * 64)
    make_download(second, ip_hash="a" * 64)
    make_download(second, ip_hash="b" * 64)

    response = coordinator_client.get(f"{URL}export.csv")
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    lines = body.strip().split("\r\n")

    assert response["Content-Type"].startswith("text/csv")
    assert "unikalne IP – 7 dni" in lines[0]
    assert lines[1].startswith("Plakat A;")
    assert lines[-1].startswith("RAZEM")
    # Suma pobrań 3, unikalnych adresów 2 – a nie 3, bo adres „a” pobrał oba plakaty.
    assert ";3;2;3;2;3;2;" in lines[-1]
    assert AuditLog.objects.filter(action="export.generated", diff__kind="promo_downloads").exists()


# --- uprawnienia i izolacja -----------------------------------------------------------------------


def test_participant_is_denied(client_for, competition):
    participant = ParticipantFactory()
    client = client_for(competition)
    client.force_login(participant.user)

    assert client.get(URL).status_code == 403
    assert client.get(f"{URL}new/").status_code == 403
    assert client.get(f"{URL}export.csv").status_code == 403


def test_anonymous_is_sent_to_login(client_for, competition):
    response = client_for(competition).get(URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_material_of_another_competition_is_404(coordinator_client, other_competition):
    theirs = make_material(other_competition)

    assert coordinator_client.get(f"{URL}{theirs.pk}/edit/").status_code == 404
    assert coordinator_client.get(f"{URL}{theirs.pk}/file/").status_code == 404
    assert coordinator_client.post(URL, {"action": "delete", "pk": theirs.pk}).status_code == 404
    assert PromoMaterial.objects.filter(pk=theirs.pk).exists()


def test_menu_has_the_posters_item(coordinator_client):
    content = coordinator_client.get(URL).content.decode()

    assert 'aria-current="page"' in content
    assert "Plakaty do pobrania" in content
