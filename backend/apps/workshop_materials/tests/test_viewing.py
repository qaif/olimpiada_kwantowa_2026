"""Oglądanie materiałów: kto, co i jakim adresem – ``/warsztaty/materialy/…`` i zapowiedź na ``/warsztaty/``.

Przedmiotem jest reguła organizatora („tylko na stronie po zalogowaniu”) i jej skutki uboczne:

- anonim → logowanie; konto spoza konkursu → 403; materiał cudzego konkursu, szkic i plik przed
  werdyktem skanera → 404; wyłączony przełącznik → 404 dla wszystkich,
- film dostaje **podpisany, krótkotrwały** adres w ``<video>`` na stronie materiału; lista nie ma
  żadnego adresu pliku; pobranie pliku to przekierowanie na podpis z ``Content-Disposition``,
- odpowiedzi z podpisem mają ``no-store`` i **nie** trafiają do pamięci stron publicznych, a wersja
  ``/warsztaty/`` dla gościa (która do tej pamięci trafia) nie ma żadnego adresu z magazynu,
- wyświetlenia liczą się bez zapisu konta – a koordynatora nie liczymy wcale.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.tenancy.tests.factories import grant_membership
from apps.workshop_materials.models import MaterialStatus, WorkshopMaterialViewer
from apps.workshop_materials.stats import viewer_hash

from .helpers import enable, make_material, workshops_page_for

pytestmark = pytest.mark.django_db

LIST = "/warsztaty/materialy/"


@pytest.fixture
def ready(competition):
    enable(competition)
    workshops_page_for(competition)
    return competition


def detail(material) -> str:
    return f"{LIST}{material.pk}/"


def open_url(material) -> str:
    return f"{LIST}{material.pk}/pobierz/"


# --- bramki -------------------------------------------------------------------------------------


def test_anonymous_is_sent_to_the_login_page(client_for, ready):
    material = make_material(ready)
    client = client_for(ready)

    for url in (LIST, detail(material), open_url(material)):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response["Location"].startswith("/login/?next=")


def test_flag_off_is_404_for_everyone(client_for, participant_client, competition):
    workshops_page_for(competition)
    material = make_material(competition)

    assert client_for(competition).get(LIST).status_code == 404
    assert participant_client.get(LIST).status_code == 404
    assert participant_client.get(detail(material)).status_code == 404


def test_participant_of_another_competition_is_403(client_for, ready, other_competition):
    profile = ParticipantFactory(competition=other_competition)
    grant_membership(profile.user, other_competition, CompetitionRole.PARTICIPANT)
    client = client_for(ready)
    client.force_login(profile.user)

    assert client.get(LIST).status_code == 403


def test_account_without_any_role_is_403(client_for, ready):
    client = client_for(ready)
    client.force_login(UserFactory())

    assert client.get(LIST).status_code == 403


@pytest.mark.parametrize(
    "role", [CompetitionRole.SUPERVISOR, CompetitionRole.REVIEWER, CompetitionRole.COORDINATOR]
)
def test_staff_roles_of_this_competition_may_watch(client_for, ready, role):
    user = UserFactory(groups=[role.value])
    grant_membership(user, ready, role)
    client = client_for(ready)
    client.force_login(user)

    assert client.get(LIST).status_code == 200


def test_material_of_another_competition_is_404(participant_client, ready, other_competition):
    enable(other_competition)
    foreign = make_material(other_competition, title="Cudze nagranie")

    assert participant_client.get(detail(foreign)).status_code == 404
    assert participant_client.get(open_url(foreign)).status_code == 404


@pytest.mark.parametrize(
    ("published", "status"),
    [
        (False, MaterialStatus.READY),
        (True, MaterialStatus.SCANNING),
        (True, MaterialStatus.UPLOADING),
        (True, MaterialStatus.REJECTED),
    ],
)
def test_drafts_and_unready_materials_are_404(participant_client, ready, published, status):
    material = make_material(ready, kind="file", published=published, status=status)

    assert participant_client.get(detail(material)).status_code == 404
    assert participant_client.get(open_url(material)).status_code == 404
    assert material.title not in participant_client.get(LIST).content.decode()


# --- adresy podpisane -------------------------------------------------------------------------------


def test_list_groups_by_workshop_and_carries_no_storage_address(participant_client, ready):
    make_material(ready, title="Nagranie zajęć")
    make_material(ready, kind="file", title="Slajdy", index=1)

    response = participant_client.get(LIST)

    content = response.content.decode()
    assert response.status_code == 200
    assert "Kubity i bramki kwantowe" in content
    assert "Splątanie i nierówności Bella" in content
    assert "s3.test" not in content
    assert "X-Amz" not in content
    assert "no-store" in response["Cache-Control"]


def test_video_page_embeds_a_short_lived_signed_url(participant_client, ready):
    material = make_material(ready)

    response = participant_client.get(detail(material))

    content = response.content.decode()
    assert response.status_code == 200
    assert '<video class="workshop-video" src="https://s3.test/submissions/' in content
    assert "X-Amz-Expires=7200" in content
    assert "response-content-type=video/mp4" in content
    assert 'controlslist="nodownload"' in content
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


def test_video_has_no_direct_download_address(participant_client, ready):
    material = make_material(ready)

    response = participant_client.get(open_url(material))

    assert response.status_code == 302
    assert response["Location"] == detail(material)


def test_file_download_redirects_to_a_signed_attachment(participant_client, ready, storage):
    material = make_material(ready, kind="file", title="Slajdy z wykładu")

    response = participant_client.get(open_url(material))

    assert response.status_code == 302
    location = response["Location"]
    assert location.startswith("https://s3.test/submissions/")
    assert "X-Amz-Expires=300" in location
    assert "response-content-type=application/pdf" in location
    # PDF otwiera się w przeglądarce (inline), pod nazwą z tytułu.
    assert "inline" in location
    assert "slajdy-z-wykladu.pdf" in location
    assert "no-store" in response["Cache-Control"]


def test_link_redirects_to_the_external_address(participant_client, ready):
    material = make_material(ready, kind="link")

    response = participant_client.get(open_url(material))

    assert response.status_code == 302
    assert response["Location"] == "https://example.com/nagranie"


# --- statystyka -------------------------------------------------------------------------------------


def test_views_are_counted_with_a_pseudonym_only(participant_client, ready):
    material = make_material(ready)

    participant_client.get(detail(material))
    participant_client.get(detail(material))

    material.refresh_from_db()
    assert material.view_count == 2
    viewers = list(WorkshopMaterialViewer.objects.all())
    assert len(viewers) == 1
    assert viewers[0].viewer_hash == viewer_hash(material.pk, participant_client.user.pk)
    assert str(participant_client.user.pk) not in viewers[0].viewer_hash


def test_the_same_person_has_unrelated_pseudonyms_per_material():
    assert viewer_hash(1, 7) != viewer_hash(2, 7)


def test_coordinator_views_are_not_counted(coordinator_client, ready):
    material = make_material(ready)

    assert coordinator_client.get(detail(material)).status_code == 200

    material.refresh_from_db()
    assert material.view_count == 0
    assert not WorkshopMaterialViewer.objects.exists()


# --- zapowiedź na /warsztaty/ i pamięć stron publicznych --------------------------------------------


@pytest.fixture
def page_cache_on(settings):
    settings.PAGE_CACHE_ENABLED = True
    settings.PAGE_CACHE_SECONDS = 60


def test_guest_sees_the_teaser_without_any_file_address(client_for, ready, page_cache_on):
    make_material(ready)
    client = client_for(ready)

    response = client.get("/warsztaty/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Materiały z warsztatów" in content
    assert "/login/?next=/warsztaty/materialy/" in content
    assert "s3.test" not in content
    assert "workshop-materials/" not in content


def test_teaser_is_absent_with_the_flag_off(client_for, competition):
    workshops_page_for(competition)
    make_material(competition)

    content = client_for(competition).get("/warsztaty/").content.decode()

    assert "Zaloguj się, aby obejrzeć" not in content


def test_publishing_a_material_refreshes_the_cached_workshops_page(client_for, ready, page_cache_on):
    material = make_material(ready, published=False)
    client = client_for(ready)
    assert "Zaloguj się, aby obejrzeć" not in client.get("/warsztaty/").content.decode()
    assert client.get("/warsztaty/")["X-Page-Cache"] == "HIT"

    material.is_published = True
    material.save()

    response = client.get("/warsztaty/")
    assert response["X-Page-Cache"] == "MISS"
    assert "Zaloguj się, aby obejrzeć" in response.content.decode()


def test_material_pages_never_reach_the_page_cache(participant_client, ready, page_cache_on):
    material = make_material(ready)

    for url in (LIST, detail(material)):
        first = participant_client.get(url)
        second = participant_client.get(url)
        assert first.get("X-Page-Cache") in (None, "BYPASS"), url
        assert second.get("X-Page-Cache") in (None, "BYPASS"), url
        # Każde wyświetlenie to nowy podpis – nic nie jest odtwarzane z pamięci.
    assert first.content != b"" and "X-Amz" in second.content.decode()


def test_anonymous_login_redirect_is_not_cached(client_for, ready, page_cache_on):
    from apps.web.page_cache import is_cacheable_path

    assert not is_cacheable_path(LIST)
    assert not is_cacheable_path(f"{LIST}1/")
    assert client_for(ready).get(LIST).status_code == 302


# --- rejestr czynności przetwarzania ----------------------------------------------------------------


def test_register_row_exists_only_with_the_feature(competition):
    from apps.accounts.processing_register import WORKSHOP_MATERIALS_ACTIVITY, activities_for

    assert WORKSHOP_MATERIALS_ACTIVITY not in activities_for(competition)
    enable(competition)
    assert WORKSHOP_MATERIALS_ACTIVITY in activities_for(competition)
