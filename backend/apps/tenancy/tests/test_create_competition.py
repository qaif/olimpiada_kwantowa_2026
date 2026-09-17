"""``manage.py create_competition`` — zakłada komplet, odmawia duplikatu, nie zostawia połowy.

Komenda jest jedynym wejściem do zakładania konkursu, więc testy pilnują trzech rzeczy:

1. **kompletu.** Konkurs bez witryny nie ma drzewa stron, a witryna bez konkursu oddaje pod swoim
   adresem treść konkursu domyślnego — dlatego sprawdzamy wszystkie cztery wiersze naraz.
2. **odmowy.** Zajęty identyfikator albo zajęta domena to nie jest sytuacja do „dopisania obok”:
   druga witryna pod tym samym hostem znaczyłaby, że o treści pod adresem decyduje kolejność
   wierszy w bazie.
3. **braku śladu.** ``--dry-run`` i każda odmowa mają zostawić bazę dokładnie taką, jaka była.
4. **wartości początkowych, które widać jako początkowe.** Edycja i etapy powstają (§ 4.5, kroki
   5 i 6), ale ich terminy są odłożone od dnia bazowego, a nie wzięte z harmonogramu Konkursu #1 —
   testy sprawdzają **odstępy**, bo to one są tu treścią, a nie konkretna data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command
from freezegun import freeze_time
from wagtail.models import Page, Site

from apps.cms.models import HomePage, SiteSettings
from apps.competitions.models import TRAINING_DEADLINE, Edition, QualificationRule, ScoringScale
from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.templates_catalog import DEFAULT_PAGES, TEMPLATES

pytestmark = pytest.mark.django_db

WARSAW = ZoneInfo("Europe/Warsaw")

#: Dzień, w którym „uruchamiamy” komendę w testach terminów, i dzień bazowy, który z niego wynika
#: (pierwszy dzień następnego miesiąca). Zamrożony zegar, bo cała konwencja jest **względna**:
#: bez niego test o odstępach przechodziłby albo nie w zależności od tego, którego dnia leci.
TODAY = "2026-09-17 12:00:00"
BASE_DAY = date(2026, 10, 1)

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


# =================================================================================================
# Edycja i etapy (§ 4.5, kroki 5 i 6)
# =================================================================================================


@pytest.fixture
def no_seeds(monkeypatch):
    """Szablony z ``safe_seeds`` bez prawdziwego ``seed_schools``.

    Wykaz SIO/RSPO to kilka tysięcy wierszy i cudza funkcja — test o zakładaniu edycji nie ma
    powodu jej uruchamiać, a ma powód nie czekać na nią przy każdym przebiegu.
    """
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: None,
    )


def edition() -> Edition:
    return Edition.objects.get(competition__slug="fizyczna")


def stages() -> list:
    """Etapy w kolejności **zakładania**, czyli w kolejności szablonu.

    Nie po ``opens_at``: trening otwiera się tego samego dnia, co pierwszy etap zawodów, więc
    sortowanie po dacie mieszałoby piaskownicę w środek kolejki zawodów.
    """
    return list(edition().stages.order_by("id"))


def warsaw_day(moment: datetime) -> date:
    """Dzień terminu **w strefie organizatora** – w niej jest liczony i w niej jest czytany.

    Bez tego przeliczenia termin z 23:59 czasu polskiego wypadałby w UTC na dzień wcześniej
    i test o odstępie jednego dnia sprawdzałby przesunięcie strefy zamiast odstępu.
    """
    return moment.astimezone(WARSAW).date()


@freeze_time(TODAY)
def test_creates_current_edition_with_school_year_label():
    """Rocznik liczy się od września i z dnia uruchomienia, a nie ze stałej w kodzie."""
    create()

    row = edition()
    assert row.year_label == "I edycja 2026/2027"
    assert row.is_current is True


@freeze_time("2026-05-17 12:00:00")
def test_edition_label_before_september_belongs_to_the_year_that_ends():
    create()

    assert edition().year_label == "I edycja 2025/2026"


@freeze_time(TODAY)
def test_edition_label_argument_wins():
    create(edition_label="XV (2026/2027)")

    assert edition().year_label == "XV (2026/2027)"


@freeze_time(TODAY)
def test_same_edition_label_in_two_competitions_is_allowed():
    """Oznaczenie rocznika jest unikalne **u organizatora**, a nie w instalacji (§ 1.5)."""
    create()
    create(slug="chemiczna", name="Olimpiada Chemiczna", domain="olimpiadachemiczna.invalid")

    labels = Edition.objects.filter(year_label="I edycja 2026/2027").count()
    assert labels == 2
    # I obie są bieżące — każda w swoim konkursie.
    assert Edition.objects.filter(is_current=True).count() == 2


@freeze_time(TODAY)
def test_creates_stages_from_template_in_order(no_seeds):  # noqa: ARG001 - działa efektem ubocznym
    """Rodzaje, kolejność i formy pochodzą z szablonu, nie z seeda Olimpiady Kwantowej."""
    create(from_template="kwantowa")

    created = stages()
    wanted = TEMPLATES["kwantowa"]["stages"]
    assert [stage.kind for stage in created] == [spec["kind"] for spec in wanted]
    assert [stage.format for stage in created] == [spec["format"] for spec in wanted]
    assert [stage.name for stage in created] == [spec["name"] for spec in wanted]


@freeze_time(TODAY)
def test_first_stage_opens_on_the_first_day_of_the_next_month():
    """Konwencja dat zastępczych: dzień bazowy jest rozpoznawalny jako wartość do poprawienia."""
    create(from_template="przedmiotowa")

    first = stages()[0]
    assert first.opens_at == datetime(2026, 10, 1, 0, 0, tzinfo=WARSAW)
    # 60 dni okna oddawania prac z szablonu, termin o 23:59 czasu polskiego.
    assert first.deadline_at == datetime(2026, 11, 30, 23, 59, tzinfo=WARSAW)


@freeze_time(TODAY)
def test_stage_timeline_repeats_the_rules_of_seed_edition_kwantowa():
    """Recenzje +14 dni po terminie oddania, okno reklamacji +2/+9 dni po terminie recenzji."""
    create(from_template="przedmiotowa")

    first = stages()[0]
    assert first.review_deadline_at == first.deadline_at + timedelta(days=14)
    assert first.appeal_window_opens_at == first.review_deadline_at + timedelta(days=2)
    assert first.appeal_window_closes_at == first.review_deadline_at + timedelta(days=9)


@freeze_time(TODAY)
def test_next_stage_opens_after_the_previous_deadline():
    """Odstęp z szablonu liczy się od **terminu oddania** poprzedniego etapu, a nie od dnia bazowego."""
    create(from_template="przedmiotowa")

    first, second, third = stages()
    assert warsaw_day(second.opens_at) == warsaw_day(first.deadline_at) + timedelta(days=1)
    assert warsaw_day(third.opens_at) == warsaw_day(second.deadline_at) + timedelta(days=1)


@freeze_time(TODAY)
def test_training_stage_has_the_sentinel_deadline_and_does_not_move_the_others(no_seeds):  # noqa: ARG001
    """Piaskownica stoi obok zawodów: bez terminu i bez wpływu na oś czasu następnego etapu.

    Gdyby trening przesuwał kursor, finał wypadłby po dacie-wartowniku z 2099 roku — czyli poza
    kalendarzem, w którym cokolwiek się odbywa.
    """
    create(from_template="kwantowa")

    by_kind = {stage.kind: stage for stage in stages()}
    assert by_kind["TRAINING"].deadline_at == TRAINING_DEADLINE
    assert by_kind["TRAINING"].opens_at == datetime(BASE_DAY.year, BASE_DAY.month, 1, 0, 0, tzinfo=WARSAW)
    assert by_kind["FINAL"].deadline_at.year == 2027


@freeze_time(TODAY)
def test_stages_get_scale_and_qualification_rule():
    """Dowód, że etapy idą przez ``create_stage``, a nie przez własną kopię tej wiedzy."""
    create(from_template="przedmiotowa")

    for stage in stages():
        assert ScoringScale.objects.filter(stage=stage).exists()
        assert QualificationRule.objects.filter(stage=stage).exists()


@freeze_time(TODAY)
def test_empty_template_gives_an_edition_without_stages():
    """Konkurs bez etapów ma mimo to bieżącą edycję — inaczej nie pokazałby nawet rejestracji."""
    create()

    assert edition().stages.count() == 0


@freeze_time(TODAY)
def test_dry_run_leaves_no_edition_and_no_stages():
    before = (Edition.objects.count(), ScoringScale.objects.count())

    create(from_template="przedmiotowa", dry_run=True)

    assert (Edition.objects.count(), ScoringScale.objects.count()) == before


# =================================================================================================
# Rola koordynatora (§ 4.5, krok 6)
# =================================================================================================


def make_user(email: str = "koordynator@example.invalid"):
    from apps.accounts.models import User

    return User.objects.create_user(email=email, password="haslo-testowe-123")


@freeze_time(TODAY)
def test_coordinator_email_grants_membership_and_group():
    """Serwis zapisuje oba: rolę w konkursie i grupę Django (uprawnienie do ``/cms/``, § 3.8)."""
    from apps.accounts.models import CompetitionRole, Membership

    user = make_user()

    create(coordinator_email="Koordynator@Example.Invalid")

    competition = Competition.objects.get(slug="fizyczna")
    assert Membership.objects.filter(
        user=user, competition=competition, role=CompetitionRole.COORDINATOR
    ).exists()
    assert user.groups.filter(name=CompetitionRole.COORDINATOR).exists()


@freeze_time(TODAY)
def test_unknown_coordinator_email_refuses_before_writing_anything():
    """Komenda nie zakłada kont — i nie zakłada konkursu, dla którego nie ma kogo ustawić."""
    before = counts()

    with pytest.raises(CommandError, match="Nie ma konta"):
        create(coordinator_email="nikt@example.invalid")

    assert counts() == before
    assert not Edition.objects.filter(competition__slug="fizyczna").exists()


@freeze_time(TODAY)
def test_dry_run_does_not_grant_the_role():
    from apps.accounts.models import Membership

    user = make_user()

    create(coordinator_email=user.email, dry_run=True)

    assert not Membership.objects.filter(user=user).exists()
