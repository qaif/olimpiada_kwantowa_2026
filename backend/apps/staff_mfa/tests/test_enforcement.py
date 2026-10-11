"""Warstwa wymuszająca z polityką SEC-01: okres przejściowy z banerem, potem poczekalnia.

Pytania, na które odpowiada tylko żądanie HTTP: czy baner naprawdę stoi na stronie, czy po
terminie panel się zamyka, czy konkurs bez funkcji wrażliwych niczego nie zauważa i czy uczestnik
jest nietknięty – także z wyłącznikiem w pozycji „wyłączone”.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import twofactor
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.staff_mfa.models import PolicyMode, TwoFactorGrace, TwoFactorPolicy

from .conftest import PASSWORD, enable_fees, enable_for, fresh_code

pytestmark = pytest.mark.django_db

SETUP_URL = "/account/2fa/"
BANNER = "wymaga logowania dwuskładnikowego"


def _expire_grace(user):
    TwoFactorGrace.objects.filter(user=user).update(required_since=timezone.now() - timedelta(days=60))


def test_in_the_grace_period_the_panel_works_and_shows_a_banner(client, competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)

    response = client.get("/coordinator/")

    assert response.status_code == 200
    body = response.content.decode()
    assert BANNER in body and SETUP_URL in body
    assert TwoFactorGrace.objects.filter(user=coordinator).exists()


def test_after_the_grace_period_the_whole_session_waits_for_setup(client, competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)
    client.get("/coordinator/")
    _expire_grace(coordinator)
    # Nowa sesja – znacznik okresu przejściowego niesie termin i tak, ale sprawdzamy drogę „od zera”.
    client.logout()
    client.force_login(coordinator)

    for url in ("/coordinator/", "/account/email/", "/cms/", "/admin/"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"] == SETUP_URL, url
    assert client.get(SETUP_URL).status_code == 200


def test_the_grace_deadline_is_enforced_inside_a_running_session(client, competition):
    """Znacznik w sesji niesie termin – po nim sesja liczy wymóg od nowa, bez wylogowania."""
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)
    client.get("/coordinator/")
    session = client.session
    marker = session[twofactor.SESSION_GRACE_KEY]
    session[twofactor.SESSION_GRACE_KEY] = [*marker[:3], timezone.now().timestamp() - 1]
    session.save()
    _expire_grace(coordinator)

    assert client.get("/coordinator/").headers["Location"] == SETUP_URL


def test_a_competition_without_sensitive_features_sees_no_banner(client, competition):
    client.force_login(CoordinatorFactory())

    response = client.get("/coordinator/")

    assert response.status_code == 200
    assert BANNER not in response.content.decode()


def test_the_same_coordinator_is_free_on_the_other_competition(client_for, competition, other_competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    TwoFactorPolicy.objects.create(competition=competition, grace_days=0)
    here, there = client_for(competition), client_for(other_competition)
    here.force_login(coordinator)
    there.force_login(coordinator)

    assert here.get("/coordinator/").headers["Location"] == SETUP_URL
    assert there.get("/coordinator/").status_code == 200


def test_tightening_the_policy_reaches_running_sessions(client, competition):
    """Zwolnienie zapamiętane w sesji niesie wersję polityki – zapis polityki je unieważnia."""
    from apps.staff_mfa import policy

    coordinator = CoordinatorFactory()
    client.force_login(coordinator)
    assert client.get("/coordinator/").status_code == 200

    TwoFactorPolicy.objects.create(
        competition=competition, mode=PolicyMode.CUSTOM, roles=["coordinator"], grace_days=0
    )
    policy.bump_version()

    assert client.get("/coordinator/").headers["Location"] == SETUP_URL


def test_enabling_the_second_factor_ends_the_banner(client, competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    client.force_login(coordinator)
    client.get("/coordinator/")
    client.get(SETUP_URL)
    device = twofactor.device_for(coordinator)

    client.post(SETUP_URL, {"code": twofactor.totp_code(device.plain_secret(), twofactor.current_counter())})

    response = client.get("/coordinator/")
    assert response.status_code == 200
    assert BANNER not in response.content.decode()


def test_disabling_a_required_second_factor_sends_back_to_setup(client, competition):
    """Okres przejściowy jest jednorazowy: „wyłącz i poczekaj” nie jest obejściem."""
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    enable_for(coordinator)
    client.force_login(coordinator)
    client.post("/login/2fa/", {"code": fresh_code(coordinator)})

    client.post(
        "/account/2fa/disable/", {"password": PASSWORD, "code": twofactor.generate_backup_codes()[0][0]}
    )
    assert twofactor.confirmed_device(coordinator) is not None  # zły kod – nic się nie stało

    codes = twofactor.regenerate_backup_codes(coordinator)
    client.post("/account/2fa/disable/", {"password": PASSWORD, "code": codes[0]})

    assert twofactor.confirmed_device(coordinator) is None
    assert client.get("/coordinator/").headers["Location"] == SETUP_URL


# --- uczestnik i wyłącznik -------------------------------------------------------------------------


def test_a_participant_is_never_sent_anywhere(client, competition, settings):
    settings.TWO_FACTOR_REQUIRED_ROLES = ["superkoordynator", "admin", "coordinator"]
    enable_fees(competition)
    participant = ParticipantFactory(competition=competition)
    client.force_login(participant.user)

    response = client.get("/me/")

    assert response.status_code == 200
    assert BANNER not in response.content.decode()
    assert not TwoFactorGrace.objects.filter(user=participant.user).exists()


def test_with_the_switch_off_a_participant_session_is_exactly_as_before(client, competition, settings):
    """Wyłącznik = zachowanie sprzed SEC-01: zero znaczników w sesji, zero wierszy, zero banerów."""
    settings.TWO_FACTOR_ENABLED = False
    enable_fees(competition)
    participant = ParticipantFactory(competition=competition)
    client.force_login(participant.user)

    response = client.get("/me/")

    assert response.status_code == 200
    session = client.session
    for key in (twofactor.SESSION_VERIFIED_KEY, twofactor.SESSION_EXEMPT_KEY, twofactor.SESSION_GRACE_KEY):
        assert key not in session
    assert not TwoFactorGrace.objects.exists()
    assert BANNER not in response.content.decode()
    assert client.get(SETUP_URL).status_code == 404
    assert client.get("/coordinator/security/2fa/").status_code in (403, 404)


def test_with_the_switch_off_a_coordinator_of_a_sensitive_competition_is_not_asked(
    client, competition, settings
):
    settings.TWO_FACTOR_ENABLED = False
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    enable_for(coordinator)
    client.force_login(coordinator)

    response = client.get("/coordinator/")

    assert response.status_code == 200
    assert "Bezpieczeństwo logowania" not in response.content.decode()
