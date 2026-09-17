"""Testy `create_participant_with_public_code`: retry na kolizji, propagacja obcych błędów, brak sierot."""

from unittest import mock

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Participant, User
from apps.core.api import DomainError

from .factories import ParticipantFactory, UserFactory


def _fields(user, competition):
    """Komplet pól profilu. Konkurs jest **obowiązkowy** od wydania D – kolumna jest ``NOT NULL``."""
    return {
        "user": user,
        "competition": competition,
        "school": "LO nr 1",
        "district": "mazowieckie",
        "birth_year": 2008,
        "gdpr_consent_at": timezone.now(),
    }


@pytest.mark.django_db
def test_kolizja_kodu_konczy_sie_retry_i_sukcesem(competition):
    taken = ParticipantFactory(competition=competition).public_code
    with mock.patch.object(services, "generate_public_code", side_effect=[taken, "OLM-ZZZZZ2"]):
        participant = services.create_participant_with_public_code(**_fields(UserFactory(), competition))
    assert participant.public_code == "OLM-ZZZZZ2"


@pytest.mark.django_db
def test_obcy_integrity_error_jest_propagowany(competition):
    user = UserFactory()
    # Drugi profil tej samej osoby w **tym samym** konkursie łamie
    # ``accounts_participant_unique_per_competition``, a nie więz kodu publicznego – ponawianie
    # go nie dotyczy i błąd ma wyjść na wierzch.
    ParticipantFactory(user=user, competition=competition)
    with pytest.raises(IntegrityError):
        services.create_participant_with_public_code(**_fields(user, competition))


@pytest.mark.django_db
def test_wyczerpanie_prob_nie_zostawia_osieroconego_usera(open_registration):
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
            school="LO nr 1",
            district="mazowieckie",
            grade=2,
            birth_year=2008,
            phone="600 100 200",
            terms_consent=True,
            gdpr_consent=True,
            guardian_consent=True,
        )
    assert exc.value.machine_code == "PUBLIC_CODE_UNAVAILABLE"
    assert not User.objects.filter(email="sierota@example.test").exists()
    assert Participant.objects.count() == 1
