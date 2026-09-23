"""Strona „Plakaty do pobrania” (``/plakaty/``), pobranie i odnośniki do niej.

Czego pilnują te testy:

- **lista** pokazuje wyłącznie opublikowane plakaty konkursu z żądania; bez nich – 404,
- **pobranie** oddaje plik jako załącznik, zapisuje zdarzenie (bez adresu IP) i nigdy nie trafia
  do pamięci stron; ``HEAD`` i roboty nie są liczone,
- **izolacja**: plakatu cudzego konkursu nie widać ani na liście, ani pod adresem pobrania,
- **odnośnik** w stopce i w panelu opiekuna pojawia się i znika razem z plakatami – bez zapytania
  na odsłonę (pamięć podręczna) i bez nieświeżej strony w pamięci stron publicznych.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor
from apps.accounts.tests.factories import UserFactory
from apps.promo import availability
from apps.promo.models import PromoDownload
from apps.promo.tests.helpers import PDF_BYTES, make_material
from apps.promo.tracking import ip_hash

pytestmark = pytest.mark.django_db

BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
BOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


@pytest.fixture
def browser(client_for, competition):
    return client_for(competition, HTTP_USER_AGENT=BROWSER, REMOTE_ADDR="203.0.113.10")


def download_url(material) -> str:
    return f"/plakaty/{material.pk}/pobierz/"


# --- lista -------------------------------------------------------------------------------------------


def test_list_is_404_without_published_materials(browser, competition):
    make_material(competition, published=False)

    assert browser.get("/plakaty/").status_code == 404


def test_list_shows_published_materials_in_order(browser, competition):
    make_material(competition, title="Drugi", position=2)
    make_material(competition, title="Pierwszy", position=1)
    make_material(competition, title="Szkic", published=False)

    response = browser.get("/plakaty/")
    content = response.content.decode()

    assert response.status_code == 200
    assert content.index("Pierwszy") < content.index("Drugi")
    assert "Szkic" not in content
    assert "Pobierz" in content
    assert "PDF" in content


def test_archived_material_is_not_listed(browser, competition):
    from django.utils import timezone

    make_material(competition, title="Stary", archived_at=timezone.now())

    assert browser.get("/plakaty/").status_code == 404


def test_list_shows_preview_or_the_document_icon(browser, competition):
    make_material(competition, title="Bez podglądu")

    content = browser.get("/plakaty/").content.decode()

    assert 'class="poster-card__icon"' in content


# --- pobranie ----------------------------------------------------------------------------------------


def test_download_serves_an_attachment_and_records_the_event(browser, competition):
    material = make_material(competition, title="Plakat A4 pionowy")

    response = browser.get(download_url(material))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'attachment; filename="plakat-a4-pionowy.pdf"'
    assert "no-store" in response["Cache-Control"]
    event = PromoDownload.objects.get()
    assert event.material == material
    assert event.competition == competition
    assert event.ip_hash == ip_hash("203.0.113.10")


def test_head_is_answered_but_not_counted(browser, competition):
    material = make_material(competition)

    response = browser.head(download_url(material))

    assert response.status_code == 200
    assert response["Content-Length"] == str(len(PDF_BYTES))
    assert "attachment" in response["Content-Disposition"]
    assert PromoDownload.objects.count() == 0


def test_head_is_404_when_the_file_is_missing_in_storage(browser, competition):
    """Ta sama odpowiedź co ``GET`` – ``HEAD`` nie może obiecywać pliku, którego nie ma."""
    material = make_material(competition)
    material.file.storage.delete(material.file.name)

    assert browser.head(download_url(material)).status_code == 404
    assert browser.get(download_url(material)).status_code == 404


def test_bot_gets_the_file_but_is_not_counted(client_for, competition):
    material = make_material(competition)

    response = client_for(competition, HTTP_USER_AGENT=BOT).get(download_url(material))

    assert response.status_code == 200
    assert PromoDownload.objects.count() == 0


def test_draft_cannot_be_downloaded(browser, competition):
    material = make_material(competition, published=False)

    assert browser.get(download_url(material)).status_code == 404
    assert PromoDownload.objects.count() == 0


def test_missing_file_in_storage_is_404_and_not_counted(browser, competition):
    material = make_material(competition)
    material.file.storage.delete(material.file.name)

    assert browser.get(download_url(material)).status_code == 404
    assert PromoDownload.objects.count() == 0


# --- izolacja konkursów -----------------------------------------------------------------------------


def test_other_competition_cannot_see_or_download(client_for, competition, other_competition):
    theirs = make_material(other_competition, title="Plakat sąsiada")
    client = client_for(competition, HTTP_USER_AGENT=BROWSER)

    assert client.get("/plakaty/").status_code == 404
    assert client.get(download_url(theirs)).status_code == 404
    assert PromoDownload.objects.count() == 0

    mine = make_material(competition, title="Nasz plakat")
    content = client.get("/plakaty/").content.decode()
    assert "Nasz plakat" in content
    assert "Plakat sąsiada" not in content
    # A pod własną domeną sąsiad widzi swój plakat, nie nasz.
    other = client_for(other_competition, HTTP_USER_AGENT=BROWSER)
    assert other.get(download_url(mine)).status_code == 404
    assert other.get(download_url(theirs)).status_code == 200


# --- odnośniki: stopka i panel opiekuna ---------------------------------------------------------


def test_footer_link_follows_published_materials(browser, competition):
    assert 'href="/plakaty/"' not in browser.get("/").content.decode()

    material = make_material(competition)
    assert 'href="/plakaty/"' in browser.get("/").content.decode()

    material.is_published = False
    material.save()
    assert 'href="/plakaty/"' not in browser.get("/").content.decode()


def test_footer_flag_is_cached_between_requests(browser, competition, django_assert_num_queries):
    make_material(competition)
    assert availability.has_public_materials(competition) is True

    with django_assert_num_queries(0):
        assert availability.has_public_materials(competition) is True


def test_warm_page_does_not_ask_about_posters(browser, competition):
    """Budżet zapytań (``test_invariants.QUERY_BUDGET``) liczy stopkę na zimno; na ciepło – zero."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    make_material(competition)
    browser.get("/")

    with CaptureQueriesContext(connection) as captured:
        assert 'href="/plakaty/"' in browser.get("/").content.decode()

    assert not [query for query in captured.captured_queries if "promo_" in query["sql"]]


