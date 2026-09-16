"""Powiadomienia e-mail dla uczestnika: potwierdzenie, werdykt skanu, wyniki, reklamacja.

Testy patrzą na ``django.core.mail.outbox`` – tak samo jak testy aktywacji konta – bo w trybie
testowym ``CELERY_TASK_ALWAYS_EAGER`` wykonuje ``send_mail_task`` natychmiast, a backend
``locmem`` zapisuje wiadomość zamiast otwierać gniazdo. Listy są kolejkowane **po commicie**,
więc każdy test, który chce je zobaczyć, opakowuje operację w
``django_capture_on_commit_callbacks`` – dokładnie tak, jak zachowuje się produkcja.

Czego te testy pilnują:

- **czysty skan jest cichy.** Gdyby szedł po nim list, każda wysyłka dawałaby dwie wiadomości,
  a pierwsza przestałaby cokolwiek znaczyć,
- **w liście nie ma punktów.** Powiadomienie o wynikach niesie odnośnik i nic poza nim,
- **konto nieaktywne nie dostaje poczty**, a w audycie zostaje wyłącznie rodzaj powiadomienia.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import GROUP_APPEALS, CommitteeStatus
from apps.accounts.tests.factories import CommitteeMemberFactory, ParticipantFactory, UserFactory
from apps.appeals.models import Appeal, AppealStatus
from apps.appeals.services import decide_appeal
from apps.competitions.models import Stage
from apps.competitions.tests.factories import (
    EditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.services import publish_results
from apps.submissions.antivirus import VERDICT_CLEAN, VERDICT_INFECTED
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.notifications import (
    APPEAL_DECIDED_SUBJECT,
    RESULTS_PUBLISHED_SUBJECT,
    SUBMISSION_INFECTED_SUBJECT,
    SUBMISSION_RECEIVED_SUBJECT,
    TYPE_RESULTS_PUBLISHED,
    TYPE_SUBMISSION_RECEIVED,
)
from apps.submissions.services import apply_scan_verdict, create_submission
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory, pdf_upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    """Otwarty etap eliminacyjny z jednym zadaniem, skalą i progiem kwalifikacji."""
    stage = StageFactory(edition=EditionFactory())
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    ProblemFactory(stage=stage, number=1, title="Splątanie dwóch kubitów")
    return stage


@pytest.fixture
def entry(stage):
    return StageEntryFactory(stage=stage, participant=ParticipantFactory())


def notification_types() -> list[str]:
    return list(AuditLog.objects.filter(action="notification.sent").values_list("diff__type", flat=True))


def close_timeline(stage: Stage) -> None:
    """Etap całkowicie zamknięty – publikacja wyników jest możliwa dopiero wtedy (PROJEKT.md 2.4)."""
    now = timezone.now()
    Stage.objects.filter(pk=stage.pk).update(
        opens_at=now - timedelta(days=60),
        deadline_at=now - timedelta(days=50),
        review_deadline_at=now - timedelta(days=40),
        appeal_window_opens_at=now - timedelta(days=30),
        appeal_window_closes_at=now - timedelta(days=20),
    )
    stage.refresh_from_db()


# --- (a) potwierdzenie przyjęcia rozwiązania ----------------------------------------------------


def test_upload_sends_a_receipt_with_version_and_checksum(entry, stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        submission = create_submission(
            user=entry.participant.user, stage=stage, problem_number=1, upload=pdf_upload()
        )

    message = next(item for item in mail.outbox if item.subject == SUBMISSION_RECEIVED_SUBJECT)
    assert message.to == [entry.participant.user.email]
    assert "Zadanie: 1. Splątanie dwóch kubitów" in message.body
    assert f"Wersja: {submission.version}" in message.body
    assert submission.files.get().sha256 in message.body
    assert notification_types() == [TYPE_SUBMISSION_RECEIVED]


def test_receipt_is_not_sent_to_a_disabled_account(entry, stage, django_capture_on_commit_callbacks):
    """Konto zablokowane albo nieaktywowane nie jest adresem, pod którym ktoś czeka na list."""
    user = entry.participant.user
    user.is_active = False
    user.save(update_fields=["is_active"])

    with django_capture_on_commit_callbacks(execute=True):
        create_submission(user=user, stage=stage, problem_number=1, upload=pdf_upload())

    assert mail.outbox == []
    assert notification_types() == []


def test_receipt_is_not_sent_when_the_upload_is_refused(entry, stage, django_capture_on_commit_callbacks):
    """Odmowa nie zostawia śladu – ani zgłoszenia, ani listu o jego przyjęciu."""
    Stage.objects.filter(pk=stage.pk).update(deadline_at=timezone.now() - timedelta(days=1))
    stage.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(DomainError):
        create_submission(user=entry.participant.user, stage=stage, problem_number=1, upload=pdf_upload())

    assert mail.outbox == []


# --- (b) werdykt skanu ---------------------------------------------------------------------------


def test_clean_scan_is_silent(entry, django_capture_on_commit_callbacks):
    submission_file = SubmissionFileFactory(submission=SubmissionFactory(entry=entry))

    with django_capture_on_commit_callbacks(execute=True):
        apply_scan_verdict(submission_file, VERDICT_CLEAN)

    assert mail.outbox == []
    assert notification_types() == []


def test_infected_scan_tells_the_participant_to_resend(entry, django_capture_on_commit_callbacks):
    submission_file = SubmissionFileFactory(submission=SubmissionFactory(entry=entry))

    with django_capture_on_commit_callbacks(execute=True):
        apply_scan_verdict(submission_file, VERDICT_INFECTED, "Eicar-Test-Signature")

    message = next(item for item in mail.outbox if item.subject == SUBMISSION_INFECTED_SUBJECT)
    assert message.to == [entry.participant.user.email]
    assert "wyślij" in message.body
    # Sygnatura wirusa nie jest informacją dla uczestnika – zostaje w bazie i w logu workera.
    assert "Eicar-Test-Signature" not in message.body
    submission_file.refresh_from_db()
    assert submission_file.av_status == AvStatus.INFECTED


# --- (c) ogłoszenie wyników ----------------------------------------------------------------------


def test_publication_notifies_every_participant_of_the_stage(
    stage, entry, django_capture_on_commit_callbacks
):
    second = StageEntryFactory(stage=stage, participant=ParticipantFactory())
    problem = stage.problems.get()
    for target in (entry, second):
        submission = SubmissionFactory(entry=target, problem=problem, status=SubmissionStatus.FINAL)
        FinalGradeFactory(submission=submission, score=6)
    close_timeline(stage)

    with django_capture_on_commit_callbacks(execute=True):
        publish_results(stage, UserFactory(), "CODE")

    letters = [item for item in mail.outbox if item.subject == RESULTS_PUBLISHED_SUBJECT]
    assert len(letters) == 2
    assert f"/results/{stage.pk}/" in letters[0].body
    assert f"/me/stages/{stage.pk}/feedback/" in letters[0].body
    # Punktów w liście nie ma: skrzynka pocztowa nie jest kanałem, w którym trzyma się wynik.
    assert "pkt" not in letters[0].body
    assert notification_types() == [TYPE_RESULTS_PUBLISHED]


# --- (d) decyzja w sprawie reklamacji -------------------------------------------------------------


def test_appeal_decision_reaches_the_participant(stage, entry, django_capture_on_commit_callbacks):
    submission = SubmissionFactory(
        entry=entry, problem=stage.problems.get(), status=SubmissionStatus.APPEALED
    )
    FinalGradeFactory(submission=submission, score=2)
    appeal = Appeal.objects.create(
        submission=submission,
        filed_by=entry.participant,
        argument="Proszę o ponowne sprawdzenie drugiego kroku dowodu.",
        status=AppealStatus.OPEN,
    )
    member = CommitteeMemberFactory(
        user=UserFactory(groups=[GROUP_APPEALS]),
        is_appeals_committee=True,
        status=CommitteeStatus.ACTIVE,
    )

    with django_capture_on_commit_callbacks(execute=True):
        decide_appeal(
            appeal,
            member,
            AppealStatus.REJECTED,
            justification="Rozwiązanie nie zawiera brakującego kroku.",
        )

    message = next(item for item in mail.outbox if item.subject == APPEAL_DECIDED_SUBJECT)
    assert message.to == [entry.participant.user.email]
    assert "Rozwiązanie nie zawiera brakującego kroku." in message.body
    assert "odrzucona" in message.body
