"""Regresje z przeglądu Critica taska T-06. Jedno znalezisko = jeden test (albo kilka).

Cztery obszary:

1. ``GET /api/me/submissions/`` nie może oddać uczestnikowi ``FinalGrade.rationale``, gdy ocena
   zapadła w trybie ``THIRD_REVIEW`` – to dosłowna kopia ``Review.comment_internal`` trzeciego
   recenzenta (PROJEKT.md 2.4). Test sprawdza **treść** odpowiedzi, nie tylko nazwy pól.
2. ``Submission.objects.for_user`` w gałęzi komisji odwoławczej musi wykluczać rozwiązania
   z konfliktem interesów podzapytaniem, a nie negacją skorelowaną z pojedynczym wierszem recenzji.
3. ``finalize_unappealed`` idzie partiami, a beat nie przemiata etapów, w których nie ma nic do
   zrobienia.
4. Drobne: jedna definicja konfliktu, uczciwy ``download_url``, kolejka wyłącznie z rundą 1,
   limit długości jako walidacja (a nie ciche obcięcie), skład komisji pod ``PROTECT``
   i status reklamacji bez wartości, której nic nie ustawia.
"""

import pytest
from django.db.models import ProtectedError

from apps.accounts.models import GROUP_APPEALS, CommitteeStatus
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    ParticipantFactory,
)
from apps.appeals.models import (
    PENDING_STATUSES,
    Appeal,
    AppealDecision,
    AppealStatus,
)
from apps.appeals.services import (
    FINALIZE_BATCH_SIZE,
    decide_appeal,
    file_appeal,
    finalize_unappealed,
    stages_with_closed_appeal_window,
)
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_BLIND, ROUND_TIEBREAK, FinalGrade, GradeMethod
from apps.grading.services import submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, Submission, SubmissionStatus
from apps.submissions.serializers import GRADE_METHOD_APPEAL
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

from .conftest import graded_submission, round_one_reviews
from .factories import VALID_ARGUMENT, AppealsCommitteeMemberFactory

pytestmark = pytest.mark.django_db

APPEALS_URL = "/api/appeals/"
MY_SUBMISSIONS_URL = "/api/me/submissions/"

#: Treść, która nigdy nie ma prawa dojechać do uczestnika – komentarz wewnętrzny recenzenta.
SECRET_INTERNAL = "NOTATKA-WEWNETRZNA-Kwiatkowski-zawyzyl-o-dwa-punkty"


def filed(submission) -> Appeal:
    return file_appeal(submission.entry.participant.user, submission, VALID_ARGUMENT)


def appeals_only_member():
    """Członek komisji odwoławczej **bez** grupy ``reviewer``.

    Sztuczny, ale konieczny: profil z rejestracji ma obie role naraz, więc rozwiązanie recenzowane
    przez taką osobę i tak byłoby widoczne z gałęzi recenzenta. Dopiero konto bez tej grupy izoluje
    gałąź komisji odwoławczej w ``for_user`` i pokazuje, czy wykluczenie konfliktu w ogóle działa.
    """
    return CommitteeMemberFactory(
        user__groups=[GROUP_APPEALS],
        status=CommitteeStatus.ACTIVE,
        is_appeals_committee=True,
        district_verified=True,
    )


def third_review_grade(stage, *, participant=None, score: int = 5):
    """Rozwiązanie z oceną z rundy rozjemczej – ``FinalGrade.rationale`` to komentarz wewnętrzny."""
    participant = participant or ParticipantFactory()
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage, participant=participant),
        problem=ProblemFactory(stage=stage),
        status=SubmissionStatus.MODERATION,
    )
    review = ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory(), round=ROUND_TIEBREAK)
    submit_review(review, score, SECRET_INTERNAL, "Uzasadnienie napisane do uczestnika.")
    submission.refresh_from_db()
    return submission


# --- 1. rationale nie wycieka uczestnikowi ------------------------------------------------------


