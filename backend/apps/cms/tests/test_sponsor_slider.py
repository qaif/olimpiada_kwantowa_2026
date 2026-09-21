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
from django.core.cache import cache as django_cache
from django.core.files.images import ImageFile
from PIL import Image as PILImage
from wagtail.images import get_image_model
from wagtail.images.models import SourceImageIOError

from apps.cms.models import ContentPage, FAQPage, HomePage, PartnersPage, SiteSettings
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


def test_the_link_and_image_are_not_natively_draggable(web_client, competition, partners_page):
    """``draggable="false"`` na obu znacznikach: bez tego przeciąganie myszą nad logotypem zaczyna
    natywne przeciąganie obrazka/odnośnika zamiast przesuwać taśmę (sponsor-slider.js)."""
    set_partners(
        partners_page, [partner("Partner A", logo=make_image("Partner A"), url="https://a.example/")]
    )

    content = web_client.get("/").content.decode()
    nav = content.split('class="nav nav--cms"', 1)[1].split("</nav>", 1)[0]

    assert 'class="sponsor-slider__link"' in nav
    link = nav.split('class="sponsor-slider__link"', 1)[1].split(">", 1)[0]
    assert 'draggable="false"' in link
    img = nav.split('class="sponsor-slider__logo"', 1)[1].split(">", 1)[0]
    assert 'draggable="false"' in img


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


# --- odporność na uszkodzone logotypy (Critic review PR #9, blocker 1) --------------------------
#
# Ten sam plik w bibliotece bywa nieosiągalny: skasowany bezpośrednio w magazynie, awaria
# S3/MinIO, uszkodzony format. To jest stan danych, na który organizator nie ma wpływu z poziomu
# przeglądarki, a ten kod stoi w procesorze kontekstu wołanym na **każdej** stronie serwisu –
# jeden zepsuty plik nie może dawać pięćsetki na całym serwisie.


def test_a_broken_organizer_logo_is_skipped_and_partners_still_show(competition, partners_page, monkeypatch):
    row = settings_for(competition)
    organizer_logo = make_image("Organizator")
    row.organizer_logo = organizer_logo
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    Image = get_image_model()
    original = Image.get_rendition

    def broken(self, *args, **kwargs):
        if self.pk == organizer_logo.pk:
            raise SourceImageIOError("plik nieosiągalny")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image, "get_rendition", broken)

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Partner A"]


def test_a_broken_partner_logo_is_skipped_other_partners_still_show(competition, partners_page, monkeypatch):
    bad = make_image("Zły")
    good = make_image("Dobry")
    set_partners(partners_page, [partner("Zły", logo=bad), partner("Dobry", logo=good)])

    Image = get_image_model()
    original = Image.get_rendition

    def broken(self, *args, **kwargs):
        if self.pk == bad.pk:
            raise SourceImageIOError("plik nieosiągalny")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image, "get_rendition", broken)

    payload = build_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Dobry"]


def test_every_broken_logo_yields_an_empty_slider_not_a_crash(competition, partners_page, monkeypatch):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    def always_broken(self, *args, **kwargs):
        raise SourceImageIOError("plik nieosiągalny")

    monkeypatch.setattr(get_image_model(), "get_rendition", always_broken)

    assert build_payload(competition) == {"seconds": 0, "entries": []}


def test_an_unexpected_exception_in_the_builder_yields_an_empty_slider(
    competition, partners_page, monkeypatch
):
    """Nie tylko błąd obrazu – dowolny nieprzewidziany wyjątek ma dawać pusty pasek, nie 500."""
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()

    def boom(*args, **kwargs):
        raise RuntimeError("awaria magazynu")

    monkeypatch.setattr("apps.cms.sponsor_slider.partner_entries", boom)

    assert build_payload(competition) == {"seconds": 0, "entries": []}


def test_an_image_deleted_from_storage_is_skipped(competition, partners_page):
    """Rekord istnieje, ale plik zniknął z magazynu – to samo zachowanie, co uszkodzony plik.

    Plik kasujemy **przed** publikacją: publikacja strony „Partnerzy” już przelicza i zapisuje
    rendition (``_warm`` – patrz medium 7), więc gdybyśmy skasowali plik dopiero potem, test
    trafiłby w rendition już wygenerowany i zapisany, a nie w ten sam, zepsuty plik źródłowy.
    """
    image = make_image("Partner A")
    image.file.storage.delete(image.file.name)
    set_partners(partners_page, [partner("Partner A", logo=image)])

    assert build_payload(competition) == {"seconds": 0, "entries": []}


def test_a_deleted_image_file_does_not_crash_the_page(web_client, competition, partners_page):
    """Sprawdzone na stronie **innej** niż główna: ``/`` ma też własny pas partnerów
    (``partner-strip``, poza zakresem tej zmiany), który dotknąłby tego samego pliku niezależnie
    od slidera – tu przedmiotem jest wyłącznie odporność procesora kontekstu."""
    image = make_image("Partner A")
    image.file.storage.delete(image.file.name)
    set_partners(partners_page, [partner("Partner A", logo=image)])

    response = web_client.get("/zadania/")

    assert response.status_code == 200
    assert "data-sponsor-slider" not in response.content.decode()


