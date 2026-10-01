"""Wzorcówka zadania i uwagi dla recenzentów: kto to widzi, a kto nie ma prawa.

Reguła jest ostrzejsza niż przy treści zadania: treść staje się jawna po ``opens_at``, a wzorcówka
nie staje się jawna nigdy. Wydaje ją wyłącznie widok panelu – aktywnemu członkowi komitetu albo
koordynatorowi. Uczestnik nie ma do niej żadnej drogi, także w trakcie zawodów.
"""

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    PendingReviewerFactory,
)
from apps.competitions.services import update_problem
from apps.competitions.storage import PRIVATE_MEDIA_ALIAS
from apps.competitions.tests.factories import ProblemFactory
from apps.tenancy.tests.factories import enforce_memberships, grant_membership

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 wzorcowe rozwiazanie"


@pytest.fixture
def problem(stage):
    created = ProblemFactory(stage=stage, number=1)
    created.model_solution_pdf.save("wzorcowka.pdf", SimpleUploadedFile("wzorcowka.pdf", PDF))
    return created


@pytest.fixture
def url(problem):
    return reverse("web:problem-model-solution", kwargs={"pk": problem.pk})


def test_model_solution_lands_on_the_private_storage(problem):
    """Ten sam prywatny bucket, co treść zadania – publicznego adresu ma nie być w ogóle."""
    assert storages[PRIVATE_MEDIA_ALIAS].exists(problem.model_solution_pdf.name)
    assert not storages["default"].exists(problem.model_solution_pdf.name)


def test_active_reviewer_downloads_the_model_solution(url):
    client = Client()
    client.force_login(ActiveReviewerFactory().user)

    response = client.get(url)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF


def test_coordinator_downloads_the_model_solution(url):
    client = Client()
    client.force_login(CoordinatorFactory())

    assert client.get(url).status_code == 200


def test_participant_is_refused(url):
    client = Client()
    client.force_login(ParticipantFactory().user)

    assert client.get(url).status_code == 403


def test_pending_committee_member_is_refused(url):
    """Zatwierdzenia przez koordynatora nie da się ominąć – to ta sama bramka, co przy pracach."""
    client = Client()
    client.force_login(PendingReviewerFactory().user)

    assert client.get(url).status_code == 403


def test_anonymous_is_sent_to_the_login_page(url):
    response = Client().get(url)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_problem_without_model_solution_is_404(stage):
    client = Client()
    client.force_login(ActiveReviewerFactory().user)
    empty = ProblemFactory(stage=stage, number=7)

    url = reverse("web:problem-model-solution", kwargs={"pk": empty.pk})
    assert client.get(url).status_code == 404


def test_committee_member_of_another_competition_is_refused(url, competition, other_competition, client_for):
    """Poprawka po audycie izolacji (01.10.2026): profil komitetu konkursu B nie otwiera wzorcówki A.

    Przy wyłączonym ``memberships_enforced`` grupa ``reviewer`` jest globalna, więc do poprawki
    wystarczał aktywny profil komitetu **dowolnego** konkursu – klucz odpowiedzi w trakcie zawodów
    A trafiał do członka komitetu B.
    """
    client = client_for(competition)
    client.force_login(ActiveReviewerFactory(competition=other_competition).user)

    assert client.get(url).status_code == 403


def test_coordinator_of_another_competition_is_refused_with_memberships_on(
    url, competition, other_competition, client_for
):
    """Globalna grupa ``coordinator`` nie jest rolą w konkursie z włączonym ``memberships_enforced``."""
    enforce_memberships(competition)
    coordinator_b = CoordinatorFactory()
    grant_membership(coordinator_b, other_competition, "coordinator")
    coordinator_a = CoordinatorFactory()
    grant_membership(coordinator_a, competition, "coordinator")

    refused = client_for(competition)
    refused.force_login(coordinator_b)
    allowed = client_for(competition)
    allowed.force_login(coordinator_a)

    assert refused.get(url).status_code == 403
    assert allowed.get(url).status_code == 200


def test_coordinator_service_stores_the_model_solution_and_reviewer_notes(stage):
    problem = ProblemFactory(stage=stage, number=3)
    coordinator = CoordinatorFactory()

    updated = update_problem(
        problem,
        coordinator,
        model_solution=SimpleUploadedFile("klucz.pdf", PDF),
        reviewer_notes="Dowód przez indukcję bez podstawy uznajemy za pełny.",
    )

    assert updated.model_solution_pdf
    assert "indukcję" in updated.reviewer_notes
