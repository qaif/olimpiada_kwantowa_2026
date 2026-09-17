"""T3: wyniki i dokumenty należą do konkursu.

Dwa miejsca są tu istotniejsze niż reszta:

- **publiczna tabela wyników** (``/api/public/results/<stage_id>/``) nie wymaga logowania, więc bez
  zakresu byłaby najtańszą drogą do cudzych wyników – wystarczy przejechać identyfikatory etapów,
- **adres weryfikacji dokumentu** w kodzie QR bierze się z konkursu **edycji dokumentu**, a nie
  z kontekstu przebiegu: dyplom wystawiony w konkursie A ma odsyłać pod domenę A także wtedy, gdy
  składa go przebieg wsadowy konkursu B.

Wydanie D dokłada do tego dwa miejsca: **prefiks numeru** dokumentu, który przenosi się ze stałej
modułu do ``Competition.certificate_prefix``, oraz **szablon graficzny**, który dostaje własną
kolumnę konkursu (jego ``edition`` bywa puste, więc drogi przez edycję po prostu nie ma).

Czego ten plik nie dotyka: anonimizacji ani k-anonimowości ``INITIALS_SCHOOL``. Numer i kod
zostają unikalne **globalnie** i to jest świadome – strona weryfikacji jest publiczna i ma działać
bez wskazania konkursu (§ 3.3).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory, host_of
from apps.results.certificates import (
    _next_number,
    number_prefix,
    resolve_template,
    supervisors_with_participants,
    verification_url,
)
from apps.results.models import (
    CERTIFICATE_NUMBER_PREFIX,
    Certificate,
    CertificateTemplate,
    ResultsPublication,
)
from apps.results.services import results_for_participant

from .factories import CertificateFactory, CertificateTemplateFactory, ResultsPublicationFactory

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
        # Własna kolumna, a nie droga przez edycję: ``edition`` bywa puste („szablon dla
        # wszystkich edycji”), więc taki wiersz nie doszedłby do żadnego konkursu (§ 3.2).
        (CertificateTemplate, "competition"),
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


def test_template_for_all_editions_belongs_to_one_competition(competition, other_competition):
    """„Szablon dla wszystkich edycji” jest szablonem wszystkich edycji **jednego** konkursu.

    To jest przypadek, dla którego ten model dostał w wydaniu D własną kolumnę. Dopóki zakres
    szedł przez ``edition``, wiersz bez edycji nie należał do nikogo – a ``resolve_template``
    kończy dopasowanie właśnie na nim, więc winieta organizatora A trafiłaby na dyplom
    organizatora B bez żadnego kliknięcia.
    """
    template_b = CertificateTemplateFactory(competition=other_competition)

    assert list(CertificateTemplate.objects.for_competition(competition)) == []
    assert list(CertificateTemplate.objects.for_competition(other_competition)) == [template_b]


def test_template_of_another_competition_is_not_used_for_our_certificate(competition, other_competition):
    """Dopasowanie szablonu idzie przez konkurs **edycji dokumentu**, a nie przez kontekst."""
    CertificateTemplateFactory(competition=other_competition)
    certificate_a = CertificateFactory(competition=competition)

    assert resolve_template(certificate_a.kind, certificate_a.edition) is None


def test_certificate_number_takes_the_prefix_of_its_competition(competition, other_competition):
    """Numer dokumentu zaczyna się prefiksem konkursu (§ 3.3), a Konkurs #1 zostaje przy ``OK``.

    Prefiks jest tym, co widać na papierze i w pismach: dwa konkursy jednej instalacji nie mogą
    wystawiać dokumentów o numerach nierozróżnialnych na wydruku. Unikalność numeru zostaje przy
    tym globalna, bo to prefiks – a nie osobna numeracja – rozdziela serie.
    """
    other_competition.certificate_prefix = "FIZ"
    other_competition.save(update_fields=["certificate_prefix"])

    assert competition.certificate_prefix == "OK"
    assert number_prefix(competition) == "OK"
    assert number_prefix(other_competition) == "FIZ"
    assert _next_number(2026, other_competition).startswith("FIZ/2026/")
    # Instalacja bez konkursów zachowuje się jak przed etapem 1 – stała z modelu.
    assert number_prefix(None) == CERTIFICATE_NUMBER_PREFIX


def test_supervisor_certificates_do_not_cross_to_a_namesake_in_another_competition(
    competition, other_competition
):
    """Lista opiekunów do zaświadczeń dopasowuje się **po adresie e-mail** – i to jest pułapka.

    Ten sam nauczyciel bywa opiekunem w dwóch olimpiadach z tego samego adresu, więc bez
    zawężenia do konkursu edycji zaświadczenie z konkursu A trafiłoby na jego profil w konkursie B
    (o tym, który wygra, decydowałaby kolejność alfabetyczna). Dopasowanie po adresie zostaje –
    zmienia się zbiór, w którym szukamy.
    """
    from apps.accounts.models import SchoolSupervisor
    from apps.accounts.tests.factories import UserFactory
    from apps.competitions.tests.factories import StageEntryFactory, StageFactory

    teacher = UserFactory(first_name="Anna", last_name="Nauczycielska")
    SchoolSupervisor.objects.create(user=teacher, school="XIV LO", competition=other_competition)
    stage_a = StageFactory(competition=competition)
    StageEntryFactory(
        stage=stage_a,
        competition=competition,
        participant__competition=competition,
        participant__supervisor_email=teacher.email,
    )

    assert supervisors_with_participants(stage_a.edition) == []


def test_verification_url_points_at_the_domain_of_the_issuing_competition(competition, other_competition):
    """Kod QR na dyplomie prowadzi pod domenę organizatora, który go wystawił.

    Dotąd stał tu odwrót do ``settings.SITE_URL`` – ustawienia, którego ta instalacja nie definiuje
    – więc adres w kodzie QR był **względny**, czyli nie prowadził nigdzie.
    """
    link = verification_url("ABC123", other_competition)

    assert link == f"https://{host_of(other_competition)}/dyplomy/ABC123/"
    assert host_of(competition) not in link
