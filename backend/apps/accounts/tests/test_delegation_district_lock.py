"""Kraj ucznia delegacji wynika z delegacji – uczeń go sobie nie przestawia (audyt 10.10.2026, S9).

Uczeń delegacji DE, który wysłał ``district=fr`` z ``/me/profile/`` albo ``PATCH /api/auth/me/``,
stawał w wynikach jako uczeń z Francji, liczył się do licznika FR i wypadał spod reguły konfliktu
interesów etapu krajowego – a jego ``delegation`` dalej wskazywała DE.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.profile import update_participant_profile
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db


@pytest.fixture
def student(competition):
    from apps.accounts.tests.factories import CoordinatorFactory

    iqo = make_delegations_competition(competition)
    leader = leader_for_country(iqo, CoordinatorFactory(), "lead@example.test")
    student = add(leader)
    # Konto uruchomione – tak, jak po przyjęciu zaproszenia; przedmiotem testu jest profil, nie aktywacja.
    student.user.is_active = True
    student.user.set_password("Haslo-Ucznia-2026!")
    student.user.save(update_fields=["is_active", "password"])
    return student


def test_the_service_refuses_another_country(student):
    with pytest.raises(DomainError) as refused:
        update_participant_profile(student, actor=student.user, district="fr")

    assert refused.value.machine_code == "DISTRICT_FROM_DELEGATION"
    student.refresh_from_db()
    assert student.district == "de"


def test_the_same_country_is_ignored_and_the_rest_is_saved(student):
    update_participant_profile(student, actor=student.user, district="de", phone="600 300 400")

    student.refresh_from_db()
    assert student.district == "de"
    assert student.phone.endswith("600300400")


def test_a_former_delegation_student_is_locked_too(student):
    from apps.accounts.models import Participant

    Participant.objects.filter(pk=student.pk).update(
        former_delegation_id=student.delegation_id, delegation_id=None
    )
    student.refresh_from_db()

    with pytest.raises(DomainError):
        update_participant_profile(student, actor=student.user, district="fr")


def test_the_profile_form_has_no_country_field(student):
    from apps.web.forms import ParticipantProfileForm

    assert "district" not in ParticipantProfileForm(participant=student).fields
    assert "district" in ParticipantProfileForm().fields


def test_the_api_refuses_another_country(student):
    api = APIClient()
    api.force_authenticate(user=student.user)

    response = api.patch("/api/auth/me/", {"district": "fr"}, format="json")

    assert response.status_code == 400
    student.refresh_from_db()
    assert student.district == "de"
