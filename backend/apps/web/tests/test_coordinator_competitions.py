"""Ekran „Nowy konkurs” ``/coordinator/competitions/new/`` i spis ``/coordinator/competitions/``.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran ma **dwie** bramki i obie są konieczne: ustawienie instalacji (``PLATFORM_SUBDOMAINS``,
  „na tym serwerze jest rekord wieloznaczny”) i flaga konkursu (``competition_creation``, „temu
  organizatorowi wolno zakładać kolejne”). Brak którejkolwiek daje **404**, a nie 403 — Olimpiada
  Kwantowa po wdrożeniu ma mieć adresy dokładnie takie, jak przed nim (§ 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu obu bramek: odpowiedź nie zdradza konfiguracji,
- identyfikator jest zarazem etykietą subdomeny, więc formularz odrzuca nazwy z infrastruktury
  platformy (``www``, ``mail``, ``ns1``) i adresy aplikacji (``admin``, ``cms``) — nie dlatego, że
  są brzydkie, tylko dlatego, że konkurs przejąłby pod tą domeną cudzą rolę,
- **podgląd nie zapisuje niczego**: pierwszy ``POST`` wykonuje całą czynność i wycofuje transakcję,
  więc liczba wierszy **każdej** tabeli ma zostać ta sama (porównanie jak w złotym pliku),
- potwierdzenie zakłada komplet i czyni zakładającego koordynatorem **nowego** konkursu — i tylko
  jego: rola w konkursie, z którego panelu wywołano ekran, ma zostać jedna.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps

from apps.accounts.models import CompetitionRole, Membership
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.models import Competition
from apps.tenancy.tests.factories import grant_membership
from apps.web.views.coordinator_competitions import CONFIRM_FIELD, FEATURE

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/competitions/"
NEW_URL = "/coordinator/competitions/new/"

#: Domena platformy w testach. Sufiks ``.test`` jest zarezerwowany normą (RFC 6761) i dopisuje go
#: do ``ALLOWED_HOSTS`` helper ``client_for``, więc adres nowego konkursu jest osiągalny.
PLATFORM = "platforma.test"


def payload(**overrides) -> dict:
    data = {
        "name": "Olimpiada Fizyczna",
        "short_name": "",
        "slug": "fizyczna",
        "template": "przedmiotowa",
        "organizer": "Polskie Towarzystwo Fizyczne",
        "contact_email": "biuro@example.invalid",
        "accent_colour": "",
        "edition_label": "",
    }
    data.update(overrides)
    return data


def enable(competition):
    """Włącza ekran tak, jak zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def platform(settings):
    """Instalacja z subdomenami platformy — druga połowa bramki, ta po stronie serwera."""
    settings.SITE_DOMAIN = PLATFORM
    settings.PLATFORM_SUBDOMAINS = True
    return PLATFORM


@pytest.fixture
def coordinator_client(client_for, competition, platform):  # noqa: ARG001 - fikstura ustawień
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** ekranem."""
    enable(competition)
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client, user


def row_counts() -> dict[str, int]:
    """Liczba wierszy każdej tabeli instalacji — ``_base_manager``, bo pytamy o tabelę, nie o widok."""
    return {
        model._meta.label: model._base_manager.count()
        for model in django_apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }


# --- bramki --------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [LIST_URL, NEW_URL])
def test_without_the_flag_the_address_does_not_exist(client_for, competition, platform, url):  # noqa: ARG001
    """Konkurs z domyślnymi przełącznikami nie ma tego adresu — 404, a nie 403."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert client.get(url).status_code == 404


@pytest.mark.parametrize("url", [LIST_URL, NEW_URL])
def test_without_the_installation_setting_the_address_does_not_exist(client_for, competition, settings, url):
    """Sama flaga nie wystarcza: bez rekordu wieloznacznego konkurs stanąłby pod martwym adresem."""
    settings.SITE_DOMAIN = PLATFORM
    settings.PLATFORM_SUBDOMAINS = False
    enable(competition)
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert client.get(url).status_code == 404


