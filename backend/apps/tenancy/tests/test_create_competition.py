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


# --- zestawy startowe etapu 2 --------------------------------------------------------------------
#
# Cztery zestawy, które konkursom stojącym w bazie dały migracje danych (``accounts.0024``,
# ``accounts.0026``, ``tenancy.0005``) i grupa redakcyjna z § 1.1.5. Migracja z definicji dotyczy
# wierszy istniejących w chwili jej wykonania, więc przedmiotem tych testów jest to, że konkurs
# założony **później** dostaje to samo — i że nie dostaje przy okazji cudzej marki.
# =================================================================================================


def new_competition() -> Competition:
    create()
    return Competition.objects.get(slug="fizyczna")


@freeze_time(TODAY)
def test_new_competition_gets_the_default_consent_definitions():
    """Zestaw zgód ze stałej, znak w znak — inaczej rejestracja po włączeniu flagi byłaby pusta."""
    from apps.accounts.consents import DEFAULT_CONSENTS
    from apps.accounts.models import ConsentDefinition

    competition = new_competition()

    definitions = list(ConsentDefinition.objects.for_competition(competition).order_by("ordering"))
    assert [row.kind for row in definitions] == [str(consent.kind) for consent in DEFAULT_CONSENTS]
    for row, consent in zip(definitions, DEFAULT_CONSENTS, strict=True):
        assert row.field_name == consent.field_name
        assert row.text == consent.text
        assert row.version == consent.version
        assert row.required == consent.required
        assert row.required_for_minor == consent.required_for_minor
        assert row.is_active is True


@freeze_time(TODAY)
def test_new_competition_gets_document_templates_without_the_kwantowa_brand():
    """Pięć rodzajów w wersji ``1.0``, a marka w zdaniu jest **znacznikiem**, a nie cudzą nazwą.

    Napisy migracji ``tenancy.0005`` mówią „Olimpiady Kwantowej” w dopełniaczu i dostaje je tam
    wyłącznie konkurs o najniższym ``pk``. Nowy konkurs dostaje te same zdania ze znacznikiem
    ``{competition_genitive}`` — czyli ten sam dokument, tylko z własną nazwą.
    """
    from apps.results.certificates import DOCUMENT_TITLES
    from apps.tenancy.documents import DocumentKind, templates_of

    competition = new_competition()

    kinds = set(templates_of(competition).values_list("kind", flat=True))
    assert kinds == {
        DocumentKind.LAUREAT,
        DocumentKind.FINALISTA,
        DocumentKind.UCZESTNIK,
        DocumentKind.OPIEKUN,
        DocumentKind.WARSZTATY,
    }
    # Wiersz bierzemy z querysetu, a nie z ``current_template``: ta funkcja przy wyłączonej fladze
    # ``document_templates`` **nie pyta bazy** (§ 5.6), a nowy konkurs flagi nie włącza — wiersze
    # mają leżeć gotowe na dzień, w którym ktoś ją włączy.
    laureat = templates_of(competition).get(kind=DocumentKind.LAUREAT, is_current=True)
    assert laureat.version == "1.0"
    assert laureat.title == DOCUMENT_TITLES[DocumentKind.LAUREAT]
    assert "Kwantow" not in laureat.statement
    assert "{competition_genitive}" in laureat.statement
    assert "{competition_genitive}" in laureat.signature_line
    # Podstawienie działa, czyli znacznik nie zostanie na papierze.
    assert competition.name in laureat.render(competition).statement


@freeze_time(TODAY)
def test_new_competition_gets_the_starting_regions():
    """Kraj, szesnaście województw z listy ``Voivodeship`` i nieaktywne „poza Polską”."""
    from apps.accounts.models import Region, RegionLevel, Voivodeship

    competition = new_competition()

    regions = list(Region.objects.for_competition(competition).order_by("position", "id"))
    assert len(regions) == len(Voivodeship.choices) + 2

    country = regions[0]
    assert (country.code, country.level) == ("pl", RegionLevel.COUNTRY)
    voivodeships = regions[1:-1]
    assert [region.code for region in voivodeships] == list(Voivodeship.values)
    assert [region.name for region in voivodeships] == [str(label) for _, label in Voivodeship.choices]
    assert {region.parent_id for region in voivodeships} == {country.pk}
    assert {region.level for region in voivodeships} == {RegionLevel.REGION}

    abroad = regions[-1]
    assert abroad.code == "poza-polska"
    assert abroad.is_active is False
    assert abroad.counts_for_conflict is False