# --- schemat adresu (Critic review PR #9) --------------------------------------------------------


def test_a_javascript_partner_url_written_directly_to_block_data_is_ignored(competition, partners_page):
    """Ominięcie walidacji formularza: adres wpisany wprost do danych bloku po stronie Pythona."""
    set_partners(
        partners_page,
        [partner("Zła strona", logo=make_image("Zła strona"), url="javascript:alert(1)")],
    )

    payload = build_payload(competition)

    assert payload["entries"][0]["url"] == ""


def test_a_javascript_organizer_url_written_directly_to_the_row_is_ignored(competition):
    row = settings_for(competition)
    row.organizer_logo = make_image("Organizator")
    row.save()
    SiteSettings.objects.filter(pk=row.pk).update(contact_url="javascript:alert(1)")

    payload = build_payload(competition)

    assert payload["entries"][0]["url"] == ""


# --- unieważnianie pamięci podręcznej: skasowanie i przeniesienie strony, obraz w bibliotece ------
# (Critic review PR #9, high 2)


def _seed_sentinel(competition) -> None:
    """Wpisuje wartownika wprost do pamięci podręcznej – test wykrywa sam fakt jego zniknięcia,
    a nie przypadkową zgodność danych sprzed i po zdarzeniu."""
    django_cache.set(cache_key(competition), {"seconds": 999, "entries": ["wartownik"]}, 300)


def test_deleting_the_partners_page_invalidates_the_cache(competition, partners_page):
    set_partners(partners_page, [partner("Jedyny", logo=make_image("Jedyny"))])
    cached_payload(competition)

    PartnersPage.objects.get(pk=partners_page.pk).delete()

    assert cached_payload(competition) == {"seconds": 0, "entries": []}


def test_moving_the_partners_page_invalidates_the_cache(competition, partners_page, home):
    set_partners(partners_page, [partner("Jedyny", logo=make_image("Jedyny"))])
    cached_payload(competition)
    _seed_sentinel(competition)

    other_branch = home.add_child(instance=ContentPage(title="Inna gałąź", slug="inna-galaz"))
    PartnersPage.objects.get(pk=partners_page.pk).move(other_branch, pos="last-child")

    assert cached_payload(competition) != {"seconds": 999, "entries": ["wartownik"]}


def test_saving_an_image_invalidates_the_cache(competition, partners_page):
    image = make_image("Partner A")
    set_partners(partners_page, [partner("Partner A", logo=image)])
    cached_payload(competition)
    _seed_sentinel(competition)

    image.title = "Zmieniona nazwa"
    image.save()

    assert cached_payload(competition) != {"seconds": 999, "entries": ["wartownik"]}


def test_deleting_an_image_invalidates_the_cache(competition, partners_page):
    image = make_image("Partner A")
    set_partners(partners_page, [partner("Partner A", logo=image)])
    cached_payload(competition)
    _seed_sentinel(competition)

    image.delete()

    assert cached_payload(competition) != {"seconds": 999, "entries": ["wartownik"]}


# --- odbudowa poza żądaniem czytelnika (Critic review PR #9, medium 7) ---------------------------


def test_saving_settings_warms_the_cache_for_the_next_visitor(
    competition, partners_page, django_assert_max_num_queries, monkeypatch
):
    """Po zapisie ustawień pierwszy kolejny odczyt (gość na stronie głównej) nie płaci niczym:
    ani zapytaniem, ani wywołaniem ``get_rendition`` – rendition powstał już w tym żądaniu."""
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])
    row = settings_for(competition)

    Image = get_image_model()
    original = Image.get_rendition
    calls: list[int] = []

    def counting(self, *args, **kwargs):
        calls.append(self.pk)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Image, "get_rendition", counting)

    row.organizer_logo = make_image("Organizator")
    row.save()  # -> sygnał: reset_cache() + _warm() przelicza i zapisuje ładunek od razu

    calls.clear()
    with django_assert_max_num_queries(0):
        payload = cached_payload(competition)

    assert calls == [], "odczyt po zapisie nie może wołać get_rendition drugi raz"
    assert [entry["name"] for entry in payload["entries"]] == ["Fundacja Quantum AI", "Partner A"]


def test_publishing_partners_warms_the_cache_for_the_next_visitor(
    competition, partners_page, django_assert_max_num_queries
):
    set_partners(partners_page, [partner("Partner A", logo=make_image("Partner A"))])

    with django_assert_max_num_queries(0):
        payload = cached_payload(competition)

    assert [entry["name"] for entry in payload["entries"]] == ["Partner A"]
