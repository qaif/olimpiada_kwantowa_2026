"""Reguły oceny AI: klucz, zlecenia i ich idempotencja, kolejka, przebieg oceny i koszt.

Model jest podmieniony na poziomie ``services.call_model``: test dostaje gotową odpowiedź albo
błąd i sprawdza, co z nim zrobi serwis – status, komunikat dla koordynatora, liczniki kosztu,
ponowienie. Żadne żądanie nie wychodzi do sieci.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.profile import anonymise_account
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.ai_grading import services, tasks
from apps.ai_grading.client import ApiFailure, Usage
from apps.ai_grading.models import (
    AiAssessment,
    AiAssessmentStatus,
    AiGradingSettings,
    AiProviderAccount,
    AiStageVisibility,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import RubricCriterion
from apps.submissions.models import SubmissionStatus
from apps.tenancy.models import FEATURE_DEFAULTS

from .conftest import (
    FAKE_KEY,
    RESULT_COST,
    SENTINEL_SUMMARY,
    answer,
    enable_ai,
    make_submission,
    result,
    with_key,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def ready(competition, coordinator):
    enable_ai(competition)
    with_key(competition, coordinator)
    return competition


@pytest.fixture
def dispatched(monkeypatch):
    """Zadania wypuszczone przez kolejkę – zamiast Celery, lista argumentów."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        tasks.run_ai_assessment, "delay", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    return calls


@pytest.fixture
def model(monkeypatch):
    """Podmieniony model: ``model.replies`` to kolejne odpowiedzi (``CallResult`` albo wyjątek)."""
    state = {"replies": [], "requests": []}

    def fake_call(api_key, request):
        state["requests"].append((api_key, request))
        reply = state["replies"].pop(0) if state["replies"] else result()
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(services, "call_model", fake_call)
    return state


class Retried(Exception):
    def __init__(self, countdown):
        super().__init__(countdown)
        self.countdown = countdown


@pytest.fixture
def drive(monkeypatch):
    """Przebieg zadania z ponowieniami, prowadzony ręcznie – tak, jak zrobiłby to worker.

    ``self.retry`` jest podmienione na wyjątek z opóźnieniem: w trybie eager Celery z
    ``task_eager_propagates`` podnosi ``Retry`` zamiast ponowić, więc kolejne próby wołamy sami,
    z rosnącym ``request.retries``. Zwraca listę opóźnień, o które prosiło zadanie.
    """

    def fake_retry(*, countdown):
        raise Retried(countdown)

    monkeypatch.setattr(tasks.run_ai_assessment, "retry", fake_retry)

    def run(assessment_id: int) -> list[int]:
        countdowns: list[int] = []
        for attempt in range(tasks.MAX_RETRIES + 1):
            try:
                tasks.run_ai_assessment.apply(args=(assessment_id,), retries=attempt)
            except Retried as retried:
                countdowns.append(retried.countdown)
                continue
            break
        return countdowns

    return run


def queued(problem, submission, coordinator, *, regenerate=False):
    services.request_generation(problem, regenerate=regenerate, actor=coordinator)
    return AiAssessment.objects.get(submission=submission)


# --- flaga i klucz --------------------------------------------------------------------------------


def test_flag_is_in_the_catalogue_and_off_by_default(competition):
    assert FEATURE_DEFAULTS["ai_grading"] is False
    assert services.is_enabled(competition) is False


def test_key_is_stored_encrypted_with_only_the_last_four_characters_readable(competition, coordinator):
    row = with_key(competition, coordinator)

    stored = AiProviderAccount.objects.get(pk=row.pk)
    assert FAKE_KEY not in stored.api_key_encrypted
    assert stored.api_key_last4 == "WXYZ"
    assert stored.masked_key == "…WXYZ"
    assert stored.api_key_set_by == coordinator


