"""List „Twoje zaświadczenie z warsztatów jest gotowe”: przełącznik, jeden list na dokument, treść."""

from __future__ import annotations

import pytest

from apps.workshop_certificates.models import AutoIssuedCertificate, AutoIssueTrigger
from apps.workshop_certificates.notifications import notify_ready
from apps.workshop_certificates.rules import issue_due

from .helpers import attend, enable, participant_in

pytestmark = pytest.mark.django_db


def run(edition):
    return issue_due(edition, trigger=AutoIssueTrigger.RECALCULATE)


def test_mail_goes_out_when_the_switch_is_on(stage, mailoutbox, django_capture_on_commit_callbacks):
    enable(stage.edition, threshold=1, notify=True)
    participant = participant_in(stage)
    attend(participant, 1)

    with django_capture_on_commit_callbacks(execute=True):
        result = run(stage.edition)

    assert result.notified == 1
    assert [message.to for message in mailoutbox] == [[participant.user.email]]
    message = mailoutbox[0]
    assert "Zaświadczenie z warsztatów jest gotowe" in message.subject
    assert "/me/certificates/" in message.body
    assert stage.edition.year_label in message.body
    # Numer i kod dokumentu uczestnik zobaczy po zalogowaniu – list ich nie niesie.
    assert result.issued[0].code not in message.body
    assert AutoIssuedCertificate.objects.get().notified_at is not None


def test_no_mail_when_the_switch_is_off(stage, mailoutbox, django_capture_on_commit_callbacks):
    enable(stage.edition, threshold=1, notify=False)
    attend(participant_in(stage), 1)

    with django_capture_on_commit_callbacks(execute=True):
        result = run(stage.edition)

    assert len(result.issued) == 1
    assert mailoutbox == []
    assert AutoIssuedCertificate.objects.get().notified_at is None


def test_one_mail_per_certificate_even_when_notified_twice(
    stage, mailoutbox, django_capture_on_commit_callbacks
):
    enable(stage.edition, threshold=1, notify=True)
    attend(participant_in(stage), 1)

    with django_capture_on_commit_callbacks(execute=True):
        [certificate] = run(stage.edition).issued
        assert notify_ready(certificate) is False
        run(stage.edition)

    assert len(mailoutbox) == 1
