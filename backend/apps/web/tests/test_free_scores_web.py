"""Dowolne wartości ocen w panelu (wydanie 0.35.0): recenzent, koordynator, zadanie z samym maksimum.

Ekrany mają pokazać to samo, co przyjmie serwis: w etapie „tylko ze skali” listę radio jak dotąd,
w etapie z dowolnymi wartościami pole liczbowe z krokiem 0,01, granicami zakresu i wartościami skali
obok jako podpowiedzią; przełącznik trybu na ekranie skali z odmową powrotu, gdy są oceny spoza skali.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.competitions.models import Problem, ScoringScale
from apps.grading.models import FinalGrade, Review, ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

SCALE_TEXT = "0;brak istotnego postępu\n2;istotny postęp\n5;drobne usterki\n6;rozwiązanie pełne i poprawne"


def scale_url(stage) -> str:
    return reverse("web:coordinator-stage-scale", args=[stage.pk])


def free(stage):
    scale = stage.scoring_scale
    scale.free_values = True
    scale.save(update_fields=["free_values"])
    return stage


@pytest.fixture
def submission(entry, problems):
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)


@pytest.fixture
def review(submission, reviewer):
    return ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)


# --- recenzent ------------------------------------------------------------------------------------


def test_etap_skali_ma_radio_i_maksimum_zadania(web_client, review):
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert 'name="score" value="5"' in content
    assert 'type="number" id="id_score"' not in content
    assert "Maksimum za to zadanie: 6 pkt." in content


def test_etap_dowolny_ma_pole_liczbowe_z_zakresem_i_podpowiedzia(web_client, review, elim_stage):
    free(elim_stage)
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert 'type="number" id="id_score" name="score"' in content
    assert 'step="0.01" min="0" max="6"' in content
    assert 'inputmode="decimal"' in content
    assert 'type="radio" name="score"' not in content
    assert "Punkty (max 6)" in content
    assert "rozwiązanie pełne i poprawne" in content  # podpowiedź ze skali


def test_recenzent_wystawia_4_25_przecinkiem(web_client, review, elim_stage):
    free(elim_stage)
    ReviewFactory(submission=review.submission, status=ReviewStatus.ASSIGNED)
    web_client.force_login(review.reviewer.user)

    response = web_client.post(
        f"/review/{review.pk}/submit/",
        {"score": "4,25", "comment_internal": "", "comment_for_participant": "", "annotations": ""},
    )

    assert response.status_code == 302
    review.refresh_from_db()
    assert review.status == ReviewStatus.SUBMITTED
    assert review.score == Decimal("4.25")


def test_zly_format_wraca_z_komunikatem_pola(web_client, review, elim_stage):
    free(elim_stage)
    web_client.force_login(review.reviewer.user)

    response = web_client.post(
        f"/review/{review.pk}/submit/",
        {"score": "4,255", "comment_internal": "", "comment_for_participant": "", "annotations": ""},
        follow=True,
    )

    review.refresh_from_db()
    assert review.status == ReviewStatus.ASSIGNED
    assert "dwa miejsca po przecinku" in response.content.decode()


def test_wystawiona_ocena_wraca_do_pola_z_kropka(web_client, review, elim_stage):
    free(elim_stage)
    review.score = Decimal("4.25")
    review.save(update_fields=["score"])
    web_client.force_login(review.reviewer.user)

    content = web_client.get(f"/review/{review.pk}/").content.decode()

    assert 'value="4.25"' in content


def test_szkic_przyjmuje_przecinek(web_client, review, elim_stage):
    free(elim_stage)
    web_client.force_login(review.reviewer.user)

    web_client.post(
        f"/review/{review.pk}/draft/",
        {"score": "3,5", "comment_internal": "", "comment_for_participant": "", "annotations": ""},
        HTTP_HX_REQUEST="true",
    )

    assert Review.objects.get(pk=review.pk).score == Decimal("3.50")


# --- koordynator: skala etapu ---------------------------------------------------------------------


def test_ekran_skali_ma_przelacznik_trybu(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    content = web_client.get(scale_url(elim_stage)).content.decode()

    assert "tylko wartości ze skali" in content
    assert "dowolna wartość od min do max (co 0,01)" in content
    assert 'value="scale"' in content and 'value="free"' in content


def test_przelaczenie_na_dowolne_wartosci(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SCALE_TEXT, "max_value": 6, "mode": "free"})

    assert response.status_code == 302
    assert ScoringScale.objects.get(stage=elim_stage).free_values is True


def test_powrot_do_skali_z_ocena_4_25_odmowiony_z_licznikiem(web_client, coordinator, elim_stage, submission):
    free(elim_stage)
    FinalGradeFactory(submission=submission, score=Decimal("4.25"))
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SCALE_TEXT, "max_value": 6, "mode": "scale"})

    assert response.status_code == 409
    content = response.content.decode()
    assert "oceny spoza skali: 1" in content
    assert ScoringScale.objects.get(stage=elim_stage).free_values is True


def test_zapis_bez_pola_trybu_go_nie_zmienia(web_client, coordinator, elim_stage):
    free(elim_stage)
    web_client.force_login(coordinator)

    web_client.post(scale_url(elim_stage), {"values": SCALE_TEXT, "max_value": 6})

    assert ScoringScale.objects.get(stage=elim_stage).free_values is True


def test_ekran_skali_innego_konkursu_to_404(client_for, other_competition, elim_stage):
    """Zakres konkursu bez zmian: koordynator obcego konkursu nie przełączy cudzego etapu."""
    from apps.accounts.models import CompetitionRole
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.tenancy.tests.factories import grant_membership

    stranger = CoordinatorFactory()
    grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(stranger)

    response = client.post(scale_url(elim_stage), {"values": SCALE_TEXT, "max_value": 6, "mode": "free"})

    assert response.status_code == 404
    assert ScoringScale.objects.get(stage=elim_stage).free_values is False


# --- koordynator: zadanie z samym maksimum ---------------------------------------------------------


def problem_payload(problem, **overrides) -> dict:
    data = {
        "number": problem.number,
        "title": problem.title,
        "allowed_formats": ["pdf"],
        "max_file_mb": 20,
        "scoring_values": "",
        "max_points": "",
    }
    data.update(overrides)
    return data


def test_samo_maksimum_przyjete_w_etapie_dowolnym(web_client, coordinator, elim_stage, problems):
    free(elim_stage)
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problems[0].pk]),
        problem_payload(problems[0], max_points="12,5"),
    )

    assert response.status_code == 302
    assert Problem.objects.get(pk=problems[0].pk).max_points == Decimal("12.5")
    listing = web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").content.decode()
    assert "12,5" in listing
    assert "Maksymalna suma etapu: <strong>18,5 pkt</strong>" in listing


def test_samo_maksimum_odrzucone_w_etapie_skali(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problems[0].pk]),
        problem_payload(problems[0], max_points="7"),
    )

    assert response.status_code in (200, 400)
    assert "Samo maksimum wolno podać tylko w etapie z dowolnymi wartościami" in response.content.decode()
    assert Problem.objects.get(pk=problems[0].pk).max_points is None


def test_korekta_koordynatora_polem_liczbowym(web_client, coordinator, elim_stage, submission):
    free(elim_stage)
    submission.status = SubmissionStatus.GRADED_PROVISIONAL
    submission.save(update_fields=["status"])
    FinalGradeFactory(submission=submission, score=5)
    web_client.force_login(coordinator)

    page = web_client.get(f"/coordinator/stages/{elim_stage.pk}/assignments/").content.decode()
    assert f'id="final-score-{submission.pk}" name="score"' in page
    assert 'step="0.01"' in page

    web_client.post(
        reverse("web:coordinator-final-grade", args=[submission.pk]),
        {"score": "5,5", "rationale": "Korekta po posiedzeniu komisji."},
    )

    assert FinalGrade.objects.get(submission=submission).score == Decimal("5.50")


def test_moderacja_pokazuje_ulamek_z_przecinkiem(web_client, coordinator, elim_stage, submission):
    free(elim_stage)
    submission.status = SubmissionStatus.MODERATION
    submission.save(update_fields=["status"])
    for score in (Decimal("4.25"), Decimal("5")):
        ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=score)
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/moderation/").content.decode()

    assert ">4,25<" in content
    assert "4,25" in content and "5,00" not in content
