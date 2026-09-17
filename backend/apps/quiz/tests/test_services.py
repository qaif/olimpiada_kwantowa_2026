"""Serwisy testów online: start podejścia, losowanie, terminy, ocena, przeliczenie, wsad do wyników.

Podział względem ``test_grading.py``: tam sprawdzamy, **ile punktów** należy się za odpowiedź, tutaj
– **kiedy i komu** wolno coś zrobić. Dlatego to jest jedyny z dwóch plików, który potrzebuje bazy.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.competitions.models import StageEntryStatus
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.quiz import services
from apps.quiz.models import AttemptStatus, NegativeFloor, QuizAnswer, QuizAttempt
from apps.quiz.tests.factories import (
    QuizFactory,
    QuizQuestionFactory,
    choice_question,
    numeric_question,
    text_question,
)

from apps.competitions.tests.factories import StageEntryFactory  # isort: skip

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiz():
    return QuizFactory()


@pytest.fixture
def entry(quiz):
    return StageEntryFactory(stage=quiz.stage, status=StageEntryStatus.REGISTERED)


def _correct_option(question):
    return question.options.filter(is_correct=True).first()


def _answer(attempt, question, payload):
    services.save_answers(attempt=attempt, answers={str(question.pk): payload})


def _pool_question(quiz, *, points: Decimal, pool: str = "a"):
    """Pytanie tekstowe w podanej puli – skrót dla testów losowania, którym treść jest obojętna."""
    return QuizQuestionFactory(
        quiz=quiz, pool=pool, points=points, kind="SHORT_TEXT", settings={"accepted": ["x"]}
    )


def _rewind(attempt, *, deadline_ago: timedelta) -> None:
    """Cofa **cały** zegar podejścia tak, żeby jego termin wypadł ``deadline_ago`` temu.

    Przesuwamy obie daty, a nie sam termin, bo upływ czasu w rzeczywistości przesuwa je razem –
    i bo constraint ``quiz_attempt_deadline_after_start`` (słusznie) nie pozwala zapisać podejścia,
    które skończyło się przed rozpoczęciem. Ta sama sztuczka, co ``shift_stage`` w testach WWW:
    interesuje nas stan po terminie, a nie droga do niego.
    """
    now = timezone.now()
    deadline = now - deadline_ago
    QuizAttempt.objects.filter(pk=attempt.pk).update(
        started_at=deadline - timedelta(minutes=attempt.quiz.duration_minutes),
        deadline_at=deadline,
    )
    attempt.refresh_from_db()


# --- start podejścia ----------------------------------------------------------------------------


def test_start_odmawia_testu_bez_pytan(quiz, entry):
    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)
    assert exc.value.machine_code == "QUIZ_EMPTY"


def test_start_zaklada_podejscie_z_terminem_i_zestawem(quiz, entry):
    choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    assert attempt.status == AttemptStatus.IN_PROGRESS
    assert attempt.deadline_at > attempt.started_at
    assert len(attempt.drawn_question_ids) == 1


def test_start_skraca_termin_podejscia_do_konca_okna_testu(quiz, entry):
    """Kto zaczyna pięć minut przed zamknięciem, dostaje pięć minut – a nie pełny czas trwania."""
    choice_question(quiz)
    quiz.closes_at = timezone.now() + timedelta(minutes=5)
    quiz.save(update_fields=["closes_at"])
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    assert attempt.deadline_at == quiz.closes_at


def test_start_dwa_razy_oddaje_to_samo_podejscie(quiz, entry):
    """Jedno aktywne podejście: drugie kliknięcie „Rozpocznij” wraca do trwającego, a nie otwiera drugie."""
    choice_question(quiz)
    pierwsze = services.start_attempt(quiz=quiz, entry=entry)
    drugie = services.start_attempt(quiz=quiz, entry=entry)
    assert pierwsze.pk == drugie.pk
    assert QuizAttempt.objects.filter(quiz=quiz, entry=entry).count() == 1


def test_start_odmawia_po_wyczerpaniu_limitu_podejsc(quiz, entry):
    choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.submit_attempt(attempt=attempt)
    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)
    assert exc.value.machine_code == "QUIZ_NO_ATTEMPTS_LEFT"


def test_start_odmawia_poza_oknem_testu(quiz, entry):
    choice_question(quiz)
    quiz.opens_at = timezone.now() + timedelta(days=1)
    quiz.closes_at = timezone.now() + timedelta(days=2)
    quiz.save(update_fields=["opens_at", "closes_at"])
    with pytest.raises(DomainError) as exc:
        services.start_attempt(quiz=quiz, entry=entry)
    assert exc.value.machine_code == "QUIZ_CLOSED"


def test_start_domyka_porzucone_podejscie_zanim_otworzy_nastepne(quiz, entry):
    """Kto zamknął kartę i wraca po godzinie, ma dostać kolejne podejście, a nie martwy licznik."""
    choice_question(quiz)
    quiz.attempts_allowed = 2
    quiz.save(update_fields=["attempts_allowed"])
    stare = services.start_attempt(quiz=quiz, entry=entry)
    _rewind(stare, deadline_ago=timedelta(hours=1))

    nowe = services.start_attempt(quiz=quiz, entry=entry)

    stare.refresh_from_db()
    assert stare.status == AttemptStatus.EXPIRED
    assert nowe.pk != stare.pk


def test_okno_testu_domyslnie_jest_oknem_etapu(quiz):
    assert quiz.window == (quiz.stage.opens_at, quiz.stage.deadline_at)


# --- losowanie zestawów --------------------------------------------------------------------------


def test_losowanie_ciagnie_zadana_liczbe_pytan_z_kazdej_puli(quiz, entry):
    for pool in ("kinematyka", "splatanie"):
        for _ in range(4):
            QuizQuestionFactory(quiz=quiz, pool=pool, kind="SHORT_TEXT", settings={"accepted": ["x"]})
    quiz.questions_per_attempt = 2
    quiz.save(update_fields=["questions_per_attempt"])

    attempt = services.start_attempt(quiz=quiz, entry=entry)

    pule = [question.pool for question in services.attempt_questions(attempt)]
    assert sorted(pule) == ["kinematyka", "kinematyka", "splatanie", "splatanie"]


def test_losowanie_z_puli_mniejszej_niz_limit_oddaje_cala_pule(quiz, entry):
    QuizQuestionFactory(quiz=quiz, pool="mala", kind="SHORT_TEXT", settings={"accepted": ["x"]})
    quiz.questions_per_attempt = 5
    quiz.save(update_fields=["questions_per_attempt"])
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    assert len(attempt.drawn_question_ids) == 1


def test_wylosowany_zestaw_jest_staly_dla_podejscia(quiz, entry):
    """Powtarzalność bierze się z **zapisu**: odświeżenie strony czyta gotowy porządek, nie losuje."""
    for _ in range(8):
        QuizQuestionFactory(quiz=quiz, kind="SHORT_TEXT", settings={"accepted": ["x"]})
    quiz.questions_per_attempt = 3
    quiz.shuffle_questions = True
    quiz.save(update_fields=["questions_per_attempt", "shuffle_questions"])

    attempt = services.start_attempt(quiz=quiz, entry=entry)
    pierwszy = attempt.drawn_question_ids

    attempt.refresh_from_db()
    assert attempt.drawn_question_ids == pierwszy
    assert [q.pk for q in services.attempt_questions(attempt)] == pierwszy


def test_kolejnosc_wariantow_jest_zapisana_i_stosowana(quiz, entry):
    question = choice_question(quiz, total=4)
    quiz.shuffle_options = True
    quiz.save(update_fields=["shuffle_options"])
    attempt = services.start_attempt(quiz=quiz, entry=entry)

    zapisana = attempt.option_order_for(question.pk)
    assert sorted(zapisana) == sorted(option.pk for option in question.options.all())
    widziana = [option.pk for option in services.attempt_questions(attempt)[0].drawn_options]
    assert widziana == zapisana


def test_max_points_testu_jest_ograniczeniem_gornym_przy_losowaniu(quiz):
    _pool_question(quiz, points=Decimal("5"))
    _pool_question(quiz, points=Decimal("1"))
    quiz.questions_per_attempt = 1
    quiz.save(update_fields=["questions_per_attempt"])
    assert quiz.max_points == Decimal("5")


def test_ostrzezenia_wskazuja_pule_o_nierownych_punktach_i_za_mala(quiz):
    _pool_question(quiz, points=Decimal("5"))
    _pool_question(quiz, points=Decimal("1"))
    quiz.questions_per_attempt = 3
    quiz.save(update_fields=["questions_per_attempt"])
    warnings = services.draw_warnings(quiz)
    assert any("warte różnie" in item for item in warnings)
    assert any("mniej niż 3" in item for item in warnings)


# --- terminy i zapis odpowiedzi -------------------------------------------------------------------


def test_zapis_po_terminie_jest_odrzucany_i_domyka_podejscie(quiz, entry):
    question = choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, question, {"options": [_correct_option(question).pk]})
    _rewind(attempt, deadline_ago=timedelta(minutes=1))

    with pytest.raises(DomainError) as exc:
        _answer(attempt, question, {"options": []})

    assert exc.value.machine_code == "QUIZ_ATTEMPT_EXPIRED"
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    # …i liczy się to, co zdążyło się zapisać przed terminem.
    assert attempt.score == Decimal("1.00")


def test_zapis_w_oknie_tolerancji_sieciowej_jest_przyjmowany(quiz, entry):
    """Kto kliknął o czasie, nie może dostać odmowy za cudzy problem z łączem."""
    question = choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _rewind(attempt, deadline_ago=timedelta(seconds=10))

    _answer(attempt, question, {"options": [_correct_option(question).pk]})

    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.IN_PROGRESS


def test_zapis_pomija_pytania_spoza_wylosowanego_zestawu(quiz, entry):
    swoje = choice_question(quiz, pool="a")
    obce = choice_question(quiz, pool="b")
    quiz.questions_per_attempt = 1
    quiz.save(update_fields=["questions_per_attempt"])
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    # Zestaw ma po jednym pytaniu z każdej puli, więc oba są „swoje” – sprawdzamy pytanie z innego
    # testu, czyli sytuację, w której identyfikator jest prawdziwy, ale nie należy do podejścia.
    inny_quiz = QuizFactory()
    cudze = choice_question(inny_quiz)

    services.save_answers(attempt=attempt, answers={str(cudze.pk): {"options": [1]}})

    assert not QuizAnswer.objects.filter(attempt=attempt, question=cudze).exists()
    assert {swoje.pk, obce.pk} == set(attempt.drawn_question_ids)


def test_zapis_odrzuca_warianty_spoza_pytania(quiz, entry):
    question = choice_question(quiz)
    inne = choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)

    _answer(attempt, question, {"options": [option.pk for option in inne.options.all()]})

    answer = QuizAnswer.objects.get(attempt=attempt, question=question)
    assert answer.payload == {"options": []}


def test_zapis_zeruje_werdykt_poprzedniej_oceny(quiz, entry):
    """Odpowiedź zmieniona po przeliczeniu nie może zostawić punktów za poprzednią treść."""
    question = choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, question, {"options": [_correct_option(question).pk]})
    services.grade_attempt(attempt)
    assert QuizAnswer.objects.get(attempt=attempt).points_awarded == Decimal("1.00")

    _answer(attempt, question, {"options": []})

    answer = QuizAnswer.objects.get(attempt=attempt)
    assert answer.is_correct is None and answer.points_awarded is None


def test_finalise_overdue_domyka_porzucone_podejscia_z_data_terminu(quiz, entry):
    choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _rewind(attempt, deadline_ago=timedelta(hours=2))
    deadline = attempt.deadline_at

    assert services.finalise_overdue(quiz=quiz) == 1

    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    # Data oddania to termin podejścia, nie chwila, w której serwer to zauważył.
    assert attempt.submitted_at == deadline
    assert services.finalise_overdue(quiz=quiz) == 0


# --- ocena --------------------------------------------------------------------------------------


def test_ocena_liczy_wszystkie_pytania_zestawu_takze_bez_odpowiedzi(quiz, entry):
    trafione = choice_question(quiz, points=Decimal("2"))
    choice_question(quiz, points=Decimal("3"))
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, trafione, {"options": [_correct_option(trafione).pk]})

    services.grade_attempt(attempt)

    assert attempt.score == Decimal("2.00")
    assert attempt.max_points == Decimal("5")


def test_ocena_jest_idempotentna(quiz, entry):
    question = numeric_question(quiz, answer="9.81", points=Decimal("2"))
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, question, {"value": "9,81"})

    pierwsza = services.grade_attempt(attempt)
    druga = services.grade_attempt(attempt)
    trzecia = services.grade_attempt(attempt)

    assert pierwsza == druga == trzecia == Decimal("2.00")


def test_ocena_stosuje_podloge_ujemnych_z_ustawien_testu(quiz, entry):
    dobre = text_question(quiz, accepted=("tak",), points=Decimal("5"))
    zle = text_question(quiz, accepted=("tak",), points=Decimal("2"), negative_points=Decimal("2"))
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, dobre, {"text": "tak"})
    _answer(attempt, zle, {"text": "nie"})

    services.grade_attempt(attempt)
    assert attempt.score == Decimal("5.00")  # podłoga „za pytanie” (domyślna)

    quiz.negative_floor = NegativeFloor.QUIZ
    quiz.save(update_fields=["negative_floor"])
    services.grade_attempt(attempt)
    assert attempt.score == Decimal("3.00")


def test_przeliczenie_po_poprawce_klucza_zmienia_wynik_i_zostawia_slad_w_audycie(quiz, entry):
    question = text_question(quiz, accepted=("splątanie",), points=Decimal("4"))
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    _answer(attempt, question, {"text": "entanglement"})
    services.submit_attempt(attempt=attempt)
    assert attempt.score == Decimal("0.00")

    question.settings = {**question.settings, "accepted": ["splątanie", "entanglement"]}
    question.save(update_fields=["settings"])
    wynik = services.regrade_quiz(quiz=quiz, actor=None)

    attempt.refresh_from_db()
    assert attempt.score == Decimal("4.00")
    assert wynik == {"attempts": 1, "changed": 1}
    wpis = AuditLog.objects.filter(action="quiz.regraded").latest("id")
    # Audyt niesie liczniki, nigdy dane osobowe – tak samo jak w ``apps.results.services``.
    assert wpis.diff == {"attempts": ["", "1"], "changed": ["", "1"]}


def test_edytor_odmawia_zmian_w_tescie_z_podejsciami(quiz, entry):
    choice_question(quiz)
    services.start_attempt(quiz=quiz, entry=entry)
    with pytest.raises(DomainError) as exc:
        services.assert_editable(quiz)
    assert exc.value.machine_code == "QUIZ_HAS_ATTEMPTS"


# --- wsad do wyników etapu -------------------------------------------------------------------------


def test_stage_scores_oddaje_najlepsze_podejscie_zaokraglone_do_pelnych_punktow(quiz, entry):
    question = numeric_question(quiz, answer="1", points=Decimal("2.50"))
    quiz.attempts_allowed = 2
    quiz.save(update_fields=["attempts_allowed"])

    pierwsze = services.start_attempt(quiz=quiz, entry=entry)
    services.submit_attempt(attempt=pierwsze)  # bez odpowiedzi → 0
    drugie = services.start_attempt(quiz=quiz, entry=entry)
    _answer(drugie, question, {"value": "1"})
    services.submit_attempt(attempt=drugie)

    # 2,50 zaokrąglone „w górę przy połówce” → 3; ``StageEntry.total_points`` jest polem całkowitym.
    assert services.stage_scores(quiz.stage) == {entry.pk: 3}


def test_stage_scores_pomija_podejscia_w_toku(quiz, entry):
    choice_question(quiz)
    services.start_attempt(quiz=quiz, entry=entry)
    assert services.stage_scores(quiz.stage) == {}


def test_stage_scores_dla_etapu_bez_testu_jest_puste():
    from apps.competitions.tests.factories import StageFactory

    assert services.stage_scores(StageFactory()) == {}


def test_may_show_result_respektuje_ustawienie_testu(quiz, entry):
    choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.submit_attempt(attempt=attempt)

    quiz.show_results_after = "NEVER"
    assert services.may_show_result(quiz, attempt) is False
    quiz.show_results_after = "IMMEDIATELY"
    assert services.may_show_result(quiz, attempt) is True
    quiz.show_results_after = "AFTER_CLOSE"
    assert services.may_show_result(quiz, attempt) is False
    assert services.may_show_result(quiz, attempt, quiz.window[1] + timedelta(seconds=1)) is True


# --- statystyka pytań --------------------------------------------------------------------------------


def test_statystyka_rozdziela_brak_odpowiedzi_od_odpowiedzi_blednej(quiz):
    question = text_question(quiz, accepted=("tak",))
    umiejacy = StageEntryFactory(stage=quiz.stage)
    niepewny = StageEntryFactory(stage=quiz.stage)
    StageEntryFactory(stage=quiz.stage)

    for kandydat, odpowiedz in ((umiejacy, "tak"), (niepewny, "nie")):
        attempt = services.start_attempt(quiz=quiz, entry=kandydat)
        _answer(attempt, question, {"text": odpowiedz})
        services.submit_attempt(attempt=attempt)

    stat = services.question_stats(quiz)[0]
    assert stat["answered"] == 2
    assert stat["blank"] == 0
    assert stat["difficulty"] == pytest.approx(0.5)


def test_statystyka_bez_danych_nie_zmysla_liczb(quiz):
    text_question(quiz, accepted=("tak",))
    stat = services.question_stats(quiz)[0]
    assert stat["difficulty"] is None
    assert stat["discrimination"] is None