def test_third_review_rationale_is_never_serialized_to_participant(client, open_stage):
    """Komentarz wewnętrzny trzeciego recenzenta nie pojawia się nigdzie w ``me/submissions/``."""
    submission = third_review_grade(open_stage)
    grade = FinalGrade.objects.get(submission=submission)
    # Ślad dla koordynatora zostaje w bazie – chroni warstwa prezentacji, nie kasowanie danych.
    assert (grade.method, grade.rationale) == (GradeMethod.THIRD_REVIEW, SECRET_INTERNAL)

    client.force_authenticate(submission.entry.participant.user)
    response = client.get(MY_SUBMISSIONS_URL)

    assert response.status_code == 200
    assert SECRET_INTERNAL not in response.content.decode()
    latest = response.data[0]["latest"]
    assert latest["final_grade"]["score"] == 5
    assert latest["final_grade"]["method"] == GradeMethod.THIRD_REVIEW
    assert latest["final_grade"]["rationale"] is None


def test_consensus_rationale_is_hidden_but_appeal_justification_is_visible(client, open_stage):
    """Uzasadnienie widzi uczestnik wyłącznie po reklamacji – tam tekst jest pisany do niego."""
    submission = graded_submission(open_stage, score=2)
    FinalGrade.objects.filter(submission=submission).update(rationale=SECRET_INTERNAL)
    client.force_authenticate(submission.entry.participant.user)
    before = client.get(MY_SUBMISSIONS_URL)

    assert before.data[0]["latest"]["final_grade"]["rationale"] is None
    assert SECRET_INTERNAL not in before.content.decode()

    appeal = filed(submission)
    decide_appeal(appeal, AppealsCommitteeMemberFactory(), AppealStatus.ACCEPTED, 6, "Zarzut zasadny.")
    after = client.get(MY_SUBMISSIONS_URL)

    grade = after.data[0]["latest"]["final_grade"]
    assert (grade["score"], grade["method"]) == (6, GradeMethod.APPEAL)
    assert grade["rationale"] == "Zarzut zasadny."


def test_appeal_method_constant_matches_grading_choice():
    """Literał w ``apps.submissions`` (brak importu w drugą stronę) musi zgadzać się z ``GradeMethod``."""
    assert GRADE_METHOD_APPEAL == GradeMethod.APPEAL


# --- 2. widoczność rozwiązań dla komisji --------------------------------------------------------


def test_committee_branch_hides_own_reviewed_submission_and_keeps_the_rest(open_stage):
    """Konflikt interesów wycina konkretne rozwiązanie, a nie „cokolwiek, byle miało recenzje”.

    Praca z konfliktem ma tu **także cudzą** recenzję: to ten układ wywracał negację skorelowaną
    z wierszem recenzji zamiast ze zgłoszeniem (patrz komentarz w ``SubmissionQuerySet.for_user``).
    """
    member = appeals_only_member()
    own = graded_submission(open_stage)
    ReviewFactory(submission=own, reviewer=member, round=ROUND_BLIND, score=2)
    ReviewFactory(submission=own, reviewer=ActiveReviewerFactory(), round=ROUND_TIEBREAK, score=5)
    filed(own)
    other = graded_submission(open_stage)
    round_one_reviews(other)
    filed(other)

    visible = set(Submission.objects.for_user(member.user).values_list("pk", flat=True))

    assert other.pk in visible
    assert own.pk not in visible


def test_committee_branch_ignores_reviews_outside_conflicting_rounds(open_stage):
    """Recenzja spoza rund konfliktowych nie ukrywa reklamacji (jedna definicja konfliktu)."""
    member = appeals_only_member()
    submission = graded_submission(open_stage)
    ReviewFactory(submission=submission, reviewer=member, round=99, score=2)
    filed(submission)

    visible = set(Submission.objects.for_user(member.user).values_list("pk", flat=True))

    assert submission.pk in visible


# --- 3. finalizacja partiami --------------------------------------------------------------------


