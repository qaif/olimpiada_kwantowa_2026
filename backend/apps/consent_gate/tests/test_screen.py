"""Ekran „Uzupełnij zgody” (CONS-01 § 3–4): co pokazuje, co zapisuje, dokąd wraca – w obu motywach.

Dowód zgody ma być tym samym dowodem, co przy rejestracji i zgodzie opiekuna online: wpis
``ConsentRecord`` z wersją, czasem, drogą i adresem IP – plus wpis audytu z językiem i skrótem treści.
"""

from __future__ import annotations

import hashlib

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.accounts.consents import (
    BY_KIND,
    PRIVACY_VERSION,
    TERMS_VERSION,
    ConsentKind,
    ConsentSource,
    organizer_name,
    plain_text,
)
from apps.accounts.guardian import GUARDIAN_SUBJECT
from apps.accounts.models import ConsentRecord
from apps.accounts.tests.factories import UserFactory
from apps.consent_gate import services
from apps.core.models import AuditLog
from conftest import make_competition

from .conftest import give, make_adult

pytestmark = pytest.mark.django_db

SCREEN = "/me/consents/complete/"


def test_screen_lists_only_the_missing_consents_with_the_registration_wording(web, adult):
    give(adult, {ConsentKind.PRIVACY})
    web.force_login(adult.user)

    html = web.get(SCREEN).content.decode()

    assert 'name="terms_consent"' in html
    assert 'name="gdpr_consent"' not in html
    assert 'name="publish_name_consent"' not in html
    # Ta sama etykieta co w rejestracji: zdanie oświadczenia z odnośnikiem do dokumentu.
    assert "Regulaminem Olimpiady Kwantowej" in html
    assert 'target="_blank" rel="noopener"' in html
    assert TERMS_VERSION in html


def test_screen_says_the_document_changed(web, adult):
    give(adult, {ConsentKind.PRIVACY})
    give(adult, {ConsentKind.TERMS}, version="stara wersja")
    web.force_login(adult.user)

    html = web.get(SCREEN).content.decode()

    assert "Organizator zmienił co najmniej jeden dokument" in html
    assert "stara wersja" in html


def test_saving_records_the_evidence_and_returns_to_next(web, adult):
    adult.publish_full_name = True
    adult.save(update_fields=["publish_full_name"])
    web.force_login(adult.user)

    response = web.post(
        SCREEN,
        {"terms_consent": "on", "gdpr_consent": "on", "next": "/me/?tab=zgody"},
        REMOTE_ADDR="203.0.113.7",
        HTTP_ACCEPT_LANGUAGE="pl",
    )

    assert response.status_code == 302
    assert response["Location"] == "/me/?tab=zgody"
    records = {record.kind: record for record in ConsentRecord.objects.filter(participant=adult)}
    assert set(records) == {ConsentKind.TERMS, ConsentKind.PRIVACY}
    assert records[ConsentKind.TERMS].document_version == TERMS_VERSION
    assert records[ConsentKind.PRIVACY].document_version == PRIVACY_VERSION
    assert {record.source for record in records.values()} == {ConsentSource.PANEL}
    assert all(record.given_at is not None for record in records.values())
    adult.refresh_from_db()
    assert adult.terms_accepted_at is not None and adult.gdpr_consent_at is not None
    # Ekran nie dotyka zgód, których nie pokazuje.
    assert adult.publish_full_name is True

    entry = AuditLog.objects.get(action="participant.consents_completed")
    assert entry.actor == adult.user
    assert entry.diff["source"] == ConsentSource.PANEL
    assert entry.diff["language"] == "pl"
    organizer = organizer_name(adult.competition)
    expected = hashlib.sha256(
        plain_text(BY_KIND[ConsentKind.TERMS], organizer=organizer).encode()
    ).hexdigest()
    assert entry.diff["consents"][ConsentKind.TERMS]["text_sha256"] == expected
    assert entry.diff["consents"][ConsentKind.TERMS]["version"] == TERMS_VERSION
    assert web.get("/me/").status_code == 200


def test_ip_address_follows_the_shared_proxy_rule(web, adult):
    """Ten sam odczyt adresu co w audycie i przy zgodzie opiekuna (``client_ip``)."""
    web.force_login(adult.user)
    web.post(SCREEN, {"terms_consent": "on", "gdpr_consent": "on"}, REMOTE_ADDR="203.0.113.7")

    record = ConsentRecord.objects.filter(participant=adult).first()
    entry = AuditLog.objects.get(action="participant.consents_completed")
    assert record.ip_address == entry.ip == "203.0.113.7"


def test_unticked_box_is_refused_and_nothing_is_written(web, adult):
    web.force_login(adult.user)

    response = web.post(SCREEN, {"terms_consent": "on"})

    assert response.status_code == 400
    assert "Zgoda na przetwarzanie danych osobowych jest wymagana." in response.content.decode()
    assert not ConsentRecord.objects.filter(participant=adult).exists()
    assert not AuditLog.objects.filter(action="participant.consents_completed").exists()


