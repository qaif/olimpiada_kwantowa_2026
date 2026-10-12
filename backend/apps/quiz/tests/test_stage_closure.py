"""Test online a ręczne „Zamknij etap” i ogłoszone wyniki – audyt bezpieczeństwa 10.10.2026, S3.

Do audytu ``apps/quiz`` nie znało ``Stage.closed_at``: po zamknięciu etapu (np. po wycieku pytań)
uczestnik dalej zaczynał i kończył podejście, które wchodziło do ``stage_scores``. Upload plików
w tej samej sytuacji odmawiał. Testy są negatywne – sprawdzają, że po zamknięciu nic już nie wchodzi.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.competitions.models import Stage, StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.api import DomainError
from apps.quiz import services
from apps.quiz.forms import QuizSettingsForm
from apps.quiz.models import AttemptStatus, QuizAnswer
from apps.quiz.tests.factories import QuizFactory, choice_question
from apps.results.models import ResultsPublication

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiz():
    created = QuizFactory()
    choice_question(created)
    return created


@pytest.fixture
def entry(quiz):
    return StageEntryFactory(stage=quiz.stage, status=StageEntryStatus.REGISTERED)


def _close_stage(quiz, *, at=None):
    """Zamknięcie etapu z pominięciem obiektu w ręku testu – tak, jak robi to inne żądanie."""
    Stage.objects.filter(pk=quiz.stage_id).update(closed_at=at or timezone.now())


#: Chwila „po zamknięciu i po tolerancji sieciowej” (``SUBMIT_GRACE_SECONDS`` = 30 s).
LATER = timedelta(minutes=1)


def _answer_payload(quiz):
    question = quiz.questions.first()
    return {str(question.pk): {"options": [question.options.filter(is_correct=True).first().pk]}}


def test_start_is_refused_after_the_stage_was_closed(quiz, entry):
    _close_stage(quiz)

    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)

    assert exc.value.machine_code == "QUIZ_CLOSED"
    assert not quiz.attempts.exists()


def test_answers_are_refused_and_the_attempt_closed_after_the_stage_was_closed(quiz, entry):
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _close_stage(quiz)

    with pytest.raises(DomainError) as exc:
        services.save_answers(attempt=attempt, answers=_answer_payload(quiz), now=timezone.now() + LATER)

    assert exc.value.machine_code == "QUIZ_ATTEMPT_EXPIRED"
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    assert not QuizAnswer.objects.filter(attempt=attempt).exists()


def test_an_answer_in_flight_at_the_closure_still_gets_the_network_tolerance(quiz, entry):
    """Tolerancja sieciowa liczy się od zamknięcia – kliknięcie sprzed zamknięcia nie przepada."""
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _close_stage(quiz)

    saved = services.save_answers(attempt=attempt, answers=_answer_payload(quiz))

    assert saved == 1


def test_submit_after_closure_grades_only_what_was_saved_before(quiz, entry):
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers=_answer_payload(quiz))
    closed_at = timezone.now()
    _close_stage(quiz, at=closed_at)

    services.submit_attempt(attempt=attempt, now=closed_at + LATER)

    attempt.refresh_from_db()
    # Zamknięcie domyka podejście jako przeterminowane, ze stemplem chwili zamknięcia.
    assert attempt.status == AttemptStatus.EXPIRED
    assert attempt.submitted_at <= closed_at
    assert attempt.score == 1


def test_returning_to_an_open_attempt_after_closure_does_not_reopen_it(quiz, entry):
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _close_stage(quiz)

    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry, now=timezone.now() + LATER)

    assert exc.value.machine_code == "QUIZ_CLOSED"
    # Odmowa wycofuje transakcję startu razem z domknięciem (tak samo jak po terminie podejścia),
    # ale podejście i tak nie przyjmie już ani jednej odpowiedzi, a domyka je ``finalise_overdue``
    # wołane przez stronę startową i przeliczenie wyników.
    attempt.refresh_from_db()
    with pytest.raises(DomainError):
        services.save_answers(attempt=attempt, answers=_answer_payload(quiz), now=timezone.now() + LATER)
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED


def test_finalise_closes_running_attempts_of_a_closed_stage_before_their_deadline(quiz, entry):
    """Przeliczenie wyników woła ``finalise_overdue`` – podejście z zamkniętego etapu nie czeka."""
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    assert attempt.deadline_at > timezone.now() + timedelta(minutes=10)
    _close_stage(quiz)

    closed = services.finalise_overdue(stage=quiz.stage, now=timezone.now() + LATER)

    attempt.refresh_from_db()
    assert closed == 1
    assert attempt.status == AttemptStatus.EXPIRED
    assert services.stage_scores(quiz.stage) == {entry.pk: 0}


def test_close_stage_now_finalises_running_attempts_after_the_network_tolerance(
    quiz, entry, django_capture_on_commit_callbacks
):
    """„Zamknij etap” z panelu domyka trwające podejście bez czekania na przeliczenie wyników.

    Do commitu (i do końca tolerancji) podejście zostaje otwarte – odpowiedź w drodze się liczy.
    Zadanie kolejkowane po commicie domyka je ze stemplem chwili zamknięcia i z oceną tego, co
    zdążyło się zapisać.
    """
    from apps.submissions.services import close_stage_now

    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers=_answer_payload(quiz))

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        close_stage_now(quiz.stage)
        attempt.refresh_from_db()
        assert attempt.status == AttemptStatus.IN_PROGRESS

    assert len(callbacks) >= 1
    attempt.refresh_from_db()
    quiz.stage.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    assert attempt.submitted_at == quiz.stage.closed_at
    assert attempt.score == 1


def test_close_stage_now_finalises_an_abandoned_overdue_attempt_at_once(quiz, entry):
    """Podejście porzucone po własnym terminie domyka się w tej samej transakcji, co zamknięcie."""
    from apps.submissions.services import close_stage_now

    attempt = services.start_attempt(quiz=quiz, entry=entry, now=timezone.now() - timedelta(hours=3))
    assert attempt.deadline_at < timezone.now() - LATER

    close_stage_now(quiz.stage)

    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    assert attempt.submitted_at == attempt.deadline_at


def test_window_ends_at_the_manual_closure(quiz):
    closed_at = timezone.now() - timedelta(minutes=1)
    _close_stage(quiz, at=closed_at)
    quiz.stage.refresh_from_db()

    assert quiz.window[1] == closed_at
    assert not quiz.is_open()


def test_start_is_refused_after_results_were_published(quiz, entry):
    ResultsPublication.objects.create(stage=quiz.stage)

    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)

    assert exc.value.machine_code == "QUIZ_RESULTS_PUBLISHED"


def test_withdrawn_publication_still_blocks_new_attempts(quiz, entry):
    """Wycofanie ogłoszenia nie cofa tego, co już przeczytano – klucz odpowiedzi jest znany."""
    ResultsPublication.objects.create(stage=quiz.stage)
    Stage.objects.filter(pk=quiz.stage_id).update(results_published_at=None)

    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)

    assert exc.value.machine_code == "QUIZ_RESULTS_PUBLISHED"


# --- zamknięcie testu nie później niż termin etapu ------------------------------------------------


def test_model_refuses_closes_at_after_the_stage_deadline(quiz):
    quiz.closes_at = quiz.stage.deadline_at + timedelta(hours=1)

    with pytest.raises(ValidationError) as exc:
        quiz.full_clean(exclude=["stage"])

    assert "closes_at" in exc.value.message_dict


def test_window_is_capped_by_the_stage_deadline_for_legacy_rows(quiz):
    """Test zapisany przed walidacją (``update`` omija ``clean``) i tak kończy się z etapem."""
    type(quiz).objects.filter(pk=quiz.pk).update(closes_at=quiz.stage.deadline_at + timedelta(days=3))
    quiz.refresh_from_db()

    assert quiz.window[1] == quiz.stage.deadline_at


def test_settings_form_refuses_closes_at_after_the_stage_deadline_for_a_new_quiz():
    stage = QuizFactory().stage
    stage.quiz.delete()
    stage.refresh_from_db()
    late = timezone.localtime(stage.deadline_at + timedelta(days=1))

    form = QuizSettingsForm(
        data={
            "title": "Test",
            "instructions": "",
            "duration_minutes": 30,
            "opens_at": "",
            "closes_at": late.strftime("%Y-%m-%dT%H:%M"),
            "attempts_allowed": 1,
            "questions_per_attempt": "",
            "show_results_after": "AFTER_CLOSE",
            "negative_floor": "QUESTION",
        },
        stage=stage,
    )

    assert not form.is_valid()
    assert "closes_at" in form.errors