def test_audit_records_the_fact_but_never_the_key(competition, coordinator):
    with_key(competition, coordinator)
    with_key(competition, coordinator)
    services.remove_api_key(competition, actor=coordinator)

    entries = AuditLog.objects.filter(action__startswith="ai_grading.key")
    assert [entry.action for entry in entries.order_by("id")] == [
        "ai_grading.key_set",
        "ai_grading.key_set",
        "ai_grading.key_removed",
    ]
    dumped = json.dumps([entry.diff for entry in entries])
    assert FAKE_KEY not in dumped
    assert "WXYZ" not in dumped


def test_invalid_key_is_a_domain_error(competition, coordinator):
    with pytest.raises(DomainError) as info:
        services.set_api_key(competition, "nie-klucz", actor=coordinator)

    assert info.value.machine_code == "AI_KEY_INVALID"
    assert (
        not AiProviderAccount.objects.filter(competition=competition).exclude(api_key_encrypted="").exists()
    )


def test_check_key_stores_the_verdict(competition, coordinator, monkeypatch):
    with_key(competition, coordinator)
    seen = {}

    def fake_check(key, model, provider="anthropic"):
        seen["key"] = key.reveal()
        seen["model"] = model
        seen["provider"] = provider
        return True, "Klucz działa."

    monkeypatch.setattr(services, "check_key", fake_check)

    ok, _ = services.check_api_key(competition, actor=coordinator)

    row = services.account_for(competition, "anthropic")
    assert ok is True and row.api_key_check_ok is True
    assert seen == {"key": FAKE_KEY, "model": "claude-opus-5", "provider": "anthropic"}


def test_replacing_the_key_clears_the_previous_check(competition, coordinator):
    row = with_key(competition, coordinator)
    AiProviderAccount.objects.filter(pk=row.pk).update(
        api_key_check_ok=True, api_key_checked_at=timezone.now()
    )

    with_key(competition, coordinator)

    assert services.account_for(competition, "anthropic").api_key_check_ok is None


# --- zlecenie -------------------------------------------------------------------------------------


def test_request_needs_the_flag_and_a_key(competition, problem, coordinator):
    make_submission(problem)
    with pytest.raises(DomainError) as info:
        services.request_generation(problem, regenerate=False, actor=coordinator)
    assert info.value.machine_code == "AI_DISABLED"

    enable_ai(competition)
    with pytest.raises(DomainError) as info:
        services.request_generation(problem, regenerate=False, actor=coordinator)
    assert info.value.machine_code == "AI_KEY_MISSING"


def test_request_queues_only_the_latest_clean_version_of_each_work(
    ready, problem, coordinator, dispatched, django_capture_on_commit_callbacks
):
    first = make_submission(problem, status=SubmissionStatus.SUBMITTED)
    latest = make_submission(problem, entry=first.entry, version=2)
    make_submission(problem, clean=False)  # plik jeszcze w skanie – pomijany

    with django_capture_on_commit_callbacks(execute=True):
        plan = services.request_generation(problem, regenerate=False, actor=coordinator)

    assert [item.pk for item in plan.to_generate] == [latest.pk]
    assert list(AiAssessment.objects.values_list("submission_id", flat=True)) == [latest.pk]


def test_double_click_does_not_queue_twice(
    ready, problem, coordinator, dispatched, django_capture_on_commit_callbacks
):
    make_submission(problem)
    make_submission(problem)

    with django_capture_on_commit_callbacks(execute=True):
        services.request_generation(problem, regenerate=False, actor=coordinator)
        second = services.request_generation(problem, regenerate=False, actor=coordinator)
        third = services.request_generation(problem, regenerate=True, actor=coordinator)

    assert second.count == 0
    assert third.count == 0  # w toku – także „wygeneruj ponownie” nie zleca drugi raz
    assert AiAssessment.objects.count() == 2


