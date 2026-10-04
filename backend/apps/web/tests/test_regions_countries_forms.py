"""Formularze przy podziale na kraje (docs/tasks/REG-01.md § 1.1).

Olimpiada Kwantowa (flaga ``custom_regions`` wyłączona) ma zachować listę szesnastu województw co
do znaku; konkurs przestawiony na kraje – pokazać aktywne kraje, zapisać region i odrzucić kod
spoza aktywnej listy.
"""

from __future__ import annotations

import re

import pytest

from apps.accounts.models import Participant
from apps.accounts.regions import switch_to_countries

from .conftest import participant_extra_fields

pytestmark = pytest.mark.django_db


def _register_payload(**overrides) -> dict:
    data = {
        "email": "kraj@example.test",
        "first_name": "Anna",
        "last_name": "Müller",
        "school_custom": "on",
        "school": "Gymnasium Berlin",
        "district": "de",
        "grade": 2,
        "birth_date": "1990-12-31",
        "terms_consent": "on",
        "gdpr_consent": "on",
        **participant_extra_fields(),
    }
    data.update(overrides)
    return data


def test_competition_one_registration_form_lists_voivodeships(web_client, edition, competition):  # noqa: ARG001
    content = web_client.get("/register/").content.decode()

    assert "— wybierz województwo —" in content
    assert 'value="mazowieckie"' in content
    assert 'value="de"' not in content


def test_country_competition_registration_form_lists_countries(web_client, edition, competition):  # noqa: ARG001
    switch_to_countries(competition)

    content = web_client.get("/register/").content.decode()

    assert re.search(r'<label for="id_district"[^>]*>Kraj:?</label>', content)
    assert '<option value="de">Germany</option>' in content
    assert 'value="mazowieckie"' not in content
    assert "— wybierz województwo —" not in content


def test_registration_saves_the_country_as_region(web_client, edition, competition):  # noqa: ARG001
    switch_to_countries(competition)

    response = web_client.post("/register/", _register_payload())

    assert response.status_code in (200, 302)
    participant = Participant.objects.get(user__email="kraj@example.test")
    assert participant.district == "de"
    assert participant.region.code == "de"
    assert participant.get_district_display() == "Germany"


def test_registration_refuses_an_inactive_region(web_client, edition, competition):  # noqa: ARG001
    switch_to_countries(competition)

    web_client.post("/register/", _register_payload(district="mazowieckie"))

    assert not Participant.objects.filter(user__email="kraj@example.test").exists()


def test_committee_district_accepts_a_country(competition):
    from apps.accounts.models import CommitteeMember, CommitteeStatus
    from apps.accounts.services import verify_committee_district
    from apps.accounts.tests.factories import UserFactory

    switch_to_countries(competition)
    member = CommitteeMember.objects.create(
        user=UserFactory(), competition=competition, status=CommitteeStatus.ACTIVE
    )
    actor = UserFactory()

    saved = verify_committee_district(member, district="fr", actor=actor, competition=competition)

    assert saved.district == "fr"
    assert saved.region.code == "fr"
    assert saved.get_district_display() == "France"
