"""Ekrany delegacji krajowych (DEL-01): koordynator, zaproszenie, panel opiekuna i bramki.

Konkurs w trybie delegacji to Konkurs #1 przestawiony na kraje i na ``DELEGATIONS`` (tak, jak
operator przestawi ``iqo``); Olimpiada Kwantowa to ten sam konkurs bez przestawienia – i tam
żadnego z tych ekranów nie ma.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts import delegation_services as service
from apps.accounts.consents import ConsentKind
from apps.accounts.delegations import DelegationInvitation, DelegationLeader
from apps.accounts.models import CompetitionRole, Participant, User
from apps.accounts.tests.conftest import api_captcha_fields
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.accounts.tests.test_delegations import (
    ADULT_BIRTH,
    LEADER_CONSENTS,
    PASSWORD,
    add,
    invite,
    leader_for_country,
    make_delegations_competition,
)
from apps.competitions.tests.factories import CurrentEditionFactory
from apps.core.models import AuditLog
from apps.tenancy.models import RegistrationMode

from .conftest import participant_extra_fields

pytestmark = pytest.mark.django_db


@pytest.fixture
def iqo(competition):
    return make_delegations_competition(competition)


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def coordinator_client(client_for, iqo, coordinator):
    client = client_for(iqo)
    client.force_login(coordinator)
    return client


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- Olimpiada Kwantowa bez zmian -------------------------------------------------------------------


def test_open_competition_has_no_delegation_screens(client_for, competition):
    CurrentEditionFactory(competition=competition)
    coordinator = logged_in(client_for, competition, CoordinatorFactory())

    assert coordinator.get("/coordinator/delegations/").status_code == 404
    assert "Delegacje" not in coordinator.get("/coordinator/").content.decode()


def test_open_competition_registration_form_is_unchanged(client_for, competition):
    CurrentEditionFactory(competition=competition)
    content = client_for(competition).get("/register/").content.decode()

    assert '<form method="post" class="form">' in content
    assert "opiekunowie drużyn" not in content


# --- tryb delegacji: samodzielna rejestracja zamknięta na każdej drodze -------------------------


def test_register_page_explains_delegations_and_shows_contact(client_for, iqo):
    iqo.contact_email = "office@iqo.test"
    iqo.save(update_fields=["contact_email"])

    content = client_for(iqo).get("/register/").content.decode()

    assert '<form method="post" class="form">' not in content
    assert "opiekunowie drużyn narodowych" in content
    assert "mailto:office@iqo.test" in content


def test_register_post_is_refused(client_for, iqo):
    payload = {
        "email": "self@example.test",
        "first_name": "Self",
        "last_name": "Registered",
        "district": "de",
        "school_custom": "on",
        "school": "Gymnasium Berlin",
        "grade": 2,
        "birth_date": ADULT_BIRTH.isoformat(),
        "terms_consent": "on",
        "gdpr_consent": "on",
        **participant_extra_fields(),
    }
    client_for(iqo).post("/register/", payload)

    assert not User.objects.filter(email="self@example.test").exists()


def test_api_registration_is_refused(client_for, iqo):
    response = client_for(iqo).post(
        "/api/auth/register/participant/",
        {
            "email": "api@example.test",
            "password": PASSWORD,
            "first_name": "Api",
            "last_name": "Client",
            "district": "de",
            "school": "Gymnasium Berlin",
            "grade": 2,
            "birth_date": ADULT_BIRTH.isoformat(),
            "phone": "+49301234567",
            "gdpr_consent": True,
            "terms_consent": True,
            **api_captcha_fields(),
        },
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "REGISTRATION_CLOSED"
    assert not User.objects.filter(email="api@example.test").exists()


def test_supervisor_registration_does_not_exist(client_for, iqo, supervisor_registration_on):  # noqa: ARG001
    assert client_for(iqo).get("/register/supervisor/").status_code == 404


def test_coordinator_class_list_import_is_refused(coordinator_client):
    import io

    upload = io.BytesIO("imię;nazwisko;e-mail;rok urodzenia;klasa\nA;B;c@example.test;2008;2\n".encode())
    upload.name = "lista.csv"
    response = coordinator_client.post(
        "/coordinator/accounts/import/", {"file": upload, "school_custom": "on", "school": "Szkoła Testowa"}
    )

    assert "import listy jest wyłączony" in response.content.decode()
    assert not User.objects.filter(email="c@example.test").exists()


# --- koordynator ------------------------------------------------------------------------------------


def test_coordinator_menu_has_delegations(coordinator_client):
    content = coordinator_client.get("/coordinator/").content.decode()
    assert reverse("web:coordinator-delegations") in content


def test_coordinator_invites_a_leader(coordinator_client, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = coordinator_client.post(
            reverse("web:coordinator-delegations-invite"), {"email": "Lead@Example.test", "country": "de"}
        )

    assert response.status_code == 302
    invitation = DelegationInvitation.objects.get()
    assert invitation.email == "lead@example.test"
    assert invitation.delegation.country.code == "de"
    assert mailoutbox[0].to == ["lead@example.test"]
    listing = coordinator_client.get(reverse("web:coordinator-delegations")).content.decode()
    assert "Germany" in listing


def test_coordinator_changes_limit_and_closes_the_delegation(coordinator_client, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    url = reverse("web:coordinator-delegation", args=[leader.delegation_id])

    response = coordinator_client.post(url, {"max_students": 4, "status": "CLOSED", "note": "lot 12.07"})

    assert response.status_code == 302
    leader.delegation.refresh_from_db()
    assert (leader.delegation.max_students, leader.delegation.status) == (4, "CLOSED")
    assert AuditLog.objects.filter(action="delegation.updated").exists()


def test_coordinator_cannot_lower_the_limit_below_registered_students(coordinator_client, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    add(leader, email="one@example.test")
    add(leader, email="two@example.test")
    url = reverse("web:coordinator-delegation", args=[leader.delegation_id])

    response = coordinator_client.post(url, {"max_students": 1, "status": "ACTIVE", "note": ""})

    assert response.status_code == 400
    leader.delegation.refresh_from_db()
    assert leader.delegation.max_students == iqo.delegation_max_students


def test_coordinator_revokes_and_resends_invitations(coordinator_client, iqo, coordinator):
    invitation, token = invite(iqo, coordinator)

    coordinator_client.post(reverse("web:coordinator-delegation-invitation-resend", args=[invitation.pk]))
    with pytest.raises(Exception):  # noqa: B017 - stary token po ponownym wysłaniu nie działa
        service.read_leader_invitation(token, iqo)
    coordinator_client.post(reverse("web:coordinator-delegation-invitation-revoke", args=[invitation.pk]))

    invitation.refresh_from_db()
    assert invitation.revoked_at is not None


def test_coordinator_removes_a_leader(coordinator_client, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")

    coordinator_client.post(reverse("web:coordinator-delegation-leader-remove", args=[leader.pk]))

    leader.refresh_from_db()
    assert leader.removed_at is not None
    assert not DelegationLeader.objects.active().filter(pk=leader.pk).exists()


def test_coordinator_export_lists_leaders_and_students(coordinator_client, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    add(leader)

    response = coordinator_client.get(reverse("web:coordinator-delegations-export"))
    content = b"".join(response.streaming_content).decode("utf-8")

    assert response["Content-Type"].startswith("text/csv")
    assert "kraj;kod kraju" in content
    assert "lead@example.test" in content and "kid@example.test" in content
    assert AuditLog.objects.filter(action="delegation.exported").exists()


def test_other_roles_cannot_open_coordinator_screens(client_for, iqo):
    reviewer = logged_in(client_for, iqo, ActiveReviewerFactory().user)
    assert reviewer.get(reverse("web:coordinator-delegations")).status_code == 403


def test_coordinator_of_another_competition_sees_404(client_for, iqo, coordinator, other_competition):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    make_delegations_competition(other_competition)
    stranger = logged_in(client_for, other_competition, CoordinatorFactory())

    response = stranger.get(reverse("web:coordinator-delegation", args=[leader.delegation_id]))

    assert response.status_code == 404


def test_switching_registration_mode_is_audited(client_for, iqo):
    from apps.tenancy.tests.factories import grant_membership
    from apps.web.competition_forms import EDITABLE_FIELDS, CompetitionSettingsForm

    iqo.feature_flags = {**iqo.feature_flags, "competition_settings_page": True}
    iqo.save(update_fields=["feature_flags"])
    user = CoordinatorFactory()
    grant_membership(user, iqo, CompetitionRole.COORDINATOR)
    client = logged_in(client_for, iqo, user)
    form = CompetitionSettingsForm(instance=iqo)
    data = {name: form.initial.get(name) for name in EDITABLE_FIELDS if form.initial.get(name) is not None}
    data["interface_languages"] = list(iqo.ui_languages)
    data["registration_mode"] = RegistrationMode.OPEN
    for name, value in list(data.items()):
        if value is None:
            data.pop(name)

    client.post(reverse("web:coordinator-competition"), data)

    iqo.refresh_from_db()
    assert iqo.registration_mode == RegistrationMode.OPEN
    assert AuditLog.objects.filter(action="competition.registration_mode_changed").exists()


# --- zaproszenie opiekuna -----------------------------------------------------------------------------


def test_invitation_page_signs_up_a_new_leader(client_for, iqo, coordinator):
    invitation, token = invite(iqo, coordinator)
    client = client_for(iqo)
    url = reverse("web:delegation-accept", args=[token])

    page = client.get(url).content.decode()
    assert "lead@example.test" in page
    assert 'name="password"' in page
    response = client.post(
        url,
        {
            "first_name": "Lena",
            "last_name": "Leiter",
            "password": PASSWORD,
            "password2": PASSWORD,
            "terms_consent": "on",
            "gdpr_consent": "on",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("web:delegation-accept-done")
    leader = DelegationLeader.objects.get()
    assert leader.user.email == "lead@example.test"
    assert client.get(url).status_code == 400  # link jednorazowy


def test_invitation_page_asks_an_existing_account_to_log_in(client_for, iqo, coordinator):
    from apps.accounts.tests.factories import UserFactory

    UserFactory(email="lead@example.test")
    _invitation, token = invite(iqo, coordinator)

    page = client_for(iqo).get(reverse("web:delegation-accept", args=[token])).content.decode()

    assert "/login/?next=/delegation/accept/" in page
    assert 'name="password"' not in page


def test_invitation_page_refuses_another_logged_in_account(client_for, iqo, coordinator):
    from apps.accounts.tests.factories import UserFactory

    _invitation, token = invite(iqo, coordinator)
    client = logged_in(client_for, iqo, UserFactory(email="someone@example.test"))
    url = reverse("web:delegation-accept", args=[token])

    page = client.get(url).content.decode()
    client.post(url, {"terms_consent": "on", "gdpr_consent": "on"})

    assert reverse("web:delegation-accept-logout", args=[token]) in page
    assert not DelegationLeader.objects.exists()


def test_invitation_link_from_another_competition_does_not_open(
    client_for, iqo, coordinator, other_competition
):
    _invitation, token = invite(iqo, coordinator)
    make_delegations_competition(other_competition)

    response = client_for(other_competition).get(reverse("web:delegation-accept", args=[token]))

    assert response.status_code == 400


# --- panel opiekuna ---------------------------------------------------------------------------------------


def test_leader_registers_a_student_from_the_panel(
    client_for, iqo, coordinator, mailoutbox, django_capture_on_commit_callbacks
):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    client = logged_in(client_for, iqo, leader.user)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("web:delegation-student-add"),
            {
                "first_name": "Kurt",
                "last_name": "Kind",
                "email": "kid@example.test",
                "birth_date": ADULT_BIRTH.isoformat(),
                "school": "Gymnasium Berlin",
                "grade": 2,
            },
        )

    assert response.status_code == 302
    student = Participant.objects.get(user__email="kid@example.test")
    assert student.region.code == "de"
    assert mailoutbox[-1].to == ["kid@example.test"]
    dashboard = client.get(reverse("web:delegation")).content.decode()
    assert "kid@example.test" in dashboard


def test_two_leaders_of_one_country_see_the_same_students(client_for, iqo, coordinator):
    first = leader_for_country(iqo, coordinator, "a@example.test")
    second = leader_for_country(iqo, coordinator, "b@example.test")
    add(first)

    dashboard = logged_in(client_for, iqo, second.user).get(reverse("web:delegation")).content.decode()

    assert "kid@example.test" in dashboard
    assert "a@example.test" in dashboard  # współopiekun widzi drugiego opiekuna


def test_leader_cannot_open_a_student_of_another_country(client_for, iqo, coordinator):
    german = leader_for_country(iqo, coordinator, "de@example.test", country="de")
    french = leader_for_country(iqo, coordinator, "fr@example.test", country="fr")
    student = add(german)
    client = logged_in(client_for, iqo, french.user)

    assert client.get(reverse("web:delegation-student-edit", args=[student.pk])).status_code == 404
    assert client.post(reverse("web:delegation-student-delete", args=[student.pk])).status_code == 404
    assert Participant.objects.filter(pk=student.pk).exists()
    assert "kid@example.test" not in client.get(reverse("web:delegation")).content.decode()


def test_leader_of_another_competition_gets_404(client_for, iqo, coordinator, other_competition):
    student = add(leader_for_country(iqo, coordinator, "de@example.test"))
    make_delegations_competition(other_competition)
    from apps.accounts.tests.test_delegations import invite as invite_in

    other_invitation, _ = invite_in(other_competition, CoordinatorFactory(), email="other@example.test")
    other_leader = service.accept_leader_invitation(
        other_invitation, first_name="O", last_name="L", password=PASSWORD, given=LEADER_CONSENTS
    )
    client = logged_in(client_for, other_competition, other_leader.user)

    assert client.get(reverse("web:delegation-student-edit", args=[student.pk])).status_code == 404


def test_reviewer_and_participant_get_403_and_anonymous_is_sent_to_login(client_for, iqo):
    reviewer = logged_in(client_for, iqo, ActiveReviewerFactory().user)
    participant = logged_in(client_for, iqo, ParticipantFactory(user__groups=["participant"]).user)

    assert reviewer.get(reverse("web:delegation")).status_code == 403
    assert participant.get(reverse("web:delegation")).status_code == 403
    response = client_for(iqo).get(reverse("web:delegation"))
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_leader_has_no_access_to_participant_chat(client_for, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    client = logged_in(client_for, iqo, leader.user)

    assert client.get(reverse("web:chat")).status_code in (403, 404)


def test_leader_lands_on_the_team_panel_after_login(client_for, iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "lead@example.test")

    response = client_for(iqo).post("/login/", {"username": "lead@example.test", "password": PASSWORD})

    assert response.status_code == 302
    assert response["Location"] == reverse("web:delegation")
    assert leader.user.is_active


def test_delegation_student_invite_page_has_no_country_field(client_for, iqo, coordinator):
    from apps.accounts.bulk_registration import make_invite_token

    student = add(leader_for_country(iqo, coordinator, "lead@example.test"))
    page = (
        client_for(iqo).get(reverse("web:student-invite", args=[make_invite_token(student)])).content.decode()
    )

    assert 'name="district"' not in page
    assert "Germany" in page


def test_leader_accept_form_requires_consents(client_for, iqo, coordinator):
    _invitation, token = invite(iqo, coordinator)
    response = client_for(iqo).post(
        reverse("web:delegation-accept", args=[token]),
        {"first_name": "Lena", "last_name": "Leiter", "password": PASSWORD, "password2": PASSWORD},
    )

    assert response.status_code == 400
    assert not User.objects.filter(email="lead@example.test").exists()


def test_consent_kinds_of_the_leader_form_match_the_service():
    from apps.web.delegation_forms import LEADER_CONSENT_KINDS

    assert LEADER_CONSENT_KINDS == service.LEADER_CONSENT_KINDS == (ConsentKind.TERMS, ConsentKind.PRIVACY)


def test_open_competition_next_to_a_delegations_competition_keeps_self_registration(
    client_for, iqo, other_competition, coordinator
):
    """Dwa konkursy na jednej platformie: tryb delegacji jednego nie zamyka rejestracji drugiego."""
    from apps.competitions.models import current_registration_status

    CurrentEditionFactory(competition=other_competition)
    leader_for_country(iqo, coordinator, "lead@example.test")

    assert other_competition.registration_mode == RegistrationMode.OPEN
    assert current_registration_status(competition=other_competition).is_open
    assert not current_registration_status(competition=iqo).is_open
    open_page = client_for(other_competition).get("/register/").content.decode()
    assert '<form method="post" class="form">' in open_page
    other_coordinator = logged_in(client_for, other_competition, CoordinatorFactory())
    assert other_coordinator.get("/coordinator/delegations/").status_code == 404
    leader_page = logged_in(client_for, other_competition, DelegationLeader.objects.get().user)
    assert leader_page.get(reverse("web:delegation")).status_code in (403, 404)


# --- poprawki po przeglądzie --------------------------------------------------------------------------


def test_leader_without_a_current_delegation_gets_an_explanation(client_for, iqo, coordinator):
    """M1: rola bez delegacji w bieżącej edycji – strona z wyjaśnieniem, bez pozycji w pasku konta."""
    from apps.competitions.models import Edition

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    Edition.objects.filter(pk=leader.delegation.edition_id).update(is_current=False)
    CurrentEditionFactory(competition=iqo)
    client = logged_in(client_for, iqo, leader.user)

    response = client.get(reverse("web:delegation"))
    assert response.status_code == 200
    assert "Nie prowadzisz drużyny w bieżącej edycji" in response.content.decode()
    assert f'href="{reverse("web:delegation")}"' not in client.get("/plakaty/").content.decode()
    login = client_for(iqo).post("/login/", {"username": "lead@example.test", "password": PASSWORD})
    assert login["Location"] != reverse("web:delegation")


def test_edit_page_of_an_activated_student_is_read_only(client_for, iqo, coordinator):
    """L5: po uruchomieniu konta formularza (z adresem rodzica) już nie ma."""
    from apps.accounts.activation import mark_activated

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader, guardian_email="parent@example.test")
    mark_activated(student.user)
    client = logged_in(client_for, iqo, leader.user)

    response = client.get(reverse("web:delegation-student-edit", args=[student.pk]))

    assert response.status_code == 409
    content = response.content.decode()
    assert 'name="guardian_email"' not in content
    assert "parent@example.test" not in content


def test_coordinator_sees_unlinked_students(coordinator_client, iqo, coordinator):
    """M3: wypisany uczeń z uruchomionym kontem czeka na decyzję koordynatora."""
    from apps.accounts.activation import mark_activated

    leader = leader_for_country(iqo, coordinator, "lead@example.test")
    student = add(leader)
    mark_activated(student.user)
    student.refresh_from_db()
    service.remove_student(leader, student)

    detail = coordinator_client.get(reverse("web:coordinator-delegation", args=[leader.delegation_id]))

    assert "Wypisani przez opiekuna" in detail.content.decode()
    assert "kid@example.test" in detail.content.decode()


def test_coordinator_dashboard_shows_the_window_for_team_leaders(coordinator_client):
    """L4: pulpit mówi, czy okno edycji – które bramkuje opiekunów – jest otwarte."""
    content = coordinator_client.get("/coordinator/").content.decode()
    assert "przez delegacje krajowe – okno dla opiekunów drużyn otwarte" in content


def test_team_leader_has_a_role_label_in_the_accounts_list(coordinator_client, iqo, coordinator):
    """L8: opiekun drużyny nie jest na liście kont „bez roli”."""
    leader_for_country(iqo, coordinator, "lead@example.test")

    content = coordinator_client.get("/coordinator/accounts/?q=lead%40example.test").content.decode()

    assert "opiekun drużyny" in content


def test_public_edition_api_reports_delegations(client_for, iqo):
    """L3: publiczne API edycji nie mówi „otwarta” w trybie delegacji."""
    data = client_for(iqo).get("/api/competitions/editions/current/").json()
    assert data["registration"]["is_open"] is False
    assert data["registration"]["reason"] == "delegations"
