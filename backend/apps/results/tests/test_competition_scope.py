"""T3: wyniki i dokumenty należą do konkursu.

Dwa miejsca są tu istotniejsze niż reszta:

- **publiczna tabela wyników** (``/api/public/results/<stage_id>/``) nie wymaga logowania, więc bez
  zakresu byłaby najtańszą drogą do cudzych wyników – wystarczy przejechać identyfikatory etapów,
- **adres weryfikacji dokumentu** w kodzie QR bierze się z konkursu **edycji dokumentu**, a nie
  z kontekstu przebiegu: dyplom wystawiony w konkursie A ma odsyłać pod domenę A także wtedy, gdy
  składa go przebieg wsadowy konkursu B.

Czego ten plik nie dotyka: anonimizacji, k-anonimowości ``INITIALS_SCHOOL`` i numeracji dyplomów.
Numer i kod zostają unikalne **globalnie** i to jest świadome – strona weryfikacji jest publiczna
i ma działać bez wskazania konkursu (§ 3.3).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory, host_of
from apps.results.certificates import verification_url
from apps.results.models import Certificate, CertificateTemplate, ResultsPublication
from apps.results.services import results_for_participant

from .factories import CertificateFactory, ResultsPublicationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (ResultsPublication, "stage__edition__competition"),
        (Certificate, "edition__competition"),
        (CertificateTemplate, "edition__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    """Ścieżka jest deklaracją modelu (§ 3.5) i ma zgadzać się z tabelą dróg z dokumentu."""
    assert model.objects.all().competition_path == path


def test_publications_and_certificates_of_another_competition_are_invisible(competition, other_competition):
    publication_b = ResultsPublicationFactory(competition=other_competition)
    certificate_b = CertificateFactory(competition=other_competition)

    assert list(ResultsPublication.objects.for_competition(competition)) == []
    assert list(Certificate.objects.for_competition(competition)) == []
    assert list(ResultsPublication.objects.for_competition(other_competition)) == [publication_b]
    assert list(Certificate.objects.for_competition(other_competition)) == [certificate_b]


def test_public_results_of_another_competition_are_not_found(api_for, competition, other_competition):
    """Tabela jest publiczna, ale publiczna **w swoim konkursie**. 404, nie 403 – i bez logowania."""
    stage_b = StageFactory(competition=other_competition, results_published_at=timezone.now())
    publication_b = ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    ours = api_for(competition).get(f"/api/public/results/{publication_b.stage_id}/")
    theirs = api_for(other_competition).get(f"/api/public/results/{publication_b.stage_id}/")

    assert ours.status_code == 404
    assert theirs.status_code == 200


def test_computing_results_of_a_stage_of_another_competition_is_not_found(
    api_for, competition, other_competition
):
    """Przeliczenie wyników zapisuje ``StageEntry.total_points`` – na cudzym etapie ma dać 404."""
    stage_b = StageFactory(competition=other_competition)

    response = api_for(competition, CoordinatorFactory()).post(f"/api/stages/{stage_b.pk}/results/compute/")

    assert response.status_code == 404


def test_participant_results_are_scoped_to_the_competition(competition, other_competition):
    """Uczeń startujący w dwóch olimpiadach widzi pod każdą domeną wyniki tej jednej (§ 3.3)."""
    participant_b = ParticipantFactory(competition=other_competition)

    assert results_for_participant(participant_b.user, competition) == []


def test_verification_url_points_at_the_domain_of_the_issuing_competition(competition, other_competition):
    """Kod QR na dyplomie prowadzi pod domenę organizatora, który go wystawił.

    Dotąd stał tu odwrót do ``settings.SITE_URL`` – ustawienia, którego ta instalacja nie definiuje
    – więc adres w kodzie QR był **względny**, czyli nie prowadził nigdzie.
    """
    link = verification_url("ABC123", other_competition)

    assert link == f"https://{host_of(other_competition)}/dyplomy/ABC123/"
    assert host_of(competition) not in link
