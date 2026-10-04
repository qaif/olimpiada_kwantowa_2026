"""Ekrany logistyki finału (LOG-01): bramki, role, izolacja i najważniejsze przepływy."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.competitions.tests.factories import CurrentEditionFactory
from apps.delegation_logistics import services
from apps.delegation_logistics.models import DelegationMember, MemberKind

pytestmark = pytest.mark.django_db


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def first_student(leader):
    return next(m for m in services.members_of(leader.delegation) if m.kind == MemberKind.STUDENT)


# --- Olimpiada Kwantowa bez zmian --------------------------------------------------------------------


def test_open_competition_has_no_logistics_screens_or_menu(client_for, competition):
    CurrentEditionFactory(competition=competition)
    client = logged_in(client_for, competition, CoordinatorFactory())

    assert client.get(reverse("web:coordinator-onsite")).status_code == 404
    assert client.get(reverse("web:onsite-checkin")).status_code in (403, 404)
    assert "Logistyka finału" not in client.get("/coordinator/").content.decode()


def test_delegations_without_flag_have_no_screens(client_for, competition):
    from apps.accounts.tests.test_delegations import make_delegations_competition

    iqo = make_delegations_competition(competition)
    client = logged_in(client_for, iqo, CoordinatorFactory())
    assert client.get(reverse("web:coordinator-onsite")).status_code == 404


# --- opiekun drużyny -----------------------------------------------------------------------------------


def test_leader_fills_the_form_of_own_member(client_for, iqo, leader, students):
    client = logged_in(client_for, iqo, leader.user)
    dashboard = client.get(reverse("web:delegation-logistics"))
    assert dashboard.status_code == 200
    assert dashboard["Cache-Control"] == "private, no-store"
    member = first_student(leader)

    page = client.get(reverse("web:delegation-logistics-member", args=[member.pk]))
    assert page.status_code == 200
    response = client.post(
        reverse("web:delegation-logistics-member", args=[member.pk]),
        {"passport_number": "P123", "nationality": "de", "tshirt_size": "L", "needs_accommodation": "on"},
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assert member.passport_number == "P123" and member.tshirt_size == "L"


def test_locked_section_is_read_only_but_the_rest_of_the_form_saves(client_for, iqo, leader, students, event):
    from datetime import timedelta

    from django.utils import timezone

    event.deadline_identity = timezone.now() - timedelta(hours=1)
    event.save()
    member = first_student(leader)
    client = logged_in(client_for, iqo, leader.user)
    page = client.get(reverse("web:delegation-logistics-member", args=[member.pk])).content.decode()
    assert 'name="passport_number"' in page and "disabled" in page

    # Spreparowany POST z polem zablokowanej sekcji – pole ``disabled`` ignoruje wartość z żądania.
    response = client.post(
        reverse("web:delegation-logistics-member", args=[member.pk]),
        {"passport_number": "HACK", "tshirt_size": "S", "needs_accommodation": "on"},
    )
    assert response.status_code == 302
    member.refresh_from_db()
    assert member.passport_number == "" and member.tshirt_size == "S"


def test_leader_gets_404_for_another_country_member(client_for, iqo, leader, other_leader, students):
    foreign = next(m for m in services.members_of(other_leader.delegation))
    client = logged_in(client_for, iqo, leader.user)
    assert client.get(reverse("web:delegation-logistics-member", args=[foreign.pk])).status_code == 404
    assert client.post(reverse("web:delegation-logistics-member", args=[foreign.pk]), {}).status_code == 404


def test_participant_cannot_open_leader_screens(client_for, iqo, students):
    adult, _minor = students
    adult.user.is_active = True
    adult.user.save(update_fields=["is_active"])
    client = logged_in(client_for, iqo, adult.user)
    assert client.get(reverse("web:delegation-logistics")).status_code == 403


def test_leader_adds_a_guest(client_for, iqo, leader):
    client = logged_in(client_for, iqo, leader.user)
    response = client.post(
        reverse("web:delegation-logistics-guest-add"),
        {"first_name": "Olga", "last_name": "Observer", "role": "OBSERVER"},
    )
    assert response.status_code == 302
    assert DelegationMember.objects.filter(delegation=leader.delegation, kind=MemberKind.GUEST).count() == 1


def test_delegation_dashboard_links_to_logistics(client_for, iqo, leader):
    client = logged_in(client_for, iqo, leader.user)
    assert reverse("web:delegation-logistics") in client.get(reverse("web:delegation")).content.decode()


# --- koordynator i oficer ------------------------------------------------------------------------------


def test_coordinator_without_grant_sees_counts_but_not_people(client_for, iqo, coordinator, leader, students):
    client = logged_in(client_for, iqo, coordinator)
    overview = client.get(reverse("web:coordinator-onsite"))
    assert overview.status_code == 200
    assert "Germany" in overview.content.decode() or "Niemcy" in overview.content.decode()
    member = first_student(leader)
    assert client.get(reverse("web:coordinator-onsite-members")).status_code == 403
    assert client.get(reverse("web:coordinator-onsite-member", args=[member.pk])).status_code == 403
    assert client.get(reverse("web:coordinator-onsite-export", args=["full"])).status_code == 403
    assert "Logistyka finału" in client.get("/coordinator/").content.decode()


def test_officer_sees_member_card_and_audit(client_for, iqo, officer, leader, students):
    from datetime import date, time

    from apps.core.models import AuditLog
    from apps.delegation_logistics import badges, rooming

    member = first_student(leader)
    edition = leader.delegation.edition
    # Dane, które zapełniają każdy ekran: przylot, pokój z mieszkańcem, punkt kontroli.
    services.save_member(
        member,
        {
            "gender": "F",
            "arrival_date": date(2027, 7, 1),
            "arrival_time": time(9, 30),
            "arrival_mode": "TRAIN",
        },
        actor=officer,
        as_officer=True,
    )
    member.refresh_from_db()
    room = rooming.create_room(iqo, edition, name="12", capacity=2, gender="F", actor=officer)
    rooming.assign(member, room, actor=officer)
    badges.create_checkpoint(iqo, edition, name="Arrival", actor=officer)
    client = logged_in(client_for, iqo, officer)
    page = client.get(reverse("web:coordinator-onsite-member", args=[member.pk]))
    assert page.status_code == 200
    assert AuditLog.objects.filter(action="logistics.member_viewed", target_id=str(member.pk)).exists()
    for name in (
        "coordinator-onsite-members",
        "coordinator-onsite-travel",
        "coordinator-onsite-rooming",
        "coordinator-onsite-dietary",
        "coordinator-onsite-tshirts",
        "coordinator-onsite-letters",
        "coordinator-onsite-settings",
    ):
        assert client.get(reverse(f"web:{name}")).status_code == 200, name
    export = client.get(reverse("web:coordinator-onsite-export", args=["rooming"]))
    assert export.status_code == 200
    assert client.get(reverse("web:coordinator-onsite-badges")).content.startswith(b"%PDF")


def test_coordinator_saves_event_settings_and_grants_access(client_for, iqo, coordinator):
    from apps.delegation_logistics.models import FinalEvent, LogisticsAccess

    client = logged_in(client_for, iqo, coordinator)
    response = client.post(
        reverse("web:coordinator-onsite-settings"),
        {
            "name": "IQO 2027",
            "city": "Warsaw",
            "starts_on": "2027-07-10",
            "ends_on": "2027-07-16",
            "retention_days": "30",
            "letter_prefix": "IQO",
            "deadline_identity": "2027-05-01T23:59",
        },
    )
    assert response.status_code == 302
    event = FinalEvent.objects.get(competition=iqo)
    assert event.city == "Warsaw" and event.deadline_identity is not None

    response = client.post(
        reverse("web:coordinator-onsite-access"), {"email": coordinator.email, "role": "OFFICER"}
    )
    assert response.status_code == 302
    assert LogisticsAccess.objects.filter(competition=iqo, user=coordinator, role="OFFICER").exists()
    assert client.get(reverse("web:coordinator-onsite-members")).status_code == 200


def test_officer_of_another_competition_gets_404(
    client_for, iqo, officer, leader, students, other_competition
):
    from apps.accounts.tests.test_delegations import make_delegations_competition
    from apps.delegation_logistics.models import AccessRole, LogisticsAccess

    from .conftest import enable

    member = first_student(leader)
    # Drugi konkurs z delegacjami i logistyką, w którym ta sama osoba też jest oficerem – a członek
    # delegacji pierwszego konkursu i tak nie istnieje „tutaj”.
    other = enable(make_delegations_competition(other_competition))
    LogisticsAccess.objects.create(competition=other, user=officer, role=AccessRole.OFFICER)
    client = logged_in(client_for, other, officer)
    assert client.get(reverse("web:coordinator-onsite-member", args=[member.pk])).status_code == 404


# --- obsługa rejestracji ---------------------------------------------------------------------------------


def test_checkin_staff_scans_a_badge(client_for, iqo, officer, checkin_user, leader, students, event):
    from apps.delegation_logistics import badges

    checkpoint = badges.create_checkpoint(iqo, event.edition, name="Arrival", actor=officer)
    member = first_student(leader)
    client = logged_in(client_for, iqo, checkin_user)
    url = reverse("web:onsite-checkin-member", args=[member.badge_token])

    page = client.get(url)
    assert page.status_code == 200
    content = page.content.decode()
    assert member.full_name in content and "C01X00T47" not in content
    assert client.post(url, {"cp": checkpoint.pk}).status_code == 302
    assert badges.status_of(member)
    # Obsługa nie widzi kart osób ani zestawień oficera.
    assert client.get(reverse("web:coordinator-onsite-member", args=[member.pk])).status_code == 403
    assert client.get(reverse("web:onsite-checkin-member", args=["nieznany"])).status_code == 404


def test_account_without_grant_cannot_scan(client_for, iqo, leader, students):
    member = first_student(leader)
    client = logged_in(client_for, iqo, UserFactory(email="nobody@example.test"))
    assert client.get(reverse("web:onsite-checkin-member", args=[member.badge_token])).status_code == 403
    anonymous = client_for(iqo)
    assert anonymous.get(reverse("web:onsite-checkin")).status_code == 302
