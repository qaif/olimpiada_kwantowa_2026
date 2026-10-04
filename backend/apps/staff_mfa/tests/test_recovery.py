"""Kody, blokada, zapamiętane urządzenie, wyłączenie, nowe kody i reset (SEC-01 § 4–5, § 7)."""

from __future__ import annotations

import pytest
from django.core import mail

from apps.accounts import twofactor
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog
from apps.staff_mfa import trust
from apps.staff_mfa.models import TwoFactorPolicy

from .conftest import PASSWORD, enable_fees, enable_for, fresh_code, super_coordinator

pytestmark = pytest.mark.django_db

VERIFY_URL = "/login/2fa/"


# --- jednorazowość ---------------------------------------------------------------------------------


def test_the_same_totp_step_is_accepted_once():
    user = UserFactory()
    enable_for(user)
    code = fresh_code(user)

    assert twofactor.verify(user, code) is True
    assert twofactor.verify(user, code) is False


def test_a_parallel_request_with_a_stale_device_cannot_reuse_the_step():
    """Dwa żądania czytają urządzenie przed zapisem – baza przyjmuje krok tylko raz (warunkowy UPDATE)."""
    user = UserFactory()
    enable_for(user)
    first, second = twofactor.device_for(user), twofactor.device_for(user)
    code = fresh_code(user)

    assert twofactor._check_totp(first, code) is True
    assert twofactor._check_totp(second, code) is False


def test_a_backup_code_works_once_even_from_a_stale_device_object():
    user = UserFactory()
    codes = enable_for(user)
    stale = twofactor.device_for(user)

    assert twofactor.verify(user, codes[0]) is True
    assert twofactor._check_backup_code(stale, codes[0]) is False
    assert twofactor.verify(user, codes[0]) is False
    assert twofactor.device_for(user).backup_codes_left == twofactor.BACKUP_CODE_COUNT - 1


