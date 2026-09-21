"""Ekran „Slider sponsorów” ``/coordinator/sponsor-slider/``.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran zapisuje wiersz ``cms.SiteSettings`` **witryny konkursu z żądania**, a nie ``Competition`` –
  odwrotnie niż większość tego panelu (patrz ``test_coordinator_forwarding.py``),
- włącznik jest **jedynym** sposobem wyłączenia paska – „zero sekund” nie jest już drugą drogą do
  tego samego stanu, więc pole sekund ma dolną granicę jeden,
- podgląd pod formularzem pokazuje **każdego** partnera z logotypem, także odfiltrowanego, z
  powodem, a nie po cichu pomija,
- zapis zostawia ślad ``site.sponsor_slider_updated`` ze starym i nowym stanem trzech pól,
  a zapis bez zmiany nie zostawia śladu w ogóle.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.images import ImageFile
from PIL import Image as PILImage
from wagtail.images import get_image_model

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.cms.models import HomePage, PartnersPage, SiteSettings
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/coordinator/sponsor-slider/"


def make_image(title: str, size=(120, 40)):
    buffer = BytesIO()
    PILImage.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    buffer.seek(0)
    return get_image_model().objects.create(title=title, file=ImageFile(buffer, name=f"{title}.png"))


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def home(competition) -> HomePage:
    return HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).get()


@pytest.fixture
def partners_page(home) -> PartnersPage:
    page = home.add_child(instance=PartnersPage(title="Partnerzy", slug="partnerzy"))
    page.partners = [
        (
            "partner",
            {
                "name": "Złoty",
                "level": "sponsor-zloty",
                "logo": make_image("Złoty"),
                "url": "",
                "description": "",
            },
        )
    ]
    page.save()
    page.save_revision().publish()
    return PartnersPage.objects.get(pk=page.pk)


def settings_for(competition) -> SiteSettings:
    return SiteSettings.for_site(competition.site)


def audit_entries():
    return AuditLog.objects.filter(action="site.sponsor_slider_updated")


def base_post(**overrides) -> dict:
    data = {"enabled": "on", "seconds": "5", "levels": []}
    data.update(overrides)
    return data


# --- odczyt ---------------------------------------------------------------------------------------


def test_the_screen_shows_the_default_state(coordinator_client, competition):
    content = coordinator_client.get(URL).content.decode()

    assert 'name="seconds"' in content
    assert 'value="5"' in content
    assert 'name="enabled"' in content
    assert "checked" in content  # domyślnie włączony


def test_the_preview_lists_the_organizer_first(coordinator_client, competition, partners_page):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()

    content = coordinator_client.get(URL).content.decode()
    # Nagłówek serwisu renderuje też **żywy** slider (ten sam fragment, co na każdej innej
    # stronie), więc nazwa partnera może wystąpić wcześniej w samym menu – podgląd sprawdzamy
    # wyłącznie w obrębie tabeli pod formularzem.
    table = content.split("Co pokaże slider teraz", 1)[1].split("</table>", 1)[0]

    assert table.index("Organizator – zawsze pierwszy") < table.index("Złoty")


def test_a_filtered_out_partner_is_shown_with_a_reason(coordinator_client, competition, partners_page):
    row = settings_for(competition)
    row.sponsor_slider_levels = ["partner-naukowy"]
    row.save()

    content = coordinator_client.get(URL).content.decode()

    assert "Poziom nie zaznaczony w sliderze" in content


def test_a_partner_without_a_logo_is_marked_in_the_preview(coordinator_client, competition, home):
    page = home.add_child(instance=PartnersPage(title="Partnerzy", slug="partnerzy"))
    page.partners = [
        (
            "partner",
            {
                "name": "Bez logotypu",
                "level": "partner-naukowy",
                "logo": None,
                "url": "",
                "description": "",
            },
        )
    ]
    page.save()
    page.save_revision().publish()

    content = coordinator_client.get(URL).content.decode()

    assert "Bez logotypu" in content
    assert "Brak logotypu" in content


# --- zapis ----------------------------------------------------------------------------------------


def test_saving_updates_seconds_and_leaves_an_audit_entry(coordinator_client, competition):
    response = coordinator_client.post(URL, base_post(seconds="12"))

    row = settings_for(competition)
    assert response.status_code == 302
    assert row.sponsor_slider_seconds == 12
    entry = audit_entries().get()
    assert entry.diff["seconds"] == {"old": 5, "new": 12}


def test_turning_the_switch_off_is_recorded(coordinator_client, competition):
    response = coordinator_client.post(URL, base_post(enabled=""))

    row = settings_for(competition)
    assert response.status_code == 302
    assert row.sponsor_slider_enabled is False
    entry = audit_entries().get()
    assert entry.diff["enabled"] == {"old": True, "new": False}


def test_saving_levels_stores_the_selected_keys(coordinator_client, competition):
    coordinator_client.post(URL, base_post(levels=["sponsor-zloty", "partner-naukowy"]))

    row = settings_for(competition)
    assert set(row.sponsor_slider_levels) == {"sponsor-zloty", "partner-naukowy"}


def test_saving_without_changes_leaves_no_trace(coordinator_client, competition):
    coordinator_client.post(URL, base_post())

    assert not audit_entries().exists()


# --- walidacja ------------------------------------------------------------------------------------


def test_zero_seconds_is_refused(coordinator_client, competition):
    """Zero przestało być sposobem na wyłączenie slidera – od tego jest włącznik."""
    response = coordinator_client.post(URL, base_post(seconds="0"))

    assert response.status_code == 400
    assert settings_for(competition).sponsor_slider_seconds == 5


def test_more_than_the_limit_is_refused(coordinator_client, competition):
    response = coordinator_client.post(URL, base_post(seconds="121"))

    assert response.status_code == 400
    assert settings_for(competition).sponsor_slider_seconds == 5


def test_a_non_numeric_value_is_refused(coordinator_client, competition):
    response = coordinator_client.post(URL, base_post(seconds="pięć"))

    assert response.status_code == 400
    assert settings_for(competition).sponsor_slider_seconds == 5


def test_an_unknown_level_key_is_refused(coordinator_client, competition):
    response = coordinator_client.post(URL, base_post(levels=["nieistniejacy-poziom"]))

    assert response.status_code == 400
    assert settings_for(competition).sponsor_slider_levels == []


# --- uprawnienia i zakres konkursu ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_open_the_screen(web_client, participant, reviewer, role):
    web_client.force_login(participant.user if role == "participant" else reviewer.user)

    assert web_client.get(URL).status_code == 403
    assert web_client.post(URL, base_post()).status_code == 403


def test_anonymous_is_redirected_to_login(web_client):
    response = web_client.get(URL)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


def test_the_save_touches_only_the_competition_of_the_request(client_for, competition, other_competition):
    user = CoordinatorFactory()
    grant_membership(user, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(user)

    client.post(URL, base_post(seconds="17"))

    assert settings_for(other_competition).sponsor_slider_seconds == 17
    assert settings_for(competition).sponsor_slider_seconds == 5


def test_the_menu_links_to_the_screen(coordinator_client):
    content = coordinator_client.get("/coordinator/").content.decode()

    assert f'href="{URL}"' in content
    assert "Slider sponsorów" in content
