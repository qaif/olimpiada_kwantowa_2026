"""Autozapis testu online hurtem (PERF-01, ``apps.quiz.services._write_answers``).

Test obciążenia z 4.10.2026: autozapis co 20 s wysyła komplet dotychczasowych odpowiedzi, a każda
szła osobnym ``update_or_create`` – liczba instrukcji SQL rosła z liczbą odpowiedzi (ok. 80 na
jedno odświeżenie pod koniec 20-pytaniowego testu, przy 40 % z 3000 uczniów piszących test – ponad
tysiąc instrukcji na sekundę). Tu: stała liczba zapytań, nietknięte odpowiedzi bez zmian
i – jak dotąd – nadpisanie zerujące werdykt.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory
from apps.quiz import services
from apps.quiz.models import QuizAnswer
from apps.quiz.tests.factories import QuizFactory, choice_question

pytestmark = pytest.mark.django_db


def _attempt(questions: int):
    quiz = QuizFactory()
    items = [choice_question(quiz) for _ in range(questions)]
    entry = StageEntryFactory(stage=quiz.stage, status=StageEntryStatus.REGISTERED)
    return services.start_attempt(quiz=quiz, entry=entry), items


def _answers(items, pick: int = 0) -> dict:
    return {str(q.pk): {"options": [q.options.order_by("order", "id")[pick].pk]} for q in items}


def _save_queries(attempt, answers) -> int:
    with CaptureQueriesContext(connection) as captured:
        services.save_answers(attempt=attempt, answers=answers)
    return len(captured.captured_queries)


def test_query_count_does_not_grow_with_the_number_of_answers():
    small, small_items = _attempt(2)
    large, large_items = _attempt(12)
    _answers(small_items), _answers(large_items)  # warianty wczytane przed pomiarem

    small_new = _save_queries(small, _answers(small_items))
    large_new = _save_queries(large, _answers(large_items))
    small_changed = _save_queries(small, _answers(small_items, pick=1))
    large_changed = _save_queries(large, _answers(large_items, pick=1))

    assert large_new == small_new
    assert large_changed == small_changed
    assert QuizAnswer.objects.filter(attempt=large).count() == 12


def test_unchanged_answers_are_not_rewritten():
    attempt, items = _attempt(5)
    answers = _answers(items)
    services.save_answers(attempt=attempt, answers=answers)
    stamps = dict(QuizAnswer.objects.filter(attempt=attempt).values_list("question_id", "updated_at"))

    with CaptureQueriesContext(connection) as captured:
        saved = services.save_answers(attempt=attempt, answers=answers)

    assert saved == 5
    assert not [q for q in captured.captured_queries if q["sql"].startswith('UPDATE "quiz_quizanswer"')]
    assert not [q for q in captured.captured_queries if q["sql"].startswith('INSERT INTO "quiz_quizanswer"')]
    assert dict(QuizAnswer.objects.filter(attempt=attempt).values_list("question_id", "updated_at")) == stamps


def test_changed_answer_is_saved_and_others_are_kept():
    attempt, items = _attempt(3)
    services.save_answers(attempt=attempt, answers=_answers(items))
    changed = items[1]
    new_option = changed.options.order_by("order", "id")[2].pk

    services.save_answers(
        attempt=attempt, answers={**_answers(items), str(changed.pk): {"options": [new_option]}}
    )

    payloads = dict(QuizAnswer.objects.filter(attempt=attempt).values_list("question_id", "payload"))
    assert payloads[changed.pk] == {"options": [new_option]}
    assert payloads[items[0].pk] == _answers(items)[str(items[0].pk)]


def test_rewrite_of_a_graded_answer_still_clears_the_verdict():
    """Ta sama treść, ale z werdyktem (przeliczenie w trakcie) – zapis ma wyzerować werdykt jak dotąd."""
    attempt, items = _attempt(1)
    answers = _answers(items)
    services.save_answers(attempt=attempt, answers=answers)
    QuizAnswer.objects.filter(attempt=attempt).update(is_correct=True, points_awarded=Decimal("1"))

    services.save_answers(attempt=attempt, answers=answers)

    answer = QuizAnswer.objects.get(attempt=attempt)
    assert answer.is_correct is None
    assert answer.points_awarded is None


def test_duplicate_keys_for_one_question_store_one_answer():
    """``"7"`` i ``"07"`` to ten sam identyfikator pytania – jeden wiersz, bez naruszenia unikalności."""
    attempt, items = _attempt(1)
    question = items[0]
    first, second = question.options.order_by("order", "id")[:2]

    services.save_answers(
        attempt=attempt,
        answers={str(question.pk): {"options": [first.pk]}, f"0{question.pk}": {"options": [second.pk]}},
    )

    assert QuizAnswer.objects.filter(attempt=attempt).count() == 1
