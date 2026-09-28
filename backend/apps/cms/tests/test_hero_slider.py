"""Slider w nagłówku strony głównej: plakaty redakcji, plansza z hasłem i aktualności.

Plakat „Rozpoczęliśmy rejestrację!” wstawia migracja ``cms.0029`` na stronę główną domyślnej
witryny – na produkcji ma się pokazać zaraz po wdrożeniu, bez wizyty redakcji w ``/cms/``.
"""

import pytest
from django.core.exceptions import ValidationError
from wagtail.blocks.struct_block import StructBlockValidationError

from apps.cms.blocks import PosterSlideBlock
from apps.cms.models import HomePage, NewsIndexPage, NewsPage

pytestmark = pytest.mark.django_db

POSTER = {
    "kicker": "Olimpiada Kwantowa",
    "title": "Rozpoczęliśmy rejestrację!",
    "text": "Załóż konto uczestnika.",
    "stamp": "zapisy otwarte",
    "button_label": "Zarejestruj się",
    "button_url": "/register/",
    "theme": "czerwony",
    "registration_only": False,
}


def home() -> HomePage:
    return HomePage.objects.get()


def test_migration_puts_the_registration_poster_on_the_home_page():
    slides = list(home().hero_slides)

    assert [slide.block_type for slide in slides] == ["poster"]
    assert slides[0].value["title"] == "Rozpoczęliśmy rejestrację!"
    assert slides[0].value["button_url"] == "/register/"
    assert slides[0].value["registration_only"] is True
    assert home().hero_show_news is True


def test_poster_is_the_first_slide_and_the_tagline_slide_stays(web_client):
    page = home()
    page.hero_slides = [("poster", POSTER)]
    page.save()

    content = web_client.get("/").content.decode()

    assert "data-hero-slider" in content
    assert content.index("Rozpoczęliśmy rejestrację!") < content.index("Przyszłość ma naturę kwantową.")
    assert 'class="hero-slide hero-poster hero-poster--czerwony"' in content
    assert 'href="/register/"' in content
    assert "zapisy otwarte" in content
    assert "js/hero-slider.js" in content
    # Kontrolki odsłania skrypt – bez niego byłyby atrapą.
    assert "data-hero-slider-controls hidden" in content


def test_latest_news_become_slides_and_can_be_switched_off(web_client):
    page = home()
    page.hero_slides = []
    page.save()
    index = page.add_child(instance=NewsIndexPage(title="Aktualności", slug="aktualnosci-test"))
    index.add_child(
        instance=NewsPage(
            title="Ruszyły warsztaty online", slug="warsztaty-ruszyly", lead="Pierwsze zajęcia."
        )
    )

    content = web_client.get("/").content.decode()
    assert 'aria-label="Aktualność: Ruszyły warsztaty online"' in content

    page = home()
    page.hero_show_news = False
    page.save()
    content = web_client.get("/").content.decode()
    assert "Aktualność: Ruszyły warsztaty online" not in content


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "//evil.example/",
        "data:text/html,x",
        "/\\evil.example",
        "/\t/evil.example",
        "http://x.pl",
    ],
)
def test_poster_button_rejects_non_http_addresses(url):
    block = PosterSlideBlock()

    with pytest.raises((StructBlockValidationError, ValidationError)):
        block.clean(block.to_python({**POSTER, "button_url": url}))


@pytest.mark.parametrize("url", ["/register/", "https://olimpiadakwantowa.pl/warsztaty/", ""])
def test_poster_button_accepts_site_and_https_addresses(url):
    block = PosterSlideBlock()

    assert block.clean(block.to_python({**POSTER, "button_url": url}))["button_url"] == url


def test_registration_poster_disappears_when_registration_is_not_open(web_client):
    """Plakat „tylko przy otwartej rejestracji” nie zapowiada formularza, który nie przyjmuje zgłoszeń."""
    page = home()
    page.hero_slides = [("poster", {**POSTER, "registration_only": True})]
    page.save()

    content = web_client.get("/").content.decode()

    assert "Rozpoczęliśmy rejestrację!" not in content