@pytest.mark.parametrize("url", [LIST_URL, NEW_URL])
def test_a_participant_gets_403_whatever_the_configuration(client_for, competition, platform, url):  # noqa: ARG001
    """Rola sprawdza się **przed** bramkami, więc odpowiedź nie mówi nic o konfiguracji."""
    enable(competition)
    participant = ParticipantFactory().user
    client = client_for(competition)
    client.force_login(participant)

    assert client.get(url).status_code == 403


@pytest.mark.parametrize("url", [LIST_URL, NEW_URL])
def test_an_anonymous_visitor_is_sent_to_the_login_page(client_for, competition, platform, url):  # noqa: ARG001
    enable(competition)

    response = client_for(competition).get(url)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


# --- spis konkursów --------------------------------------------------------------------------------


def test_the_list_shows_the_competitions_of_this_account_with_their_addresses(coordinator_client):
    client, _ = coordinator_client

    content = client.get(LIST_URL).content.decode()

    assert "Moje konkursy" in content
    assert "kwantowa.invalid" in content


def test_the_list_does_not_show_a_competition_of_someone_else(coordinator_client, other_competition):
    """Spis wychodzi z **członkostw**, a nie z globalnej grupy ``coordinator``."""
    client, _ = coordinator_client

    content = client.get(LIST_URL).content.decode()

    assert other_competition.primary_domain not in content


# --- walidacja identyfikatora ----------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["www", "mail", "ns1", "admin", "cms", "internal", "setup"])
def test_a_reserved_label_is_refused(coordinator_client, slug):
    """Nazwy poczty, serwerów nazw i adresów aplikacji nie mogą zostać identyfikatorem konkursu."""
    client, _ = coordinator_client

    response = client.post(NEW_URL, payload(slug=slug))

    assert response.status_code == 400
    assert not Competition.objects.filter(slug=slug).exists()


@pytest.mark.parametrize("slug", ["ab", "-fizyczna", "fizyczna-", "fi zyczna", "fizyczna!", "fizyczna."])
def test_a_label_of_the_wrong_shape_is_refused(coordinator_client, slug):
    client, _ = coordinator_client

    assert client.post(NEW_URL, payload(slug=slug)).status_code == 400


def test_capital_letters_are_normalised_and_not_refused(coordinator_client, platform):
    """Nazwa hosta jest nieczuła na wielkość liter, więc ``Fizyczna`` jest **tym samym** adresem.

    Odmowa byłaby tu odmową z powodu, którego nie widać w adresie; sprowadzenie do małych liter
    jest natomiast jawne — podgląd pokazuje adres, który naprawdę powstanie.
    """
    client, _ = coordinator_client

    content = client.post(NEW_URL, payload(slug="Fizyczna")).content.decode()

    assert f"https://fizyczna.{platform}/" in content


def test_a_slug_already_taken_by_a_competition_is_refused(coordinator_client, competition):
    client, _ = coordinator_client

    response = client.post(NEW_URL, payload(slug=competition.slug))

    assert response.status_code == 400
    assert Competition.objects.filter(slug=competition.slug).count() == 1


def test_a_label_already_used_under_the_platform_domain_is_refused(coordinator_client, platform):
    """Witryna bez konkursu też zajmuje host — drugie drzewo pod tym adresem byłoby loterią."""
    from apps.tenancy.tests.conftest import make_site

    client, _ = coordinator_client
    make_site(f"zajeta.{platform}", own_root=True)

    assert client.post(NEW_URL, payload(slug="zajeta")).status_code == 400


# --- podgląd i potwierdzenie -----------------------------------------------------------------------


def test_the_preview_writes_nothing_at_all(coordinator_client, platform):
    """Pierwszy ``POST`` wykonuje całość i wycofuje transakcję — tabela po tabeli, bez wyjątków."""
    client, _ = coordinator_client
    before = row_counts()

    response = client.post(NEW_URL, payload())

    assert response.status_code == 200
    content = response.content.decode()
    assert "Co powstanie" in content
    # Podgląd pokazuje **adres**, bo to jest pytanie, które pada w tym miejscu.
    assert f"https://fizyczna.{platform}/" in content
    assert row_counts() == before


