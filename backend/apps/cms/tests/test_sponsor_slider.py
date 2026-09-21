"""Pasek rotujących logotypów w menu (``apps.cms.sponsor_slider``): co trafia do ładunku, kiedy
znika i kiedy pamięć podręczna się czyści.

Uwaga organizatora z 21.09.2026, „jak na Olimpiadzie Biologicznej”: pierwsza plansza to zawsze
logotyp organizatora (``SiteSettings.organizer_logo``), potem partnerzy z ``/partnerzy/`` –
w kolejności strony, z pominięciem tych bez logotypu, spoza wybranych poziomów i zdublowanych
z organizatorem. Włącznik (``sponsor_slider_enabled``) jest jedynym sposobem wyłączenia paska.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.images import ImageFile
from PIL import Image as PILImage
from wagtail.images import get_image_model

from apps.cms.models import FAQPage, HomePage, PartnersPage, SiteSettings
from apps.cms.sponsor_slider import build_payload, cache_key, cached_payload, normalize_url

pytestmark = pytest.mark.django_db


def make_image(title: str, size=(120, 40)):
    buffer = BytesIO()
    PILImage.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    buffer.seek(0)
    return get_image_model().objects.create(title=title, file=ImageFile(buffer, name=f"{title}.png"))


@pytest.fixture
def home(competition) -> HomePage:
    return HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).get()


@pytest.fixture
def partners_page(home) -> PartnersPage:
    return home.add_child(instance=PartnersPage(title="Partnerzy", slug="partnerzy"))


def set_partners(page: PartnersPage, entries: list[dict]) -> None:
    page.partners = [("partner", entry) for entry in entries]
    page.save()
    page.save_revision().publish()


def settings_for(competition) -> SiteSettings:
    return SiteSettings.for_site(competition.site)


def partner(name, level="partner-naukowy", logo=None, url="") -> dict:
    return {"name": name, "level": level, "logo": logo, "url": url, "description": ""}


# --- ładunek: organizator ---------------------------------------------------------------------


def test_default_state_has_no_organizer_and_no_partners(competition):
    """Domyślnie slider jest włączony, ale bez logotypu organizatora i bez partnerów nie ma nic."""
    assert build_payload(competition) == {"seconds": 0, "entries": []}


def test_the_organizer_logo_is_the_first_entry(competition, partners_page):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.organizer_name = "Fundacja Testowa"
    row.contact_url = "https://organizator.example/"
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    payload = build_payload(competition)

    assert payload["entries"][0]["name"] == "Fundacja Testowa"
    assert payload["entries"][0]["url"] == "https://organizator.example/"
    assert len(payload["entries"]) == 2


def test_without_an_organizer_logo_the_slider_starts_with_the_first_partner(competition, partners_page):
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Partner A"]


def test_the_organizer_alone_is_a_single_static_entry(competition):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()

    payload = build_payload(competition)

    assert len(payload["entries"]) == 1
    assert payload["seconds"] == row.sponsor_slider_seconds


# --- ładunek: włącznik i partnerzy ---------------------------------------------------------------


def test_disabling_the_switch_hides_everything(competition, partners_page):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.sponsor_slider_enabled = False
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    assert build_payload(competition) == {"seconds": 0, "entries": []}


def test_partners_without_a_logo_are_skipped(competition, partners_page):
    set_partners(
        partners_page,
        [partner("Bez logotypu"), partner("Z logotypem", logo=make_image("Z logotypem"))],
    )

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Z logotypem"]


def test_a_partner_without_a_url_has_no_link(competition, partners_page):
    set_partners(partners_page, [partner("Bez adresu", logo=make_image("Bez adresu"), url="")])

    payload = build_payload(competition)

    assert payload["entries"][0]["url"] == ""


def test_partners_keep_the_order_of_the_partners_page(competition, partners_page):
    set_partners(
        partners_page,
        [
            partner("Pierwszy", logo=make_image("Pierwszy")),
            partner("Drugi", logo=make_image("Drugi")),
            partner("Trzeci", logo=make_image("Trzeci")),
        ],
    )

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Pierwszy", "Drugi", "Trzeci"]


def test_the_level_filter_limits_which_partners_show(competition, partners_page):
    row = settings_for(competition)
    row.sponsor_slider_levels = ["sponsor-zloty"]
    row.save()
    set_partners(
        partners_page,
        [
            partner("Złoty", level="sponsor-zloty", logo=make_image("Złoty")),
            partner("Naukowy", level="partner-naukowy", logo=make_image("Naukowy")),
        ],
    )

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Złoty"]


def test_an_empty_level_filter_shows_every_level(competition, partners_page):
    set_partners(
        partners_page,
        [
            partner("Złoty", level="sponsor-zloty", logo=make_image("Złoty")),
            partner("Naukowy", level="partner-naukowy", logo=make_image("Naukowy")),
        ],
    )

    payload = build_payload(competition)

    assert {entry["name"] for entry in payload["entries"]} == {"Złoty", "Naukowy"}


def test_an_unknown_level_key_next_to_a_known_one_is_ignored(competition, partners_page):
    """Poziom usunięty z kodu po zapisaniu filtra nie wywraca payloadu – po prostu nic nie wybiera."""
    row = settings_for(competition)
    SiteSettings.objects.filter(pk=row.pk).update(
        sponsor_slider_levels=["sponsor-zloty", "nieistniejacy-poziom"]
    )
    set_partners(
        partners_page,
        [
            partner("Złoty", level="sponsor-zloty", logo=make_image("Złoty")),
            partner("Naukowy", level="partner-naukowy", logo=make_image("Naukowy")),
        ],
    )

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Złoty"]


def test_a_filter_made_only_of_unknown_keys_falls_back_to_showing_every_level(competition, partners_page):
    row = settings_for(competition)
    SiteSettings.objects.filter(pk=row.pk).update(sponsor_slider_levels=["nieistniejacy-poziom"])
    set_partners(partners_page, [partner("Naukowy", logo=make_image("Naukowy"))])

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Naukowy"]


# --- duplikat organizatora ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "partner_url",
    [
        "https://organizator.example/",
        "https://www.organizator.example/",
        "http://organizator.example",
        "https://organizator.example",
    ],
)
def test_a_partner_at_the_organizer_address_is_not_shown_twice(competition, partners_page, partner_url):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.contact_url = "https://organizator.example/"
    row.save()
    set_partners(
        partners_page,
        [partner("Duplikat", logo=make_image("Duplikat"), url=partner_url)],
    )

    payload = build_payload(competition)

    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["url"] == row.contact_url


def test_a_partner_at_a_different_address_stays_next_to_the_organizer(competition, partners_page):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.contact_url = "https://organizator.example/"
    row.save()
    set_partners(
        partners_page,
        [partner("Inny", logo=make_image("Inny"), url="https://inny.example/")],
    )

    payload = build_payload(competition)

    assert len(payload["entries"]) == 2


def test_normalize_url_ignores_scheme_www_and_trailing_slash():
    assert normalize_url("https://www.example.org/") == normalize_url("http://example.org")


# --- pamięć podręczna -------------------------------------------------------------------------


def test_a_warm_cache_costs_zero_queries(competition, partners_page, django_assert_max_num_queries):
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    cached_payload(competition)  # zimne wywołanie – wypełnia pamięć podręczną

    with django_assert_max_num_queries(0):
        cached_payload(competition)


def test_publishing_the_partners_page_invalidates_the_cache(competition, partners_page):
    set_partners(partners_page, [partner("Pierwszy", logo=make_image("Pierwszy"))])
    cached_payload(competition)

    set_partners(partners_page, [partner("Drugi", logo=make_image("Drugi"))])

    assert [entry["name"] for entry in cached_payload(competition)["entries"]] == ["Drugi"]


def test_unpublishing_the_partners_page_invalidates_the_cache(competition, partners_page):
    set_partners(partners_page, [partner("Jedyny", logo=make_image("Jedyny"))])
    cached_payload(competition)

    partners_page.unpublish()

    assert cached_payload(competition) == {"seconds": 0, "entries": []}


def test_saving_site_settings_invalidates_the_cache(competition):
    cached_payload(competition)  # payload pusty trafia do pamięci

    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()

    assert cached_payload(competition)["entries"]


def test_the_cache_is_scoped_per_site(competition, other_competition, partners_page):
    set_partners(partners_page, [partner("Tylko konkurs 1", logo=make_image("Tylko konkurs 1"))])
    cached_payload(competition)

    assert cache_key(competition) != cache_key(other_competition)
    assert cached_payload(other_competition) == {"seconds": 0, "entries": []}


# --- to, co widzi czytelnik ---------------------------------------------------------------------


def test_the_slider_markup_sits_in_the_nav_after_faq(web_client, competition, home, partners_page):
    home.add_child(instance=FAQPage(title="FAQ", slug="faq", show_in_menus=True))
    set_partners(
        partners_page, [partner("Partner A", logo=make_image("Partner A"), url="https://a.example/")]
    )

    content = web_client.get("/").content.decode()

    nav = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]
    assert "data-sponsor-slider" in nav
    faq_index = nav.find("FAQ")
    slider_index = nav.find("data-sponsor-slider")
    assert 0 <= faq_index < slider_index


def test_data_interval_matches_the_setting(web_client, competition, partners_page):
    row = settings_for(competition)
    row.sponsor_slider_seconds = 9
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    content = web_client.get("/").content.decode()

    assert 'data-interval="9"' in content


def test_each_logo_appears_exactly_once_in_the_server_html(web_client, competition, partners_page):
    set_partners(
        partners_page,
        [
            partner("Alfa", logo=make_image("Alfa")),
            partner("Beta", logo=make_image("Beta")),
        ],
    )

    content = web_client.get("/").content.decode()
    nav = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    # Poza nawigacją strona główna ma **własny** pas partnerów (``.partner-strip``, niezależny
    # od filtra slidera), więc te same napisy liczymy wyłącznie w obrębie ``<nav>``.
    assert nav.count('alt="Alfa"') == 1
    assert nav.count('alt="Beta"') == 1
    assert nav.count("sponsor-slider__item") == 2


def test_a_link_opens_in_a_new_tab_without_an_opener(web_client, competition, partners_page):
    set_partners(
        partners_page, [partner("Partner A", logo=make_image("Partner A"), url="https://a.example/")]
    )

    content = web_client.get("/").content.decode()

    assert 'href="https://a.example/" rel="noopener" target="_blank"' in content


def test_a_disabled_slider_renders_nothing(web_client, competition, partners_page):
    row = settings_for(competition)
    row.sponsor_slider_enabled = False
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    content = web_client.get("/").content.decode()

    assert "data-sponsor-slider" not in content


def test_no_partners_page_and_no_organizer_logo_renders_nothing(web_client, competition):
    content = web_client.get("/").content.decode()

    assert "data-sponsor-slider" not in content


def test_only_the_organizer_renders_a_single_item(web_client, competition):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()

    content = web_client.get("/").content.decode()

    assert content.count("sponsor-slider__item") == 1
    assert "data-sponsor-slider" in content
