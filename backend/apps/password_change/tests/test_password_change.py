"""Zmiana hasła w panelu konta (AUTH-01b): ekran, serwis, list, audyt, limit, sesje, 2FA, motyw.

Testy idą przez **żądania** (klient testowy pod hostem konkursu), a nie przez wołanie serwisu:
przedmiotem zadania jest ekran dostępny z panelu, a połowa zabezpieczeń (CSRF, ``never_cache``,
kolejność logowania i limitu, zachowanie sesji) żyje w warstwie widoku i warstwach pośrednich.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.contrib.auth.models import Group
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.accounts import twofactor
from apps.accounts.models import GROUP_SUPER_COORDINATOR, CompetitionRole, User
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import (
    DEFAULT_PASSWORD,
    ActiveReviewerFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.core.models import AuditLog
from apps.password_change import services
from apps.tenancy.models import RoutingMode
from apps.web import page_cache
from conftest import make_competition

pytestmark = pytest.mark.django_db

URL = "/account/password/"
SET_LINK_URL = "/account/password/set-link/"
NEW_PASSWORD = "Nowe-Haslo-Kwantowe-2026"


def change_payload(old: str = DEFAULT_PASSWORD, new: str = NEW_PASSWORD, repeat: str | None = None) -> dict:
    return {"old_password": old, "new_password1": new, "new_password2": new if repeat is None else repeat}


def _account(role: str, competition) -> User:
    """Konto danej roli w konkursie – tą samą drogą, którą rolę nadaje serwis (``grant_role``)."""
    if role == "participant":
        return ParticipantFactory(competition=competition).user
    if role == "reviewer":
        return ActiveReviewerFactory(competition=competition).user
    if role == "super_coordinator":
        user = UserFactory()
        user.groups.add(Group.objects.get_or_create(name=GROUP_SUPER_COORDINATOR)[0])
        return user
    user = UserFactory()
    grant_role(user, role, competition=competition)
    return user


ROLES = [
    "participant",
    CompetitionRole.SUPERVISOR.value,
    CompetitionRole.TEAM_LEADER.value,
    "reviewer",
    CompetitionRole.APPEALS.value,
    CompetitionRole.COORDINATOR.value,
    "super_coordinator",
]


@pytest.fixture
def web(client_for, competition):
    return client_for(competition)


@pytest.fixture
def account():
    return UserFactory(email="ola@example.test")


def _rest_framework_with(settings, **rates) -> dict:
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


# --- każda rola ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_every_role_reaches_the_screen_from_account_settings_and_changes_the_password(
    role, competition, web, django_capture_on_commit_callbacks
):
    user = _account(role, competition)
    web.force_login(user)

    # Droga z paska konta: adres e-mail jest odnośnikiem do ustawień konta …
    bar = web.get(URL).content.decode()
    assert f'class="account-bar__who who" href="{reverse("web:account-profile")}"' in bar
    # … a ustawienia konta (uczestnik: /me/profile/ przez przekierowanie) mają sekcję „Hasło”.
    settings_page = web.get(reverse("web:account-profile"), follow=True)
    assert settings_page.status_code == 200
    assert f'href="{URL}"' in settings_page.content.decode()
    settings_url = reverse("web:profile") if role == "participant" else reverse("web:account-profile")
    if role == "participant":
        # Uczestnik ma pełny formularz danych pod /me/profile/ – tam prowadzi go też /account/profile/.
        assert settings_page.redirect_chain == [(settings_url, 302)]

    screen = web.get(URL)
    assert screen.status_code == 200
    assert 'name="old_password"' in screen.content.decode()

    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(URL, change_payload())

    assert response.status_code == 302
    assert response["Location"] == settings_url
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert [message.to for message in mail.outbox] == [[user.email]]


def test_anonymous_visitor_is_sent_to_login(web):
    response = web.get(URL)

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={URL}"
    assert web.post(URL, change_payload()).status_code == 302


# --- odmowy --------------------------------------------------------------------------------------


def test_wrong_current_password_is_refused_and_audited_without_the_text(web, account):
    web.force_login(account)

    response = web.post(URL, change_payload(old="Zle-Haslo-Zgadywane"))

    assert response.status_code == 200
    assert response.context["form"].errors["old_password"] == ["Aktualne hasło jest nieprawidłowe."]
    account.refresh_from_db()
    assert account.check_password(DEFAULT_PASSWORD)
    entry = AuditLog.objects.get(action=services.AUDIT_FAILED)
    assert entry.diff == {"reason": "wrong_current", "consecutive": 1, "session_ended": False}
    assert entry.actor_id == account.pk and entry.target_id == str(account.pk)
    assert not mail.outbox


@pytest.mark.parametrize(
    ("new", "repeat", "field"),
    [
        ("Krotkie1", None, "new_password1"),  # za krótkie (min. 10)
        ("1234567890123", None, "new_password1"),  # same cyfry
        ("password123", None, "new_password1"),  # popularne
        ("ola@example.test", None, "new_password1"),  # podobne do adresu konta
        (NEW_PASSWORD, "Inne-Haslo-Kwantowe-2026", "new_password2"),  # niezgodne powtórzenie
        (DEFAULT_PASSWORD, None, "new_password1"),  # takie samo jak aktualne
    ],
)
def test_new_password_must_pass_the_validators(web, account, new, repeat, field):
    web.force_login(account)

    response = web.post(URL, change_payload(new=new, repeat=repeat))

    assert response.status_code == 200
    assert response.context["form"].errors[field]
    account.refresh_from_db()
    assert account.check_password(DEFAULT_PASSWORD)
    assert not AuditLog.objects.filter(action=services.AUDIT_CHANGED).exists()


# --- skutki zmiany -------------------------------------------------------------------------------


def test_current_session_stays_other_sessions_and_api_tokens_end(
    client_for, competition, account, django_capture_on_commit_callbacks
):
    here, elsewhere = client_for(competition), client_for(competition)
    here.force_login(account)
    elsewhere.force_login(account)
    Token.objects.create(user=account)
    session_before = here.session.session_key

    with django_capture_on_commit_callbacks(execute=True):
        response = here.post(URL, change_payload())

    assert response.status_code == 302
    assert response["Location"] == reverse("web:account-profile")
    # Bieżąca sesja trwa (z nowym kluczem – ``update_session_auth_hash`` zmienia go jak przy logowaniu).
    assert here.get(URL).status_code == 200
    assert here.session.session_key != session_before
    # Druga przeglądarka jest wylogowana: jej sesja niesie skrót starego hasła.
    assert elsewhere.get(URL)["Location"].startswith("/login/")
    assert not Token.objects.filter(user=account).exists()


def test_audit_entry_and_letter_carry_no_secret(web, account, django_capture_on_commit_callbacks):
    web.force_login(account)
    Token.objects.create(user=account)

    with django_capture_on_commit_callbacks(execute=True):
        web.post(URL, change_payload())

    entry = AuditLog.objects.get(action=services.AUDIT_CHANGED)
    assert entry.diff == {"via": "account", "api_tokens_revoked": 1}
    assert entry.actor_id == account.pk
    for row in AuditLog.objects.all():
        dumped = str(row.diff)
        assert NEW_PASSWORD not in dumped and DEFAULT_PASSWORD not in dumped and account.email not in dumped
    [letter] = mail.outbox
    assert letter.subject == "Hasło do konta zostało zmienione – Olimpiada Kwantowa"
    assert NEW_PASSWORD not in letter.body and DEFAULT_PASSWORD not in letter.body
    assert "Hasło do Twojego konta w serwisie Olimpiady Kwantowej zostało zmienione" in letter.body
    assert f"://{competition_host(web)}/password-reset/" in letter.body


def competition_host(client) -> str:
    return client.defaults["HTTP_HOST"]


def test_letter_goes_out_in_english_from_the_host_of_an_english_competition(
    client_for, settings, account, django_capture_on_commit_callbacks
):
    """IQO-podobny konkurs: własna domena, angielski domyślny – list po angielsku, link pod jego domeną."""
    iqo = make_competition("iqo.test", "iqo-test", default_language="en", interface_languages=["en"])
    web = client_for(iqo)
    web.force_login(account)

    page = web.get(URL).content.decode()
    assert "Change password" in page
    # Napisy z ``{% blocktranslate trimmed %}`` (ekran, pasek konta, sekcja ustawień) mają przekład –
    # literówka w msgid zostawiłaby tu polskie zdanie na angielskiej stronie.
    assert "Enter your current password and the new one twice." in page
    assert f'title="Account settings: {account.email}"' in page
    assert (
        "Changing your password requires your current one."
        in web.get(reverse("web:account-profile")).content.decode()
    )

    with django_capture_on_commit_callbacks(execute=True):
        web.post(URL, change_payload())

    [letter] = mail.outbox
    assert letter.subject.startswith("Account password changed – ")
    assert "was changed" in letter.body and "Forgotten your password?" in letter.body
    assert "://iqo.test/password-reset/" in letter.body


def test_path_prefix_competition_keeps_its_prefix_in_redirects_and_in_the_letter(
    client_for, competition, account, django_capture_on_commit_callbacks
):
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    make_competition("druga.invalid", "druga", routing_mode=RoutingMode.PATH, path_prefix="druga")
    web = client_for(competition)

    assert web.get(f"/druga{URL}")["Location"] == f"/druga/login/?next=/druga{URL}"

    web.force_login(account)
    assert web.get(f"/druga{URL}").status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(f"/druga{URL}", change_payload())

    assert response["Location"] == f"/druga{reverse('web:account-profile')}"
    [letter] = mail.outbox
    assert f"://{competition_host(web)}/druga/password-reset/" in letter.body


# --- limit, CSRF, cache --------------------------------------------------------------------------


def test_posts_are_throttled_per_account(web, account, settings):
    settings.REST_FRAMEWORK = _rest_framework_with(settings, password_change="2/hour")
    web.force_login(account)

    for _attempt in range(2):
        assert web.post(URL, change_payload(old="Zle-Haslo-Zgadywane")).status_code == 200
    blocked = web.post(URL, change_payload())

    assert blocked.status_code == 429
    assert int(blocked["Retry-After"]) > 0
    account.refresh_from_db()
    assert account.check_password(DEFAULT_PASSWORD)
    # Kubełek jest kontem, nie adresem: inne konto z tego samego adresu ma własny budżet.
    other = UserFactory()
    web.force_login(other)
    assert web.post(URL, change_payload(old="Zle-Haslo-Zgadywane")).status_code == 200


def test_post_without_csrf_token_is_refused(client_for, competition, account):
    strict = client_for(competition, enforce_csrf_checks=True)
    strict.force_login(account)

    assert strict.post(URL, change_payload()).status_code == 403
    account.refresh_from_db()
    assert account.check_password(DEFAULT_PASSWORD)


def test_screen_is_never_cached(web, account):
    web.force_login(account)

    response = web.get(URL)

    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]
    assert page_cache.is_cacheable_path(URL) is False
    # Także przekierowanie gościa do logowania nie może zostać w pamięci pośrednika.
    web.logout()
    assert "no-store" in web.get(URL)["Cache-Control"]


# --- konto bez hasła -------------------------------------------------------------------------------


@pytest.fixture
def social_account():
    user = UserFactory(email="google@example.test")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def test_account_without_password_gets_a_link_to_its_own_address_and_no_form(
    web, social_account, django_capture_on_commit_callbacks
):
    web.force_login(social_account)

    page = web.get(URL).content.decode()
    assert 'name="old_password"' not in page
    assert f'action="{SET_LINK_URL}"' in page
    # Formularz zmiany nie przyjmuje POST-a od konta bez hasła – wraca na ekran z wyjaśnieniem.
    assert web.post(URL, change_payload())["Location"] == URL

    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(SET_LINK_URL)

    assert response["Location"] == URL
    [letter] = mail.outbox
    assert letter.to == [social_account.email]
    link = re.search(r"https?://\S+/reset/\S+/", letter.body).group(0)
    assert AuditLog.objects.filter(action=services.AUDIT_SET_LINK_SENT, actor=social_account).exists()

    # Link prowadzi do zwykłego ekranu nowego hasła z resetu – i ten ustawia pierwsze hasło.
    path = link.split(competition_host(web), 1)[1]
    form_page = web.get(path, follow=True)
    assert form_page.context["validlink"] is True
    web.post(form_page.redirect_chain[-1][0], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    social_account.refresh_from_db()
    assert social_account.check_password(NEW_PASSWORD)


def test_set_link_is_refused_for_an_account_with_a_password(web, account, django_capture_on_commit_callbacks):
    web.force_login(account)

    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(SET_LINK_URL, follow=True)

    assert "To konto ma już hasło" in response.content.decode()
    assert not mail.outbox
    assert web.get(SET_LINK_URL).status_code == 405


# --- drugi składnik ------------------------------------------------------------------------------


def _with_confirmed_device(user):
    device = twofactor.begin_setup(user)
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed_at"])
    return device


def test_two_factor_session_waiting_for_the_code_does_not_reach_the_screen(web, account, settings):
    settings.TWO_FACTOR_ENABLED = True
    _with_confirmed_device(account)
    web.force_login(account)

    assert web.get(URL)["Location"] == reverse("web:twofactor-verify")
    assert web.post(URL, change_payload())["Location"] == reverse("web:twofactor-verify")
    account.refresh_from_db()
    assert account.check_password(DEFAULT_PASSWORD)


def test_two_factor_verification_survives_the_change(
    web, account, settings, django_capture_on_commit_callbacks
):
    settings.TWO_FACTOR_ENABLED = True
    _with_confirmed_device(account)
    web.force_login(account)
    session = web.session
    session[twofactor.SESSION_VERIFIED_KEY] = account.pk
    session.save()

    with django_capture_on_commit_callbacks(execute=True):
        assert web.post(URL, change_payload()).status_code == 302

    assert web.session[twofactor.SESSION_VERIFIED_KEY] == account.pk
    assert web.get(URL).status_code == 200
    assert twofactor.confirmed_device(account) is not None


# --- motyw ---------------------------------------------------------------------------------------


def test_iqo_theme_111_package_links_the_account_settings(client_for, competition, monkeypatch, account):
    """Prawdziwa paczka ``iqo-quantum-1.1.1.zip`` (zbudowana z ``themes/iqo-quantum``) – przegląd L6.

    Wymaga aplikacji 0.45.0 (wydanie z fragmentem ``web/_account_who.html``); na starszej wersji
    walidator ją odrzuca, zamiast dopuścić ``include`` szablonu, którego aplikacja nie ma.
    """
    from apps.themes import services as theme_services
    from apps.themes.package import validate_package
    from apps.themes.rendering import forget_engines
    from apps.themes.runtime import forget_runtime
    from apps.themes.tests.helpers import FIXTURES

    package = (FIXTURES / "iqo-quantum-1.1.1.zip").read_bytes()
    assert validate_package(package, app_version="v0.44.0").errors
    monkeypatch.setenv("APP_VERSION", "v0.45.0")
    version, result = theme_services.install_package(package)
    assert result.errors == []
    assert result.manifest["version"] == "1.1.1" and result.manifest["min_app_version"] == "0.45.0"
    theme_services.activate(competition, version)
    forget_runtime()
    forget_engines()
    web = client_for(competition)
    web.force_login(account)

    html = web.get("/").content.decode()

    assert 'class="iqo-header"' in html
    assert f'class="account-bar__who who iqo-acct__who" href="{reverse("web:account-profile")}"' in html


# --- przegląd: H1 – inne drogi do hasła i adresu ---------------------------------------------------


def test_wagtail_account_page_has_no_password_or_email_panel(client_for, competition):
    coordinator = _account(CompetitionRole.COORDINATOR.value, competition)
    web = client_for(competition)
    web.force_login(coordinator)

    page = web.get("/cms/account/")

    assert page.status_code == 200
    body = page.content.decode()
    assert 'name="password-old_password"' not in body
    assert 'name="name_email-email"' not in body


@pytest.mark.parametrize("path", ["/admin/password_change/", "/admin/password_change/done/"])
def test_django_admin_password_change_redirects_to_the_account_screen(client_for, competition, path):
    web = client_for(competition)
    web.force_login(UserFactory(is_staff=True, is_superuser=True))

    assert web.get(path)["Location"] == URL
    response = web.post(path, change_payload())
    assert response.status_code == 302 and response["Location"] == URL


EMAIL_URL = "/account/email/"


def test_email_change_requires_the_current_password(web, account, django_capture_on_commit_callbacks):
    web.force_login(account)

    with django_capture_on_commit_callbacks(execute=True):
        missing = web.post(EMAIL_URL, {"new_email": "napastnik@example.test"})
        wrong = web.post(EMAIL_URL, {"new_email": "napastnik@example.test", "current_password": "Zle-Haslo"})

    for response in (missing, wrong):
        assert response.status_code == 200
        assert response.context["form"].errors["current_password"] == ["Aktualne hasło jest nieprawidłowe."]
    assert not mail.outbox
    failures = AuditLog.objects.filter(action="account.email_change_failed").order_by("pk")
    assert [row.diff["consecutive"] for row in failures] == [1, 2]

    with django_capture_on_commit_callbacks(execute=True):
        accepted = web.post(
            EMAIL_URL, {"new_email": "nowy-ola@example.test", "current_password": DEFAULT_PASSWORD}
        )
    assert accepted.status_code == 302
    assert [message.to for message in mail.outbox] == [["nowy-ola@example.test"]]


def test_email_change_of_an_account_without_password_is_refused(web, django_capture_on_commit_callbacks):
    user = UserFactory()
    user.set_unusable_password()
    user.save(update_fields=["password"])
    web.force_login(user)

    assert 'action="/account/email/"' not in web.get(reverse("web:account-profile")).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(EMAIL_URL, {"new_email": "napastnik@example.test"})

    assert response.status_code == 200
    assert "nie ma jeszcze hasła" in response.content.decode()
    assert not mail.outbox


# --- przegląd: M1 – awaria brokera ----------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_broker_outage_neither_fails_the_change_nor_logs_the_user_out(client_for, monkeypatch, caplog):
    """``on_commit`` biegnie tu naprawdę (test transakcyjny) – kolejka niedostępna, zmiana i sesja zostają."""
    from apps.core import tasks

    competition = make_competition("awaria.test", "awaria-test")
    user = UserFactory()

    def broken_delay(*args, **kwargs):
        raise ConnectionError("broker niedostępny")

    monkeypatch.setattr(tasks.send_mail_task, "delay", broken_delay)
    web = client_for(competition)
    web.force_login(user)

    response = web.post(URL, change_payload())

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert web.get(URL).status_code == 200
    assert f"dla konta {user.pk}" in caplog.text


# --- przegląd: L1 – podniesienie skrótu hasła ------------------------------------------------------


class FastPBKDF2(PBKDF2PasswordHasher):
    """Szybki PBKDF2 – test podniesienia skrótu nie ma czekać sekundy na każde liczenie."""

    iterations = 1000


def test_hash_upgrade_during_a_refused_change_keeps_the_session(web, account, settings):
    settings.PASSWORD_HASHERS = [
        "apps.password_change.tests.test_password_change.FastPBKDF2",
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    assert account.password.startswith("md5$")
    web.force_login(account)

    refused = web.post(URL, change_payload(new="krotkie"))

    assert refused.status_code == 200 and refused.context["form"].errors["new_password1"]
    account.refresh_from_db()
    assert account.password.startswith("pbkdf2_sha256$")
    # Skrót sesji przepisany razem z hashem – bez tego następne żądanie byłoby już wylogowane.
    assert web.get(URL).status_code == 200


# --- przegląd: L3 – seria pomyłek, komunikat limitu ------------------------------------------------


def test_five_wrong_passwords_in_a_row_end_the_session_across_screens(web, account):
    web.force_login(account)

    for _attempt in range(2):
        web.post(EMAIL_URL, {"new_email": "x@example.test", "current_password": "Zle-Haslo"})
    for _attempt in range(2):
        assert web.post(URL, change_payload(old="Zle-Haslo")).status_code == 200
    locked = web.post(URL, change_payload(old="Zle-Haslo"))

    assert locked.status_code == 302
    assert locked["Location"] == f"/login/?next={URL}"
    assert web.get(URL)["Location"].startswith("/login/")
    last = AuditLog.objects.filter(action=services.AUDIT_FAILED).order_by("pk").last()
    assert last.diff == {"reason": "wrong_current", "consecutive": 5, "session_ended": True}


def test_a_correct_password_resets_the_series(web, account, django_capture_on_commit_callbacks):
    web.force_login(account)
    for _attempt in range(4):
        web.post(URL, change_payload(old="Zle-Haslo"))

    with django_capture_on_commit_callbacks(execute=True):
        assert web.post(URL, change_payload()).status_code == 302
    assert "reauth_failures" not in web.session


def test_throttle_message_names_the_account_not_the_address(web, account, settings):
    settings.REST_FRAMEWORK = _rest_framework_with(settings, password_change="1/hour")
    web.force_login(account)
    web.post(URL, change_payload(old="Zle-Haslo"))

    blocked = web.post(URL, change_payload(old="Zle-Haslo"))

    assert blocked.status_code == 429
    assert "Zbyt wiele prób na tym koncie." in blocked.content.decode()
    assert "z tego adresu" not in blocked.content.decode()


# --- przegląd: L2 – sesje django CMS --------------------------------------------------------------


def test_djcms_editor_is_told_that_editor_sessions_are_not_ended(
    client_for, competition, settings, django_capture_on_commit_callbacks
):
    settings.DJCMS_SSO_KEY = "k" * 40
    coordinator = _account(CompetitionRole.COORDINATOR.value, competition)
    web = client_for(competition)
    web.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        page = web.post(URL, change_payload(), follow=True).content.decode()

    assert "sesja edytora django CMS" in page
    [letter] = mail.outbox
    assert "sesja edytora django CMS" in letter.body


def test_non_editor_letter_has_no_djcms_sentence(web, account, settings, django_capture_on_commit_callbacks):
    settings.DJCMS_SSO_KEY = "k" * 40
    web.force_login(account)

    with django_capture_on_commit_callbacks(execute=True):
        web.post(URL, change_payload())

    [letter] = mail.outbox
    assert "django CMS" not in letter.body


# --- przegląd: L7 – brakujące ścieżki ---------------------------------------------------------------


def test_set_link_is_throttled_per_account(web, settings, django_capture_on_commit_callbacks):
    settings.REST_FRAMEWORK = _rest_framework_with(settings, password_reset="1/hour")
    user = UserFactory()
    user.set_unusable_password()
    user.save(update_fields=["password"])
    web.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        assert web.post(SET_LINK_URL).status_code == 302
        blocked = web.post(SET_LINK_URL)

    assert blocked.status_code == 429
    assert len(mail.outbox) == 1


def test_set_link_is_refused_for_an_account_with_an_unconfirmed_address(
    web, django_capture_on_commit_callbacks
):
    user = UserFactory(email_verified_at=None)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    web.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        page = web.post(SET_LINK_URL, follow=True).content.decode()

    assert "Na to konto nie można teraz wysłać linku" in page
    assert not mail.outbox
    assert not AuditLog.objects.filter(action=services.AUDIT_SET_LINK_SENT).exists()


# --- przegląd: L8 – strefa czasowa w liście -------------------------------------------------------


def test_letter_names_the_competition_timezone_unambiguously(
    client_for, account, django_capture_on_commit_callbacks
):
    tokyo = make_competition("tokio.test", "tokio-test", time_zone="Asia/Tokyo")
    web = client_for(tokyo)
    web.force_login(account)

    with django_capture_on_commit_callbacks(execute=True):
        web.post(URL, change_payload())

    [letter] = mail.outbox
    assert "(Asia/Tokyo, UTC+09:00)" in letter.body
    assert "CEST" not in letter.body and "CET" not in letter.body
