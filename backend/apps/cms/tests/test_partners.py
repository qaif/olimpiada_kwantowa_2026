"""Logotypy partnerów i organizatora: ``seed_partners``.

Testy pilnują czterech rzeczy, na których ta komenda stoi:

- **normalizacja pliku.** Logotyp Wydziału Fizyki UW przyszedł w przestrzeni **CMYK** – w tej
  postaci przeglądarka rysuje go z przekłamanymi barwami albo wcale, a Wagtail nie zrobi z niego
  miniatury PNG. Test otwiera plik z biblioteki i sprawdza tryb, bo to jedyny sposób, żeby cicha
  zmiana w ``apps.cms.images`` nie przeszła niezauważona,
- **idempotencja.** Komenda ma być bezpieczna przy każdym wdrożeniu: drugi przebieg nie tworzy ani
  jednego obrazu i nie dokłada drugiego wpisu o tej samej nazwie,
- **rozdział ról obu komend importujących.** ``seed_legacy_content`` uruchomione **po**
  ``seed_partners`` nie może wyzerować listy partnerów – wcześniej robiło dokładnie to, więc
  kolejność komend w skrypcie wdrożeniowym decydowała o tym, czy strona ma partnerów,
- **co widzi czytelnik.** Sześć logotypów na ``/partnerzy/`` i sześć w pasie na stronie głównej,
  każdy z ``alt`` niosącym nazwę instytucji (w pasie logotyp jest jedynym nośnikiem nazwy).
"""

import json
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from PIL import Image as PILImage
from wagtail.images import get_image_model

from apps.cms.images import MAX_WIDTH, PARTNERS_DIR, normalize
from apps.cms.models import HomePage, PartnersPage, SiteSettings

pytestmark = pytest.mark.django_db

MANIFEST = json.loads((PARTNERS_DIR / "partners.json").read_text(encoding="utf-8"))
PARTNER_NAMES = [entry["name"] for entry in MANIFEST["partners"]]
ORGANIZER_TITLE = MANIFEST["organizer"]["title"]

#: Plik, który organizator przekazał w przestrzeni CMYK – patrz docstring modułu.
CMYK_SOURCE = "fuw.jpg"
CMYK_PARTNER = "Wydział Fizyki Uniwersytetu Warszawskiego"


@pytest.fixture
def partners():
    call_command("seed_legacy_content", verbosity=0)
    call_command("seed_partners", verbosity=0)


# --- wgrywanie i normalizacja -------------------------------------------------------------------


def test_seed_creates_every_partner_with_a_logo(partners):
    page = PartnersPage.objects.get(slug="partnerzy")
    entries = [block.value for block in page.partners]

    assert [entry["name"] for entry in entries] == PARTNER_NAMES
    for entry in entries:
        assert entry["logo"] is not None, entry["name"]
        assert entry["logo"].title == entry["name"]
    assert page.partners_empty() is False


def test_seed_sets_the_organizer_logo_in_site_settings(partners):
    settings = SiteSettings.for_site(HomePage.objects.get().get_site())

    assert settings.organizer_logo is not None
    assert settings.organizer_logo.title == ORGANIZER_TITLE
    # Logotyp organizatora nie jest partnerem – nie może stać w liście na ``/partnerzy/``.
    assert ORGANIZER_TITLE not in [block.value["name"] for block in PartnersPage.objects.get().partners]


def test_seed_converts_the_cmyk_logo_to_rgb(partners):
    """Plik źródłowy jest w CMYK; w bibliotece ma leżeć obraz, który przeglądarka narysuje."""
    with PILImage.open(PARTNERS_DIR / CMYK_SOURCE) as original:
        assert original.mode == "CMYK", "plik źródłowy przestał być testem konwersji"

    image = get_image_model().objects.get(title=CMYK_PARTNER)
    with image.file.open("rb") as handle, PILImage.open(handle) as stored:
        assert stored.mode in {"RGB", "RGBA"}


def test_seed_scales_oversized_logos_down(partners):
    """Żaden oryginał w bibliotece nie jest szerszy niż ``MAX_WIDTH`` (PCSS przyszedł 8082 px)."""
    images = get_image_model().objects.filter(title__in=[*PARTNER_NAMES, ORGANIZER_TITLE])

    assert images.count() == len(PARTNER_NAMES) + 1
    for image in images:
        assert 0 < image.width <= MAX_WIDTH
        assert image.file_size > 0
        assert image.file_hash


