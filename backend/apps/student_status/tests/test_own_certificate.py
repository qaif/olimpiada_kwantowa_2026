"""Koordynator nie akceptuje własnego zaświadczenia (audyt 10.10.2026, pozycja niska).

Koordynator bywa też uczestnikiem (student-organizator, konto testowe). Akceptacja własnego
zaświadczenia byłaby poświadczeniem statusu przez samego zainteresowanego.
"""

import pytest

from apps.accounts.models import GROUP_COORDINATOR
from apps.core.api import DomainError
from apps.student_status import services
from apps.student_status.models import CertificateStatus

from .helpers import make_certificate

pytestmark = pytest.mark.django_db


def test_akceptacja_wlasnego_zaswiadczenia_jest_odmowa(participant, edition):
    from django.contrib.auth.models import Group

    participant.user.groups.add(Group.objects.get_or_create(name=GROUP_COORDINATOR)[0])
    certificate = make_certificate(participant, edition)

    with pytest.raises(DomainError) as caught:
        services.accept(certificate, actor=participant.user)

    assert caught.value.machine_code == "STUDENT_STATUS_OWN_CERTIFICATE"
    certificate.refresh_from_db()
    assert certificate.status != CertificateStatus.ACCEPTED
