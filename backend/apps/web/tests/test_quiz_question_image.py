"""Ilustracje pytań testu online idą przez widok aplikacji (audyt 10.10.2026, niskie).

Do tej zmiany szablon wypisywał ``question.image.url`` – w produkcji podpisany adres na wewnętrzny
``http://minio:9000`` (nie działał), a „naprawa” podpisanym adresem publicznym dałaby link ważny
godzinę do rozesłania w trakcie testu. Widok wpuszcza wyłącznie uczestnika z otwartym podejściem,
w którym pytanie wylosowano, oraz koordynatora konkursu w podglądzie – i nie pozwala niczego
zbuforować.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import StageEntryFactory
from apps.quiz import services
from apps.quiz.models import QuizAttempt
from apps.quiz.tests.factories import QuizFactory, choice_question

pytestmark = pytest.mark.django_db


def _png() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def quiz(edition):
    from apps.quiz.tests.factories import QuizStageFactory

    return QuizFactory(stage=QuizStageFactory(edition=edition))


@pytest.fixture
def question(quiz):
    question = choice_question(quiz)
    question.image = SimpleUploadedFile("schemat.png", _png(), content_type="image/png")
    question.save(update_fields=["image"])
    return question


@pytest.fixture
def attempt(quiz, question, participant):
    entry = StageEntryFactory(participant=participant, stage=quiz.stage, status=StageEntryStatus.QUALIFIED)
    return services.start_attempt(quiz=quiz, entry=entry)


def _image_url(attempt, question) -> str:
    return reverse("web:quiz-question-image", args=[attempt.pk, question.pk])


def test_the_participant_gets_the_image_during_the_attempt_without_caching(
    web_client, participant, attempt, question
):
    web_client.force_login(participant.user)

    response = web_client.get(_image_url(attempt, question))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content) == _png()


def test_the_attempt_page_points_to_the_view_not_to_the_storage(web_client, participant, attempt, question):
    web_client.force_login(participant.user)

    body = web_client.get(reverse("web:quiz-attempt", args=[attempt.pk])).content.decode()

    assert _image_url(attempt, question) in body
    assert question.image.name not in body


def test_after_the_deadline_the_image_is_gone(web_client, participant, attempt, question):
    deadline = timezone.now() - timedelta(minutes=1)
    QuizAttempt.objects.filter(pk=attempt.pk).update(
        started_at=deadline - timedelta(hours=1), deadline_at=deadline
    )
    web_client.force_login(participant.user)

    assert web_client.get(_image_url(attempt, question)).status_code == 404


def test_somebody_elses_attempt_is_404(web_client, quiz, question, attempt):
    from apps.accounts.tests.factories import ParticipantFactory

    other = ParticipantFactory()
    web_client.force_login(other.user)

    assert web_client.get(_image_url(attempt, question)).status_code == 404


def test_a_question_outside_the_drawn_set_is_404(web_client, participant, quiz, attempt):
    """Identyfikator pytania z puli, którego to podejście nie wylosowało, nie jest furtką do ilustracji."""
    stranger = choice_question(quiz)
    stranger.image = SimpleUploadedFile("inne.png", _png(), content_type="image/png")
    stranger.save(update_fields=["image"])
    assert stranger.pk not in attempt.drawn_question_ids
    web_client.force_login(participant.user)

    assert web_client.get(_image_url(attempt, stranger)).status_code == 404


def test_anonymous_is_sent_to_login(web_client, attempt, question):
    response = web_client.get(_image_url(attempt, question))

    assert response.status_code == 302


def test_the_coordinator_preview_serves_the_image(web_client, coordinator, quiz, question):
    web_client.force_login(coordinator)

    preview = web_client.get(
        reverse("web:coordinator-stage-quiz-preview", args=[quiz.stage.pk])
    ).content.decode()
    url = reverse("web:coordinator-stage-quiz-preview-image", args=[quiz.stage.pk, question.pk])
    response = web_client.get(url)

    assert url in preview
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]


def test_the_participant_cannot_use_the_coordinator_preview_image(web_client, participant, quiz, question):
    web_client.force_login(participant.user)

    url = reverse("web:coordinator-stage-quiz-preview-image", args=[quiz.stage.pk, question.pk])

    assert web_client.get(url).status_code in (302, 403)