def test_the_queue_releases_one_task_at_a_time_with_only_the_id(
    ready, problem, coordinator, dispatched, django_capture_on_commit_callbacks, settings
):
    settings.AI_GRADING_MAX_CONCURRENCY = 1
    for _ in range(3):
        make_submission(problem)

    with django_capture_on_commit_callbacks(execute=True):
        services.request_generation(problem, regenerate=False, actor=coordinator)

    assert len(dispatched) == 1
    args, kwargs = dispatched[0]
    assert kwargs == {}
    assert len(args) == 1 and isinstance(args[0], int)
    assert FAKE_KEY not in repr(dispatched)
    assert AiAssessment.objects.filter(dispatched_at__isnull=False).count() == 1


def test_pump_releases_the_next_one_when_a_slot_frees_up(
    ready, problem, coordinator, dispatched, django_capture_on_commit_callbacks, model
):
    for _ in range(2):
        make_submission(problem)
    with django_capture_on_commit_callbacks(execute=True):
        services.request_generation(problem, regenerate=False, actor=coordinator)
    first_id = dispatched[0][0][0]

    services.run_assessment(first_id)
    with django_capture_on_commit_callbacks(execute=True):
        services.pump()

    assert len(dispatched) == 2
    assert dispatched[1][0][0] != first_id


def test_stale_dispatch_does_not_block_the_queue_forever(ready, problem, coordinator, dispatched, settings):
    settings.AI_GRADING_STALE_MINUTES = 30
    submission = make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    long_ago = timezone.now() - timedelta(hours=2)
    AiAssessment.objects.filter(submission=submission).update(dispatched_at=long_ago)

    assert services.pump() == [AiAssessment.objects.get(submission=submission).pk]


def test_stale_running_assessment_becomes_an_error_not_a_silent_retry(ready, problem, coordinator):
    submission = make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    AiAssessment.objects.filter(submission=submission).update(
        status=AiAssessmentStatus.RUNNING, started_at=timezone.now() - timedelta(hours=1)
    )

    assert services.recover_stale() == 1
    assessment = AiAssessment.objects.get(submission=submission)
    assert assessment.status == AiAssessmentStatus.ERROR
    assert assessment.error_code == "interrupted"


def test_regenerate_resets_a_finished_assessment(ready, problem, coordinator, model):
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)
    services.run_assessment(assessment.pk)

    plan = services.request_generation(problem, regenerate=True, actor=coordinator)

    assessment.refresh_from_db()
    assert plan.count == 1
    assert assessment.status == AiAssessmentStatus.PENDING
    assert assessment.summary == ""
    # Koszt poprzedniego przebiegu zostaje – przy ocenie i w licznikach konkursu.
    assert assessment.cost_usd == RESULT_COST
    assert services.settings_for(ready).total_cost_usd == RESULT_COST


def test_plan_estimates_cost_before_anything_is_spent(ready, problem, coordinator):
    make_submission(problem)
    make_submission(problem)

    plan = services.plan_generation(problem, regenerate=False)

    assert plan.count == 2
    assert plan.estimate_usd > Decimal("0")
    assert not AiAssessment.objects.exists()


def test_single_submission_request_ignores_ids_from_another_problem(ready, problem, coordinator, stage):
    from apps.competitions.tests.factories import ProblemFactory

    other = make_submission(ProblemFactory(stage=stage, number=9))

    plan = services.request_generation(
        problem, regenerate=False, submission_ids=[other.pk], actor=coordinator
    )

    assert plan.count == 0
    assert not AiAssessment.objects.exists()


def test_spending_limit_blocks_new_requests(ready, problem, coordinator):
    make_submission(problem)
    services.update_options(
        ready, model="claude-sonnet-5", spending_limit_usd=Decimal("1"), actor=coordinator
    )
    AiGradingSettings.objects.filter(competition=ready).update(total_cost_usd=Decimal("1.5"))

    with pytest.raises(DomainError) as info:
        services.request_generation(problem, regenerate=False, actor=coordinator)
    assert info.value.machine_code == "AI_BUDGET_REACHED"


