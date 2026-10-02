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
from apps.appeals.services import (
    appeals_committee_profile,
    appeals_for_participant,
    appeals_queue,
    decide_appeal,
)
from apps.appeals.tasks import finalize_closed_appeal_windows
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory, host_of
from apps.core.api import DomainError
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import enforce_memberships, grant_membership

from .factories import VALID_ARGUMENT, AppealFactory, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_for(settings):
    """Klient DRF pod domeną wskazanego konkursu – reguła stoi w ``scope_helpers``."""
    return api_client_factory(settings)


def client_for_html(competition, user):
    """Klient panelu HTML pod domeną konkursu, zalogowany bez formularza (jak ``api_for``)."""
    from django.test import Client

    host = host_of(competition)
    client = Client(HTTP_HOST=host, SERVER_NAME=host)
    client.force_login(user)
    return client


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


# --- komisja innego konkursu (poprawka po audycie izolacji, 01.10.2026) ----------------------------
#
# Profil komitetu jest jeden na konto i ma własny konkurs, a grupa ``appeals`` jest przy wyłączonym
# ``memberships_enforced`` globalna. Do poprawki członek komisji konkursu B przechodził bramkę komisji
# pod adresem konkursu A z profilem B: widział kolejkę spraw A, ich pliki i mógł je rozstrzygać.
# Każdy test sprawdza oba stany przełącznika – przy włączonym rolę w B daje członkostwo.


@pytest.fixture(params=[False, True], ids=["memberships_off", "memberships_on"])
def committee_b(request, competition, other_competition):
    """Członek komisji odwoławczej konkursu B; przy włączonej fladze – z członkostwami w B."""
    if request.param:
        enforce_memberships(competition)
        enforce_memberships(other_competition)
    member = AppealsCommitteeMemberFactory(competition=other_competition)
    for role in ("reviewer", "appeals"):
        grant_membership(member.user, other_competition, role)
    return member


def test_committee_of_another_competition_is_refused_at_the_gate(api_for, competition, committee_b):
    appeal = AppealFactory(competition=competition)
    client = api_for(competition, committee_b.user)

    assert client.get("/api/appeals/").status_code == 403
    response = client.post(
        f"/api/appeals/{appeal.pk}/decide/",
        {"status": AppealStatus.REJECTED, "new_score": None, "justification": "x" * 60},
        format="json",
    )
    assert response.status_code == 403
    appeal.refresh_from_db()
    assert appeal.status == AppealStatus.OPEN
    # Panel HTML ma tę samą bramkę (``AppealsCommitteeRequiredMixin``).
    html = client_for_html(competition, committee_b.user)
    assert html.get("/appeals/").status_code == 403


def test_committee_profile_of_another_competition_is_not_a_profile_here(competition, committee_b):
    assert appeals_committee_profile(committee_b.user, competition) is None
    assert appeals_committee_profile(committee_b.user, committee_b.competition) == committee_b
    # Kolejka dla takiego profilu jest pusta, nawet gdyby ktoś zawołał ją z pominięciem bramki.
    AppealFactory(competition=competition)
    assert list(appeals_queue(committee_b, competition)) == []


def test_decision_by_a_member_of_another_competition_is_refused_by_the_service(competition, committee_b):
    """Serwis nie polega na bramce widoku: decyzja cudzej komisji to 403, a sprawa zostaje otwarta."""
    appeal = AppealFactory(competition=competition)

    with pytest.raises(DomainError) as refused:
        decide_appeal(appeal, committee_b, AppealStatus.REJECTED, None, "x" * 60)

    assert refused.value.machine_code == "NOT_APPEALS_COMMITTEE"
    appeal.refresh_from_db()
    assert appeal.status == AppealStatus.OPEN
    assert not AppealDecision.objects.filter(appeal=appeal).exists()


def test_appealed_work_is_not_visible_to_the_committee_of_another_competition(competition, committee_b):
    """Gałąź komisji w ``Submission.objects.for_user`` – ta sama reguła, co bramka."""
    appeal = AppealFactory(competition=competition)

    assert list(Submission.objects.for_user(committee_b.user, competition)) == []
    mine = AppealsCommitteeMemberFactory(competition=competition)
    grant_membership(mine.user, competition, "appeals")
    assert list(Submission.objects.for_user(mine.user, competition)) == [appeal.submission]
