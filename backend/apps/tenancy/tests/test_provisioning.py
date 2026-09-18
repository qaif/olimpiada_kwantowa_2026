"""``apps.tenancy.provisioning`` — czynność zakładania konkursu wyjęta z komendy.

Przedmiotem jest **równoważność**: od wydzielenia czynności istnieją dwie drogi do konkursu
(``manage.py create_competition`` i ekran „Nowy konkurs”), a druga z nich powstała po to, żeby
robić dokładnie to samo. Gdyby robiła mniej, objaw byłby cichy i odroczony: konkurs założony
z panelu miałby np. pusty formularz zgód albo brakującą grupę ``/cms/`` i dowiedziałby się o tym
jego koordynator, w dniu, w którym zaczyna to być potrzebne.

Dlatego porównujemy **wiersze wszystkich tabel**, a nie wybrane pola: lista rzeczy, które ma
dostać nowy konkurs, rośnie z każdym wydaniem, a test wymieniający je z nazwy przestałby ich
pilnować dokładnie wtedy, gdy przybywa nowa.

Druga rzecz pilnowana tutaj to ``run_safe_seeds``: seedy szablonu (dziś ``seed_schools``, kilka
tysięcy wierszy wykazu SIO/RSPO wpisywanych **poza** transakcją) nie mogą uruchomić się w środku
żądania HTTP. Domyślne ``False`` jest wartością dla panelu; komenda woła je sama.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from freezegun import freeze_time

from apps.tenancy.models import Competition, RoutingMode
from apps.tenancy.provisioning import (
    ProvisioningError,
    coordinator_from_email,
    create_competition_from_template,
)

pytestmark = pytest.mark.django_db

#: Zamrożony zegar, bo oznaczenie edycji liczy się z dnia uruchomienia. Ten sam dzień, co
#: w ``test_create_competition.py`` — obie drogi mają wyjść na to samo.
TODAY = "2026-09-17 12:00:00"

#: Szablon z etapami i z niepustym ``safe_seeds``: porównanie dwóch dróg ma objąć edycję, etapy,
#: skale, reguły kwalifikacji i kroki toru, a nie sam szkielet serwisu.
TEMPLATE = "przedmiotowa"


@pytest.fixture(autouse=True)
def no_seeds(monkeypatch):
    """Ani komenda, ani czynność nie uruchamiają w tych testach prawdziwego ``seed_schools``.

    Wykaz SIO/RSPO to kilka tysięcy cudzych wierszy. W teście **porównującym** dwie drogi byłby
    dodatkowo szkodliwy: seedy chodzą poza transakcją, więc pierwsze wywołanie wpisałoby wiersze,
    a drugie nie miałoby czego wpisać i różnica wyszłaby w liczniku tabel.
    """
    monkeypatch.setattr(
        "apps.tenancy.management.commands.create_competition.call_command",
        lambda name, *args, **kwargs: None,
    )
    monkeypatch.setattr("apps.tenancy.provisioning.call_command", lambda name, *args, **kwargs: None)


def row_counts() -> dict[str, int]:
    """Liczba wierszy każdej tabeli instalacji, po etykiecie modelu.

    ``_base_manager``, a nie ``objects``: menedżery zakresowane konkursem filtrują po kontekście,
    a tu pytamy o **tabelę**. Ta sama funkcja, co w ``test_golden_single_competition.py``, i z tego
    samego powodu.
    """
    return {
        model._meta.label: model._base_manager.count()
        for model in django_apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }


def delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {label: after[label] - before[label] for label in after if after[label] != before[label]}


def make_user(email: str = "koordynator@example.invalid"):
    from apps.accounts.models import User

    return User.objects.create_user(email=email, password="haslo-testowe-123")


# --- równoważność z komendą ----------------------------------------------------------------------


@freeze_time(TODAY)
def test_the_function_writes_the_same_rows_as_the_command():
    """Ta sama liczba wierszy w tych samych tabelach — tabela po tabeli, bez listy wyjątków."""
    before = row_counts()
    call_command(
        "create_competition",
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        from_template=TEMPLATE,
    )
    by_command = delta(before, row_counts())

    before = row_counts()
    create_competition_from_template(
        slug="chemiczna",
        name="Olimpiada Chemiczna",
        domain="olimpiadachemiczna.invalid",
        template=TEMPLATE,
    )
    by_function = delta(before, row_counts())

    assert by_function == by_command
    # Siatka pod sygnałem: gdyby obie drogi nie zapisywały **niczego**, porównanie wyżej też by
    # przeszło. Konkurs, witryna i strony muszą być w tej różnicy.
    assert by_command["tenancy.Competition"] == 1
    assert by_command["wagtailcore.Site"] == 1
    assert by_command["competitions.Edition"] == 1


@freeze_time(TODAY)
def test_the_function_fills_the_same_fields_as_the_command():
    """Pola, które widać w serwisie: nazwa z wzorca szablonu, prefiksy z sluga, tryb adresowania."""
    create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        organizer="Polskie Towarzystwo Fizyczne",
        contact_email="biuro@example.invalid",
    )

    competition = Competition.objects.get(slug="fizyczna")
    assert competition.name == "Olimpiada Fizyczna"
    assert competition.organizer_name == "Polskie Towarzystwo Fizyczne"
    assert competition.email_subject_prefix == "[Olimpiada Fizyczna] "
    assert competition.routing_mode == RoutingMode.DOMAIN
    assert competition.primary_domain == "olimpiadafizyczna.invalid"
    assert (competition.public_code_prefix, competition.certificate_prefix) == ("FIZ-", "FI")
    assert competition.site.is_default_site is False


@freeze_time(TODAY)
def test_the_result_carries_what_the_caller_needs_to_show():
    """Wynik jest **obiektami i liczbami**, a nie napisem — po to czynność została wydzielona."""
    result = create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
    )

    assert result.competition.slug == "fizyczna"
    assert result.edition.year_label == "I edycja 2026/2027"
    assert [stage.kind for stage in result.stages] == ["ELIM", "DISTRICT", "FINAL"]
    # Zestaw zgód jest **domyślny** (``accounts.DEFAULT_CONSENTS``), a nie wzięty z listy
    # kontrolnej szablonu: szablon mówi, o co konkurs pyta przy rejestracji, a definicje powstają
    # w komplecie, żeby dało się je włączać i wyłączać z panelu.
    assert result.seeded["consents"] == 4
    assert result.seeded["documents"] == 5
    assert result.seeded["regions"] == 18
    assert result.seeded["pipeline"] == 3
    assert result.seeded["cms_group"] == "cms:fizyczna"
    assert result.template_name == TEMPLATE
    assert result.dry_run is False


@freeze_time(TODAY)
def test_dry_run_leaves_the_database_exactly_as_it_was():
    before = row_counts()

    result = create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        dry_run=True,
    )

    assert row_counts() == before
    # Wynik mimo to opisuje komplet: to jest cała wartość próby na sucho dla ekranu podglądu.
    assert result.dry_run is True
    assert result.seeded["regions"] == 18
    assert len(result.stages) == 3


@freeze_time(TODAY)
def test_refusals_speak_the_same_sentences_as_the_command():
    """Komunikaty odmowy są kontraktem: komenda oddaje je bez własnego wstępu."""
    create_competition_from_template(
        slug="fizyczna", name="Olimpiada Fizyczna", domain="olimpiadafizyczna.invalid", template=TEMPLATE
    )

    with pytest.raises(ProvisioningError, match="już istnieje"):
        create_competition_from_template(
            slug="fizyczna", name="Inna", domain="inna.invalid", template=TEMPLATE
        )
    with pytest.raises(ProvisioningError, match="Witryna"):
        create_competition_from_template(
            slug="inna", name="Inna", domain="olimpiadafizyczna.invalid", template=TEMPLATE
        )
    with pytest.raises(ProvisioningError, match="szablonu"):
        create_competition_from_template(
            slug="inna", name="Inna", domain="inna.invalid", template="nie-ma-takiego"
        )


@freeze_time(TODAY)
def test_unknown_coordinator_email_is_a_refusal_and_not_a_new_account():
    before = row_counts()

    with pytest.raises(ProvisioningError, match="Nie ma konta"):
        coordinator_from_email("nikt@example.invalid")

    assert row_counts() == before
    assert coordinator_from_email("") is None


@freeze_time(TODAY)
def test_the_coordinator_gets_the_membership_and_the_group():
    from apps.accounts.models import CompetitionRole, Membership

    user = make_user()

    create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        coordinator=user,
    )

    competition = Competition.objects.get(slug="fizyczna")
    assert Membership.objects.filter(
        user=user, competition=competition, role=CompetitionRole.COORDINATOR
    ).exists()
    assert user.groups.filter(name=CompetitionRole.COORDINATOR).exists()


# --- ``safe_seeds`` jako parametr, a nie jako zawsze ----------------------------------------------


@freeze_time(TODAY)
def test_safe_seeds_do_not_run_by_default(monkeypatch):
    """Domyślna wartość jest wartością dla panelu: w żądaniu HTTP seedy nie chodzą."""
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.provisioning.call_command", lambda name, *args, **kwargs: called.append(name)
    )

    create_competition_from_template(
        slug="fizyczna", name="Olimpiada Fizyczna", domain="olimpiadafizyczna.invalid", template=TEMPLATE
    )

    assert called == []


@freeze_time(TODAY)
def test_safe_seeds_run_when_the_caller_asks_for_them(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.provisioning.call_command", lambda name, *args, **kwargs: called.append(name)
    )

    create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        run_safe_seeds=True,
    )

    assert called == ["seed_schools"]


@freeze_time(TODAY)
def test_safe_seeds_never_run_on_a_dry_run(monkeypatch):
    """Seedy chodzą poza transakcją, więc ich wierszy próba na sucho nie miałaby jak wycofać."""
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.provisioning.call_command", lambda name, *args, **kwargs: called.append(name)
    )

    create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        run_safe_seeds=True,
        dry_run=True,
    )

    assert called == []


# --- linijki do ``.env`` --------------------------------------------------------------------------


@freeze_time(TODAY)
def test_env_lines_name_the_single_variable_for_a_domain_of_its_own():
    result = create_competition_from_template(
        slug="fizyczna", name="Olimpiada Fizyczna", domain="olimpiadafizyczna.invalid", template=TEMPLATE
    )

    assert result.env_lines == ("EXTRA_DOMAINS=… olimpiadafizyczna.invalid",)


@freeze_time(TODAY)
def test_env_lines_are_empty_for_a_competition_under_a_path_prefix():
    """Konkurs pod prefiksem ścieżki nie ma własnego hosta, więc nie ma czego wpisywać."""
    result = create_competition_from_template(
        slug="fizyczna",
        name="Olimpiada Fizyczna",
        domain="olimpiadafizyczna.invalid",
        template=TEMPLATE,
        path_prefix="fizyczna",
    )

    assert result.competition.routing_mode == RoutingMode.PATH
    assert result.env_lines == ()


@freeze_time(TODAY)
def test_env_lines_are_empty_for_a_platform_subdomain(settings):
    """Subdomenę platformy obejmuje wildcard, więc wpis w ``EXTRA_DOMAINS`` niczego by nie zmienił."""
    settings.SITE_DOMAIN = "platforma.test"
    settings.PLATFORM_SUBDOMAINS = True

    result = create_competition_from_template(
        slug="fizyczna", name="Olimpiada Fizyczna", domain="fizyczna.platforma.test", template=TEMPLATE
    )

    assert result.env_lines == ()