def bulk_provisional(stage, count: int) -> None:
    """``count`` rozwiązań w GRADED_PROVISIONAL jednym zapisem – kolejne wersje tego samego zadania.

    Konkurs wpisujemy tu wprost, bo ``bulk_create`` z definicji omija ``Submission.save()``, czyli
    jedyne miejsce, które kolumnę denormalizacyjną wypełnia samo. Bierzemy go z **etapu**, tak samo
    jak zrobiłby to zapis pojedynczy – nie z kontekstu, bo konkurs pracy wynika z etapu, w którym
    ją oddano.
    """
    entry = StageEntryFactory(stage=stage, participant=ParticipantFactory())
    problem = ProblemFactory(stage=stage)
    Submission.objects.bulk_create(
        Submission(
            competition_id=stage.edition.competition_id,
            entry=entry,
            problem=problem,
            version=version,
            status=SubmissionStatus.GRADED_PROVISIONAL,
        )
        for version in range(1, count + 1)
    )


def test_finalize_processes_large_stage_in_batches_without_id_lists(stage_after_window):
    """1200 rozwiązań → wszystkie FINAL, audyt to licznik i numer partii, a nie lista identyfikatorów."""
    bulk_provisional(stage_after_window, 1200)

    finalized = finalize_unappealed(stage_after_window)

    assert finalized == 1200
    assert not Submission.objects.filter(status=SubmissionStatus.GRADED_PROVISIONAL).exists()
    assert Submission.objects.filter(status=SubmissionStatus.FINAL).count() == 1200
    entries = list(AuditLog.objects.filter(action="submission.finalized"))
    assert len(entries) <= 3, f"{len(entries)} wpisów audytu na jedną finalizację to za dużo"
    assert sum(entry.diff["submissions"] for entry in entries) == 1200
    assert all("submission_ids" not in entry.diff for entry in entries)
    assert all(entry.diff["submissions"] <= FINALIZE_BATCH_SIZE for entry in entries)
    # Idempotencja nie może się zepsuć przy partiach.
    assert finalize_unappealed(stage_after_window) == 0


def test_stage_without_provisional_grades_is_not_selected(open_stage, stage_after_window):
    """Beat bierze wyłącznie etapy, w których faktycznie zostało coś do sfinalizowania."""
    assert list(stages_with_closed_appeal_window()) == []

    submission = graded_submission(stage_after_window, score=2)
    assert list(stages_with_closed_appeal_window()) == [stage_after_window]

    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.FINAL)
    assert list(stages_with_closed_appeal_window()) == []

    # Etap z otwartym oknem nie wchodzi, choćby miał komplet ocen wstępnych.
    graded_submission(open_stage, score=2)
    assert list(stages_with_closed_appeal_window()) == []


# --- 4. drobne: konflikt, link, runda, limity, skład, status ------------------------------------


def test_tiebreak_reviewer_has_the_same_conflict_as_round_one(client, open_stage):
    """Autor rundy 2 nie widzi reklamacji w kolejce i nie może jej rozstrzygnąć."""
    submission = graded_submission(open_stage)
    conflicted = AppealsCommitteeMemberFactory()
    ReviewFactory(submission=submission, reviewer=conflicted, round=ROUND_TIEBREAK, score=5)
    appeal = filed(submission)
    client.force_authenticate(conflicted.user)

    listing = client.get(APPEALS_URL)
    decision = client.post(
        f"/api/appeals/{appeal.pk}/decide/",
        {"status": AppealStatus.REJECTED, "justification": "Bez zmian."},
        format="json",
    )

    assert listing.data == []
    assert decision.status_code == 403
    assert decision.data["code"] == "CONFLICT_OF_INTEREST"
    assert not AppealDecision.objects.exists()


