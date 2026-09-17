"""Fabryki factory_boy dla testów online. Wyłącznie w testach.

Fabryki są jawne tak samo, jak w ``apps.competitions.tests.factories``: ``QuizFactory`` nie tworzy
pytań, a ``QuizQuestionFactory`` nie tworzy wariantów. Test, który potrzebuje pytania wyboru,
zakłada je razem z wariantami przez ``choice_question`` – i wtedy widać w nim, ile tych wariantów
jest i który jest poprawny, czyli dokładnie to, o co w takim teście chodzi.
"""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from apps.competitions.models import StageFormat
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.quiz.models import (
    QuestionKind,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
)


class QuizStageFactory(StageFactory):
    """Etap w formie testu online – ta sama oś czasu, co zwykły etap, inna forma."""

    format = StageFormat.QUIZ


class QuizFactory(factory.django.DjangoModelFactory):
    """Test dziedziczący okno etapu (puste ``opens_at``/``closes_at``), jedno podejście, 30 minut."""

    class Meta:
        model = Quiz

    stage = factory.SubFactory(QuizStageFactory)
    title = factory.Sequence(lambda n: f"Test testowy {n}")
    instructions = ""
    duration_minutes = 30
    attempts_allowed = 1


class QuizQuestionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuizQuestion

    quiz = factory.SubFactory(QuizFactory)
    pool = ""
    order = factory.Sequence(lambda n: n + 1)
    kind = QuestionKind.SINGLE_CHOICE
    text = factory.Sequence(lambda n: f"Pytanie testowe {n}")
    points = Decimal("1")
    negative_points = Decimal("0")
    settings = factory.LazyFunction(dict)


class QuizOptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuizOption

    question = factory.SubFactory(QuizQuestionFactory)
    order = factory.Sequence(lambda n: n + 1)
    text = factory.Sequence(lambda n: f"Wariant {n}")
    is_correct = False


class QuizAttemptFactory(factory.django.DjangoModelFactory):
    """Podejście w toku z terminem za pół godziny. Zestaw pytań podaje test (``question_order``)."""

    class Meta:
        model = QuizAttempt

    quiz = factory.SubFactory(QuizFactory)
    entry = factory.SubFactory(StageEntryFactory)
    # ``started_at`` jest zadeklarowane jawnie, mimo że model ma domyślne ``timezone.now``:
    # ``deadline_at`` liczy się **z niego**, a atrybut wzięty z domyślnej wartości pola nie jest
    # widoczny dla ``LazyAttribute``.
    started_at = factory.LazyFunction(timezone.now)
    deadline_at = factory.LazyAttribute(lambda obj: obj.started_at + timedelta(minutes=30))
    question_order = factory.LazyFunction(dict)


class QuizAnswerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuizAnswer

    attempt = factory.SubFactory(QuizAttemptFactory)
    question = factory.SubFactory(QuizQuestionFactory)
    payload = factory.LazyFunction(dict)


def choice_question(quiz, *, correct: int = 1, total: int = 4, **kwargs) -> QuizQuestion:
    """Pytanie wyboru razem z wariantami: ``correct`` pierwszych jest poprawnych.

    Zwraca pytanie z wariantami w kolejności ``order``, więc test może sięgnąć po nie przez
    ``question.options.all()`` i wziąć pierwszy jako „ten poprawny” – bez zgadywania, który to.
    """
    kind = QuestionKind.MULTIPLE_CHOICE if correct > 1 else QuestionKind.SINGLE_CHOICE
    question = QuizQuestionFactory(quiz=quiz, kind=kwargs.pop("kind", kind), **kwargs)
    for index in range(1, total + 1):
        QuizOptionFactory(
            question=question, order=index, text=f"Wariant {index}", is_correct=index <= correct
        )
    return question


def numeric_question(quiz, *, answer: str = "9.81", tolerance_abs: str = "0", **kwargs) -> QuizQuestion:
    """Pytanie liczbowe z gotowym kluczem odpowiedzi w postaci, jaką zapisuje edytor (napisy)."""
    return QuizQuestionFactory(
        quiz=quiz,
        kind=QuestionKind.NUMERIC,
        settings={
            "answer": answer,
            "tolerance_abs": tolerance_abs,
            "tolerance_rel": "0",
            "unit": "",
        },
        **kwargs,
    )


def text_question(quiz, *, accepted=("splątanie",), **kwargs) -> QuizQuestion:
    """Pytanie tekstowe z domyślnymi flagami normalizacji (wszystkie włączone)."""
    return QuizQuestionFactory(
        quiz=quiz,
        kind=QuestionKind.SHORT_TEXT,
        settings={
            "accepted": list(accepted),
            "fold_case": True,
            "fold_whitespace": True,
            "fold_diacritics": True,
        },
        **kwargs,
    )
