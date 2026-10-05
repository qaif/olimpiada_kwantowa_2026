"""Ostrzeżenie o nadawcy listów konkursu dla koordynatora (MAIL-01 § 4.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.mail_domains.models import SenderDomain
from apps.mail_domains.services import email_domain, sender_warnings

pytestmark = pytest.mark.django_db


@pytest.fixture
def relay(settings):
    settings.DEFAULT_FROM_EMAIL = "Olimpiada <noreply@platforma.test>"
    settings.MAIL_ALLOWED_SENDER_DOMAINS = ["platforma.test", "iqo.test"]
    return settings


def competition_with(sender: str):
    return SimpleNamespace(from_email=sender)


def mark(domain: str, *, verified: bool, failed=("dkim",)):
    when = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    checks = [
        {"name": name, "status": "fail" if name in failed else "ok"} for name in ("spf", "dkim", "dmarc")
    ]
    return SenderDomain.objects.create(
        domain=domain,
        verified=verified,
        checked_at=when,
        verified_at=when if verified else None,
        report={"checks": checks},
    )


def test_email_domain_handles_display_names_and_garbage():
    assert email_domain("IQO <NoReply@IQO.test>") == "iqo.test"
    assert email_domain("") == "" and email_domain("bez-malpy") == ""


@pytest.mark.parametrize("sender", ["", "   ", "noreply@platforma.test", "Inny <biuro@PLATFORMA.test>"])
def test_no_warning_for_installation_sender(relay, sender, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert sender_warnings(competition_with(sender)) == []


def test_sender_outside_the_relay_list_says_mail_goes_from_installation(relay):
    [warning] = sender_warnings(competition_with("noreply@obca.test"))
    assert "nie jest obsługiwany" in warning and "noreply@platforma.test" in warning


def test_allowed_but_never_checked_domain(relay):
    [warning] = sender_warnings(competition_with("noreply@iqo.test"))
    assert "nie zostały jeszcze sprawdzone" in warning and "§ 49" in warning


def test_failed_check_names_what_failed(relay):
    mark("iqo.test", verified=False, failed=("spf", "dmarc"))
    [warning] = sender_warnings(competition_with("noreply@iqo.test"))
    assert "nie przeszło: SPF, DMARC" in warning
    assert "05.10.2026" in warning


def test_verified_domain_has_no_warning(relay):
    mark("iqo.test", verified=True, failed=())
    assert sender_warnings(competition_with("noreply@iqo.test")) == []


def test_external_provider_variant_is_not_second_guessed(relay):
    relay.MAIL_ALLOWED_SENDER_DOMAINS = None
    assert sender_warnings(competition_with("noreply@obca.test")) == []


# --- ekrany koordynatora ------------------------------------------------------------------------


@pytest.fixture
def coordinator_client(client_for, competition, relay):
    from apps.tenancy.tests.factories import grant_membership

    competition.feature_flags = {**(competition.feature_flags or {}), "competition_settings_page": True}
    competition.from_email = "noreply@iqo.test"
    competition.save(update_fields=["feature_flags", "from_email"])
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.mark.parametrize("url_name", ["web:coordinator", "web:coordinator-competition"])
def test_coordinator_screens_show_the_warning_until_the_domain_is_verified(coordinator_client, url_name):
    page = coordinator_client.get(reverse(url_name)).content.decode()
    assert "Nadawca listów konkursu" in page and "iqo.test" in page

    mark("iqo.test", verified=True, failed=())
    page = coordinator_client.get(reverse(url_name)).content.decode()
    assert "Nadawca listów konkursu" not in page


def test_settings_page_warns_about_the_saved_sender_not_the_rejected_input(coordinator_client, competition):
    mark("iqo.test", verified=True, failed=())
    # Niepoprawny formularz (pusta nazwa) z nowym nadawcą w obcej domenie: ostrzeżenie nie może
    # mówić o adresie, którego nikt nie zapisał.
    response = coordinator_client.post(
        reverse("web:coordinator-competition"), {"name": "", "from_email": "x@obca.test"}
    )
    assert response.status_code == 400
    assert "Nadawca listów konkursu" not in response.content.decode()
