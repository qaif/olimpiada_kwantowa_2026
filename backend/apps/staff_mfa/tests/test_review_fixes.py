"""Regresje z przeglądu SEC-01 (PR #71): H1, H2, M1–M3, L1–L8. Nazwa testu zaczyna się od ID uwagi."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.accounts import twofactor
from apps.accounts.models import GROUP_COORDINATOR, GROUP_TEAM_LEADER, Membership
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog
from apps.staff_mfa import policy, trust
from apps.staff_mfa.models import PolicyMode, PreviousEmail, TwoFactorGrace, TwoFactorPolicy

from .conftest import PASSWORD, enable_fees, enable_for, fresh_code, super_coordinator

pytestmark = pytest.mark.django_db

SETUP_URL = "/account/2fa/"
VERIFY_URL = "/login/2fa/"


def _verified(client, user):
    codes = enable_for(user)
    client.force_login(user)
    client.post(VERIFY_URL, {"code": codes.pop()})
    return codes


def _account_fields(user, **overrides) -> dict:
    data = {
        "account-first_name": user.first_name,
        "account-last_name": user.last_name,
        "account-email": user.email,
        "account-is_active": "on" if user.is_active else "",
    }
    data.update(overrides)
    return data


@pytest.fixture
def coordinator_client(client):
    """Zwykły koordynator konkursu (bez roli superkoordynatora), sesja po drugim składniku."""
    actor = CoordinatorFactory(email="koordynator@example.test")
    _verified(client, actor)
    return client


# --- H1: blokada konta → reset 2FA → zmiana adresu → reset hasła ---------------------------------


def test_h1_blocking_a_staff_account_does_not_unlock_the_reset(coordinator_client, competition):
    """PoC z przeglądu: koordynator blokuje konto opiekuna drużyny i zdejmuje mu 2FA."""
    super_coordinator()
    leader = UserFactory(email="leader@example.test", groups=[GROUP_TEAM_LEADER])
    enable_for(leader)

    coordinator_client.post(
        f"/coordinator/accounts/{leader.pk}/", _account_fields(leader, **{"account-is_active": ""})
    )
    leader.refresh_from_db()
    assert leader.is_active is False

    response = coordinator_client.post(f"/coordinator/accounts/{leader.pk}/2fa-reset/")

    assert response.status_code == 403
    assert twofactor.confirmed_device(leader) is not None


def test_h1_control_a_coordinator_changes_the_email_of_a_plain_account(coordinator_client, competition):
    """Kontrola: ten sam POST na koncie bez 2FA i bez roli personelu zmienia adres (jak dotąd)."""
    account = UserFactory(email="konto@example.test")

    coordinator_client.post(
        f"/coordinator/accounts/{account.pk}/", _account_fields(account, **{"account-email": "nowy@example.test"})
    )

    account.refresh_from_db()
    assert account.email == "nowy@example.test"


def test_h1_a_coordinator_cannot_change_the_email_of_an_account_with_2fa(coordinator_client, competition):
    account = UserFactory(email="konto@example.test")
    enable_for(account)

    response = coordinator_client.post(
        f"/coordinator/accounts/{account.pk}/",
        _account_fields(account, **{"account-email": "przejety@example.test"}),
    )

    account.refresh_from_db()
    assert account.email == "konto@example.test"
    assert "wyłącznie" in response.content.decode() or response.status_code in (302, 400)


def test_h1_a_coordinator_cannot_change_the_email_of_a_staff_account_even_without_2fa(
    coordinator_client, competition
):
    """Bez tego łańcuch „reset 2FA (wyjątek bez superkoordynatora) → nowy adres” zostaje otwarty."""
    leader = UserFactory(email="leader@example.test", groups=[GROUP_TEAM_LEADER])

    coordinator_client.post(
        f"/coordinator/accounts/{leader.pk}/",
        _account_fields(leader, **{"account-email": "przejety@example.test"}),
    )

    leader.refresh_from_db()
    assert leader.email == "leader@example.test"


def test_h1_the_super_coordinator_may_change_the_email(client, competition):
    actor = super_coordinator()
    _verified(client, actor)
    account = UserFactory(email="konto@example.test")
    enable_for(account)

    client.post(
        f"/coordinator/accounts/{account.pk}/", _account_fields(account, **{"account-email": "nowy@example.test"})
    )

    account.refresh_from_db()
    assert account.email == "nowy@example.test"
    assert PreviousEmail.objects.filter(user=account, email="konto@example.test").exists()


def test_h1_the_reset_mail_also_goes_to_the_previous_address(
    client, competition, django_capture_on_commit_callbacks
):
    actor = super_coordinator()
    _verified(client, actor)
    target = UserFactory(email="konto@example.test")
    old = target.email
    enable_for(target)
    client.post(
        f"/coordinator/accounts/{target.pk}/",
        _account_fields(target, **{"account-email": "nowy@example.test"}),
    )
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/coordinator/accounts/{target.pk}/2fa-reset/")

    recipients = sorted(address for message in mail.outbox for address in message.to)
    assert recipients == sorted(["nowy@example.test", old])


def test_h1_close_grace_works_for_an_inactive_account():
    leader = UserFactory(groups=[GROUP_TEAM_LEADER], is_active=False)

    policy.close_grace(leader)

    assert TwoFactorGrace.objects.get(user=leader).required_since < timezone.now() - timedelta(days=365)


# --- H2: personel liczony w całej platformie ------------------------------------------------------


def test_h2_staff_of_another_competition_is_staff_for_the_reset(
    coordinator_client, competition, other_competition
):
    super_coordinator()
    participant = ParticipantFactory(competition=competition)
    Membership.objects.create(user=participant.user, competition=other_competition, role=GROUP_COORDINATOR)
    enable_for(participant.user)

    response = coordinator_client.post(f"/coordinator/accounts/{participant.user.pk}/2fa-reset/")

    assert response.status_code == 403
    assert policy.is_staff_anywhere(participant.user)


def test_h2_delegation_leader_and_logistics_rows_count_as_staff(competition, other_competition):
    from apps.delegation_logistics.models import AccessRole, LogisticsAccess

    user = UserFactory()
    LogisticsAccess.objects.create(competition=other_competition, user=user, role=AccessRole.CHECKIN)

    footprint = policy.staff_footprint(user)

    assert "logistics" in footprint["keys"] and footprint["competitions"] == {other_competition.pk}


# --- M1: wyjątek bez superkoordynatora, ostrzeżenia, komenda --------------------------------------


def test_m1_fallback_allows_staff_of_this_competition_only(competition, other_competition):
    actor = CoordinatorFactory(email="koordynator@example.test")
    here = UserFactory(email="here@example.test")
    Membership.objects.create(user=here, competition=competition, role="reviewer")
    there = UserFactory(email="there@example.test")
    Membership.objects.create(user=there, competition=other_competition, role="reviewer")
    admin = UserFactory(email="admin@example.test", is_staff=True)

    assert policy.may_reset(actor, here, competition) is True
    assert policy.may_reset(actor, there, competition) is False
    assert policy.may_reset(actor, admin, competition) is False


def test_m1_system_checks_warn_about_missing_super_coordinator_and_empty_roles(settings):
    from apps.staff_mfa.checks import two_factor_policy_checks

    ids = {warning.id for warning in two_factor_policy_checks()}
    assert ids == {"staff_mfa.W001", "staff_mfa.W002"}

    settings.TWO_FACTOR_REQUIRED_ROLES = ["superkoordynator", "admin"]
    super_coordinator()
    assert two_factor_policy_checks() == []

    settings.TWO_FACTOR_ENABLED = False
    settings.TWO_FACTOR_REQUIRED_ROLES = []
    assert two_factor_policy_checks() == []


def test_m1_cli_reset_with_audit_and_note():
    user = UserFactory(email="admin@example.test", is_staff=True)
    enable_for(user)

    call_command("reset_2fa", "ADMIN@example.test", note="telefon, potwierdzone wideo")

    assert twofactor.device_for(user) is None
    entry = AuditLog.objects.get(action="2fa.reset")
    assert entry.actor_id is None and entry.diff["via"] == "cli"


# --- M2: termin ról platformy ---------------------------------------------------------------------


def test_m2_the_competition_cannot_extend_the_grace_of_platform_roles(settings, competition):
    settings.TWO_FACTOR_REQUIRED_ROLES = ["superkoordynator"]
    settings.TWO_FACTOR_GRACE_DAYS = 14
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, grace_days=90)
    boss = super_coordinator()
    coordinator = CoordinatorFactory()

    boss_deadline = twofactor.requirement(boss, competition).deadline
    coordinator_deadline = twofactor.requirement(coordinator, competition).deadline

    assert (boss_deadline - timezone.now()).days <= 14
    assert (coordinator_deadline - timezone.now()).days >= 89


# --- M3: znaczniki sesji --------------------------------------------------------------------------


def test_m3a_the_exempt_marker_expires(client, competition):
    from apps.accounts.models import GROUP_REVIEWER

    reviewer = UserFactory(groups=[GROUP_REVIEWER])
    client.force_login(reviewer)
    client.get("/me/")
    TwoFactorPolicy.objects.create(
        competition=competition, mode=PolicyMode.CUSTOM, roles=["reviewer"], grace_days=0
    )
    session = client.session
    marker = session[twofactor.SESSION_EXEMPT_KEY]
    session[twofactor.SESSION_EXEMPT_KEY] = [*marker[:3], time.time() - 1, marker[4]]
    session.save()

    assert client.get("/me/").headers["Location"] == SETUP_URL


def test_m3a_granting_a_role_bumps_the_policy_version(client, competition):
    from apps.accounts.models import GROUP_REVIEWER

    TwoFactorPolicy.objects.create(
        competition=competition, mode=PolicyMode.CUSTOM, roles=["coordinator"], grace_days=0
    )
    user = UserFactory(groups=[GROUP_REVIEWER])
    client.force_login(user)
    client.get("/me/")
    before = policy.version()

    user.groups.add(Group.objects.get(name=GROUP_COORDINATOR))

    assert policy.version() != before
    assert client.get("/coordinator/").headers["Location"] == SETUP_URL


def test_m3a_registering_a_participant_does_not_bump_the_version(competition):
    before = policy.version()

    ParticipantFactory(competition=competition)
    Membership.objects.create(user=UserFactory(), competition=competition, role="participant")

    assert policy.version() == before


def test_m3b_enabling_2fa_closes_the_other_sessions(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    other = client_for(competition)
    other.force_login(user)
    assert other.get("/me/").status_code == 200
    here = client_for(competition)
    here.force_login(user)
    here.get(SETUP_URL)
    device = twofactor.device_for(user)

    here.post(SETUP_URL, {"code": twofactor.totp_code(device.plain_secret(), twofactor.current_counter())})

    assert here.get("/me/").status_code == 200
    assert other.get("/me/").headers["Location"].startswith("/login/")


def test_m3b_a_reset_closes_the_owners_sessions(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    owner = client_for(competition)
    _verified(owner, user)
    assert owner.get("/me/").status_code == 200

    twofactor.reset_by_coordinator(user, actor=super_coordinator())

    assert owner.get("/me/").headers["Location"].startswith("/login/")


def test_m3c_a_pre_deploy_verified_marker_is_ignored(client, competition):
    user = ParticipantFactory(competition=competition).user
    enable_for(user)
    client.force_login(user)
    session = client.session
    session["2fa_verified"] = user.pk  # klucz sprzed SEC-01
    session.save()

    assert twofactor.SESSION_VERIFIED_KEY != "2fa_verified"
    assert client.get("/me/").headers["Location"] == VERIFY_URL


# --- L1–L3 ----------------------------------------------------------------------------------------


def test_l1_a_broken_counter_does_not_raise(monkeypatch):
    user = UserFactory()
    enable_for(user)
    monkeypatch.setattr(cache, "incr", lambda *args, **kwargs: None)

    assert twofactor.verify(user, "000000") is False
    assert twofactor.verify(user, fresh_code(user)) is True


def test_l2_attempts_are_counted_before_the_code_is_checked():
    """Równoległa seria: miejsca zajęte przez próby w toku – kolejna, nawet z dobrym kodem, nie przechodzi."""
    user = UserFactory()
    enable_for(user)
    cache.set(twofactor._fail_key(user), twofactor.LOCKOUT_THRESHOLD, 600)

    assert twofactor.verify(user, fresh_code(user)) is False
    assert twofactor.is_locked(user)


def test_l3_grace_rows_cannot_be_deleted_in_admin(rf):
    from django.contrib.admin.sites import site

    from apps.staff_mfa.models import TwoFactorGrace as Model

    request = rf.get("/admin/")
    request.user = UserFactory(is_staff=True, is_superuser=True)
    assert site._registry[Model].has_delete_permission(request) is False


# --- L4: zapamiętane urządzenie -------------------------------------------------------------------


def _remembered(client_for, competition, user) -> str:
    codes = enable_for(user)
    browser = client_for(competition)
    browser.force_login(user)
    return browser.post(VERIFY_URL, {"code": codes[0], "remember": "1"}).cookies[trust.COOKIE_NAME].value


def test_l4_the_cookie_only_works_for_the_competition_that_issued_it(
    client_for, competition, other_competition
):
    user = ParticipantFactory(competition=competition).user
    value = _remembered(client_for, competition, user)
    browser = client_for(other_competition)
    browser.cookies[trust.COOKIE_NAME] = value
    browser.force_login(user)

    assert browser.get("/me/").headers["Location"] == VERIFY_URL


def test_l4_forget_all_devices(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered(client_for, competition, user)
    owner = client_for(competition)
    owner.cookies[trust.COOKIE_NAME] = value
    owner.force_login(user)
    assert owner.get("/me/").status_code == 200

    owner.post("/account/2fa/forget-devices/")
    stolen = client_for(competition)
    stolen.cookies[trust.COOKIE_NAME] = value
    stolen.force_login(user)

    assert stolen.get("/me/").headers["Location"] == VERIFY_URL
    assert AuditLog.objects.filter(action="2fa.devices_forgotten").exists()


# --- L5: prefiks ścieżki --------------------------------------------------------------------------


def test_l5_allowed_prefixes_are_cached_per_script_prefix():
    from django.urls import set_script_prefix

    twofactor._allowed_prefixes.cache_clear()
    try:
        set_script_prefix("/")
        root = twofactor._allowed_prefixes("web:twofactor-verify", "/")
        set_script_prefix("/druga/")
        prefixed = twofactor._allowed_prefixes("web:twofactor-verify", "/druga/")
    finally:
        set_script_prefix("/")
    assert root[0] == "/login/2fa/"
    assert prefixed[0] == "/druga/login/2fa/"


def test_l5_a_pending_session_under_a_path_prefix_reaches_its_code_screen(competition, client_for):
    from apps.tenancy.provisioning import create_competition_from_template
    from apps.tenancy.templates_catalog import TEMPLATE_PUSTY
    from apps.tenancy.tests.conftest import HOST_A
    from apps.tenancy.tests.factories import enforce_memberships_everywhere

    client_for(competition)  # dopisuje domeny testowe do ALLOWED_HOSTS
    enforce_memberships_everywhere()
    create_competition_from_template(
        slug="druga",
        name="Olimpiada Druga",
        domain="druga.test",
        template=TEMPLATE_PUSTY,
        path_prefix="druga",
    )
    twofactor._allowed_prefixes.cache_clear()
    user = UserFactory()
    enable_for(user)
    browser = Client(HTTP_HOST=HOST_A, SERVER_NAME=HOST_A)
    browser.force_login(user)
    browser.get("/me/")  # pamięć listy adresów liczona najpierw dla gospodarza (prefiks „/”)

    assert browser.get("/druga/me/").headers["Location"] == "/druga/login/2fa/"
    assert browser.get("/druga/login/2fa/").status_code == 200


# --- L6: lista personelu --------------------------------------------------------------------------


def test_l6_only_the_super_coordinator_sees_the_staff_list(coordinator_client, client_for, competition):
    CoordinatorFactory(email="bez2fa@example.test")

    assert "bez2fa@example.test" not in coordinator_client.get("/coordinator/security/2fa/").content.decode()

    boss = client_for(competition)
    _verified(boss, super_coordinator())
    assert "bez2fa@example.test" in boss.get("/coordinator/security/2fa/").content.decode()


def test_l6_the_staff_list_has_a_constant_number_of_queries(client_for, competition):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    boss = client_for(competition)
    _verified(boss, super_coordinator())
    for index in range(3):
        CoordinatorFactory(email=f"k{index}@example.test")
    with CaptureQueriesContext(connection) as few:
        boss.get("/coordinator/security/2fa/")
    for index in range(3, 13):
        CoordinatorFactory(email=f"k{index}@example.test")
    with CaptureQueriesContext(connection) as many:
        boss.get("/coordinator/security/2fa/")

    assert len(many.captured_queries) <= len(few.captured_queries) + 2


# --- L8: uczestnik nie płaci odczytem wersji polityki --------------------------------------------


def test_l8_a_participant_session_does_not_read_the_policy_version(client, competition, monkeypatch):
    participant = ParticipantFactory(competition=competition)
    client.force_login(participant.user)
    assert client.get("/me/").status_code == 200

    def boom():
        raise AssertionError("wersja polityki czytana dla uczestnika")

    monkeypatch.setattr(policy, "version", boom)

    assert client.get("/me/").status_code == 200


def test_l8_a_password_check_still_works_for_disable(client, competition):
    """Sanity: zmiany w liczniku prób nie psują wyłączenia z hasłem i kodem."""
    user = ParticipantFactory(competition=competition).user
    codes = _verified(client, user)

    client.post("/account/2fa/disable/", {"password": PASSWORD, "code": codes[0]})

    assert twofactor.device_for(user) is None
