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
- **co widzi czytelnik.** Logotyp z ``alt`` niosącym nazwę instytucji na ``/partnerzy/`` i w pasie
  na stronie głównej, nazwa jako tekst pod znakiem, odnośnik obejmujący jedno i drugie,
- **wpisy bez pliku w repozytorium.** Część partnerów organizator dopisał wprost w ``/cms/``;
  manifest zna ich z nazwy, a ich znaki leżą wyłącznie w bibliotece Wagtaila. Komenda nie może
  ani wymyślić im obrazu, ani skasować tego, który już wisi.
"""

import json
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from PIL import Image as PILImage
from wagtail.images import get_image_model

from apps.cms.blocks import WIDE_LOGO_RATIO
from apps.cms.images import MAX_WIDTH, PARTNERS_DIR, normalize
from apps.cms.models import HomePage, PartnersPage, SiteSettings

pytestmark = pytest.mark.django_db

MANIFEST = json.loads((PARTNERS_DIR / "partners.json").read_text(encoding="utf-8"))
PARTNER_NAMES = [entry["name"] for entry in MANIFEST["partners"]]
ORGANIZER_TITLE = MANIFEST["organizer"]["title"]

#: Partnerzy, których plik logotypu **jest** w repozytorium. Reszta to wpisy dopisane przez
#: organizatora w ``/cms/``: manifest zna ich z nazwy, a znak leży wyłącznie w bibliotece Wagtaila,
#: więc świeża instalacja pokazuje tam kółko z inicjałami.
WITH_LOGO = [entry["name"] for entry in MANIFEST["partners"] if entry.get("file")]
WITHOUT_LOGO = [entry["name"] for entry in MANIFEST["partners"] if not entry.get("file")]

#: Plik, który organizator przekazał w przestrzeni CMYK – patrz docstring modułu.
CMYK_SOURCE = "fuw.jpg"
CMYK_PARTNER = "Wydział Fizyki Uniwersytetu Warszawskiego"


@pytest.fixture
def partners():
    call_command("seed_legacy_content", verbosity=0)
    call_command("seed_partners", verbosity=0)


# --- wgrywanie i normalizacja -------------------------------------------------------------------


def test_seed_creates_every_partner_from_the_manifest(partners):
    """Każdy wpis manifestu ląduje na stronie; logotyp dostają te, których plik jest w repozytorium.

    Partnerzy dopisani przez organizatora w ``/cms/`` są w manifeście z samej nazwy – ich znaki
    leżą w bibliotece Wagtaila, a nie w repozytorium. Na świeżej instalacji pokazują kółko
    z inicjałami; komenda **nie** wymyśla dla nich obrazu i nie kasuje istniejącego.
    """
    page = PartnersPage.objects.get(slug="partnerzy")
    entries = [block.value for block in page.partners]

    assert [entry["name"] for entry in entries] == PARTNER_NAMES
    for entry in entries:
        if entry["name"] in WITH_LOGO:
            assert entry["logo"] is not None, entry["name"]
            assert entry["logo"].title == entry["name"]
        else:
            assert entry["logo"] is None, entry["name"]
            # Bez znaku karta pokazuje inicjały – pusty kadr wyglądałby na błąd wczytywania.
            assert entry.initials
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
    images = get_image_model().objects.filter(title__in=[*WITH_LOGO, ORGANIZER_TITLE])

    assert images.count() == len(WITH_LOGO) + 1
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


def test_a_manifest_entry_without_a_file_never_clears_a_logo_uploaded_in_cms(partners):
    """Najważniejsza asercja tej zmiany: przebieg komendy nie może skasować znaku z produkcji.

    Partnerzy dopisani przez organizatora mają logotyp wgrany w ``/cms/`` i nie mamy pliku
    źródłowego. Gdyby manifest „bez pliku” znaczył „bez logotypu”, jeden przebieg na produkcji
    zdjąłby obraz i odnośnik, których nie da się odtworzyć z repozytorium. To samo dotyczy adresu:
    brak klucza ``url`` znaczy „manifest nie wie”, a nie „adresu nie ma”.
    """
    assert WITHOUT_LOGO, "manifest przestał zawierać wpis bez pliku – test stracił przedmiot"
    name = WITHOUT_LOGO[0]
    page = PartnersPage.objects.get(slug="partnerzy")
    borrowed = get_image_model().objects.get(title=CMYK_PARTNER)

    # Udajemy stan produkcji: redakcja wgrała znak i wpisała adres wprost w panelu.
    page.partners = [
        (
            "partner",
            {**dict(block.value), "logo": borrowed, "url": "https://przyklad.test/"}
            if block.value["name"] == name
            else dict(block.value),
        )
        for block in page.partners
    ]
    page.save()
    page.save_revision().publish()

    call_command("seed_partners", verbosity=0)

    entry = next(block.value for block in PartnersPage.objects.get().partners if block.value["name"] == name)
    assert entry["logo"] == borrowed
    assert entry["url"] == "https://przyklad.test/"


def test_seed_requires_the_partners_page(home_page):
    """Bez strony ``/partnerzy/`` komenda ma stanąć z komunikatem, a nie utworzyć strony po cichu."""
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="seed_legacy_content"):
        call_command("seed_partners", verbosity=0)


# --- co widzi czytelnik -------------------------------------------------------------------------


def test_partners_page_shows_every_logo_with_alt(web_client, partners):
    content = web_client.get("/partnerzy/").content.decode()

    assert content.count('class="partner-card__logo"') == len(WITH_LOGO)
    for name in WITH_LOGO:
        assert f'alt="{name}"' in content
    # Partner bez pliku w repozytorium ma na świeżej instalacji inicjały, a nie pusty kadr.
    assert content.count('class="partner-card__initials"') == len(WITHOUT_LOGO)
    for entry in MANIFEST["partners"]:
        if entry.get("url"):
            assert f'href="{entry["url"]}" target="_blank" rel="noopener noreferrer"' in content


def test_home_strip_shows_every_logo_with_the_name_underneath(web_client, partners):
    """Uwaga organizatora z 16.09: „napis Wydziału Informatyki i Telekomunikacji PWr nieczytelny”.

    Nazwa instytucji stoi pod znakiem **jako tekst**. Wcześniej logotyp był w pasie jej jedynym
    nośnikiem, więc kto nie odczytał znaku – a właśnie o to szło w zgłoszeniu – nie miał jak się
    dowiedzieć, czyj on jest. Samo powiększenie kadru (72 → 96 px) tego nie załatwia: napis wpisany
    w plik bywa drobny niezależnie od tego, jak duży jest znak.
    """
    response = web_client.get("/")
    content = response.content.decode()

    assert response.context["partners_page"] is not None
    assert content.count('class="partner-strip__logo"') == len(WITH_LOGO)
    # Podpis dostaje **każdy** kafel, także ten bez znaku – pas ma być czytelny bez logotypów.
    assert content.count('class="partner-strip__name"') == len(PARTNER_NAMES)
    strip = content.split('class="partner-strip"', 1)[1].split("</ul>", 1)[0]
    for name in PARTNER_NAMES:
        assert f'<span class="partner-strip__name">{name}</span>' in strip
    for name in WITH_LOGO:
        # ``alt`` niesie nazwę instytucji – organizator prosił, żeby zostawić go przy obrazie.
        assert f'alt="{name}"' in strip
    # Pas stoi w dolnej połowie strony – logotypy dociągają się dopiero przy przewinięciu.
    assert 'loading="lazy"' in content


def link_body(content: str, url: str) -> str:
    """Treść odnośnika o tym adresie: od atrybutu ``href`` do najbliższego ``</a>``.

    Działa, dopóki odnośniki się nie zagnieżdżają – a zagnieżdżać się nie mogą (HTML tego zabrania
    i asercja niżej tego pilnuje), więc pierwszy ``</a>`` po ``href`` zamyka właśnie ten.
    """
    assert f'href="{url}"' in content, f"brak odnośnika {url}"
    return content.split(f'href="{url}"', 1)[1].split("</a>", 1)[0]


def test_the_partner_link_wraps_the_logo_and_the_name(web_client, partners):
    """Uwaga organizatora: „linki powinny obejmować obrazek, nie tylko nazwę”.

    Znak instytucji jest tym, w co człowiek celuje myszą, a wcześniej był jedynym elementem karty,
    który nie prowadził donikąd. Jeden ``<a>`` na partnera, nie dwa pod tym samym adresem: dwa
    dawałyby nawigacji klawiaturą i czytnikowi ekranu dwa przystanki w to samo miejsce.
    """
    cards = web_client.get("/partnerzy/").content.decode()
    strip = web_client.get("/").content.decode()

    for entry in MANIFEST["partners"]:
        if not entry.get("url"):
            continue
        for page, content in (("/partnerzy/", cards), ("/", strip)):
            body = link_body(content, entry["url"])
            assert "<img" in body, f"{page}: logotyp {entry['name']} stoi poza odnośnikiem"
            assert entry["name"] in body, f"{page}: nazwa {entry['name']} stoi poza odnośnikiem"
            # Zagnieżdżony odnośnik jest niedozwolony w HTML-u i dawałby dwa przystanki.
            assert "<a " not in body, f"{page}: odnośnik w odnośniku przy {entry['name']}"
            # Adres zewnętrzny otwiera się w nowej karcie i bez dostępu do ``window.opener``.
            assert body.startswith(' target="_blank" rel="noopener noreferrer"'), page


def test_a_partner_without_a_url_has_no_link_at_all(web_client, partners):
    """Wpis bez adresu zostaje kartą, a nie odnośnikiem donikąd (w manifeście jest taki jeden)."""
    no_url = [entry["name"] for entry in MANIFEST["partners"] if not entry.get("url")]
    assert no_url, "manifest przestał zawierać partnera bez adresu – test stracił przedmiot"

    cards = web_client.get("/partnerzy/").content.decode()

    for name in no_url:
        card = cards.split(f'<h3 class="partner-card__name">{name}</h3>', 1)[0]
        assert not card.rsplit("<li", 1)[1].count("<a ")


def test_a_wide_logo_takes_two_columns_of_the_grid(web_client, partners):
    """Druga połowa odpowiedzi: znak szerszy niż sam kadr dostaje dwa razy więcej szerokości.

    Kiedy proporcja znaku przekracza proporcję kadru, ogranicznikiem staje się szerokość i
    wysokość zostaje niewykorzystana – podwojenie szerokości jest wtedy jedyną drogą, która nie
    przycina i nie rozciąga obrazu. O tym, który logotyp jest pasem, rozstrzyga **serwer**:
    przeglądarka nie zna proporcji pliku, zanim go nie pobierze, a przy ``loading="lazy"`` układ
    musi stać wcześniej.
    """
    page = PartnersPage.objects.get(slug="partnerzy")
    wide = [block.value["name"] for block in page.partners if block.value.is_wide]

    # Pas PCSS-u ma w plikach organizatora proporcję 7,7, znak AIQLAB-u 3,3 – oba są „napisami”
    # szerszymi od kadru. Reszta (godła 0,8–1,1, Wydział Fizyki UW 2,5) wypełnia kadr w pionie.
    assert wide == ["Poznańskie Centrum Superkomputerowo-Sieciowe", "AIQLAB Institute"]

    content = web_client.get("/").content.decode()
    strip = content.split('class="partner-strip"', 1)[1].split("</ul>", 1)[0]
    assert strip.count("partner-strip__item--wide") == len(wide)

    cards = web_client.get("/partnerzy/").content.decode()
    assert cards.count('class="partner--wide"') == len(wide)


def test_a_logo_that_already_fills_the_frame_is_not_widened(partners):
    """Logotyp Wydziału Fizyki UW ma 2,5 i wypełnia kadr na pełną wysokość.

    To jest sedno progu: dwie kolumny nie powiększyłyby go ani o piksel, a zostawiłyby w siatce
    kafel w połowie pusty. „Szeroki na oko” i „ograniczony szerokością” to dwie różne rzeczy.
    """
    page = PartnersPage.objects.get(slug="partnerzy")
    entries = {block.value["name"]: block.value for block in page.partners}

    logo = entries["Wydział Fizyki Uniwersytetu Warszawskiego"]["logo"]
    assert 2 < logo.width / logo.height < WIDE_LOGO_RATIO
    assert entries["Wydział Fizyki Uniwersytetu Warszawskiego"].is_wide is False


def test_partners_page_does_not_leak_a_template_comment(web_client, partners):
    """``{# … #}`` jest w Django **jednowierszowe** – wielowierszowe wypisuje się jako tekst.

    Ten sam błąd zdarzył się już na ``/wyniki/`` (patrz ``apps/web/tests/test_chrome_feedback.py``)
    i zdarzył się ponownie przy tej zmianie, więc karta partnera dostaje własnego strażnika.
    """
    content = web_client.get("/partnerzy/").content.decode()

    # Słowa, które występują **wyłącznie** w komentarzach tego szablonu.
    assert "Rendition" not in content
    assert "gęstości" not in content
    assert "kadru" not in content


def test_a_square_logo_stays_in_one_column(partners):
    """Godło bliskie kwadratu wypełnia kafel w pionie i dwóch kolumn nie potrzebuje."""
    page = PartnersPage.objects.get(slug="partnerzy")
    square = next(block.value for block in page.partners if block.value["name"] == "Instytut Fizyki PAN")

    assert square.is_wide is False


def test_a_partner_without_a_logo_is_never_wide(partners):
    """Karta bez pliku pokazuje inicjały – a te są kwadratem i nie mają czego rozciągać."""
    page = PartnersPage.objects.get(slug="partnerzy")
    page.partners = [("partner", {"name": "Bez znaku", "level": "partner-naukowy"})]
    page.save()

    assert page.partners[0].value.is_wide is False


def test_footer_shows_the_organizer_logo(web_client, partners):
    content = web_client.get("/").content.decode()

    assert 'class="footer__logo"' in content
    # ``alt`` jest pusty: nazwa organizatora stoi obok jako tekst, więc powtórzenie byłoby szumem.
    assert "Organizator: <strong>Fundacja Quantum AI</strong>" in content
