"""Superkoordynator: koordynator wszystkich konkursów i całego ``/cms/``, bez ``/admin/``.

Trzy grupy pytań: rola działa w obu konkursach (panel i ``/cms/``), zwykły koordynator zostaje
zawężony, a nadanie i odebranie idzie wyłącznie drogami z wpisem audytu — komenda i akcja
w ``/admin/`` dostępna tylko superużytkownikowi.
"""

from __future__ import annotations

import io

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts import super_coordinator
from apps.accounts.models import GROUP_COORDINATOR, GROUP_SUPER_COORDINATOR, CompetitionRole
from apps.accounts.services import grant_role, has_role, roles_for
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def enforced(competition, other_competition):
    """Dwa konkursy z rolami z członkostw — tak, jak ma wyglądać instalacja wielokonkursowa."""
    for row in (competition, other_competition):
        row.feature_flags = {"memberships_enforced": True}
        row.save()
    return competition, other_competition


def _super(email="super@example.test"):
    user = UserFactory(email=email)
    super_coordinator.grant(user)
    return user


def _audit(action, user):
    return AuditLog.objects.filter(action=action, target_type="accounts.user", target_id=str(user.pk))


# --- rola -----------------------------------------------------------------------------------------


def test_super_coordinator_is_a_coordinator_of_every_competition(enforced):
    a, b = enforced
    user = _super()

    for competition in (a, b, None):
        assert has_role(user, competition, CompetitionRole.COORDINATOR)
        assert CompetitionRole.COORDINATOR in roles_for(user, competition)


def test_super_coordinator_role_is_not_a_superuser(enforced):
    user = _super()
    user.refresh_from_db()

    assert not user.is_superuser
    assert not user.is_staff


def test_normal_coordinator_stays_scoped(enforced):
    a, b = enforced
    user = UserFactory(email="koordynator-a@example.test")
    grant_role(user, CompetitionRole.COORDINATOR, competition=a)

    assert has_role(user, a, CompetitionRole.COORDINATOR)
    assert not has_role(user, b, CompetitionRole.COORDINATOR)


def test_inactive_super_coordinator_has_no_role(enforced):
    a, _ = enforced
    user = _super()
    user.is_active = False
    user.save()

    assert not has_role(user, a, CompetitionRole.COORDINATOR)


@pytest.mark.parametrize("which", [0, 1])
def test_super_coordinator_opens_the_panel_of_each_competition(enforced, client_for, which):
    competition = enforced[which]
    client = client_for(competition)
    client.force_login(_super())

    response = client.get("/coordinator/")

    assert response.status_code == 200
    # Przełącznik konkursów w menu: oba konkursy, bieżący zaznaczony.
    text = response.content.decode()
    assert "Konkursy platformy" in text
    assert enforced[0].name in text and enforced[1].name in text


def test_normal_coordinator_gets_403_in_the_foreign_panel_and_no_switcher(enforced, client_for):
    a, b = enforced
    user = UserFactory(email="koordynator-a2@example.test")
    grant_role(user, CompetitionRole.COORDINATOR, competition=a)

    own = client_for(a)
    own.force_login(user)
    response = own.get("/coordinator/")
    assert response.status_code == 200
    assert "Konkursy platformy" not in response.content.decode()

    foreign = client_for(b)
    foreign.force_login(user)
    assert foreign.get("/coordinator/").status_code == 403


def test_super_coordinator_sees_data_of_the_competition_of_the_address(enforced, client_for):
    """Zawężenie danych zostaje: pod adresem konkursu B panel pokazuje uczestników B, nie A."""
    from apps.accounts.tests.factories import ParticipantFactory

    a, b = enforced
    ParticipantFactory(
        competition=a, user=UserFactory(email="uczestnik-a@example.test", last_name="Alfowski")
    )
    ParticipantFactory(
        competition=b, user=UserFactory(email="uczestnik-b@example.test", last_name="Betowski")
    )
    client = client_for(b)
    client.force_login(_super())

    text = client.get("/coordinator/accounts/").content.decode()

    assert "uczestnik-b@example.test" in text
    assert "uczestnik-a@example.test" not in text


def test_accounts_list_shows_the_role(enforced, client_for):
    a, _ = enforced
    user = _super("widoczny-super@example.test")
    client = client_for(a)
    client.force_login(user)

    text = client.get("/coordinator/accounts/", {"q": "widoczny-super"}).content.decode()

    assert "superkoordynator" in text


def test_my_competitions_lists_every_competition(enforced):
    from apps.web.views.coordinator_competitions import coordinated_competitions

    user = _super()

    assert set(coordinated_competitions(user)) == set(enforced)


# --- nadanie i odebranie --------------------------------------------------------------------------


def test_grant_and_revoke_via_command_are_idempotent_and_audited(competition):
    user = UserFactory(email="przez-komende@example.test")

    call_command("superkoordynator", "--grant", "PRZEZ-komende@example.test", stdout=io.StringIO())
    call_command("superkoordynator", "--grant", user.email, stdout=io.StringIO())

    assert user.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    assert _audit(super_coordinator.AUDIT_GRANTED, user).count() == 1

    call_command("superkoordynator", "--revoke", user.email, stdout=io.StringIO())
    call_command("superkoordynator", "--revoke", user.email, stdout=io.StringIO())

    assert not user.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    assert _audit(super_coordinator.AUDIT_REVOKED, user).count() == 1


