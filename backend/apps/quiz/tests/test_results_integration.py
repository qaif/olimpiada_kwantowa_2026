"""Szew między testami online a wynikami etapu – jedyne miejsce, w którym te dwie aplikacje się widzą.

Testy leżą w ``apps.quiz``, a nie w ``apps.results``, i to jest celowe: kierunek zależności idzie
stąd tam (wyniki pytają testy o punkty, nigdy odwrotnie), więc to ta strona ma pilnować, że
kontrakt ``stage_scores`` jest dotrzymany. Gdyby szew kiedyś pękł, pierwszy zapali się ten plik.

Sprawdzamy trzy rzeczy, których nie widać w testach samego serwisu:

1. punkty z testu **docierają** do ``compute_stage_results`` i do ``StageEntry.total_points``,
2. etap pisemny zachowuje się dokładnie jak dotąd (hook nie może dotknąć niczego poza etapem
   w formie testu),
3. podgląd (symulacja progu) niczego nie zapisuje – także dla etapu w formie testu.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.competitions.models import StageEntry, StageFormat
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.quiz import services
from apps.quiz.models import QuizAttempt
from apps.quiz.tests.factories import QuizFactory, QuizStageFactory, numeric_question
from apps.results.services import compute_stage_results

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiz():
    return QuizFactory(stage=QuizStageFactory(edition=CurrentEditionFactory()))


def _solve(quiz, entry, question, value: str):
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"value": value}})
    return services.submit_attempt(attempt=attempt)


def test_punkty_z_testu_trafiaja_do_tabeli_wynikow_etapu(quiz):
    question = numeric_question(quiz, answer="9.81", points=Decimal("6"))
    zdobywca = StageEntryFactory(stage=quiz.stage)
    pudlujacy = StageEntryFactory(stage=quiz.stage)
    _solve(quiz, zdobywca, question, "9.81")
    _solve(quiz, pudlujacy, question, "1")

    rows = compute_stage_results(quiz.stage)

    wyniki = {row["entry_id"]: row["total"] for row in rows}
    assert wyniki == {zdobywca.pk: 6, pudlujacy.pk: 0}
    zdobywca.refresh_from_db()
    assert zdobywca.total_points == 6


def test_wpis_bez_podejscia_ma_zero_a_nie_brak_wiersza(quiz):
    """Kto się nie stawił, ma w protokole zero – tak samo jak przy nieoddanej pracy pisemnej."""
    numeric_question(quiz, answer="1")
    nieobecny = StageEntryFactory(stage=quiz.stage)

    rows = compute_stage_results(quiz.stage)

    assert [row["entry_id"] for row in rows] == [nieobecny.pk]
    assert rows[0]["total"] == 0


def test_kolumny_zadan_zostaja_puste_dla_etapu_w_formie_testu(quiz):
    """Kolumny tabeli wyników to zadania; rozbicie na pytania stoi na własnym ekranie testu."""
    question = numeric_question(quiz, answer="2", points=Decimal("3"))
    entry = StageEntryFactory(stage=quiz.stage)
    _solve(quiz, entry, question, "2")

    rows = compute_stage_results(quiz.stage)

    assert rows[0]["points"] == {}
    assert rows[0]["total"] == 3


def test_podglad_nie_zapisuje_punktow_takze_dla_etapu_w_formie_testu(quiz):
    """``preview=True`` biegnie na żądaniu GET (symulacja progu) i nie ma prawa niczego zapisać."""
    question = numeric_question(quiz, answer="4", points=Decimal("2"))
    entry = StageEntryFactory(stage=quiz.stage)
    _solve(quiz, entry, question, "4")

    rows = compute_stage_results(quiz.stage, preview=True)

    assert rows[0]["total"] == 2
    entry.refresh_from_db()
    assert entry.total_points is None


def test_podglad_nie_domyka_porzuconych_podejsc(quiz):
    """Domknięcie jest zapisem, więc w podglądzie go nie ma – podejście w toku liczy się jako zero."""
    question = numeric_question(quiz, answer="4", points=Decimal("2"))
    entry = StageEntryFactory(stage=quiz.stage)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"value": "4"}})

    rows = compute_stage_results(quiz.stage, preview=True)

    assert rows[0]["total"] == 0
    attempt.refresh_from_db()
    assert attempt.status == "IN_PROGRESS"


def test_przeliczenie_domyka_porzucone_podejscie_i_liczy_zapisane_odpowiedzi(quiz):
    """Bez tego praca kogoś, komu padło łącze, weszłaby do protokołu jako zero."""
    question = numeric_question(quiz, answer="4", points=Decimal("2"))
    entry = StageEntryFactory(stage=quiz.stage)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"value": "4"}})
    deadline = timezone.now() - timedelta(hours=1)
    QuizAttempt.objects.filter(pk=attempt.pk).update(
        started_at=deadline - timedelta(minutes=quiz.duration_minutes), deadline_at=deadline
    )

    rows = compute_stage_results(quiz.stage)

    assert rows[0]["total"] == 2
    attempt.refresh_from_db()
    assert attempt.status == "EXPIRED"


def test_etap_pisemny_liczy_sie_dokladnie_tak_jak_dotad():
    """Hook nie może dotknąć niczego poza etapem w formie testu – to jest cała jego granica."""
    stage = StageFactory(edition=CurrentEditionFactory())
    ProblemFactory(stage=stage, number=1)
    entry = StageEntryFactory(stage=stage)

    rows = compute_stage_results(stage)

    assert stage.format == StageFormat.SUBMISSIONS
    assert rows[0]["entry_id"] == entry.pk
    # Kolumna zadania istnieje i jest zerem (brak zgłoszenia), a nie pustym słownikiem.
    assert rows[0]["points"] == {"1": 0}


def test_etap_w_formie_testu_ale_bez_arkusza_daje_zera():
    """Forma przestawiona, zanim powstał arkusz: protokół ma być pusty, a nie wywrócić się."""
    stage = QuizStageFactory(edition=CurrentEditionFactory())
    entry = StageEntryFactory(stage=stage)

    rows = compute_stage_results(stage)

    assert rows[0]["total"] == 0
    assert StageEntry.objects.get(pk=entry.pk).total_points == 0