def test_seed_is_idempotent(partners):
    """Drugi przebieg: zero nowych obrazów, zero nowych wpisów, ta sama treść plików."""
    Image = get_image_model()
    before = {image.pk: (image.file.name, image.file_hash) for image in Image.objects.all()}

    call_command("seed_partners", verbosity=0)

    assert {image.pk: (image.file.name, image.file_hash) for image in Image.objects.all()} == before
    assert [block.value["name"] for block in PartnersPage.objects.get().partners] == PARTNER_NAMES


def test_seed_restores_a_logo_whose_content_changed(partners):
    """Inna treść pod tym samym tytułem wraca do wersji z repozytorium – w **tym samym** rekordzie.

    Identyfikator obrazu wisi w adresach renditions na już opublikowanych stronach, więc podmiana
    zawartości nie może oznaczać nowego rekordu.
    """
    Image = get_image_model()
    image = Image.objects.get(title=CMYK_PARTNER)
    pk = image.pk
    wanted, *_ = normalize(PARTNERS_DIR / CMYK_SOURCE)

    # Podmieniamy zawartość w storage, a nie przez ORM: komenda porównuje **bajty pliku**, więc
    # to jest ten stan, który ma ją obudzić (przeniesiona baza, plik nadpisany poza aplikacją).
    storage, name = image.file.storage, image.file.name
    storage.delete(name)
    buffer = BytesIO()
    PILImage.new("RGB", (10, 10), (1, 2, 3)).save(buffer, format="JPEG")
    storage.save(name, ContentFile(buffer.getvalue()))

    call_command("seed_partners", verbosity=0)

    image.refresh_from_db()
    assert image.pk == pk
    with image.file.open("rb") as handle:
        assert handle.read() == wanted
    assert Image.objects.filter(title=CMYK_PARTNER).count() == 1


# --- współpraca z importem treści ---------------------------------------------------------------


def test_legacy_seed_does_not_wipe_partners(partners):
    """``seed_legacy_content`` po ``seed_partners`` zostawia listę partnerów nietkniętą."""
    call_command("seed_legacy_content", verbosity=0)

    page = PartnersPage.objects.get(slug="partnerzy")
    assert [block.value["name"] for block in page.partners] == PARTNER_NAMES
    assert page.live is True


def test_editorial_changes_survive_a_second_seed(partners):
    """Poziom i opis należą do redakcji: manifest ustawia je tylko przy zakładaniu wpisu."""
    page = PartnersPage.objects.get(slug="partnerzy")
    page.partners = [
        (
            "partner",
            {**dict(block.value), "level": "patron-honorowy", "description": "Opis redakcji."}
            if block.value["name"] == CMYK_PARTNER
            else dict(block.value),
        )
        for block in page.partners
    ]
    page.save()
    page.save_revision().publish()

    call_command("seed_partners", verbosity=0)

    entry = next(
        block.value for block in PartnersPage.objects.get().partners if block.value["name"] == CMYK_PARTNER
    )
    assert entry["level"] == "patron-honorowy"
    assert entry["description"] == "Opis redakcji."
    # Logotyp i adres pochodzą z manifestu i wracają przy każdym przebiegu.
    assert entry["url"] == "https://www.fuw.edu.pl/"


def test_seed_requires_the_partners_page(home_page):
    """Bez strony ``/partnerzy/`` komenda ma stanąć z komunikatem, a nie utworzyć strony po cichu."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="seed_legacy_content"):
        call_command("seed_partners", verbosity=0)


# --- co widzi czytelnik -------------------------------------------------------------------------


def test_partners_page_shows_every_logo_with_alt(web_client, partners):
    content = web_client.get("/partnerzy/").content.decode()

    assert content.count('class="partner-card__logo"') == len(PARTNER_NAMES)
    for name in PARTNER_NAMES:
        assert f'alt="{name}"' in content
    for entry in MANIFEST["partners"]:
        if entry["url"]:
            assert f'href="{entry["url"]}" target="_blank" rel="noopener noreferrer"' in content


def test_home_strip_shows_every_logo(web_client, partners):
    response = web_client.get("/")
    content = response.content.decode()

    assert response.context["partners_page"] is not None
    assert content.count('class="partner-strip__logo"') == len(PARTNER_NAMES)
    for name in PARTNER_NAMES:
        assert f'alt="{name}"' in content
    # Pas stoi w dolnej połowie strony – logotypy dociągają się dopiero przy przewinięciu.
    assert 'loading="lazy"' in content


def test_footer_shows_the_organizer_logo(web_client, partners):
    content = web_client.get("/").content.decode()

    assert 'class="footer__logo"' in content
    # ``alt`` jest pusty: nazwa organizatora stoi obok jako tekst, więc powtórzenie byłoby szumem.
    assert "Organizator: <strong>Fundacja Quantum AI</strong>" in content