def test_revocation_takes_the_access_away(enforced):
    a, b = enforced
    user = _super()
    super_coordinator.revoke(user)
    fresh = type(user).objects.get(pk=user.pk)

    assert not has_role(fresh, b, CompetitionRole.COORDINATOR)
    assert not has_role(fresh, a, CompetitionRole.COORDINATOR)


def test_revocation_keeps_the_competition_role(enforced):
    a, _ = enforced
    user = UserFactory(email="oba@example.test")
    grant_role(user, CompetitionRole.COORDINATOR, competition=a)
    super_coordinator.grant(user)

    super_coordinator.revoke(user)
    fresh = type(user).objects.get(pk=user.pk)

    assert has_role(fresh, a, CompetitionRole.COORDINATOR)
    assert fresh.groups.filter(name=GROUP_COORDINATOR).exists()


def test_unknown_email_is_an_error():
    with pytest.raises(CommandError, match="Nie ma konta"):
        call_command("superkoordynator", "--grant", "nie-ma@example.test")


def test_list_prints_the_super_coordinators(competition):
    _super("na-liscie@example.test")
    out = io.StringIO()

    call_command("superkoordynator", "--list", stdout=out)

    assert "na-liscie@example.test" in out.getvalue()


def test_all_current_coordinators_grants_every_active_coordinator(competition, other_competition):
    other_competition.feature_flags = {"memberships_enforced": True}
    other_competition.save()
    by_group = UserFactory(email="z-grupy@example.test", groups=[GROUP_COORDINATOR])
    by_membership = UserFactory(email="z-czlonkostwa@example.test")
    grant_role(by_membership, CompetitionRole.COORDINATOR, competition=other_competition)
    by_membership.groups.remove(Group.objects.get(name=GROUP_COORDINATOR))
    blocked = UserFactory(email="zablokowany@example.test", groups=[GROUP_COORDINATOR], is_active=False)
    bystander = UserFactory(email="uczestnik@example.test", groups=["participant"])
    out = io.StringIO()

    call_command("superkoordynator", "--all-current-coordinators", stdout=out)

    for user in (by_group, by_membership):
        assert user.groups.filter(name=GROUP_SUPER_COORDINATOR).exists(), user.email
        assert _audit(super_coordinator.AUDIT_GRANTED, user).exists()
    assert not blocked.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    assert not bystander.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    text = out.getvalue()
    assert "z-grupy@example.test" in text and "z-czlonkostwa@example.test" in text
    assert "pomijam" in text and "zablokowany@example.test" in text


def test_all_current_coordinators_dry_run_writes_nothing(competition):
    user = UserFactory(email="suchy@example.test", groups=[GROUP_COORDINATOR])
    out = io.StringIO()

    call_command("superkoordynator", "--all-current-coordinators", "--dry-run", stdout=out)

    assert not user.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    assert not _audit(super_coordinator.AUDIT_GRANTED, user).exists()
    assert "suchy@example.test" in out.getvalue()
    assert "Próba na sucho" in out.getvalue()


def test_all_current_coordinators_is_idempotent(competition):
    user = UserFactory(email="dwa-razy@example.test", groups=[GROUP_COORDINATOR])
    call_command("superkoordynator", "--all-current-coordinators", stdout=io.StringIO())
    out = io.StringIO()

    call_command("superkoordynator", "--all-current-coordinators", stdout=out)

    assert _audit(super_coordinator.AUDIT_GRANTED, user).count() == 1
    assert "Zmienionych kont: 0" in out.getvalue()


def test_admin_actions_are_for_superusers_only(competition, client):
    target = UserFactory(email="cel@example.test")
    staff = UserFactory(email="personel@example.test", is_staff=True)
    staff.user_permissions.add(*_user_admin_permissions())
    client.force_login(staff)
    payload = {"action": "grant_super_coordinator", "_selected_action": [target.pk]}

    client.post("/admin/accounts/user/", payload)
    assert not target.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()

    admin = UserFactory(email="operator@example.test", is_staff=True, is_superuser=True)
    client.force_login(admin)
    client.post("/admin/accounts/user/", payload)
    assert target.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    entry = _audit(super_coordinator.AUDIT_GRANTED, target).get()
    assert entry.actor_id == admin.pk
    assert entry.diff["via"] == "admin"

    client.post(
        "/admin/accounts/user/", {"action": "revoke_super_coordinator", "_selected_action": [target.pk]}
    )
    assert not target.groups.filter(name=GROUP_SUPER_COORDINATOR).exists()
    assert _audit(super_coordinator.AUDIT_REVOKED, target).exists()


def _user_admin_permissions():
    from django.contrib.auth.models import Permission

    return list(Permission.objects.filter(content_type__app_label="accounts", content_type__model="user"))
