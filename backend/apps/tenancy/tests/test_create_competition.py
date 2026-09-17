"""``manage.py create_competition`` — zakłada komplet, odmawia duplikatu, nie zostawia połowy.

Komenda jest jedynym wejściem do zakładania konkursu, więc testy pilnują trzech rzeczy:

1. **kompletu.** Konkurs bez witryny nie ma drzewa stron, a witryna bez konkursu oddaje pod swoim
   adresem treść konkursu domyślnego — dlatego sprawdzamy wszystkie cztery wiersze naraz.
2. **odmowy.** Zajęty identyfikator albo zajęta domena to nie jest sytuacja do „dopisania obok”:
   druga witryna pod tym samym hostem znaczyłaby, że o treści pod adresem decyduje kolejność
   wierszy w bazie.
3. **braku śladu.** ``--dry-run`` i każda odmowa mają zostawić bazę dokładnie taką, jaka była.
"""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command
from wagtail.models import Page, Site

from apps.cms.models import HomePage, SiteSettings
from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.templates_catalog import DEFAULT_PAGES

pytestmark = pytest.mark.django_db

#: Szablon użyty tam, gdzie test nie sprawdza samego szablonu. ``pusty`` ma pustą listę
#: ``safe_seeds``, więc komenda nie woła przy okazji ``seed_schools`` (wykaz SIO/RSPO to kilka
#: tysięcy wierszy — w teście o zakładaniu konkursu byłby to czas zużyty na cudzą funkcję).
TEMPLATE = "pusty"


def create(**kwargs) -> None:
    options = {
        "slug": "fizyczna",
        "name": "Olimpiada Fizyczna",
        "domain": "olimpiadafizyczna.invalid",
        "from_template": TEMPLATE,
    }
    options.update(kwargs)
    call_command("create_competition", **options)


def counts() -> tuple[int, int, int]:
    return Competition.objects.count(), Site.objects.count(), Page.objects.count()


def test_creates_site_pages_settings_and_competition():
    create(organizer="Polskie Towarzystwo Fizyczne", contact_email="biuro@example.invalid")

    competition = Competition.objects.get(slug="fizyczna")
    assert competition.name == "Olimpiada Fizyczna"
    assert competition.organizer_name == "Polskie Towarzystwo Fizyczne"
    assert competition.routing_mode == RoutingMode.DOMAIN
    assert competition.primary_domain == "olimpiadafizyczna.invalid"
    # Prefiks tematu listu z szablonu, ze spacją na końcu — tak samo, jak dzisiejszy
    # ``EMAIL_SUBJECT_PREFIX`` instalacji.
    assert competition.email_subject_prefix == "[Olimpiada Fizyczna] "
    # Pusty nadawca znaczy „weź ``DEFAULT_FROM_EMAIL`` instalacji”: relay podpisuje DKIM-em jedną
    # domenę, więc adres w domenie organizatora trafiłby do spamu.
    assert competition.from_email == ""

    site = competition.site
    assert site.hostname == "olimpiadafizyczna.invalid"
    # Witryna domyślna zostaje **jedna** i jest nią Olimpiada Kwantowa — to ona odpowiada na
    # żądania z nieznanym hostem. Odebranie jej tej roli zmieniłoby działającą produkcję.
    assert site.is_default_site is False

    home = HomePage.objects.get(pk=site.root_page_id)
    assert home.title == "Olimpiada Fizyczna"
    assert list(home.get_children().order_by("path").values_list("slug", flat=True)) == [
        slug for _, slug, _ in DEFAULT_PAGES
    ]


def test_site_settings_do_not_inherit_kwantowa_defaults():
    """Nowy konkurs nie może dostać cudzego profilu na Facebooku ani cudzej daty startu zapisów.

    ``cms.SiteSettings`` ma wartości domyślne Olimpiady Kwantowej (znak w stopce, adresy profili,
    komunikat o rejestracji). Dla Konkursu #1 to jest wygoda; dla każdego kolejnego byłby to
    cudzy serwis pokazany pod jego nazwą w dniu, w którym powstał.
    """
    create()

    settings_row = SiteSettings.for_site(Competition.objects.get(slug="fizyczna").site)
    assert settings_row.site_name == "Olimpiada Fizyczna"
    assert settings_row.facebook_url == ""
    assert settings_row.registration_note == ""
    assert settings_row.tagline == ""
    assert settings_row.organizer_logo is None


def test_dry_run_changes_nothing():
    before = counts()

    create(dry_run=True)

    assert counts() == before
    assert not Competition.objects.filter(slug="fizyczna").exists()


def test_refuses_duplicate_slug():
    create()
    before = counts()

    with pytest.raises(CommandError, match="już istnieje"):
        create(domain="inna.invalid")

    assert counts() == before


def test_refuses_duplicate_domain():
    create()
    before = counts()

    with pytest.raises(CommandError, match="już istnieje"):
        create(slug="inny")

    assert counts() == before


def test_refuses_domain_of_existing_site_without_competition():
    """Witryna bez konkursu też zajmuje host — inaczej powstałyby dwie witryny na jeden adres."""
    before = counts()
    existing = Site.objects.first()

    with pytest.raises(CommandError, match="Witryna"):
        create(domain=existing.hostname)

    assert counts() == before


def test_skip_existing_is_quiet_and_idempotent():
    """``scripts/deploy.sh`` musi dać się uruchomić ponownie bez czerwonego wdrożenia."""
    create()
    before = counts()

    create(skip_existing=True)

    assert counts() == before


def test_path_prefix_switches_routing_mode():
    create(path_prefix="fizyczna")

    competition = Competition.objects.get(slug="fizyczna")
    assert competition.routing_mode == RoutingMode.PATH
    assert competition.path_prefix == "fizyczna"


def test_refuses_reserved_path_prefix():
    """Prefiks ``coordinator`` przechwyciłby adresy panelu — walidację ma model, nie formularz."""
    before = counts()

    with pytest.raises(CommandError, match="path_prefix"):
        create(path_prefix="coordinator")

    assert counts() == before


def test_domain_with_scheme_and_port_is_understood():
    create(domain="https://web:8000")

    site = Competition.objects.get(slug="fizyczna").site
    assert (site.hostname, site.port) == ("web", 8000)


def test_safe_seeds_from_template_are_run(monkeypatch):
    """Szablon może wskazać komendy globalne i idempotentne; seedy treści — nigdy.

    Podmieniamy ``call_command`` w module komendy zamiast puszczać prawdziwy ``seed_schools``:
    sprawdzamy **decyzję** komendy (co woła), a nie cudzą implementację importu wykazu szkół.
    """
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: called.append(name),
    )

    create(from_template="przedmiotowa")

    assert called == ["seed_schools"]


def test_safe_seeds_are_not_run_on_dry_run(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: called.append(name),
    )

    create(from_template="przedmiotowa", dry_run=True)

    assert called == []
