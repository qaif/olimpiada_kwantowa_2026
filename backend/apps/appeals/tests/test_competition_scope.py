"""T3: reklamacja należy do konkursu – przez pracę, której dotyczy.

Procedura odwoławcza ma w tej zmianie jedną cechę szczególną: jej queryset zawęża się **trzema**
warunkami i każdy odpowiada na inne pytanie – konkurs („czyje to sprawy”), ``pending`` („czy jest
co rozstrzygać”) i konflikt interesów („czy ta osoba może”). Kolejność jest regułą (§ 3.5): zakres
idzie pierwszy, bo jest własnością, a nie uprawnieniem.

Rozróżnienia 403/404 ta zmiana nie rusza: konflikt interesów zostaje przy 403
(``CONFLICT_OF_INTEREST`` – autor recenzji zna numer sprawy i ma dostać jednoznaczną odmowę),
a cudzy konkurs daje 404, bo istnienie tamtej sprawy nie jest informacją dla tej komisji.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.appeals.models import Appeal, AppealDecision, AppealDecisionCommitteeMember, AppealStatus
from apps.appeals.services import appeals_for_participant, appeals_queue
from apps.appeals.tasks import finalize_closed_appeal_windows
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .factories import VALID_ARGUMENT, AppealFactory, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (Appeal, "submission__competition"),
        (AppealDecision, "appeal__submission__competition"),
        (AppealDecisionCommitteeMember, "decision__appeal__submission__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    """Ścieżka jest deklaracją modelu (§ 3.5) i ma zgadzać się z tabelą dróg z dokumentu."""
    assert model.objects.all().competition_path == path


def test_appeals_of_another_competition_are_invisible(competition, other_competition):
    appeal_b = AppealFactory(competition=other_competition)

    assert list(Appeal.objects.for_competition(competition)) == []
    assert list(Appeal.objects.for_competition(other_competition)) == [appeal_b]


def test_committee_queue_is_scoped_to_the_competition(competition, other_competition):
    """Komisja odwoławcza konkursu A nie ma w kolejce ani jednej sprawy konkursu B."""
    member = AppealsCommitteeMemberFactory(competition=competition)
    mine = AppealFactory(competition=competition)
    theirs = AppealFactory(competition=other_competition)

    queue = appeals_queue(member, competition)

    assert list(queue) == [mine]
    assert theirs not in queue


def test_participant_appeals_are_scoped_to_the_competition(competition, other_competition):
    """Reklamacja jest sprawą prowadzoną przed **konkretnym** organizatorem (§ 3.3)."""
    appeal_b = AppealFactory(competition=other_competition)
    user = appeal_b.filed_by.user

    assert list(appeals_for_participant(user, other_competition)) == [appeal_b]
    assert list(appeals_for_participant(user, competition)) == []


def test_deciding_an_appeal_of_another_competition_is_not_found(api_for, competition, other_competition):
    """404, nie 403: decyzja w cudzej sprawie nie jest odmową uprawnienia, tylko brakiem sprawy."""
    member = AppealsCommitteeMemberFactory(competition=competition)
    appeal_b = AppealFactory(competition=other_competition)

    response = api_for(competition, member.user).post(
        f"/api/appeals/{appeal_b.pk}/decide/",
        {"status": AppealStatus.REJECTED, "new_score": None, "justification": "x" * 60},
        format="json",
    )

    assert response.status_code == 404
    appeal_b.refresh_from_db()
    assert appeal_b.status == AppealStatus.OPEN


def test_filing_an_appeal_on_a_submission_of_another_competition_is_not_found(
    api_for, competition, other_competition
):
    """Praca cudzego konkursu nie istnieje dla tego adresu – ta sama reguła, co przy pobraniu."""
    participant = AppealFactory(competition=competition).filed_by
    submission_b = SubmissionFactory(
        competition=other_competition, status=SubmissionStatus.GRADED_PROVISIONAL
    )

    response = api_for(competition, participant.user).post(
        f"/api/submissions/{submission_b.pk}/appeal/", {"argument": VALID_ARGUMENT}, format="json"
    )

    assert response.status_code == 404
    assert not Appeal.objects.filter(submission=submission_b).exists()


def test_finalization_covers_every_competition(competition, other_competition):
    """Zadanie obchodzi oba konkursy, każdy w jego kontekście.

    Zawężenie per konkurs jest po to, żeby finalizacja i powiadomienia, które z niej wynikają,
    powstawały w kontekście właściciela – a nie po to, żeby czegokolwiek nie sfinalizować.
    """
    # Oś czasu etapu przesuwamy w całości: ``opens_at < deadline_at <= review_deadline_at <=
    # appeal_window_opens_at < appeal_window_closes_at`` to pięć constraintów w bazie, więc
    # cofnięcie samego zamknięcia okna reklamacji nie przeszłoby przez zapis.
    past = timezone.now() - timedelta(days=1)
    expected = {}
    for owner in (competition, other_competition):
        stage = StageFactory(
            competition=owner,
            opens_at=past - timedelta(days=30),
            deadline_at=past - timedelta(days=20),
            review_deadline_at=past - timedelta(days=10),
            appeal_window_opens_at=past - timedelta(days=5),
            appeal_window_closes_at=past,
        )
        SubmissionFactory(
            competition=owner,
            entry=StageEntryFactory(competition=owner, stage=stage),
            status=SubmissionStatus.GRADED_PROVISIONAL,
        )
        expected[str(stage.pk)] = 1

    assert finalize_closed_appeal_windows() == expected