def test_the_preview_counts_come_from_the_real_run(coordinator_client):
    """Liczby zestawów startowych są policzone przez ten sam kod, który za chwilę je zapisze."""
    client, _ = coordinator_client

    content = client.post(NEW_URL, payload()).content.decode()

    assert "cms:fizyczna" in content
    assert "Zawody I stopnia (szkolne)" in content


def test_the_confirmation_creates_the_whole_competition(coordinator_client, platform):
    client, user = coordinator_client

    response = client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    assert response.status_code == 302
    assert response.headers["Location"] == f"{LIST_URL}?utworzono=fizyczna"

    created = Competition.objects.select_related("site").get(slug="fizyczna")
    assert created.primary_domain == f"fizyczna.{platform}"
    assert created.site.hostname == f"fizyczna.{platform}"
    assert created.site.is_default_site is False
    assert created.organizer_name == "Polskie Towarzystwo Fizyczne"
    assert created.editions.filter(is_current=True).count() == 1
    assert created.editions.get().stages.count() == 3
    # Zestawy startowe etapu 2 – ta sama lista, co przy komendzie.
    from apps.accounts.models import ConsentDefinition, Region

    assert ConsentDefinition.objects.for_competition(created).count() == 4
    assert Region.objects.for_competition(created).count() == 18
    # I rola dla zakładającego, bo inaczej założyłby konkurs, do którego panelu sam by nie wszedł.
    assert Membership.objects.filter(
        user=user, competition=created, role=CompetitionRole.COORDINATOR
    ).exists()


def test_the_creator_gets_a_role_in_the_new_competition_and_nowhere_else(
    coordinator_client, competition, other_competition
):
    client, user = coordinator_client
    before = set(Membership.objects.filter(user=user).values_list("competition_id", flat=True))

    client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    created = Competition.objects.get(slug="fizyczna")
    after = set(Membership.objects.filter(user=user).values_list("competition_id", flat=True))
    assert after - before == {created.pk}
    assert competition.pk in after
    assert other_competition.pk not in after


def test_the_audit_entry_belongs_to_the_new_competition(coordinator_client, competition):
    """``competition.created`` czyta się w dzienniku **nowego** konkursu, bo to jego dotyczy."""
    client, user = coordinator_client

    client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    created = Competition.objects.get(slug="fizyczna")
    entry = AuditLog.objects.get(action="competition.created")
    assert entry.competition_id == created.pk
    assert entry.competition_id != competition.pk
    assert entry.actor_id == user.pk
    assert entry.diff == {
        "slug": "fizyczna",
        "domain": created.primary_domain,
        "template": "przedmiotowa",
    }


def test_the_second_confirmation_of_the_same_slug_is_a_form_error(coordinator_client):
    """Wyścig między podglądem a potwierdzeniem kończy się komunikatem, a nie pięćsetką."""
    client, _ = coordinator_client
    client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    response = client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    assert response.status_code == 400
    assert Competition.objects.filter(slug="fizyczna").count() == 1


def test_the_safe_seeds_of_the_template_do_not_run_in_a_request(coordinator_client, monkeypatch):
    """Wykaz SIO/RSPO to kilka tysięcy wierszy wpisywanych **poza** transakcją — nie w żądaniu HTTP."""
    called: list[str] = []
    monkeypatch.setattr(
        "apps.tenancy.provisioning.call_command", lambda name, *args, **kwargs: called.append(name)
    )
    client, _ = coordinator_client

    client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    assert called == []
    assert Competition.objects.filter(slug="fizyczna").exists()


def test_the_new_competition_answers_under_its_own_address(coordinator_client, client_for):
    """Domknięcie całej funkcji: adres z ekranu naprawdę odpowiada, a nie tylko powstaje w bazie."""
    client, _ = coordinator_client

    client.post(NEW_URL, {**payload(), CONFIRM_FIELD: "1"})

    created = Competition.objects.select_related("site").get(slug="fizyczna")
    assert client_for(created).get("/healthz/").status_code == 200