def test_second_submit_writes_nothing(adult):
    first = services.complete_consents(adult, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    second = services.complete_consents(adult, {ConsentKind.TERMS, ConsentKind.PRIVACY})

    assert len(first) == 2
    assert second == []
    assert ConsentRecord.objects.filter(participant=adult).count() == 2


def test_external_next_is_ignored(web, adult):
    web.force_login(adult.user)

    response = web.post(
        SCREEN, {"terms_consent": "on", "gdpr_consent": "on", "next": "https://evil.example/"}
    )

    assert response["Location"] == reverse("web:me")


def test_nothing_missing_redirects_away_from_the_screen(web, adult):
    give(adult)
    web.force_login(adult.user)

    assert web.get(f"{SCREEN}?next=/me/calendar/")["Location"] == "/me/calendar/"


def test_screen_is_for_participants_only(web, competition):
    assert web.get(SCREEN)["Location"].startswith("/login/")
    web.force_login(UserFactory())
    assert web.get(SCREEN).status_code == 403


def test_screen_is_never_cached(web, adult):
    web.force_login(adult.user)

    assert "no-store" in web.get(SCREEN)["Cache-Control"]


def test_screen_offers_the_gdpr_exits(web, adult):
    web.force_login(adult.user)

    html = web.get(SCREEN).content.decode()

    for name in ("web:account-export", "web:account-delete", "web:support-new", "web:logout"):
        assert reverse(name) in html, name


# --- osoba niepełnoletnia -------------------------------------------------------------------------


def test_minor_sees_the_statement_and_the_guardian_status(web, minor):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)

    html = web.get(SCREEN).content.decode()

    assert 'name="guardian_consent"' in html
    assert 'id="zgoda-opiekuna"' in html
    assert f'action="{reverse("web:guardian-request")}"' in html


def test_minor_saves_the_statement_and_keeps_the_existing_guardian_projection(web, minor):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)

    response = web.post(SCREEN, {"guardian_consent": "on", "next": "/me/"})

    assert response["Location"] == "/me/"
    minor.refresh_from_db()
    assert minor.guardian_consent is True
    assert ConsentRecord.objects.filter(
        participant=minor, kind=ConsentKind.GUARDIAN, given_by_email=""
    ).exists()


def test_blocked_minor_can_send_the_guardian_request_again(web, minor, django_capture_on_commit_callbacks):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)

    with django_capture_on_commit_callbacks(execute=True):
        response = web.post("/me/guardian/", {"guardian_email": "rodzic@example.test"}, follow=True)

    assert [item.to for item in mail.outbox if item.subject == str(GUARDIAN_SUBJECT)] == [
        ["rodzic@example.test"]
    ]
    # Powrót z prośby trafia przez bramkę z powrotem na ekran zgód – z komunikatem o wysyłce.
    assert response.redirect_chain[-1][0].startswith(SCREEN)
    assert "rodzic@example.test" in response.content.decode()


def _rest_framework_with(**rates) -> dict:
    from django.conf import settings

    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def test_guardian_request_from_the_screen_keeps_its_throttle(web, minor, django_capture_on_commit_callbacks):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)

    with override_settings(REST_FRAMEWORK=_rest_framework_with(password_reset="1/hour")):
        with django_capture_on_commit_callbacks(execute=True):
            first = web.post("/me/guardian/", {"guardian_email": "rodzic@example.test"})
            second = web.post("/me/guardian/", {"guardian_email": "rodzic@example.test"})

    assert first.status_code == 302
    assert second.status_code == 429


def test_adult_sees_no_guardian_section(web, adult):
    web.force_login(adult.user)

    assert 'id="zgoda-opiekuna"' not in web.get(SCREEN).content.decode()


# --- język i motyw --------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "zh-hans", "hi", "es", "ar", "fr", "bn", "pt", "ru", "id"])
def test_every_interface_language_has_the_screen_strings(language):
    """Dziesięć katalogów aplikacji – każdy tłumaczy tytuł, wyjaśnienie, przycisk i komunikat bramki."""
    from django.utils import translation

    with translation.override(language):
        for msgid in (
            "Uzupełnij zgody",
            "Zapisuję oświadczenia",
            "Zanim przejdziesz dalej, uzupełnij wymagane zgody.",
            "Pobierz swoje dane",
        ):
            assert translation.gettext(msgid) != msgid, (language, msgid)


def test_english_competition_shows_the_screen_in_english(client_for, settings):
    iqo = make_competition("iqo.test", "iqo-test", default_language="en", interface_languages=["en"])
    participant = make_adult(competition=iqo)
    web = client_for(iqo)
    web.force_login(participant.user)

    html = web.get(SCREEN).content.decode()

    assert "Complete your consents" in html
    assert "Uzupełnij zgody" not in html
    assert "Download your data" in html


def test_default_theme_renders_the_screen_inside_the_site_chrome(web, adult):
    web.force_login(adult.user)

    html = web.get(SCREEN).content.decode()

    assert "<header" in html
    assert 'class="consents"' in html


def test_iqo_theme_renders_the_screen_with_its_tokens_and_application_slots(client_for, monkeypatch):
    """Ekran zgód to formularz: motyw IQO daje tokeny i arkusz, sloty zostają aplikacji (jak reset hasła)."""
    from apps.themes import services as theme_services
    from apps.themes.rendering import forget_engines
    from apps.themes.runtime import forget_runtime
    from apps.themes.tests.helpers import IQO_ZIP

    iqo = make_competition("iqo.test", "iqo-test", default_language="en", interface_languages=["en"])
    participant = make_adult(competition=iqo)
    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    forget_runtime()
    forget_engines()
    version, result = theme_services.install_package(IQO_ZIP.read_bytes())
    assert result.errors == []
    theme_services.activate(iqo, version)
    web = client_for(iqo)
    web.force_login(participant.user)

    try:
        response = web.get(SCREEN)
        html = response.content.decode()
        assert response.status_code == 200
        assert 'data-theme="iqo-quantum"' in html
        assert "data-theme-slot" not in html
        assert 'name="terms_consent"' in html
        assert "Complete your consents" in html
    finally:
        forget_runtime()
        forget_engines()
