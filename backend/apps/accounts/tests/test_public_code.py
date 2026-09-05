"""Testy `create_participant_with_public_code`: retry na kolizji, propagacja obcych błędów, brak sierot."""

from unittest import mock

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Participant, User
from apps.core.api import DomainError

from .factories import ParticipantFactory, UserFactory


def _fields(user):
    return {
        "user": user,
        "school": "LO",
        "district": "mazowieckie",
        "birth_year": 2008,
        "gdpr_consent_at": timezone.now(),
    }


@pytest.mark.django_db
def test_kolizja_kodu_konczy_sie_retry_i_sukcesem():
    taken = ParticipantFactory().public_code
    with mock.patch.object(services, "generate_public_code", side_effect=[taken, "OLM-ZZZZZ2"]):
        participant = services.create_participant_with_public_code(**_fields(UserFactory()))
    assert participant.public_code == "OLM-ZZZZZ2"


@pytest.mark.django_db
def test_obcy_integrity_error_jest_propagowany():
    user = UserFactory()
    ParticipantFactory(user=user)  # drugi profil dla tego samego usera łamie OneToOne, nie public_code
    with pytest.raises(IntegrityError):
        services.create_participant_with_public_code(**_fields(user))


@pytest.mark.django_db
def test_wyczerpanie_prob_nie_zostawia_osieroconego_usera():
    taken = ParticipantFactory().public_code
    with (
        mock.patch.object(services, "generate_public_code", return_value=taken),
        pytest.raises(DomainError) as exc,
    ):
        services.register_participant(
            email="sierota@example.test",
            password="Silne.Haslo.123",
            first_name="A",
            last_name="B",
            school="LO",
            district="mazowieckie",
            birth_year=2008,
            gdpr_consent=True,
        )
    assert exc.value.machine_code == "PUBLIC_CODE_UNAVAILABLE"
    assert not User.objects.filter(email="sierota@example.test").exists()
    assert Participant.objects.count() == 1