def test_download_url_appears_only_for_a_clean_file(client, open_stage):
    """Link do pobrania obiecuje tylko to, co da się pobrać – inaczej ``None``."""
    submission = graded_submission(open_stage)
    submission_file = SubmissionFileFactory(submission=submission, av_status=AvStatus.PENDING)
    filed(submission)
    client.force_authenticate(AppealsCommitteeMemberFactory().user)

    pending = client.get(APPEALS_URL).data[0]
    assert pending["download_url"] is None
    assert pending["file_available"] is False

    submission_file.av_status = AvStatus.CLEAN
    submission_file.save(update_fields=["av_status"])
    clean = client.get(APPEALS_URL).data[0]

    assert clean["download_url"] == f"/api/submissions/{submission.pk}/download/"
    assert clean["file_available"] is True


def test_queue_shows_round_one_reviews_only(client, open_stage):
    """Kolejka to obie oceny rundy 1 – runda rozjemcza nie dokłada tam swojego komentarza."""
    submission = graded_submission(open_stage)
    round_one_reviews(submission)
    ReviewFactory(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        round=ROUND_TIEBREAK,
        score=5,
        comment_internal=SECRET_INTERNAL,
    )
    filed(submission)
    client.force_authenticate(AppealsCommitteeMemberFactory().user)

    response = client.get(APPEALS_URL)

    item = response.data[0]
    assert [review["round"] for review in item["reviews"]] == [ROUND_BLIND, ROUND_BLIND]
    assert SECRET_INTERNAL not in response.content.decode()


def test_too_long_argument_is_rejected_instead_of_truncated(client, open_stage):
    """Uzasadnienie ponad limit to 400, a nie cichy zapis obciętego tekstu."""
    submission = graded_submission(open_stage)
    too_long = "a" * 20_001
    client.force_authenticate(submission.entry.participant.user)

    response = client.post(f"/api/submissions/{submission.pk}/appeal/", {"argument": too_long}, format="json")

    assert response.status_code == 400
    assert not Appeal.objects.exists()
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL

    # Ta sama reguła obowiązuje poza HTTP – serwis nie obcina na własną rękę.
    with pytest.raises(DomainError) as excinfo:
        file_appeal(submission.entry.participant.user, submission, too_long)
    assert excinfo.value.machine_code == "ARGUMENT_TOO_LONG"


def test_too_long_justification_is_rejected_instead_of_truncated(client, open_stage):
    """Uzasadnienie decyzji ponad limit też jest odrzucane – decyzja nie może być urwana w połowie."""
    submission = graded_submission(open_stage, score=2)
    appeal = filed(submission)
    member = AppealsCommitteeMemberFactory()
    client.force_authenticate(member.user)

    response = client.post(
        f"/api/appeals/{appeal.pk}/decide/",
        {"status": AppealStatus.ACCEPTED, "new_score": 6, "justification": "b" * 20_001},
        format="json",
    )

    assert response.status_code == 400
    assert not AppealDecision.objects.exists()
    with pytest.raises(DomainError) as excinfo:
        decide_appeal(appeal, member, AppealStatus.REJECTED, None, "c" * 20_001)
    assert excinfo.value.machine_code == "JUSTIFICATION_TOO_LONG"


def test_committee_member_of_a_decision_cannot_be_deleted(open_stage):
    """Skład komisji jest chroniony: usunięcie członka odbija się o ``PROTECT``, a nie czyści skład."""
    submission = graded_submission(open_stage, score=2)
    appeal = filed(submission)
    member = AppealsCommitteeMemberFactory()
    decision = decide_appeal(appeal, member, AppealStatus.REJECTED, None, "Zarzut niezasadny.")

    with pytest.raises(ProtectedError):
        member.delete()

    assert list(decision.committee.all()) == [member]
    assert decision.committee_seats.count() == 1


def test_appeal_status_has_no_unreachable_value():
    """``IN_REVIEW`` nie było ustawiane przez żadną ścieżkę – nie ma go w wyborze ani w oczekujących."""
    assert "IN_REVIEW" not in dict(AppealStatus.choices)
    assert PENDING_STATUSES == (AppealStatus.OPEN,)
