"""Ekrany testów online: edytor koordynatora i pełna ścieżka uczestnika.

Podział względem testów w ``apps/quiz/tests``: tam sprawdzamy reguły domenowe wołane wprost, tutaj
– to, co widzi i robi **przeglądarka**. Dlatego prawie każdy test w tym pliku przechodzi przez
``client.get``/``client.post``, a nie przez serwis.

Dwie rzeczy są tu sprawdzane szczególnie uważnie, bo ich naruszenie nie objawia się awarią, tylko
cichym wyciekiem: że klucz odpowiedzi nie trafia do HTML-a uczestnika i że cudzego podejścia nie
da się otworzyć ani zapisać.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.competitions.models import StageEntryStatus, StageFormat
from apps.competitions.tests.factories import StageEntryFactory
from apps.quiz import services
from apps.quiz.models import AttemptStatus, QuizAnswer, QuizAttempt, QuizQuestion
from apps.quiz.tests.factories import QuizFactory, choice_question, numeric_question, text_question

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiz(edition):
    """Test na etapie **bieżącej** edycji – panel uczestnika pokazuje wyłącznie etap bieżący."""
    from apps.quiz.tests.factories import QuizStageFactory

    return QuizFactory(stage=QuizStageFactory(edition=edition))


@pytest.fixture
def quiz_entry(quiz, participant):
    return StageEntryFactory(participant=participant, stage=quiz.stage, status=StageEntryStatus.QUALIFIED)


def _login(web_client, who) -> None:
    """Logowanie w teście. ``participant`` jest profilem (ma ``user``), ``coordinator`` – kontem."""
    web_client.force_login(getattr(who, "user", who))


def _correct(question) -> int:
    return question.options.filter(is_correct=True).first().pk


def _rewind(attempt, *, deadline_ago: timedelta) -> None:
    """Cofa zegar podejścia (obie daty naraz) – patrz ``apps/quiz/tests/test_services.py``."""
    deadline = timezone.now() - deadline_ago
    QuizAttempt.objects.filter(pk=attempt.pk).update(
        started_at=deadline - timedelta(minutes=attempt.quiz.duration_minutes), deadline_at=deadline
    )
    attempt.refresh_from_db()


# --- dostęp do ekranów koordynatora ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "coordinator-stage-quiz",
        "coordinator-stage-quiz-questions",
        "coordinator-stage-quiz-import",
        "coordinator-stage-quiz-preview",
        "coordinator-stage-quiz-results",
        "coordinator-stage-quiz-export",
    ],
)
def test_ekrany_edytora_sa_403_dla_uczestnika(web_client, participant, quiz, name):
    """Kontrakt ról z T-08: zalogowany w złej roli dostaje 403, a nie 404 i nie przekierowanie."""
    _login(web_client, participant)
    response = web_client.get(reverse(f"web:{name}", args=[quiz.stage.pk]))
    assert response.status_code == 403


def test_ekrany_edytora_przekierowuja_niezalogowanego_na_logowanie(web_client, quiz):
    response = web_client.get(reverse("web:coordinator-stage-quiz", args=[quiz.stage.pk]))
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_koordynator_zaklada_test_na_etapie_bez_testu(web_client, coordinator, elim_stage):
    _login(web_client, coordinator)
    url = reverse("web:coordinator-stage-quiz", args=[elim_stage.pk])

    response = web_client.post(
        url,
        {
            "title": "Test eliminacyjny",
            "instructions": "",
            "duration_minutes": 45,
            "opens_at": "",
            "closes_at": "",
            "attempts_allowed": 1,
            "questions_per_attempt": "",
            "show_results_after": "AFTER_CLOSE",
            "negative_floor": "QUESTION",
        },
    )

    assert response.status_code == 302
    elim_stage.refresh_from_db()
    assert elim_stage.quiz.title == "Test eliminacyjny"
    # Zapis testu **nie** przestawia formy etapu – to osobna decyzja, na osobnym ekranie.
    assert elim_stage.format == StageFormat.SUBMISSIONS


def test_koordynator_dodaje_pytanie_wyboru_z_wariantami(web_client, coordinator, quiz):
    _login(web_client, coordinator)
    url = reverse("web:coordinator-stage-quiz-question-new", args=[quiz.stage.pk])

    response = web_client.post(
        url,
        {
            "kind": "SINGLE_CHOICE",
            "pool": "kinematyka",
            "order": 1,
            "text": "Ile trwa spadek?",
            "points": "2",
            "negative_points": "0.5",
            "options": "1 s\n*2 s\n4 s",
            "partial_credit": "ALL_OR_NOTHING",
        },
    )

    assert response.status_code == 302
    question = QuizQuestion.objects.get(quiz=quiz)
    assert question.points == Decimal("2")
    assert [option.is_correct for option in question.options.all()] == [False, True, False]


def test_edytor_odmawia_pytania_wyboru_bez_poprawnego_wariantu(web_client, coordinator, quiz):
    _login(web_client, coordinator)
    response = web_client.post(
        reverse("web:coordinator-stage-quiz-question-new", args=[quiz.stage.pk]),
        {
            "kind": "SINGLE_CHOICE",
            "order": 1,
            "text": "Pytanie",
            "points": "1",
            "negative_points": "0",
            "options": "A\nB",
        },
    )
    assert response.status_code == 200
    assert not QuizQuestion.objects.filter(quiz=quiz).exists()


def test_import_pytan_z_wklejonego_markdownu(web_client, coordinator, quiz):
    _login(web_client, coordinator)
    response = web_client.post(
        reverse("web:coordinator-stage-quiz-import", args=[quiz.stage.pk]),
        {"fmt": "markdown", "text": "## [pkt: 2]\nPytanie?\n- [x] tak\n- [ ] nie\n", "replace": ""},
    )
    assert response.status_code == 200
    assert QuizQuestion.objects.filter(quiz=quiz).count() == 1


def test_import_z_bledem_nie_zostawia_polowy_pytan(web_client, coordinator, quiz):
    """Import jest w całości albo wcale – inaczej nie da się stwierdzić, na czym stanął."""
    _login(web_client, coordinator)
    tekst = "## Pierwsze\n- [x] tak\n- [ ] nie\n\n## Drugie\n- [ ] tak\n- [ ] nie\n"

    response = web_client.post(
        reverse("web:coordinator-stage-quiz-import", args=[quiz.stage.pk]),
        {"fmt": "markdown", "text": tekst, "replace": ""},
    )

    assert response.status_code == 200
    assert QuizQuestion.objects.filter(quiz=quiz).count() == 0


def test_podglad_koordynatora_nie_zaklada_podejscia(web_client, coordinator, quiz):
    choice_question(quiz)
    _login(web_client, coordinator)

    response = web_client.get(reverse("web:coordinator-stage-quiz-preview", args=[quiz.stage.pk]))

    assert response.status_code == 200
    assert QuizAttempt.objects.count() == 0


def test_podglad_koordynatora_nie_pokazuje_klucza_odpowiedzi(web_client, coordinator, quiz):
    """Podgląd renderuje **stronę uczestnika**, więc obowiązuje go ta sama reguła co ją."""
    choice_question(quiz)
    _login(web_client, coordinator)
    response = web_client.get(reverse("web:coordinator-stage-quiz-preview", args=[quiz.stage.pk]))
    assert b"is_correct" not in response.content


def test_eksport_wynikow_oddaje_csv(web_client, coordinator, quiz, quiz_entry):
    question = choice_question(quiz)
    attempt = services.start_attempt(quiz=quiz, entry=quiz_entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"options": [_correct(question)]}})
    services.submit_attempt(attempt=attempt)
    _login(web_client, coordinator)

    response = web_client.get(reverse("web:coordinator-stage-quiz-export", args=[quiz.stage.pk]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    tresc = b"".join(response.streaming_content).decode("utf-8-sig")
    assert quiz_entry.participant.public_code in tresc


def test_przelicz_punkty_po_poprawce_klucza(web_client, coordinator, quiz, quiz_entry):
    question = text_question(quiz, accepted=("splątanie",), points=Decimal("4"))
    attempt = services.start_attempt(quiz=quiz, entry=quiz_entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"text": "entanglement"}})
    services.submit_attempt(attempt=attempt)
    question.settings = {**question.settings, "accepted": ["splątanie", "entanglement"]}
    question.save(update_fields=["settings"])
    _login(web_client, coordinator)

    response = web_client.post(reverse("web:coordinator-stage-quiz-regrade", args=[quiz.stage.pk]))

    assert response.status_code == 302
    attempt.refresh_from_db()
    assert attempt.score == Decimal("4.00")


# --- ścieżka uczestnika -----------------------------------------------------------------------------


def test_zakladka_zadania_prowadzi_do_testu_dla_etapu_w_formie_testu(web_client, participant, quiz_entry):
    _login(web_client, participant)
    response = web_client.get(reverse("web:me"))
    assert response.status_code == 200
    assert reverse("web:quiz-start", args=[quiz_entry.stage.pk]).encode() in response.content


def test_strona_startowa_wymaga_wpisu_do_etapu(web_client, participant, quiz):
    """Bez udziału w etapie nie ma czego pokazać – 404, a nie 403: tego udziału po prostu nie ma."""
    _login(web_client, participant)
    response = web_client.get(reverse("web:quiz-start", args=[quiz.stage.pk]))
    assert response.status_code == 404


def test_pelna_sciezka_start_autozapis_zakonczenie(web_client, participant, quiz, quiz_entry):
    wybor = choice_question(quiz, points=Decimal("2"))
    liczba = numeric_question(quiz, answer="9.81", tolerance_abs="0.02", points=Decimal("3"))
    _login(web_client, participant)

    # start – POST, bo zużywa podejście i uruchamia licznik
    response = web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    assert response.status_code == 302
    attempt = QuizAttempt.objects.get(entry=quiz_entry)

    # arkusz
    response = web_client.get(reverse("web:quiz-attempt", args=[attempt.pk]))
    assert response.status_code == 200

    # autozapis fetchem
    response = web_client.post(
        reverse("web:quiz-autosave", args=[attempt.pk]),
        data=json.dumps(
            {
                "answers": {
                    str(wybor.pk): {"options": [_correct(wybor)]},
                    str(liczba.pk): {"value": "9,80"},
                }
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["saved"] == 2
    assert QuizAnswer.objects.filter(attempt=attempt).count() == 2

    # zakończenie
    response = web_client.post(reverse("web:quiz-attempt", args=[attempt.pk]))
    assert response.status_code == 302

    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.SUBMITTED
    assert attempt.score == Decimal("5.00")


def test_sciezka_bez_javascriptu_wysyla_odpowiedzi_razem_z_zakonczeniem(
    web_client, participant, quiz, quiz_entry
):
    """Arkusz musi działać jako zwykły formularz – w pracowni szkolnej skrypty bywają zablokowane."""
    wybor = choice_question(quiz, points=Decimal("2"))
    tekst = text_question(quiz, accepted=("splątanie",), points=Decimal("1"))
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)

    response = web_client.post(
        reverse("web:quiz-attempt", args=[attempt.pk]),
        {f"q{wybor.pk}": str(_correct(wybor)), f"q{tekst.pk}": "SPLATANIE"},
    )

    assert response.status_code == 302
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.SUBMITTED
    assert attempt.score == Decimal("3.00")


def test_arkusz_nie_zdradza_klucza_odpowiedzi(web_client, participant, quiz, quiz_entry):
    """Ani wariantu poprawnego, ani wartości oczekiwanej – ``_public_questions`` ich nie przepisuje."""
    choice_question(quiz)
    numeric_question(quiz, answer="424242")
    text_question(quiz, accepted=("tajne-haslo",))
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)

    response = web_client.get(reverse("web:quiz-attempt", args=[attempt.pk]))

    tresc = response.content.decode()
    assert "424242" not in tresc
    assert "tajne-haslo" not in tresc
    assert "is_correct" not in tresc


def test_odswiezenie_arkusza_odtwarza_zapisane_odpowiedzi(web_client, participant, quiz, quiz_entry):
    """Autozapis ma być zapisem, do którego wolno wrócić – nie samym zabezpieczeniem na awarię."""
    tekst = text_question(quiz, accepted=("splątanie",))
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)
    web_client.post(
        reverse("web:quiz-autosave", args=[attempt.pk]),
        data=json.dumps({"answers": {str(tekst.pk): {"text": "moja odpowiedź"}}}),
        content_type="application/json",
    )

    response = web_client.get(reverse("web:quiz-attempt", args=[attempt.pk]))

    assert b"moja odpowied" in response.content


def test_autozapis_po_terminie_odmawia_i_domyka_podejscie(web_client, participant, quiz, quiz_entry):
    question = choice_question(quiz)
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)
    web_client.post(
        reverse("web:quiz-autosave", args=[attempt.pk]),
        data=json.dumps({"answers": {str(question.pk): {"options": [_correct(question)]}}}),
        content_type="application/json",
    )
    _rewind(attempt, deadline_ago=timedelta(minutes=5))

    response = web_client.post(
        reverse("web:quiz-autosave", args=[attempt.pk]),
        data=json.dumps({"answers": {str(question.pk): {"options": []}}}),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "QUIZ_ATTEMPT_EXPIRED"
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED
    # Odpowiedź zapisana przed terminem nadal się liczy – o to chodzi w statusie „czas minął”.
    assert attempt.score == Decimal("1.00")


def test_wejscie_na_przeterminowany_arkusz_kieruje_na_podsumowanie(web_client, participant, quiz, quiz_entry):
    choice_question(quiz)
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)
    _rewind(attempt, deadline_ago=timedelta(hours=1))

    response = web_client.get(reverse("web:quiz-attempt", args=[attempt.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("web:quiz-result", args=[attempt.pk])
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.EXPIRED


def test_cudze_podejscie_jest_nie_do_odroznienia_od_nieistniejacego(web_client, participant, quiz):
    """404, nie 403: odpowiedź „nie masz prawa” potwierdzałaby, że takie podejście istnieje."""
    obcy_entry = StageEntryFactory(stage=quiz.stage)
    choice_question(quiz)
    cudze = services.start_attempt(quiz=quiz, entry=obcy_entry)
    _login(web_client, participant)

    assert web_client.get(reverse("web:quiz-attempt", args=[cudze.pk])).status_code == 404
    assert web_client.get(reverse("web:quiz-result", args=[cudze.pk])).status_code == 404
    odpowiedz = web_client.post(
        reverse("web:quiz-autosave", args=[cudze.pk]),
        data=json.dumps({"answers": {}}),
        content_type="application/json",
    )
    assert odpowiedz.status_code == 404


def test_wynik_pokazuje_sie_zgodnie_z_ustawieniem_testu(web_client, participant, quiz, quiz_entry):
    question = choice_question(quiz, points=Decimal("3"))
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)
    web_client.post(
        reverse("web:quiz-attempt", args=[attempt.pk]), {f"q{question.pk}": str(_correct(question))}
    )
    url = reverse("web:quiz-result", args=[attempt.pk])

    quiz.show_results_after = "NEVER"
    quiz.save(update_fields=["show_results_after"])
    assert b"3,00" not in web_client.get(url).content

    quiz.show_results_after = "IMMEDIATELY"
    quiz.save(update_fields=["show_results_after"])
    response = web_client.get(url)
    assert response.context["show_score"] is True


def test_drugie_podejscie_nie_powstaje_przy_limicie_jednego(web_client, participant, quiz, quiz_entry):
    choice_question(quiz)
    _login(web_client, participant)
    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))
    attempt = QuizAttempt.objects.get(entry=quiz_entry)
    web_client.post(reverse("web:quiz-attempt", args=[attempt.pk]))

    web_client.post(reverse("web:quiz-start", args=[quiz.stage.pk]))

    assert QuizAttempt.objects.filter(entry=quiz_entry).count() == 1


# --- ułamki w teście (po wydaniu 0.35.0) ----------------------------------------------------------


def test_ekrany_koordynatora_pokazuja_punkty_ulamkowe_filtrem_punktow(
    web_client, coordinator, quiz, quiz_entry
):
    """„0,5 / −0,25” i wynik „0,5” – ta sama postać, co oceny recenzentów, a nie „0.50” z kolumny."""
    question = numeric_question(quiz, answer="1", points=Decimal("0.5"), negative_points=Decimal("0.25"))
    attempt = services.start_attempt(quiz=quiz, entry=quiz_entry)
    services.save_answers(attempt=attempt, answers={str(question.pk): {"value": "1"}})
    services.submit_attempt(attempt=attempt)
    _login(web_client, coordinator)

    questions = web_client.get(reverse("web:coordinator-stage-quiz-questions", args=[quiz.stage.pk]))
    results = web_client.get(reverse("web:coordinator-stage-quiz-results", args=[quiz.stage.pk]))

    assert questions.status_code == 200 and results.status_code == 200
    questions_html = questions.content.decode()
    assert "0,5 / −0,25" in questions_html
    assert "0,50" not in questions_html and "0.50" not in questions_html
    assert '<td class="num">0,5</td>' in results.content.decode()


def test_edytor_zapisuje_punkty_pytania_wpisane_z_przecinkiem(web_client, coordinator, quiz):
    _login(web_client, coordinator)

    response = web_client.post(
        reverse("web:coordinator-stage-quiz-question-new", args=[quiz.stage.pk]),
        {
            "kind": "SINGLE_CHOICE",
            "pool": "",
            "order": 1,
            "text": "Ile trwa spadek?",
            "points": "0,5",
            "negative_points": "0,25",
            "options": "1 s\n*2 s",
            "partial_credit": "ALL_OR_NOTHING",
        },
    )

    assert response.status_code == 302
    question = QuizQuestion.objects.get(quiz=quiz)
    assert (question.points, question.negative_points) == (Decimal("0.5"), Decimal("0.25"))