def test_using_a_backup_code_sends_a_mail(django_capture_on_commit_callbacks):
    user = UserFactory(email="komisja@example.test")
    codes = enable_for(user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        twofactor.verify(user, codes[0])

    assert [message.to for message in mail.outbox] == [["komisja@example.test"]]
    assert str(twofactor.BACKUP_CODE_COUNT - 1) in mail.outbox[0].body


# --- blokada ---------------------------------------------------------------------------------------


def test_five_wrong_codes_lock_the_account_even_for_the_right_code(django_capture_on_commit_callbacks):
    user = UserFactory()
    enable_for(user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        for _attempt in range(twofactor.LOCKOUT_THRESHOLD):
            assert twofactor.verify(user, "000000") is False

    assert twofactor.is_locked(user)
    assert twofactor.verify(user, fresh_code(user)) is False
    assert AuditLog.objects.filter(action="2fa.locked").count() == 1
    assert len(mail.outbox) == 1


def test_the_verify_screen_says_the_account_is_locked(client, competition):
    user = ParticipantFactory(competition=competition).user
    enable_for(user)
    client.force_login(user)
    for _attempt in range(twofactor.LOCKOUT_THRESHOLD - 1):
        assert client.post(VERIFY_URL, {"code": "000000"}).status_code == 400

    response = client.post(VERIFY_URL, {"code": "000000"})

    assert response.status_code == 429
    assert client.post(VERIFY_URL, {"code": fresh_code(user)}).status_code == 429
    assert client.get("/me/").headers["Location"] == VERIFY_URL


def test_a_good_code_resets_the_failure_counter():
    user = UserFactory()
    enable_for(user)
    for _attempt in range(twofactor.LOCKOUT_THRESHOLD - 1):
        twofactor.verify(user, "000000")

    assert twofactor.verify(user, fresh_code(user)) is True
    twofactor.verify(user, "000000")
    assert not twofactor.is_locked(user)


def test_the_api_reports_the_lock(competition):
    from rest_framework.test import APIClient

    user = ParticipantFactory(competition=competition).user
    enable_for(user)
    for _attempt in range(twofactor.LOCKOUT_THRESHOLD):
        twofactor.verify(user, "000000")

    response = APIClient().post(
        "/api/auth/login/",
        {"email": user.email, "password": PASSWORD, "code": fresh_code(user)},
        format="json",
    )

    assert response.status_code == 429
    assert response.json()["code"] == "TWO_FACTOR_LOCKED"


# --- wyłączenie i nowe kody ------------------------------------------------------------------------


def _verified_client(client, user):
    codes = enable_for(user)
    client.force_login(user)
    client.post(VERIFY_URL, {"code": codes.pop()})
    return codes


@pytest.mark.parametrize(
    ("password", "code_ok"),
    [("zle-haslo", True), (PASSWORD, False), ("", True)],
)
def test_disabling_needs_both_the_password_and_a_code(client, competition, password, code_ok):
    user = ParticipantFactory(competition=competition).user
    codes = _verified_client(client, user)

    client.post("/account/2fa/disable/", {"password": password, "code": codes[0] if code_ok else "000000"})

    assert twofactor.confirmed_device(user) is not None


def test_disabling_with_password_and_code_works_and_notifies(
    client, competition, django_capture_on_commit_callbacks
):
    user = ParticipantFactory(competition=competition).user
    codes = _verified_client(client, user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post("/account/2fa/disable/", {"password": PASSWORD, "code": codes[0]})

    assert response.status_code == 302
    assert twofactor.device_for(user) is None
    assert AuditLog.objects.filter(action="2fa.disabled").exists()
    assert any("Wyłączono" in message.subject for message in mail.outbox)


def test_new_backup_codes_replace_the_old_ones(client, competition):
    user = ParticipantFactory(competition=competition).user
    old_codes = _verified_client(client, user)

    response = client.post("/account/2fa/codes/regenerate/", {"password": PASSWORD, "code": old_codes[0]})

    assert response.status_code == 302
    assert response.headers["Location"] == "/account/2fa/codes/"
    page = client.get("/account/2fa/codes/").content.decode()
    assert page.count("<code>") == twofactor.BACKUP_CODE_COUNT
    assert twofactor.verify(user, old_codes[1]) is False
    assert AuditLog.objects.filter(action="2fa.codes_regenerated").exists()


def test_new_backup_codes_need_the_password(client, competition):
    user = ParticipantFactory(competition=competition).user
    old_codes = _verified_client(client, user)

    response = client.post("/account/2fa/codes/regenerate/", {"password": "zle", "code": old_codes[0]})

    assert response.status_code == 400
    assert twofactor.verify(user, old_codes[0]) is True


def test_a_password_only_session_cannot_regenerate_codes(client, competition):
    user = ParticipantFactory(competition=competition).user
    codes = enable_for(user)
    client.force_login(user)

    response = client.post("/account/2fa/codes/regenerate/", {"password": PASSWORD, "code": codes[0]})

    assert response.headers["Location"] == VERIFY_URL
    assert twofactor.verify(user, codes[0]) is True


# --- zapamiętane urządzenie ------------------------------------------------------------------------


def _remembered_cookie(client, user) -> str:
    codes = enable_for(user)
    client.force_login(user)
    response = client.post(VERIFY_URL, {"code": codes[0], "remember": "1"})
    cookie = response.cookies[trust.COOKIE_NAME]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    return cookie.value


def test_a_remembered_device_skips_the_code_in_the_next_session(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered_cookie(client_for(competition), user)
    browser = client_for(competition)
    browser.cookies[trust.COOKIE_NAME] = value
    browser.force_login(user)

    assert browser.get("/me/").status_code == 200
    assert AuditLog.objects.filter(action="2fa.remembered").exists()


def test_the_remembered_device_does_not_cover_another_account(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered_cookie(client_for(competition), user)
    other = ParticipantFactory(competition=competition, user=UserFactory(email="inny@example.test")).user
    enable_for(other)
    browser = client_for(competition)
    browser.cookies[trust.COOKIE_NAME] = value
    browser.force_login(other)

    assert browser.get("/me/").headers["Location"] == VERIFY_URL


def test_a_password_change_forgets_the_device(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered_cookie(client_for(competition), user)
    user.set_password("Nowe-Haslo-2026!")
    user.save()
    browser = client_for(competition)
    browser.cookies[trust.COOKIE_NAME] = value
    browser.force_login(user)

    assert browser.get("/me/").headers["Location"] == VERIFY_URL


def test_the_competition_may_forbid_remembering(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered_cookie(client_for(competition), user)
    TwoFactorPolicy.objects.create(competition=competition, allow_remember=False)
    browser = client_for(competition)
    browser.cookies[trust.COOKIE_NAME] = value
    browser.force_login(user)

    assert browser.get("/me/").headers["Location"] == VERIFY_URL
    assert "remember" not in browser.get(VERIFY_URL).content.decode()


def test_a_tampered_cookie_is_ignored(client_for, competition):
    user = ParticipantFactory(competition=competition).user
    value = _remembered_cookie(client_for(competition), user)
    browser = client_for(competition)
    browser.cookies[trust.COOKIE_NAME] = value[:-2] + ("AA" if not value.endswith("AA") else "BB")
    browser.force_login(user)

    assert browser.get("/me/").headers["Location"] == VERIFY_URL


# --- reset przez organizatora ----------------------------------------------------------------------


def _reset_url(user) -> str:
    return f"/coordinator/accounts/{user.pk}/2fa-reset/"


def test_a_coordinator_cannot_reset_a_staff_account_when_a_super_coordinator_exists(client, competition):
    enable_fees(competition)
    super_coordinator()
    colleague = CoordinatorFactory(email="kolega@example.test")
    enable_for(colleague)
    actor = CoordinatorFactory(email="koordynator@example.test")
    _verified_client(client, actor)

    page = client.get(f"/coordinator/accounts/{colleague.pk}/").content.decode()
    response = client.post(_reset_url(colleague))

    assert "Zdejmij drugi składnik" not in page
    assert response.status_code == 403
    assert twofactor.confirmed_device(colleague) is not None


def test_the_super_coordinator_resets_a_staff_account_with_audit_and_mail(
    client, competition, django_capture_on_commit_callbacks
):
    colleague = CoordinatorFactory(email="kolega@example.test")
    enable_for(colleague)
    actor = super_coordinator()
    _verified_client(client, actor)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_reset_url(colleague))

    assert response.status_code == 302
    assert twofactor.device_for(colleague) is None
    entry = AuditLog.objects.get(action="2fa.reset")
    assert entry.actor_id == actor.pk
    assert [message.to for message in mail.outbox] == [["kolega@example.test"]]


def test_without_any_super_coordinator_a_coordinator_may_reset_staff(client, competition):
    """Instalacja bez superkoordynatora nie może zostać bez drogi powrotu (jak „pierwszy oficer”)."""
    colleague = CoordinatorFactory(email="kolega@example.test")
    enable_for(colleague)
    actor = CoordinatorFactory(email="koordynator@example.test")
    _verified_client(client, actor)

    assert client.post(_reset_url(colleague)).status_code == 302
    assert twofactor.device_for(colleague) is None


def test_after_a_reset_a_required_account_configures_at_once(client, competition):
    """Reset nie daje nowego okresu przejściowego – konto konfiguruje 2FA zaraz po haśle."""
    enable_fees(competition)
    colleague = CoordinatorFactory(email="kolega@example.test")
    enable_for(colleague)
    twofactor.reset_by_coordinator(colleague, actor=super_coordinator())
    client.force_login(colleague)

    assert client.get("/coordinator/").headers["Location"] == "/account/2fa/"


# --- RODO ------------------------------------------------------------------------------------------


def test_anonymising_an_account_drops_its_second_factor_and_grace(competition):
    from apps.accounts.profile import anonymise_account
    from apps.staff_mfa.models import TwoFactorGrace

    coordinator = CoordinatorFactory(email="odchodzi@example.test")
    enable_for(coordinator)
    TwoFactorGrace.objects.create(user=coordinator)

    anonymise_account(coordinator)

    assert not twofactor.TwoFactorDevice.objects.filter(user=coordinator).exists()
    assert not TwoFactorGrace.objects.filter(user=coordinator).exists()
