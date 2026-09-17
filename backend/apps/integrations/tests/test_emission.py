"""Wpięcie emisji w serwisy domenowe: co, kiedy i z jakim ładunkiem wychodzi na zewnątrz.

Przedmiotem są **punkty wpięcia**, a nie mechanika doręczenia (ta jest w ``test_webhooks``).
Dlatego wszędzie ``django_capture_on_commit_callbacks``: wiersze doręczeń powstają w transakcji
serwisu, a wysyłka jest zaplanowana na ``on_commit`` – w teście transakcja nigdy się nie
zatwierdza, więc bez przechwycenia zadanie nie ruszyłoby i nic by tego nie zauważyło.

Każdy test sprawdza także, czego w ładunku **nie ma**: webhook leci na cudzy serwer, więc dane
osobowe w nim byłyby wyciekiem, którego nikt po naszej stronie nie zobaczy.
"""

import pytest
from django.utils import timezone

from apps.integrations.models import (
    EVENT_APPEAL_DECIDED,
    EVENT_REGISTRATION_CREATED,
    EVENT_RESULTS_PUBLISHED,
    EVENT_STAGE_CLOSED,
    EVENT_SUBMISSION_RECEIVED,
    WebhookDelivery,
)
from apps.results.models import Anonymization
from apps.results.services import publish_results
from apps.results.tests.conftest import graded_entry, make_stage
from apps.submissions.services import close_stage_now

pytestmark = pytest.mark.django_db

LAST_NAME = "Śniadecka"


def payload_of(event: str) -> dict:
    delivery = WebhookDelivery.objects.get(event=event)
    return delivery.payload


def test_publishing_results_emits_event(endpoint, edition, coordinator, django_capture_on_commit_callbacks):
    stage = make_stage(edition=edition)
    graded_entry(stage, [6, 5], user__last_name=LAST_NAME)

    with django_capture_on_commit_callbacks(execute=False):
        publication = publish_results(stage, coordinator, Anonymization.CODE)

    payload = payload_of(EVENT_RESULTS_PUBLISHED)
    assert payload["stage_id"] == stage.pk
    assert payload["edition_id"] == edition.pk
    assert payload["publication_id"] == publication.pk
    assert payload["rows"] == 1
    # Tabela wyników do ładunku nie wchodzi – od niej jest ``/api/v1/stages/<id>/results/``.
    assert "rows_data" not in payload
    assert LAST_NAME not in str(payload)


def test_closing_stage_emits_event(endpoint, stage, coordinator, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False):
        close_stage_now(stage, actor=coordinator)

    payload = payload_of(EVENT_STAGE_CLOSED)
    assert payload["stage_id"] == stage.pk
    assert payload["manual"] is True
    assert payload["closed_at"] is not None


def test_due_stage_closed_by_beat_emits_the_same_event(endpoint, stage, django_capture_on_commit_callbacks):
    """Zamknięcie z zegara nie jest dla partnera innym faktem niż zamknięcie ręczne."""
    from apps.submissions.services import close_due_stages

    # Etap z fabryki ma deadline dawno za sobą (otwarty 90 dni temu), więc przebieg beatu
    # zastanie go jako „do zamknięcia” bez przestawiania czegokolwiek. Ręczne cofnięcie
    # ``deadline_at`` do „teraz” złamałoby zresztą constraint „deadline przed recenzjami”.
    assert stage.submission_deadline < timezone.now()

    with django_capture_on_commit_callbacks(execute=False):
        close_due_stages()

    assert payload_of(EVENT_STAGE_CLOSED)["manual"] is False


def test_registration_emits_event_without_personal_data(
    endpoint, edition, django_capture_on_commit_callbacks
):
    from apps.accounts.services import register_participant

    with django_capture_on_commit_callbacks(execute=False):
        participant = register_participant(
            email="nowa@example.test",
            password="Poprawne-Haslo-2026",
            first_name="Łucja",
            last_name=LAST_NAME,
            district="mazowieckie",
            birth_year=timezone.now().year - 30,
            grade=3,
            school="XIV LO Warszawa",
            phone="600100200",
            gdpr_consent=True,
            terms_consent=True,
        )

    payload = payload_of(EVENT_REGISTRATION_CREATED)
    assert payload["participant_code"] == participant.public_code
    assert payload["edition_id"] == edition.pk
    assert payload["voivodeship"] == "mazowieckie"
    assert LAST_NAME not in str(payload)
    assert "nowa@example.test" not in str(payload)


def test_submission_payload_carries_metadata_only(endpoint, stage, entry):
    """Ładunek zgłoszenia buduje ``events`` – nazwa pliku i skrót zostają w systemie."""
    from apps.integrations.events import submission_received
    from apps.submissions.tests.factories import SubmissionFactory

    problem = stage.problems.order_by("number").first()
    submission = SubmissionFactory(entry=entry, problem=problem, version=3)
    submission_received(submission)

    payload = payload_of(EVENT_SUBMISSION_RECEIVED)
    assert payload["submission_id"] == submission.pk
    assert payload["participant_code"] == entry.participant.public_code
    assert payload["problem_number"] == problem.number
    assert payload["version"] == 3
    assert LAST_NAME not in str(payload)


def test_appeal_payload_has_no_justification(endpoint, stage, entry):
    """Uzasadnienie decyzji jest tekstem o człowieku i w ładunku go nie ma."""
    from apps.appeals.models import Appeal, AppealDecision, AppealStatus
    from apps.integrations.events import appeal_decided
    from apps.submissions.tests.factories import SubmissionFactory

    submission = SubmissionFactory(entry=entry, problem=stage.problems.first())
    appeal = Appeal.objects.create(
        submission=submission, filed_by=entry.participant, argument="Proszę o ponowną ocenę."
    )
    appeal.status = AppealStatus.ACCEPTED
    appeal.save(update_fields=["status"])
    decision = AppealDecision.objects.create(
        appeal=appeal, new_score=5, justification="Rozwiązanie jest jednak pełne."
    )

    appeal_decided(appeal, decision)

    payload = payload_of(EVENT_APPEAL_DECIDED)
    assert payload["appeal_id"] == appeal.pk
    assert payload["status"] == AppealStatus.ACCEPTED
    assert payload["score_changed"] is True
    assert "Rozwiązanie jest jednak pełne." not in str(payload)


def test_no_subscriber_means_no_work(stage, coordinator, django_capture_on_commit_callbacks):
    """Serwis bez ani jednej integracji nie zapisuje niczego – to jest stan domyślny."""
    with django_capture_on_commit_callbacks(execute=False):
        close_stage_now(stage, actor=coordinator)

    assert WebhookDelivery.objects.count() == 0