# --- przebieg oceny -------------------------------------------------------------------------------


def test_successful_run_stores_the_suggestion_and_the_cost(ready, problem, coordinator, model):
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)

    outcome = services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert outcome.status == AiAssessmentStatus.DONE
    assert assessment.status == AiAssessmentStatus.DONE
    assert assessment.proposed_points == Decimal("5.00")
    assert assessment.max_points == Decimal("6.00")
    assert assessment.summary == SENTINEL_SUMMARY
    assert assessment.confidence == "średnia"
    assert assessment.request_id == "req_test_123"
    assert assessment.sent_at is not None
    assert (assessment.input_tokens, assessment.output_tokens, assessment.cache_write_tokens) == (
        1000,
        2000,
        4000,
    )
    assert assessment.cost_usd == RESULT_COST
    row = services.settings_for(ready)
    assert row.total_calls == 1
    assert row.total_cost_usd == RESULT_COST


def test_the_request_carries_the_model_choice_and_the_decrypted_key(ready, problem, coordinator, model):
    services.update_options(ready, model="claude-sonnet-5", spending_limit_usd=None, actor=coordinator)
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)

    services.run_assessment(assessment.pk)

    key, request = model["requests"][0]
    assert key.reveal() == FAKE_KEY
    assert request["model"] == "claude-sonnet-5"
    assert request["fallbacks"] == "default"
    # Do modelu nie idzie nic, co identyfikuje uczestnika.
    dumped = json.dumps(request, ensure_ascii=False)
    participant = submission.entry.participant
    for personal in ("Zenobia", "Kwiatkowska", participant.user.email, participant.public_code):
        assert personal not in dumped


def test_personal_data_copied_by_the_model_is_redacted(ready, problem, coordinator, model):
    model["replies"] = [
        result(answer(summary="Zenobia Kwiatkowska z Liceum Ogólnokształcące nr 7 dobrze liczy."))
    ]
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert "Zenobia" not in assessment.summary
    assert "Kwiatkowska" not in assessment.summary
    assert "Liceum" not in assessment.summary


def test_second_delivery_of_the_same_task_does_nothing(ready, problem, coordinator, model):
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)

    services.run_assessment(assessment.pk)
    again = services.run_assessment(assessment.pk)

    assert again.status == "SKIPPED"
    assert len(model["requests"]) == 1


def test_refusal_is_an_error_with_the_category(ready, problem, coordinator, model):
    model["replies"] = [result("", stop_reason="refusal", refusal_category="cyber", usage=Usage())]
    assessment = queued(problem, make_submission(problem), coordinator)

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.status == AiAssessmentStatus.ERROR
    assert assessment.error_code == "refusal"
    assert "cyber" in assessment.error_message


def test_max_tokens_is_an_error_and_is_still_paid_for(ready, problem, coordinator, model):
    model["replies"] = [result('{"proposed', stop_reason="max_tokens")]
    assessment = queued(problem, make_submission(problem), coordinator)

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == "max_tokens"
    assert assessment.cost_usd == RESULT_COST


def test_invalid_output_is_an_error(ready, problem, coordinator, model):
    model["replies"] = [result('{"coś": "innego"}')]
    assessment = queued(problem, make_submission(problem), coordinator)

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == "invalid_output"


def test_rejected_key_fails_without_retry(ready, problem, coordinator, model):
    model["replies"] = [ApiFailure("auth", "Anthropic odrzucił klucz API.", request_id="req_x")]
    assessment = queued(problem, make_submission(problem), coordinator)

    outcome = services.run_assessment(assessment.pk, final_attempt=False)

    assessment.refresh_from_db()
    assert outcome.status == AiAssessmentStatus.ERROR
    assert assessment.error_code == "auth"
    assert assessment.request_id == "req_x"
    assert FAKE_KEY not in assessment.error_message


