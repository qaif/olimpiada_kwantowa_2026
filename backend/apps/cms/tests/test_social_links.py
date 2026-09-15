"""Profile organizatora w mediach społecznościowych: stopka i strona „Kontakt”.

Testy pilnują trzech rzeczy, na których ta funkcja stoi:

- **adresy są w stopce każdej strony.** Stopka jest w ``base.html``, więc regresja objawiłaby się
  wszędzie naraz albo nigdzie – sprawdzamy ją na zwykłej stronie serwisu, nie na „Kontakcie”,
- **te same adresy stoją na „Kontakcie”.** Strona ma listę redakcyjną z importu i wiersz ikon
  z ``SiteSettings``; obie postacie muszą wskazywać ten sam profil, bo inaczej czytelnik dostaje
  dwa różne adresy tej samej instytucji,
- **pusty adres znika, a reszta zostaje.** Wycofanie profilu ma być wpisem w ``/cms/``, a nie
  zmianą szablonu – to jedyna rzecz, która odróżnia te pola od adresu wpisanego w kodzie.
"""

import pytest
from django.core.management import call_command
from wagtail.models import Site

from apps.cms.models import SiteSettings

pytestmark = pytest.mark.django_db

#: Adresy z wartości początkowych pól (``cms.0014``). Ta sama czwórka stoi w ``kontakt.md``:
#: gdyby import i ustawienia się rozjechały, strona „Kontakt” pokazałaby dwa różne profile.
SOCIAL_URLS = (
    "https://www.facebook.com/olimpiadakwantowa",
    "https://linkedin.com/showcase/olimpiada-kwantowa",
    "https://www.instagram.com/olimpiadakwantowa",
    "https://x.com/olimpiadakwant",
    "https://www.tiktok.com/@olimpiadakwantowa",
    "https://www.youtube.com/@OlimpiadaKwantowa",
)


@pytest.fixture
def site_settings() -> SiteSettings:
    """Ustawienia domyślnej witryny – wiersz zakłada migracja ``cms.0006``."""
    return SiteSettings.for_site(Site.objects.get(is_default_site=True))


@pytest.fixture
def legacy_content():
    """Strona „Kontakt” powstaje z importu, a nie z fabryki – tak samo, jak na produkcji."""
    call_command("seed_legacy_content", verbosity=0)


def test_migration_defaults_fill_social_urls(site_settings):
    """Świeża baza ma komplet adresów bez wejścia redaktora do ``/cms/``.

    ``AddField`` z ``default`` uzupełnia istniejące wiersze przy ``migrate``, więc wiersz założony
    przez ``cms.0006`` (bez tych pól) dostaje adresy razem z kolumnami – produkcja nie potrzebuje
    osobnego kroku po wdrożeniu.
    """
    assert [link["url"] for link in site_settings.social_links] == list(SOCIAL_URLS)
    assert [link["label"] for link in site_settings.social_links] == [
        "Facebook",
        "LinkedIn",
        "Instagram",
        "X",
        "TikTok",
        "YouTube",
    ]


def test_footer_links_to_every_profile(web_client):
    """Stopka zwykłej strony serwisu ma wszystkie cztery profile."""
    content = web_client.get("/").content.decode()

    for url in SOCIAL_URLS:
        assert f'href="{url}"' in content
    # Odnośnik bez tekstu musi nieść nazwę serwisu inaczej – inaczej czytnik ekranu przeczyta „link”.
    assert 'aria-label="Facebook"' in content
    # Okno otwarte z ``target="_blank"`` nie może dostać uchwytu do naszej strony (``window.opener``).
    assert 'rel="noopener noreferrer"' in content


def test_contact_page_links_to_every_profile(web_client, legacy_content):
    """„Kontakt” pokazuje te same adresy – i w liście redakcyjnej, i w wierszu ikon."""
    content = web_client.get("/kontakt/").content.decode()

    assert "Media społecznościowe" in content
    for url in SOCIAL_URLS:
        # Dwa razy: raz z importu (``kontakt.md``), raz z ``SiteSettings`` – plus stopka.
        assert content.count(f'href="{url}"') >= 2


def test_blank_url_hides_only_its_own_icon(web_client, site_settings):
    """Skasowany adres zdejmuje jedną ikonę; pozostałe trzy zostają.

    To jest cały powód, dla którego adresy są polami, a nie literałami w szablonie: wycofanie
    profilu (albo jego zawieszenie) ma być wpisem w ``/cms/``, a nie wydaniem aplikacji.
    """
    site_settings.facebook_url = ""
    site_settings.save(update_fields=["facebook_url"])

    content = web_client.get("/").content.decode()

    assert SOCIAL_URLS[0] not in content
    assert 'aria-label="Facebook"' not in content
    for url in SOCIAL_URLS[1:]:
        assert f'href="{url}"' in content
