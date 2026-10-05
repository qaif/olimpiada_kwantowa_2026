"""Pole z podpowiedzią w formularzach platformy (MAIL-02 § 1.5) i pełny przebieg rejestracji."""

from __future__ import annotations

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.competitions.tests.factories import CurrentEditionFactory
from apps.email_delivery.fields import ACCEPT_SUFFIX, KEEP_SUFFIX, CheckedEmailField
from apps.web.tests.conftest import participant_extra_fields

REGISTER_URL = "/register/"


def _forms():
    from apps.web import delegation_forms, forms, supervisor_forms
    from apps.web.views import participant_extras

    return [
        (forms.ParticipantRegisterForm, "email"),
        (forms.CommitteeRegisterForm, "email"),
        (forms.EmailChangeForm, "new_email"),
        (forms.CoordinatorAccountForm, "email"),
        (supervisor_forms.SupervisorRegisterForm, "email"),
        (delegation_forms.LeaderInviteForm, "email"),
        (delegation_forms.StudentForm, "email"),
        (delegation_forms.StudentForm, "guardian_email"),
        (participant_extras.GuardianEmailForm, "guardian_email"),
    ]


@pytest.mark.parametrize(
    ("form_class", "field"), _forms(), ids=lambda value: getattr(value, "__name__", value)
)
def test_every_address_form_uses_the_checked_field(form_class, field):
    assert isinstance(form_class.base_fields[field], CheckedEmailField)


def test_guardian_form_only_blocks_dead_domains():
    from apps.web.views.participant_extras import GuardianEmailForm

    # Widok jest akcją POST z przekierowaniem – pytanie bez pola „zostaw” zablokowałoby adres na zawsze.
    assert GuardianEmailForm.base_fields["guardian_email"].suggest_typos is False


def test_email_change_form_asks_about_the_typo():
    from apps.web.forms import EmailChangeForm

    form = EmailChangeForm({"new_email": "nowy@outlok.com", "current_password": "x"})

    assert not form.is_valid()
    assert "nowy@outlook.com" in form.errors["new_email"][0]


def _payload(**overrides) -> dict:
    data = {
        "email": "uczen@gmial.com",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_date": "1990-12-31",
        "terms_consent": "on",
        "gdpr_consent": "on",
        **participant_extra_fields(),
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_registration_asks_once_and_then_accepts_the_kept_address(competition):
    CurrentEditionFactory()
    client = Client()

    first = client.post(REGISTER_URL, _payload())
    second = client.post(REGISTER_URL, _payload(**{f"email{KEEP_SUFFIX}": "uczen@gmial.com"}))

    body = first.content.decode()
    assert first.status_code == 200
    assert "uczen@gmail.com" in body and f'name="email{ACCEPT_SUFFIX}"' in body
    assert second.status_code == 302
    assert User.objects.filter(email="uczen@gmial.com").exists()


@pytest.mark.django_db
def test_registration_with_the_accepted_suggestion_creates_the_fixed_address(competition):
    CurrentEditionFactory()

    response = Client().post(REGISTER_URL, _payload(**{f"email{ACCEPT_SUFFIX}": "uczen@gmail.com"}))

    assert response.status_code == 302
    assert User.objects.filter(email="uczen@gmail.com").exists()
    assert not User.objects.filter(email="uczen@gmial.com").exists()