def test_footer_flag_cache_is_per_competition(competition, other_competition):
    make_material(other_competition)

    assert availability.has_public_materials(competition) is False
    assert availability.has_public_materials(other_competition) is True
    assert availability.has_public_materials(None) is False


def test_supervisor_dashboard_links_to_posters(client_for, competition):
    user = UserFactory(email="nauczyciel@szkola.test")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=competition)
    client = client_for(competition)
    client.force_login(user)

    assert "Plakaty do powieszenia w szkole" not in client.get("/supervisor/").content.decode()

    make_material(competition)
    assert "Plakaty do powieszenia w szkole" in client.get("/supervisor/").content.decode()


# --- pamięć stron publicznych ----------------------------------------------------------------------


def test_list_is_cached_and_invalidated_when_a_material_changes(browser, competition, settings):
    settings.PAGE_CACHE_ENABLED = True
    material = make_material(competition, title="Wersja pierwsza")

    assert browser.get("/plakaty/")["X-Page-Cache"] == "MISS"
    assert browser.get("/plakaty/")["X-Page-Cache"] == "HIT"

    material.title = "Wersja druga"
    material.save()
    response = browser.get("/plakaty/")

    assert response["X-Page-Cache"] == "MISS"
    assert "Wersja druga" in response.content.decode()


def test_home_page_in_cache_gets_the_footer_link_after_publication(browser, competition, settings):
    settings.PAGE_CACHE_ENABLED = True
    browser.get("/")
    assert browser.get("/")["X-Page-Cache"] == "HIT"

    make_material(competition)
    response = browser.get("/")

    assert response["X-Page-Cache"] == "MISS"
    assert 'href="/plakaty/"' in response.content.decode()


def test_download_is_never_cached(browser, competition, settings):
    settings.PAGE_CACHE_ENABLED = True
    material = make_material(competition)

    first = browser.get(download_url(material))
    second = browser.get(download_url(material))

    assert first["X-Page-Cache"] == "BYPASS"
    assert second["X-Page-Cache"] == "BYPASS"
    # Oba żądania dostały plik (nie trafienie w pamięć stron); drugie w oknie podwójnego
    # kliknięcia nie jest drugim pobraniem – patrz ``apps.promo.tracking.DEBOUNCE_SECONDS``.
    assert first.status_code == second.status_code == 200
    assert PromoDownload.objects.count() == 1


# --- limit żądań i podwójne kliknięcie -------------------------------------------------------------


def _with_rate(settings, rate: str | None) -> None:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], "poster_download": rate}
    settings.REST_FRAMEWORK = config


def test_download_is_rate_limited_per_address_and_throttled_requests_are_not_recorded(
    client_for, competition, settings
):
    _with_rate(settings, "3/min")
    first = make_material(competition, title="A")
    second = make_material(competition, title="B")
    client = client_for(competition, HTTP_USER_AGENT=BROWSER, REMOTE_ADDR="203.0.113.20")

    statuses = [
        client.get(download_url(first)).status_code,
        client.get(download_url(second)).status_code,
        client.head(download_url(first)).status_code,
    ]
    throttled = client.get(download_url(second))

    assert statuses == [200, 200, 200]
    assert throttled.status_code == 429
    assert int(throttled["Retry-After"]) >= 1
    assert "no-store" in throttled["Cache-Control"]
    assert PromoDownload.objects.count() == 2
    # Inny adres ma własny kubełek.
    other = client_for(competition, HTTP_USER_AGENT=BROWSER, REMOTE_ADDR="203.0.113.21")
    assert other.get(download_url(second)).status_code == 200
    assert PromoDownload.objects.count() == 3


def test_scope_has_a_rate_in_base_settings():
    from config.settings import base as base_settings

    assert base_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["poster_download"] == "30/min"


def test_double_click_serves_the_file_twice_but_records_once(browser, competition):
    material = make_material(competition)

    first = browser.get(download_url(material))
    second = browser.get(download_url(material))

    assert b"".join(second.streaming_content) == PDF_BYTES
    assert first.status_code == second.status_code == 200
    assert PromoDownload.objects.count() == 1
