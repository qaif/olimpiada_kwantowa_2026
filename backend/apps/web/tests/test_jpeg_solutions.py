"""Zdjęcie rozwiązania (JPEG) w panelach: wybór formatu, okno wyboru pliku i podgląd recenzenta."""

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.models import Problem
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def test_formularz_zadania_oferuje_format_jpeg(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/")

    assert "JPEG (zdjęcie rozwiązania)" in response.content.decode()


def test_koordynator_dopuszcza_zdjecie_dla_zadania(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/problems/",
        {
            "number": 4,
            "title": "Konstrukcja na kartce",
            "allowed_formats": ["pdf", "jpg"],
            "max_file_mb": 20,
            "scoring_values": "",
            "max_points": "",
        },
    )

    assert response.status_code == 302
    assert Problem.objects.get(stage=elim_stage, number=4).allowed_formats == ["pdf", "jpg"]


def test_okno_wyboru_pliku_proponuje_oba_rozszerzenia_zdjecia(web_client, participant, entry, elim_stage):
    ProblemFactory(stage=elim_stage, number=5, title="Zdjęcie kartki", allowed_formats=["pdf", "jpg"])
    web_client.force_login(participant.user)

    response = web_client.get(reverse("web:me"))

    assert 'accept=".pdf,.jpg,.jpeg"' in response.content.decode()


def test_recenzent_widzi_zdjecie_zamiast_podgladu_pdf(web_client, elim_stage, problems):
    """Zdjęcie renderuje ``<img>``, a nie płótno pdf.js – decyduje typ MIME wyliczony przez serwer."""
    reviewer = ActiveReviewerFactory()
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage),
        problem=problems[0],
        status=SubmissionStatus.IN_REVIEW,
    )
    SubmissionFileFactory(
        submission=submission,
        av_status=AvStatus.CLEAN,
        mime="image/jpeg",
        object_key="1/1/OLM-TEST/zdjecie/" + "a" * 64 + ".jpg",
        original_name="IMG_4821.jpg",
    )
    review = ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    response = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk}))

    body = response.content.decode()
    assert 'data-preview-kind="image"' in body
    assert "data-preview-image" in body
    assert "data-pdf-canvas" not in body
    # Nazwa pliku od uczestnika nie może stanąć na ekranie recenzenta – ocenianie jest ślepe.
    assert "IMG_4821.jpg" not in body


def test_recenzent_pdf_dostaje_nadal_podglad_pdf(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage),
        problem=problems[0],
        status=SubmissionStatus.IN_REVIEW,
    )
    SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN, mime="application/pdf")
    review = ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    body = web_client.get(reverse("web:review-detail", kwargs={"pk": review.pk})).content.decode()

    assert 'data-preview-kind="pdf"' in body
    assert "data-pdf-canvas" in body
