"""Skala punktacji edytowana przez koordynatora: etap, nadpisanie w zadaniu i blokada wartości.

Prośba organizatora: „skala punktacji zadań, jak i dozwolone punkty, powinny być edytowalne przez
koordynatora”. Dwa poziomy (etap i zadanie) oraz jedna reguła, której nie wolno zgubić: wartości
raz wystawionej w ocenie nie da się ze skali usunąć.
"""

import pytest
from django.urls import reverse

from apps.competitions.models import Problem, ScoringScale
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ReviewStatus
from apps.grading.services import allowed_scores, submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

#: Skala „szkolna” 0–3 – inna niż domyślna 0/2/5/6, więc różnica widać bez patrzenia na etap.
SIMPLE_SCALE = "0;brak\n1;szkic\n2;prawie\n3;pełne"


def scale_url(stage) -> str:
    return reverse("web:coordinator-stage-scale", args=[stage.pk])


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


# --- skala etapu -------------------------------------------------------------------------------


def test_ekran_pokazuje_obecna_skale_etapu(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(scale_url(elim_stage))

    body = response.content.decode()
    assert response.status_code == 200
    assert "0;brak istotnego postępu" in body
    assert "rozwiązanie pełne i poprawne" in body


def test_zapis_zmienia_skale_i_zostawia_slad_w_audycie(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SIMPLE_SCALE, "max_value": 3})

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.scoring_scale.allowed_values() == {0, 1, 2, 3}
    assert elim_stage.scoring_scale.max_value == 3
    log = AuditLog.objects.get(action="stage.scale_updated", target_id=str(elim_stage.pk))
    assert log.diff["from"]["max_value"] == 6
    assert log.diff["to"]["max_value"] == 3


def test_etap_bez_skali_dostaje_ja_z_tego_ekranu(web_client, coordinator, elim_stage):
    ScoringScale.objects.filter(stage=elim_stage).delete()
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SIMPLE_SCALE, "max_value": 3})

    assert response.status_code == 302
    assert ScoringScale.objects.get(stage=elim_stage).allowed_values() == {0, 1, 2, 3}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ("0 brak\n3;pełne", "brakuje średnika"),
        ("zero;brak\n3;pełne", "nie jest liczbą całkowitą"),
        ("0;\n3;pełne", "brakuje opisu"),
    ],
)
def test_bledny_zapis_wiersza_staje_pod_polem(web_client, coordinator, elim_stage, values, expected):
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": values, "max_value": 3})

    assert response.status_code == 400
    assert expected in response.content.decode()
    assert elim_stage.scoring_scale.max_value == 6


def test_maksimum_niezgodne_ze_skala_jest_odrzucane(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SIMPLE_SCALE, "max_value": 6})

    assert response.status_code == 400
    elim_stage.refresh_from_db()
    assert elim_stage.scoring_scale.allowed_values() == {0, 2, 5, 6}


def test_usuniecie_wartosci_juz_wystawionej_konczy_sie_odmowa(web_client, coordinator, elim_stage, problems):
    """``409 SCALE_LOCKED``: ocena spoza skali rozjeżdżałaby tabelę wyników i listę wyboru."""
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage),
        problem=problems[0],
        status=SubmissionStatus.IN_REVIEW,
    )
    ReviewFactory(submission=submission, score=5, status=ReviewStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(scale_url(elim_stage), {"values": SIMPLE_SCALE, "max_value": 3})

    assert response.status_code == 409
    elim_stage.refresh_from_db()
    assert elim_stage.scoring_scale.allowed_values() == {0, 2, 5, 6}


def test_dolozenie_wartosci_i_zmiana_opisu_sa_zawsze_dozwolone(web_client, coordinator, elim_stage, problems):
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage),
        problem=problems[0],
        status=SubmissionStatus.IN_REVIEW,
    )
    ReviewFactory(submission=submission, score=5, status=ReviewStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(
        scale_url(elim_stage),
        {"values": "0;nic\n2;postęp\n4;połowa\n5;prawie pełne\n6;pełne", "max_value": 6},
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.scoring_scale.allowed_values() == {0, 2, 4, 5, 6}


# --- skala zadania -----------------------------------------------------------------------------


def test_koordynator_nadaje_zadaniu_wlasna_skale(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problems[0].pk]),
        problem_payload(problems[0], scoring_values=SIMPLE_SCALE, max_points=3),
    )

    problem = Problem.objects.get(pk=problems[0].pk)
    assert response.status_code == 302
    assert problem.allowed_values() == {0, 1, 2, 3}
    assert problem.max_points == 3


