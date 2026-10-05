"""Poprawki po przeglądzie PR #97 (CONS-01): praca w toku (H1), wersja w formularzu (L3),
nieświeży cache na ekranie (L4) i ``next`` dla HTMX (L5).

H1 jest tu najważniejsze: zmiana wersji dokumentu w trakcie etapu nie może zabrać uczniowi pracy –
„Zakończ” testu z kompletem odpowiedzi, wysyłka rozwiązania (WWW i API) przechodzą z banerem.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts import consents
from apps.accounts.consents import ConsentKind
from apps.accounts.models import ConsentDefinition, ConsentRecord
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.consent_gate import services, state
from apps.core.api import DomainError
from apps.quiz.models import AttemptStatus, QuizAnswer, QuizAttempt
from apps.quiz.tests.factories import QuizFactory, QuizStageFactory, choice_question, text_question
from apps.submissions.models import Submission
from apps.submissions.tests.factories import pdf_upload

from .conftest import give, make_adult, seen
from .test_gate import SCREEN, enable_definitions

pytestmark = pytest.mark.django_db

NEW_VERSION = "2.0 z 1 listopada 2026"
BANNER = "Organizator zmienił dokument"


def bump_privacy(competition) -> None:
    definition = ConsentDefinition.objects.get(competition=competition, kind=ConsentKind.PRIVACY)
    consents.change_version(definition, NEW_VERSION)


@pytest.fixture
def consenting(competition, adult):
    """Uczestnik z kompletem zgód w konkursie z definicjami z bazy (wersję da się zmienić ekranem)."""
    enable_definitions(competition)
    give(adult)
    return adult


# --- H1: praca w toku ------------------------------------------------------------------------------


def test_version_bump_mid_attempt_keeps_the_sheet_and_the_finish_saves_every_answer(
    web, competition, consenting
):
    quiz = QuizFactory(stage=QuizStageFactory(edition=CurrentEditionFactory()))
    entry = StageEntryFactory(participant=consenting, stage=quiz.stage, status=StageEntryStatus.QUALIFIED)
    choice = choice_question(quiz, points=Decimal("2"))
    text = text_question(quiz, accepted=("splątanie",), points=Decimal("1"))
    web.force_login(consenting.user)
    assert web.post(reverse("web:quiz-start", args=[quiz.stage.pk])).status_code == 302
    attempt = QuizAttempt.objects.get(entry=entry)

    bump_privacy(competition)

    # Odświeżenie arkusza w trakcie podejścia – arkusz zostaje, nad nim baner.
    sheet = web.get(reverse("web:quiz-attempt", args=[attempt.pk]))
    assert sheet.status_code == 200
    assert BANNER in sheet.content.decode()
    # „Zakończ” z kompletem odpowiedzi (ścieżka bez JavaScriptu) – nic nie przepada.
    correct = choice.options.filter(is_correct=True).first().pk
    finish = web.post(
        reverse("web:quiz-attempt", args=[attempt.pk]),
        {f"q{choice.pk}": str(correct), f"q{text.pk}": "splątanie"},
    )

    assert finish.status_code == 302
    assert finish["Location"] == reverse("web:quiz-result", args=[attempt.pk])
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.SUBMITTED
    assert QuizAnswer.objects.filter(attempt=attempt).count() == 2
    assert attempt.score == Decimal("3.00")
    # Dopiero ekran wyniku (poza pracą w toku) prosi o nową wersję zgody.
    assert web.get(finish["Location"])["Location"].startswith(SCREEN)


def _open_stage(participant):
    stage = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    ProblemFactory(stage=stage, number=1)
    return StageEntryFactory(participant=participant, stage=stage)


def test_upload_mid_window_succeeds_after_a_version_bump(web, competition, consenting):
    entry = _open_stage(consenting)
    web.force_login(consenting.user)
    bump_privacy(competition)

    response = web.post(
        f"/me/stages/{entry.stage_id}/problems/1/upload/",
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert "wersja 1" in response.content.decode()
    assert Submission.objects.filter(entry=entry).count() == 1
    # Panel (poza pracą w toku) nadal prosi o zgodę – z banerem z wysyłki czekającym na ekranie.
    panel = web.get("/me/", follow=True)
    assert panel.redirect_chain[-1][0].startswith(SCREEN)
    assert BANNER in panel.content.decode()


def test_api_upload_mid_window_succeeds_and_flags_the_renewal(web, competition, consenting):
    entry = _open_stage(consenting)
    web.force_login(consenting.user)
    bump_privacy(competition)

    response = web.post(f"/api/stages/{entry.stage_id}/problems/1/submissions/", {"file": pdf_upload()})

    assert response.status_code == 201, response.content
    assert response["X-Consents-Required"] == SCREEN
    assert Submission.objects.filter(entry=entry).count() == 1


def test_participant_who_never_consented_cannot_upload(web, adult):
    entry = _open_stage(adult)
    give(adult, {ConsentKind.TERMS})
    web.force_login(adult.user)

    response = web.post(
        f"/me/stages/{entry.stage_id}/problems/1/upload/",
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )
    api = web.post(f"/api/stages/{entry.stage_id}/problems/1/submissions/", {"file": pdf_upload()})

    assert response.status_code == 403
    assert response["HX-Redirect"].startswith(SCREEN)
    assert api.status_code == 403
    assert not Submission.objects.filter(entry=entry).exists()


# --- L3: wersja dokumentu jedzie z formularzem -------------------------------------------------------


def test_version_changed_between_get_and_post_is_refused_and_re_rendered(web, competition, consenting):
    bump_privacy(competition)
    web.force_login(consenting.user)
    assert f'value="{NEW_VERSION}"' in web.get(SCREEN).content.decode()
    newer = "3.0 z 2 listopada 2026"
    definition = ConsentDefinition.objects.get(competition=competition, kind=ConsentKind.PRIVACY)
    consents.change_version(definition, newer)

    stale = web.post(SCREEN, {"gdpr_consent": "on", "gdpr_consent__version": NEW_VERSION, "next": "/me/"})

    assert stale.status_code == 400
    html = stale.content.decode()
    assert "Ten dokument zmienił się, kiedy ten ekran był otwarty." in html
    assert f'value="{newer}"' in html
    assert not ConsentRecord.objects.filter(
        participant=consenting, document_version__in=[NEW_VERSION, newer]
    ).exists()

    fresh = web.post(SCREEN, seen(competition, gdpr_consent="on", next="/me/"))
    assert fresh["Location"] == "/me/"
    assert ConsentRecord.objects.filter(participant=consenting, document_version=newer).exists()


def test_service_refuses_a_version_the_participant_did_not_see(competition, consenting):
    bump_privacy(competition)

    with pytest.raises(DomainError) as refused:
        services.complete_consents(consenting, {ConsentKind.PRIVACY}, versions={ConsentKind.PRIVACY: "1.0"})

    assert refused.value.machine_code == "CONSENT_VERSION_CHANGED"
    assert not ConsentRecord.objects.filter(participant=consenting, document_version=NEW_VERSION).exists()


# --- L4: nieświeży cache nie zapętla ----------------------------------------------------------------


def test_screen_with_nothing_missing_forgets_a_stale_state(web, competition, adult):
    give(adult)
    key = state.state_key(competition.pk, adult.user_id)
    cache.set(key, (adult.pk, adult.birth_date, adult.birth_year, frozenset()), state.STATE_TTL)
    web.force_login(adult.user)

    response = web.get(f"{SCREEN}?next=/me/")

    assert response["Location"] == "/me/"
    assert cache.get(key) is None
    assert web.get("/me/").status_code == 200


# --- L5: ``next`` dla HTMX z ``HX-Current-URL`` -----------------------------------------------------


def test_htmx_next_is_the_current_page_of_the_same_site(web, competition, adult):
    web.force_login(adult.user)
    host = competition.primary_domain

    same = web.get("/me/", HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL=f"http://{host}/me/?tab=wyniki")
    foreign = web.get("/me/", HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="https://evil.example/me/")

    assert same["HX-Redirect"] == f"{SCREEN}?next=%2Fme%2F%3Ftab%3Dwyniki"
    assert foreign["HX-Redirect"] == SCREEN


def test_other_participant_without_a_bump_still_needs_the_screen_for_the_panel(web, competition):
    other = make_adult(competition=competition)
    give(other, {ConsentKind.PRIVACY})
    web.force_login(other.user)

    assert web.get("/me/")["Location"].startswith(SCREEN)