def test_rate_limit_asks_for_a_retry_and_puts_the_assessment_back(ready, problem, coordinator, model):
    model["replies"] = [ApiFailure("rate_limit", "Limit.", retryable=True, retry_after=30)]
    assessment = queued(problem, make_submission(problem), coordinator)

    outcome = services.run_assessment(assessment.pk, final_attempt=False)

    assessment.refresh_from_db()
    assert outcome.status == "RETRY" and outcome.retry_after == 30
    assert assessment.status == AiAssessmentStatus.PENDING
    assert assessment.dispatched_at is not None  # nadal „w locie” dla ogranicznika
    assert services.settings_for(ready).total_calls == 0  # nic nie zapłacono


def test_last_attempt_turns_a_transient_error_into_a_final_one(ready, problem, coordinator, model):
    model["replies"] = [ApiFailure("server", "HTTP 529.", retryable=True)]
    assessment = queued(problem, make_submission(problem), coordinator)

    outcome = services.run_assessment(assessment.pk, final_attempt=True)

    assert outcome.status == AiAssessmentStatus.ERROR


def test_celery_task_retries_transient_errors_then_succeeds(ready, problem, coordinator, model, drive):
    model["replies"] = [
        ApiFailure("server", "HTTP 500.", retryable=True),
        ApiFailure("connection", "Sieć.", retryable=True),
        result(),
    ]
    assessment = queued(problem, make_submission(problem), coordinator)

    countdowns = drive(assessment.pk)

    assessment.refresh_from_db()
    assert countdowns == [60, 120]  # wykładniczo: 60 s, potem 120 s
    assert assessment.status == AiAssessmentStatus.DONE
    assert assessment.attempts == 3
    assert services.settings_for(ready).total_calls == 1  # zapłacona tylko udana odpowiedź


def test_celery_task_gives_up_after_the_retry_limit(ready, problem, coordinator, model, drive):
    model["replies"] = [ApiFailure("rate_limit", "Limit.", retryable=True, retry_after=7200)] * (
        tasks.MAX_RETRIES + 1
    )
    assessment = queued(problem, make_submission(problem), coordinator)

    countdowns = drive(assessment.pk)

    assessment.refresh_from_db()
    # ``retry-after`` dłuższy niż kwadrans jest przycinany – dłuższe czekanie wypadłoby poza okno,
    # w którym ogranicznik liczy ocenę jako „w locie”.
    assert countdowns == [tasks.RETRY_MAX_SECONDS] * tasks.MAX_RETRIES
    assert assessment.status == AiAssessmentStatus.ERROR
    assert assessment.attempts == tasks.MAX_RETRIES + 1


def test_unexpected_exception_does_not_leave_the_assessment_running(ready, problem, coordinator, model):
    model["replies"] = [RuntimeError("bum")]
    assessment = queued(problem, make_submission(problem), coordinator)

    tasks.run_ai_assessment.apply(args=(assessment.pk,))

    assessment.refresh_from_db()
    assert assessment.status == AiAssessmentStatus.ERROR
    assert assessment.error_code == "internal"


@pytest.mark.parametrize(
    ("breakage", "code"),
    [("flag", "disabled"), ("key", "no_key"), ("budget", "budget"), ("secret", "key_unreadable")],
)
def test_queued_assessment_fails_cleanly_when_the_situation_changes(
    ready, problem, coordinator, model, settings, breakage, code
):
    assessment = queued(problem, make_submission(problem), coordinator)
    if breakage == "flag":
        ready.feature_flags = {**ready.feature_flags, "ai_grading": False}
        ready.save(update_fields=["feature_flags"])
    elif breakage == "key":
        services.remove_api_key(ready, actor=coordinator)
    elif breakage == "budget":
        AiGradingSettings.objects.filter(competition=ready).update(
            spending_limit_usd=Decimal("1"), total_cost_usd=Decimal("2")
        )
    else:
        settings.SECRET_KEY = "inny-sekret-serwera-" * 4

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == code
    assert model["requests"] == []  # nic nie poszło do API


