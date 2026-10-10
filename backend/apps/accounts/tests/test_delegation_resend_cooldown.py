"""Ponowny list do ucznia delegacji ma karencję (audyt 10.10.2026, S12 – druga droga).

Ta sama reguła, co ``bulk_registration.resend_invitation``: bez karencji „wyślij ponownie” w panelu
opiekuna drużyny wysyłało listy z domeny organizatora bez ograniczenia, na adresy wpisane przez
opiekuna. Odmowa to ``INVITE_COOLDOWN`` (429), a po upływie ``INVITE_RESEND_COOLDOWN`` list wychodzi.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import delegation_services as service
from apps.accounts.bulk_registration import INVITE_RESEND_COOLDOWN
from apps.accounts.models import Participant
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def leader_and_student(competition):
    from apps.accounts.tests.factories import CoordinatorFactory

    iqo = make_delegations_competition(competition)
    leader = leader_for_country(iqo, CoordinatorFactory(), "lead@example.test")
    return leader, add(leader)


def test_a_resend_right_after_the_invitation_is_refused(leader_and_student, mailoutbox):
    leader, student = leader_and_student
    mailoutbox.clear()

    with pytest.raises(DomainError) as refused:
        service.resend_student_invitation(leader, student)

    assert refused.value.machine_code == "INVITE_COOLDOWN"
    assert refused.value.status_code == 429
    assert mailoutbox == []


def test_a_resend_after_the_cooldown_goes_out(
    leader_and_student, django_capture_on_commit_callbacks, mailoutbox
):
    leader, student = leader_and_student
    Participant.objects.filter(pk=student.pk).update(
        invitation_sent_at=timezone.now() - INVITE_RESEND_COOLDOWN - timedelta(minutes=1)
    )
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        service.resend_student_invitation(leader, student)

    assert [message.to for message in mailoutbox] == [[student.user.email]]
