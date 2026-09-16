"""Piaskownica treningowa w panelu uczestnika: zgłoszenie, upload i brak terminu.

Trening jest prostopadły do kalendarza zawodów: karta pokazuje się obok etapu bieżącego, niezależnie
od tego, który etap właśnie trwa. Ścieżka uploadu jest **ta sama**, co w zawodach (ten sam widok,
ten sam fragment karty zadania) – to właśnie ona ma być przetestowana na produkcji.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import TRAINING_DEADLINE, Problem, StageEntry, StageKind
from apps.competitions.tests.factories import (
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)
from apps.submissions.models import Submission
from apps.submissions.tests.factories import pdf_upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def training_stage(edition):
    """Etap treningowy taki, jaki zostawia ``seed_training_problems``: otwarty, terminy na 2099."""
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        opens_at=timezone.now() - timedelta(hours=1),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
    )
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


@pytest.fixture
def training_problems(training_stage):
    return [
        Problem.objects.create(stage=training_stage, number=1, title="Stan kubitu i pomiar (proste)"),
        Problem.objects.create(stage=training_stage, number=2, title="Splątanie z dwóch bramek (średnie)"),
    ]


def test_dashboard_offers_training_registration(web_client, participant, training_stage, elim_stage):
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Zadania treningowe" in content
    assert f"/me/stages/{training_stage.pk}/register/" in content
    # Bez terminu znaczy bez daty: wartownik z 2099 nie ma prawa pokazać się w panelu.
    assert str(TRAINING_DEADLINE.year) not in content


def test_participant_registers_to_training_and_sees_upload(
    web_client, participant, training_stage, training_problems, elim_stage
):
    web_client.force_login(participant.user)

    response = web_client.post(f"/me/stages/{training_stage.pk}/register/", follow=True)
    content = response.content.decode()

    assert StageEntry.objects.filter(participant=participant, stage=training_stage).exists()
    for problem in training_problems:
        assert problem.title in content
    assert f"/me/stages/{training_stage.pk}/problems/1/upload/" in content


def test_training_upload_creates_submission(
    web_client, participant, training_stage, training_problems, elim_stage
):
    web_client.force_login(participant.user)
    web_client.post(f"/me/stages/{training_stage.pk}/register/")
    entry = StageEntry.objects.get(participant=participant, stage=training_stage)

    response = web_client.post(
        f"/me/stages/{training_stage.pk}/problems/1/upload/",
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert "wersja 1" in response.content.decode()
    assert Submission.objects.filter(entry=entry, problem=training_problems[0]).count() == 1


def test_competition_stage_card_still_counts_down(web_client, participant, entry, problems, training_stage):
    """Trening nie zabiera etapowi zawodów odliczania – obie karty stoją na pulpicie naraz."""
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "data-deadline=" in content
    assert "Zadania treningowe" in content


def test_dashboard_without_training_stage_has_no_section(web_client, participant, entry, problems):
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    assert "Zadania treningowe" not in content


def test_coordinator_dashboard_shows_no_deadline_instead_of_2099(web_client, coordinator, training_stage):
    """Karta etapu w panelu koordynatora nie wypisuje daty-wartownika.

    Rok 2099 w rubryce „Deadline oddania” wyglądałby jak termin, którego ktoś zapomniał poprawić –
    a jest brakiem terminu. Test pilnuje też, że karta w ogóle się renderuje: koordynator widzi
    wszystkie etapy edycji, więc trening przechodzi przez ten sam szablon, co zawody.
    """
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Zadania treningowe" in content
    assert "bez terminu" in content
    assert str(TRAINING_DEADLINE.year) not in content