def test_skala_zadania_wygrywa_ze_skala_etapu(elim_stage, problems):
    problems[0].scoring_values = [{"value": 0, "label": "brak"}, {"value": 10, "label": "pełne"}]
    problems[0].max_points = 10
    problems[0].save(update_fields=["scoring_values", "max_points"])

    assert allowed_scores(elim_stage, problems[0]) == {0, 10}
    # Zadanie bez nadpisania dziedziczy skalę etapu – puste znaczy „dziedzicz”, nie „brak skali”.
    assert allowed_scores(elim_stage, problems[1]) == {0, 2, 5, 6}


def test_recenzent_wystawia_ocene_ze_skali_zadania(elim_stage, problems):
    """``submit_review`` przyjmuje 10 (skala zadania), a odrzuca 5 (wartość wyłącznie etapowa)."""
    problems[0].scoring_values = [{"value": 0, "label": "brak"}, {"value": 10, "label": "pełne"}]
    problems[0].max_points = 10
    problems[0].save(update_fields=["scoring_values", "max_points"])
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage),
        problem=problems[0],
        status=SubmissionStatus.IN_REVIEW,
    )
    first = ReviewFactory(submission=submission, status=ReviewStatus.ASSIGNED)
    second = ReviewFactory(submission=submission, status=ReviewStatus.ASSIGNED)

    with pytest.raises(DomainError) as excinfo:
        submit_review(first, 5)
    assert excinfo.value.machine_code == "SCORE_NOT_IN_SCALE"

    submit_review(second, 10)
    second.refresh_from_db()
    assert second.score == 10


def test_wyczyszczenie_skali_zadania_wraca_do_skali_etapu(web_client, coordinator, elim_stage, problems):
    problems[0].scoring_values = [{"value": 0, "label": "brak"}, {"value": 6, "label": "pełne"}]
    problems[0].max_points = 6
    problems[0].save(update_fields=["scoring_values", "max_points"])
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problems[0].pk]),
        problem_payload(problems[0]),
    )

    problem = Problem.objects.get(pk=problems[0].pk)
    assert response.status_code == 302
    assert problem.scoring_values is None
    assert allowed_scores(elim_stage, problem) == {0, 2, 5, 6}


def test_skala_zadania_bez_maksimum_nie_przechodzi(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problems[0].pk]),
        problem_payload(problems[0], scoring_values=SIMPLE_SCALE),
    )

    assert response.status_code == 400
    assert "Podaj maksimum punktów" in response.content.decode()


def test_zadanie_nie_traci_ze_skali_wartosci_juz_wystawionej(web_client, coordinator, elim_stage, problems):
    problem = ProblemFactory(
        stage=elim_stage,
        number=9,
        scoring_values=[{"value": 0, "label": "brak"}, {"value": 10, "label": "pełne"}],
        max_points=10,
    )
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=elim_stage), problem=problem, status=SubmissionStatus.IN_REVIEW
    )
    ReviewFactory(submission=submission, score=10, status=ReviewStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-problem-edit", args=[problem.pk]),
        problem_payload(problem, scoring_values="0;brak\n6;pełne", max_points=6),
    )

    problem.refresh_from_db()
    assert response.status_code == 409
    assert problem.allowed_values() == {0, 10}