def test_work_without_a_clean_file_fails_without_calling_the_api(ready, problem, coordinator, model):
    submission = make_submission(problem)
    assessment = queued(problem, submission, coordinator)
    submission.files.update(av_status="PENDING")

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == "no_file"
    assert model["requests"] == []


def test_oversized_file_fails_before_it_is_read_or_sent(ready, problem, coordinator, model):
    submission = make_submission(problem)
    submission.files.update(size_bytes=40 * 1024 * 1024)
    assessment = queued(problem, submission, coordinator)

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == "too_large"
    assert model["requests"] == []
    assert assessment.sent_at is None


def test_problem_without_scale_fails_cleanly(ready, problem, coordinator, model):
    assessment = queued(problem, make_submission(problem), coordinator)
    problem.stage.scoring_scale.delete()

    services.run_assessment(assessment.pk)

    assessment.refresh_from_db()
    assert assessment.error_code == "no_scale"


def test_cost_uses_cache_multipliers_and_model_rates():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )

    opus = services.cost_of(usage, served_model="claude-opus-5", requested_model="claude-opus-5")
    sonnet = services.cost_of(usage, served_model="claude-sonnet-5", requested_model="claude-sonnet-5")
    unknown = services.cost_of(usage, served_model="claude-x", requested_model="claude-sonnet-5")

    assert opus == Decimal("5") + Decimal("25") + Decimal("6.25") + Decimal("0.5")
    assert sonnet == Decimal("2") + Decimal("10") + Decimal("2.5") + Decimal("0.2")
    assert unknown == sonnet


# --- odczyty --------------------------------------------------------------------------------------


def test_problem_overview_counts_and_agreement(ready, problem, coordinator, model):
    from apps.grading.tests.factories import FinalGradeFactory

    submission = make_submission(problem)
    make_submission(problem)
    FinalGradeFactory(submission=submission, score=6)
    services.run_assessment(queued(problem, submission, coordinator).pk)

    overview = services.problem_overview(problem)

    assert overview["counts"]["DONE"] == 1
    assert overview["counts"]["PENDING"] == 1
    assert overview["agreement"]["count"] == 1
    assert overview["agreement"]["mean_abs_diff"] == Decimal("1.00")
    assert overview["agreement"]["within_one_share"] == 100


def test_reviewer_prefill_only_without_a_rubric(ready, problem, coordinator, model):
    from apps.grading.tests.factories import ReviewFactory

    submission = make_submission(problem)
    services.run_assessment(queued(problem, submission, coordinator).pk)
    review = ReviewFactory(submission=submission)

    context = services.reviewer_context(review, ready, editable=True)
    assert context["prefill_value"] == 5
    assert services.reviewer_context(review, ready, editable=False)["prefill_value"] is None

    RubricCriterion.objects.create(problem=problem, title="Równanie", max_points=6)
    assert services.reviewer_context(review, ready, editable=True)["prefill_value"] is None


def test_anonymisation_erases_the_participants_ai_assessments(ready, problem, coordinator, model):
    participant = ParticipantFactory()
    mine = make_submission(problem, participant=participant)
    other = make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)

    anonymise_account(participant.user, actor=coordinator)

    assert not AiAssessment.objects.filter(submission=mine).exists()
    assert AiAssessment.objects.filter(submission=other).exists()


def test_visibility_toggle_is_audited(ready, stage, coordinator):
    services.set_stage_visibility(stage, True, actor=coordinator)
    services.set_stage_visibility(stage, True, actor=coordinator)
    services.set_stage_visibility(stage, False, actor=coordinator)

    assert AiStageVisibility.objects.get(stage=stage).show_to_participants is False
    assert AuditLog.objects.filter(action="ai_grading.visibility_changed").count() == 2