@freeze_time(TODAY)
def test_new_competition_gets_its_own_cms_group():
    """Grupa ``cms:<slug>`` powstaje zawsze — także przy wyłączonej fladze zawężenia uprawnień."""
    from django.contrib.auth.models import Group

    competition = new_competition()

    assert Group.objects.filter(name=f"cms:{competition.slug}").exists()
    assert competition.has_feature("scoped_cms_permissions") is False


@freeze_time(TODAY)
def test_coordinator_joins_the_competition_cms_group_only_behind_the_flag(monkeypatch):
    """Zawężenie **dokłada**: koordynator zostaje w grupie globalnej i dostaje grupę konkursu.

    Flagę czyta komenda z wiersza konkursu, a wiersz powstaje z ``feature_flags`` szablonu —
    dlatego test podmienia katalog szablonów, a nie zapisuje konkursu po fakcie: chodzi
    o zachowanie **w trakcie** zakładania.
    """
    from apps.accounts.models import CompetitionRole
    from apps.tenancy import templates_catalog

    # Komenda importuje **ten sam** obiekt słownika (``from … import TEMPLATES``), więc podmiana
    # pozycji w katalogu jest podmianą dla komendy; monkeypatch przywraca ją po teście.
    template = {**TEMPLATES[TEMPLATE], "feature_flags": {"scoped_cms_permissions": True}}
    monkeypatch.setitem(templates_catalog.TEMPLATES, TEMPLATE, template)
    user = make_user()

    create(coordinator_email=user.email)

    competition = Competition.objects.get(slug="fizyczna")
    names = set(user.groups.values_list("name", flat=True))
    assert f"cms:{competition.slug}" in names
    assert CompetitionRole.COORDINATOR in names


@freeze_time(TODAY)
def test_dry_run_leaves_no_starting_sets():
    """Próba na sucho wykonuje wszystko i wycofuje — także cztery zestawy startowe."""
    from django.contrib.auth.models import Group

    from apps.accounts.models import ConsentDefinition, Region
    from apps.tenancy.documents import DocumentTemplate

    before = (
        ConsentDefinition.objects.count(),
        Region.objects.count(),
        DocumentTemplate.objects.count(),
        Group.objects.count(),
    )

    create(dry_run=True)

    assert (
        ConsentDefinition.objects.count(),
        Region.objects.count(),
        DocumentTemplate.objects.count(),
        Group.objects.count(),
    ) == before


@freeze_time(TODAY)
def test_report_names_every_starting_set():
    """Podsumowanie wymienia każdy zestaw — brakująca pozycja ma być widoczna od razu."""
    from io import StringIO

    output = StringIO()
    call_command(
        "create_competition",
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        from_template=TEMPLATE,
        stdout=output,
    )

    report = output.getvalue()
    assert "zgody:    4" in report
    assert "szablony dokumentów: 5" in report
    assert "regiony:  18" in report
    assert "grupa /cms/: cms:fizyczna" in report


# --- etap 2, uwagi T43: tor etapów i własne przedrostki kodów -------------------------------------


def test_new_competition_gets_a_pipeline_step_for_every_stage():
    """Szablon z etapami daje od razu kroki toru – bez pierwszego ręcznego „Dopisz krok”.

    Odpowiednik migracji ``competitions.0024`` dla edycji założonej po niej: etap treningowy poza
    torem, pozostałe w kolejności ``ELIM`` → ``DISTRICT`` → ``FINAL``, a próg kwalifikacji etapu
    staje się jego regułą przejścia.
    """
    from apps.competitions.models import PipelineStep, StageKind, TransitionRule

    create(from_template="przedmiotowa")
    steps = list(PipelineStep.objects.filter(edition=edition()).order_by("position", "id"))

    assert [s.stage.kind for s in steps if not s.off_pipeline] == [
        StageKind.ELIM,
        StageKind.DISTRICT,
        StageKind.FINAL,
    ]
    assert {s.stage.kind for s in steps if s.off_pipeline} <= {StageKind.TRAINING}
    for step in steps:
        if step.off_pipeline:
            continue
        has_rule = QualificationRule.objects.filter(stage=step.stage).exists()
        assert TransitionRule.objects.filter(step=step).exists() == has_rule


def test_new_competition_gets_its_own_code_prefixes():
    """``OLM-`` i ``OK`` należą do Konkursu #1; nowy konkurs dostaje przedrostki ze sluga."""
    create()
    competition = Competition.objects.get(slug="fizyczna")

    assert (competition.public_code_prefix, competition.certificate_prefix) == ("FIZ-", "FI")


def test_code_prefixes_can_be_given_explicitly():
    create(public_code_prefix="OF-", certificate_prefix="OFI")
    competition = Competition.objects.get(slug="fizyczna")

    assert (competition.public_code_prefix, competition.certificate_prefix) == ("OF-", "OFI")
