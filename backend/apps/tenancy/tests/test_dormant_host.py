"""Host bez aktywnego konkursu nie otwiera paneli (audyt S13, 10.10.2026).

Scenariusz z audytu: dwa konkursy, A wyłączony, B aktywny. Koordynator B (rola z globalnej grupy,
bo ``memberships_enforced`` wyłączone) wysyła żądania z ``Host:`` domeny A. Witryna A rozstrzyga się
do ``request.competition=None``, a ``None`` znaczyło dotąd „role z grup i konta wszystkich
konkursów” – zmiana e-maila cudzego konta, reset hasła, przejęcie.

Dwie bramki, każda sprawdzona osobno, bo każda zamyka inny przypadek ``None``:

1. warstwa (``dormant_host_miss``) – 404 dla hosta, którego witryna należy do konkursu nieaktywnego
   albo jest aliasem konkursu bez tłumaczeń; **każda** domena, nie tylko subdomeny platformy,
2. mixiny ról (``RoleRequiredMixin``) – 404 dla każdego żądania bez konkursu, także pod witryną,
   która do żadnego konkursu nie należy (tam warstwa celowo przepuszcza).

Trzecia rzecz jest warunkiem wdrożenia: instalacja z jednym konkursem i bez własnej domeny
(nieznany host → witryna domyślna → jedyny konkurs) działa dokładnie jak dotąd.
"""

from __future__ import annotations

import pytest
from django.test import Client, RequestFactory

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.tenancy.aliases import CompetitionSiteAlias
from apps.tenancy.models import Competition
from apps.tenancy.resolution import dormant_host_miss, resolve_competition

from .conftest import HOST_A, HOST_B, make_site
from .factories import grant_membership

pytestmark = pytest.mark.django_db

PANEL = "/coordinator/"


def coordinator_of(competition):
    """Koordynator konkursu: globalna grupa (fabryka) **i** członkostwo – oba źródła roli naraz."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def client_on(host: str, user=None) -> Client:
    client = Client(HTTP_HOST=host, SERVER_NAME=host)
    if user is not None:
        client.force_login(user)
    return client


def deactivate(competition) -> None:
    Competition.objects.filter(pk=competition.pk).update(is_active=False)


# --- scenariusz z audytu --------------------------------------------------------------------------


def test_coordinator_of_b_under_the_domain_of_inactive_a_gets_404(competition, other_competition):
    """Panel i edycja konta pod domeną wyłączonego konkursu: 404, a nie panel na kontach instalacji."""
    deactivate(other_competition)
    coordinator = coordinator_of(competition)
    victim = UserFactory(email="ofiara@example.test")
    client = client_on(HOST_B, coordinator)

    assert client.get(PANEL).status_code == 404
    response = client.post(
        f"/coordinator/accounts/{victim.pk}/",
        {"email": "napastnik@example.test", "first_name": "X", "last_name": "Y"},
    )

    assert response.status_code == 404
    victim.refresh_from_db()
    assert victim.email == "ofiara@example.test"


def test_the_same_coordinator_keeps_the_panel_under_their_own_domain(competition, other_competition):
    deactivate(other_competition)

    assert client_on(HOST_A, coordinator_of(competition)).get(PANEL).status_code == 200


def test_public_pages_of_the_inactive_domain_are_404_too(competition, other_competition):  # noqa: ARG001
    """Warstwa odpowiada 404 przed widokiem – także anonimowi i na stronie głównej."""
    deactivate(other_competition)

    assert client_on(HOST_B).get("/").status_code == 404
    assert client_on(HOST_B).get("/login/").status_code == 404


# --- warstwa: dormant_host_miss -------------------------------------------------------------------


def test_the_rule_is_free_when_a_competition_is_resolved(competition, django_assert_num_queries):
    request = RequestFactory().get("/", HTTP_HOST=HOST_A)

    with django_assert_num_queries(0):
        assert dormant_host_miss(request, competition) is False


def test_an_alias_without_translations_is_404(competition, other_competition, settings):  # noqa: ARG001
    """Alias konkursu bez ``content_translations`` to konfiguracja w toku – nie strona bez konkursu."""
    from wagtail.models import Locale

    alias_site = make_site("en.inny.test", own_root=True)
    locale, _ = Locale.objects.get_or_create(language_code="en")
    CompetitionSiteAlias.objects.create(competition=other_competition, site=alias_site, locale=locale)
    request = RequestFactory().get("/", HTTP_HOST="en.inny.test")

    assert resolve_competition(request) is None
    assert dormant_host_miss(request, None) is True


def test_a_site_without_any_competition_is_left_to_the_role_gate(competition):
    """Witryna bez konkursu nie jest „czyjaś” – warstwa przepuszcza, panel zamyka mixin ról."""
    make_site("bez-konkursu.test", own_root=True)
    request = RequestFactory().get("/", HTTP_HOST="bez-konkursu.test")

    assert resolve_competition(request) is None
    assert dormant_host_miss(request, None) is False
    assert client_on("bez-konkursu.test", coordinator_of(competition)).get(PANEL).status_code == 404


def test_internal_addresses_are_not_touched(competition, other_competition):  # noqa: ARG001
    deactivate(other_competition)
    request = RequestFactory().get("/internal/tls-allowed", HTTP_HOST=HOST_B)

    assert dormant_host_miss(request, None) is False


# --- instalacja z jednym konkursem ----------------------------------------------------------------


def test_a_single_competition_without_its_own_domain_works_as_before(competition):
    """Nieznany host → witryna domyślna → jedyny konkurs. To nie jest przypadek ``None``."""
    request = RequestFactory().get("/", HTTP_HOST="cokolwiek.invalid")
    assert resolve_competition(request) == competition

    assert client_on("cokolwiek.invalid", coordinator_of(competition)).get(PANEL).status_code == 200
