"""Ułamki w rubrykach (po wydaniu 0.35.0): punkty i maksimum kryterium co 0,01 w etapie dowolnym.

Wydanie 0.35.0 dopuściło ocenę 4,25, ale rubryka dalej liczyła w pełnych punktach – recenzent
oceniający według kryteriów nie miał jak jej wystawić. Sprawdzamy oba tryby etapu tą samą miarą:
w etapie „tylko ze skali” nic się nie zmienia (ułamek odpada z dotychczasowym kodem), a w etapie
dowolnym kryterium przyjmuje „1,75” z przecinkiem i kropką, a suma ląduje w ocenie co do setnej.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.scoring import criterion_rule
from apps.competitions.services import set_scoring_scale
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.grading.models import ReviewStatus, RubricCriterion
from apps.grading.rubric import (
    format_criteria_lines,
    parse_criteria_lines,
    rubric_from_post,
    rubric_rows,
    set_criteria,
    validate_rubric,
)
from apps.grading.services import save_draft, submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

DEFAULT_VALUES = [
    {"value": 0, "label": "brak"},
    {"value": 2, "label": "postęp"},
    {"value": 5, "label": "usterki"},
    {"value": 6, "label": "pełne"},
]


def make_free(stage):
    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=CoordinatorFactory(), free_values=True)
    stage.refresh_from_db()


@pytest.fixture
def submission(stage):
    created = locked_submission(stage)
    created.status = SubmissionStatus.IN_REVIEW
    created.save(update_fields=["status"])
    return created


@pytest.fixture
def criteria(submission):
    """Kryteria 2,5 + 3,5 pkt – możliwe tylko w etapie dowolnym (zakładane z pominięciem formularza)."""
    return [
        RubricCriterion.objects.create(
            problem=submission.problem, order=1, title="Pomysł", max_points=Decimal("2.5")
        ),
        RubricCriterion.objects.create(
            problem=submission.problem, order=2, title="Wykonanie", max_points=Decimal("3.5")
        ),
    ]


def payload(criteria, *points):
    return [
        {"criterion_id": criterion.pk, "points": value, "comment": ""}
        for criterion, value in zip(criteria, points, strict=True)
    ]


# --- jedna reguła ---------------------------------------------------------------------------------


def test_regula_kryterium_w_trybie_skali_to_pelne_punkty():
    rule = criterion_rule(Decimal("4.00"), free=False)

    assert rule.accepts(Decimal("4")) and rule.accepts(0)
    assert not rule.accepts(Decimal("2.5"))
    assert not rule.accepts(5)


def test_regula_kryterium_w_trybie_dowolnym_to_zakres_co_setna():
    rule = criterion_rule(Decimal("2.5"), free=True)

    assert rule.accepts(Decimal("2.5")) and rule.accepts(Decimal("0.01"))
    assert not rule.accepts(Decimal("2.51"))
    assert not rule.accepts(Decimal("1.005"))
    assert not rule.accepts(Decimal("-0.5"))


# --- tryb „tylko ze skali”: bez zmian --------------------------------------------------------------


def test_etap_skali_odrzuca_ulamek_w_kryterium_dotychczasowym_kodem(stage, submission):
    set_criteria(submission.problem, [{"max_points": 2, "title": "a"}, {"max_points": 4, "title": "b"}])
    criteria = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))

    for value in (1.5, "1,5", Decimal("1.5")):
        with pytest.raises(DomainError) as exc:
            validate_rubric(submission.problem, payload(criteria, value, 3))
        assert exc.value.machine_code == "INVALID_RUBRIC"
        assert "liczbą całkowitą" in str(exc.value.detail)


def test_etap_skali_liczy_sume_calkowita_jak_dotad(stage, submission):
    set_criteria(submission.problem, [{"max_points": 2, "title": "a"}, {"max_points": 4, "title": "b"}])
    criteria = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))
    review = ReviewFactory(submission=submission)

    submit_review(review, 0, rubric=payload(criteria, 2, 3))

    review.refresh_from_db()
    assert review.score == Decimal(5)
    # W JSON-ie zostają liczby całkowite – dokładnie ten kształt, co przed zmianą.
    assert [item["points"] for item in review.rubric] == [2, 3]
    assert all(type(item["points"]) is int for item in review.rubric)


def test_etap_skali_formularz_z_ulamkiem_odpada_przed_serwisem(stage, submission):
    set_criteria(submission.problem, [{"max_points": 2, "title": "a"}])
    [criterion] = RubricCriterion.objects.filter(problem=submission.problem)

    with pytest.raises(DomainError) as exc:
        rubric_from_post(submission.problem, {f"rubric-{criterion.pk}-points": "1,5"})

    assert exc.value.machine_code == "INVALID_RUBRIC"


# --- tryb dowolny ---------------------------------------------------------------------------------


def test_etap_dowolny_przyjmuje_ulamki_z_przecinkiem_i_kropka(stage, submission, criteria):
    make_free(stage)
    review = ReviewFactory(submission=submission)

    submit_review(review, 0, rubric=payload(criteria, "1,75", 2.5))

    review.refresh_from_db()
    assert review.status == ReviewStatus.SUBMITTED
    assert review.score == Decimal("4.25")
    # Liczby JSON (``points_json``): ułamek jako liczba, nie tekst.
    assert [item["points"] for item in review.rubric] == [1.75, 2.5]


def test_etap_dowolny_suma_z_liczb_calkowitych_zostaje_int_w_json(stage, submission, criteria):
    make_free(stage)
    review = ReviewFactory(submission=submission)

    submit_review(review, 0, rubric=payload(criteria, 2, 3))

    review.refresh_from_db()
    assert review.score == Decimal(5)
    assert [item["points"] for item in review.rubric] == [2, 3]
    assert all(type(item["points"]) is int for item in review.rubric)


def test_etap_dowolny_odrzuca_trzy_miejsca_po_przecinku(stage, submission, criteria):
    make_free(stage)

    with pytest.raises(DomainError) as exc:
        validate_rubric(submission.problem, payload(criteria, "1,755", 1))

    assert exc.value.machine_code == "INVALID_RUBRIC"
    assert "Pomysł" in str(exc.value.detail)


def test_etap_dowolny_pilnuje_ulamkowego_maksimum_kryterium(stage, submission, criteria):
    make_free(stage)

    with pytest.raises(DomainError) as exc:
        validate_rubric(submission.problem, payload(criteria, "2,51", 1))

    assert exc.value.machine_code == "RUBRIC_POINTS_OUT_OF_RANGE"
    # Maksimum pokazane tak, jak na ekranie – „2,5”, a nie „2.50”.
    assert "0–2,5" in str(exc.value.detail)


def test_etap_dowolny_suma_ponad_maksimum_zadania_to_rubric_total(stage, submission):
    make_free(stage)
    set_criteria(
        submission.problem,
        [{"max_points": Decimal("3.5"), "title": "a"}, {"max_points": Decimal("3.5"), "title": "b"}],
    )
    criteria = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))
    review = ReviewFactory(submission=submission)

    with pytest.raises(DomainError) as exc:
        submit_review(review, 0, rubric=payload(criteria, "3,5", "2,75"))

    # 6,25 > 6 (maksimum skali etapu) – system nie zaokrągla, tylko odmawia z sumą po polsku.
    assert exc.value.machine_code == "RUBRIC_TOTAL_NOT_IN_SCALE"
    assert "(6,25)" in str(exc.value.detail)
    review.refresh_from_db()
    assert review.score is None


def test_szkic_w_etapie_dowolnym_sklada_ocene_z_ulamkow(stage, submission, criteria):
    make_free(stage)
    review = ReviewFactory(submission=submission)

    save_draft(review, rubric=[{"criterion_id": criteria[0].pk, "points": "0,5"}])
    review.refresh_from_db()
    assert review.score is None
    assert review.rubric[0]["points"] == 0.5

    save_draft(review, rubric=payload(criteria, "0,5", "3,25"))
    review.refresh_from_db()
    assert review.score == Decimal("3.75")


def test_formularz_panelu_w_etapie_dowolnym_przekazuje_tekst_do_jednej_reguly(stage, submission, criteria):
    make_free(stage)
    data = {
        f"rubric-{criteria[0].pk}-points": " 1,25 ",
        f"rubric-{criteria[1].pk}-points": "",
    }

    items = rubric_from_post(submission.problem, data)
    checked, total = validate_rubric(submission.problem, items, partial=True)

    assert [item["points"] for item in checked] == [1.25, None]
    assert total == Decimal("1.25")


def test_wiersze_rubryki_oddaja_zapisane_ulamki(stage, submission, criteria):
    make_free(stage)
    review = ReviewFactory(submission=submission)
    save_draft(review, rubric=payload(criteria, "1,5", "2"))

    rows = rubric_rows(review)

    assert [row["points"] for row in rows] == [1.5, 2]


def test_api_przyjmuje_ulamki_w_rubryce_i_oddaje_liczby_json(client, stage, submission, criteria):
    make_free(stage)
    review = ReviewFactory(submission=submission)
    client.force_authenticate(review.reviewer.user)

    response = client.post(
        f"/api/grading/reviews/{review.pk}/submit/",
        {"score": 0, "rubric": payload(criteria, 1.75, "2,5")},
        format="json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["score"] == 4.25
    assert [item["points"] for item in body["rubric"]] == [1.75, 2.5]


def test_api_etapu_skali_odrzuca_ulamek_w_rubryce(client, stage, submission):
    set_criteria(submission.problem, [{"max_points": 2, "title": "a"}, {"max_points": 4, "title": "b"}])
    criteria = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))
    review = ReviewFactory(submission=submission)
    client.force_authenticate(review.reviewer.user)

    response = client.post(
        f"/api/grading/reviews/{review.pk}/submit/",
        {"score": 0, "rubric": payload(criteria, 1.5, 3.5)},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RUBRIC"


# --- zapis rubryki: tekst „punkty;tytuł;opis” -----------------------------------------------------


def test_linie_kryteriow_w_etapie_dowolnym_przyjmuja_ulamkowe_maksimum():
    items = parse_criteria_lines("2,5;Pomysł;opis\n0.75;Zapis", free=True)

    assert [item["max_points"] for item in items] == [Decimal("2.5"), Decimal("0.75")]


@pytest.mark.parametrize("text", ["0;Pomysł", "0,001;Pomysł", "1000,01;Pomysł", "x;Pomysł"])
def test_linie_kryteriow_w_etapie_dowolnym_odrzucaja_zle_maksimum(text):
    with pytest.raises(ValueError):
        parse_criteria_lines(text, free=True)


def test_linie_kryteriow_w_etapie_skali_odrzucaja_ulamek_z_wyjasnieniem():
    with pytest.raises(ValueError) as exc:
        parse_criteria_lines("2,5;Pomysł")

    assert "etapie z dowolnymi wartościami" in str(exc.value)


def test_format_linii_pokazuje_maksimum_bez_zbednych_zer_i_wraca_bez_zmian(stage, submission):
    set_criteria(
        submission.problem,
        [{"max_points": 4, "title": "Całość"}, {"max_points": Decimal("2.5"), "title": "Zapis"}],
    )
    saved = list(RubricCriterion.objects.filter(problem=submission.problem).order_by("order"))

    text = format_criteria_lines(saved)

    # Kolumna jest dziesiętna („4.00”), ale do formularza wraca „4” – inaczej etap skali nie
    # przyjąłby własnej rubryki z powrotem.
    assert text == "4;Całość\n2,5;Zapis"
    assert parse_criteria_lines(text, free=True) == [
        {"max_points": Decimal(4), "title": "Całość", "description": ""},
        {"max_points": Decimal("2.5"), "title": "Zapis", "description": ""},
    ]
    assert parse_criteria_lines("4;Całość") == [{"max_points": 4, "title": "Całość", "description": ""}]


def test_zapis_rubryki_bez_zmian_nie_jest_zdarzeniem(stage, submission):
    from apps.core.models import AuditLog

    items = [{"max_points": Decimal("2.5"), "title": "a", "description": ""}]
    set_criteria(submission.problem, items)
    before = AuditLog.objects.filter(action="problem.rubric_set").count()

    set_criteria(submission.problem, [{"max_points": Decimal("2.50"), "title": "a", "description": ""}])

    assert AuditLog.objects.filter(action="problem.rubric_set").count() == before


def test_audyt_zmiany_rubryki_zapisuje_ulamek_jako_liczbe(stage, submission):
    from apps.core.models import AuditLog

    set_criteria(submission.problem, [{"max_points": 2, "title": "a", "description": ""}])
    set_criteria(submission.problem, [{"max_points": Decimal("2.5"), "title": "a", "description": ""}])

    entry = AuditLog.objects.filter(action="problem.rubric_set").order_by("-id").first()
    assert entry.diff["from"][0]["max_points"] == 2
    assert entry.diff["to"][0]["max_points"] == 2.5


# --- baza i przełącznik etapu ---------------------------------------------------------------------


def test_baza_przyjmuje_polowe_punktu_i_odrzuca_zero(submission):
    RubricCriterion.objects.create(problem=submission.problem, title="pół", max_points=Decimal("0.5"))

    with pytest.raises(IntegrityError), transaction.atomic():
        RubricCriterion.objects.create(problem=submission.problem, title="zero", max_points=0)


def test_powrot_do_trybu_skali_blokuje_ulamkowe_maksimum_kryterium(stage):
    make_free(stage)
    problem = ProblemFactory(stage=stage)
    set_criteria(problem, [{"max_points": Decimal("2.5"), "title": "a"}, {"max_points": 3, "title": "b"}])

    with pytest.raises(DomainError) as exc:
        set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=CoordinatorFactory(), free_values=False)

    assert exc.value.machine_code == "FREE_VALUES_IN_USE"
    assert "kryteria rubryk z ułamkowym maksimum: 1" in str(exc.value.detail)
    stage.scoring_scale.refresh_from_db()
    assert stage.scoring_scale.free_values is True


def test_powrot_do_trybu_skali_z_calkowitymi_kryteriami_przechodzi(stage):
    make_free(stage)
    problem = ProblemFactory(stage=stage)
    set_criteria(problem, [{"max_points": Decimal("2.00"), "title": "a"}])

    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=CoordinatorFactory(), free_values=False)

    stage.scoring_scale.refresh_from_db()
    assert stage.scoring_scale.free_values is False
