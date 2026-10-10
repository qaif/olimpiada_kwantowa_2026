"""Audyt 10.10.2026 (S5, S6, S12, import nauczyciela) – droga przez HTTP.

- kolejka aktywacji koordynatora nie pokazuje kont zablokowanych (S5),
- „Wyślij link ponownie” koordynatora dla konta z zaproszenia wysyła zaproszenie, a ręczna
  aktywacja takiego konta jest odmową – uruchamia je uczeń, składając zgody (S6),
- „wyślij zaproszenie ponownie” opiekuna szkolnego ma limit per konto i godzinną karencję (S12),
- import nauczyciela przyjmuje ze słownika wyłącznie szkołę z jego profilu.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GROUP_SUPERVISOR, Participant, SchoolSupervisor, User
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.schools.tests.factories import SchoolFactory
from apps.tenancy.tests.factories import current_or_default_competition

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"


def rest_framework_with(**rates) -> dict:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def invited(email: str = "kasia@example.test", *, sent_ago: timedelta = timedelta(hours=2)) -> Participant:
    user = UserFactory(email=email, is_active=False, email_verified_at=None)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return ParticipantFactory(
        user=user,
        invited_at=timezone.now(),
        invitation_sent_at=timezone.now() - sent_ago,
        gdpr_consent_at=None,
        terms_accepted_at=None,
        supervisor_email=SUPERVISOR_EMAIL,
    )


@pytest.fixture
def supervisor():
    user = UserFactory(email=SUPERVISOR_EMAIL)
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


# --- panel koordynatora ---------------------------------------------------------------------------


def test_kolejka_aktywacji_pomija_konta_zablokowane(web_client, coordinator):
    waiting = UserFactory(email="czeka@example.test", is_active=False, email_verified_at=None)
    blocked = UserFactory(email="zablokowany@example.test", email_verified_at=None)
    blocked.is_active = False
    blocked.save(update_fields=["is_active"])
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator-activations")).content.decode()

    assert waiting.email in body
    assert blocked.email not in body


def test_koordynator_nie_wysle_linku_zablokowanemu(
    web_client, coordinator, django_capture_on_commit_callbacks
):
    blocked = UserFactory(email="zablokowany@example.test", email_verified_at=None)
    blocked.is_active = False
    blocked.save(update_fields=["is_active"])
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(reverse("web:coordinator-account-resend", args=[blocked.pk]))
        web_client.post(reverse("web:coordinator-account-activate", args=[blocked.pk]))

    assert mail.outbox == []
    assert User.objects.get(pk=blocked.pk).is_active is False


def test_koordynator_ponawia_zaproszenie_zamiast_linku_aktywacyjnego(
    web_client, coordinator, django_capture_on_commit_callbacks
):
    participant = invited(sent_ago=timedelta(minutes=1))
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            reverse("web:coordinator-account-resend", args=[participant.user_id]), follow=True
        )

    assert "Zaproszenie zostało wysłane ponownie" in response.content.decode()
    assert len(mail.outbox) == 1
    assert "/zaproszenie/" in mail.outbox[0].body


def test_reczna_aktywacja_konta_z_zaproszenia_jest_odmowa(web_client, coordinator):
    participant = invited()
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-account-activate", args=[participant.user_id]), follow=True
    )

    assert "powstało z zaproszenia" in response.content.decode()
    assert User.objects.get(pk=participant.user_id).is_active is False


# --- panel opiekuna szkolnego ---------------------------------------------------------------------


def test_karencja_ponownego_zaproszenia_w_panelu_opiekuna(
    web_client, supervisor, django_capture_on_commit_callbacks
):
    participant = invited()
    web_client.force_login(supervisor.user)
    url = reverse("web:supervisor-resend-invitation", args=[participant.pk])

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(url)
        second = web_client.post(url, follow=True)

    assert len(mail.outbox) == 1
    assert "Zaproszenie wysłano niedawno" in second.content.decode()


@override_settings(REST_FRAMEWORK=rest_framework_with(supervisor_resend="2/hour"))
def test_limit_zadan_ponownego_zaproszenia_liczy_sie_per_konto_opiekuna(web_client, supervisor):
    students = [invited(f"uczen{index}@example.test") for index in range(3)]
    web_client.force_login(supervisor.user)

    codes = [
        web_client.post(reverse("web:supervisor-resend-invitation", args=[student.pk])).status_code
        for student in students
    ]

    assert codes == [302, 302, 429]


# --- import nauczyciela: szkoła ze słownika ---------------------------------------------------------


HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"


def upload() -> SimpleUploadedFile:
    year = timezone.localdate().year - 25
    body = "\n".join([HEADER, f"Kasia;Nowak;kasia@example.test;{year};2;;"]).encode("utf-8")
    return SimpleUploadedFile("klasa.csv", body, content_type="text/csv")


def test_nauczyciel_nie_zaimportuje_listy_do_cudzej_szkoly_z_rejestru(web_client, supervisor):
    foreign = SchoolFactory()
    web_client.force_login(supervisor.user)

    response = web_client.post(
        "/supervisor/import/", {"file": upload(), "school_id": foreign.pk, "school": foreign.name}
    )

    assert response.status_code == 400
    assert "szkoły podanej w Twoim profilu" in response.content.decode()


def test_nauczyciel_importuje_do_wlasnej_szkoly_z_rejestru(web_client, supervisor):
    own = SchoolFactory()
    SchoolSupervisor.objects.filter(pk=supervisor.pk).update(school_ref=own, school=own.name)
    web_client.force_login(supervisor.user)

    response = web_client.post(
        "/supervisor/import/", {"file": upload(), "school_id": own.pk, "school": own.name}
    )

    assert response.status_code == 200
    assert response.context["school_id"] == own.pk
