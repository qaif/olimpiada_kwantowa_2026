"""Reset hasła w świecie wielu konkursów i kont bez hasła (AUTH-01a, audyt 4.10.2026).

``test_password_reset.py`` sprawdza przepływ pod domyślną witryną. Tutaj to, czego tamten plik
nie widzi:

- **host i język listu.** Link prowadzi pod host, z którego przyszło żądanie (domena IQO, domena
  Olimpiady Kwantowej, konkurs pod prefiksem ścieżki), list jest w języku interfejsu tego konkursu
  i wychodzi od **jego** nadawcy (``Competition.from_email``) – do AUTH-01a szedł zawsze od
  ``DEFAULT_FROM_EMAIL``,
- **cała droga pod tym hostem**: link → formularz → nowe hasło → logowanie, bez przeskoku na inny
  host i z prefiksem ścieżki zachowanym w przekierowaniu ``set-password``,
- **konta bez hasła platformy** (Google/Facebook) – do AUTH-01a list po cichu nie wychodził,
- **konta jeszcze nieuruchomione** – zaproszony uczeń (import, delegacja) dostaje zaproszenie,
  konto z samodzielnej rejestracji – link aktywacyjny; reset konta nie aktywuje,
- zablokowane i zanonimizowane konta – nic; odpowiedź zawsze ta sama,
- pamięć stron, CSRF i motyw konkursu na stronach resetu.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.test import Client
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.activation import activate_with_token, make_activation_token, resend_activation
from apps.accounts.models import User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, ParticipantFactory, UserFactory
from apps.core.tasks import mail_from
from conftest import HOST_COMPETITION, HOST_OTHER_COMPETITION, allow_test_hosts

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Nowe-Dlugie-Haslo-2026"  # noqa: S105 - hasło testowe
IQO_SENDER = "noreply@iqo.test"

#: Pełny link z listu: ``<protokół>://<host><prefiks?>/reset/<uidb64>/<token>/``.
FULL_RESET_LINK = re.compile(r"(https?)://([^/\s]+)(/[^\s]*?)?/reset/([^/\s]+)/([^/\s]+)/")


@pytest.fixture
def iqo(other_competition):
    """Konkurs anglojęzyczny pod własną domeną i z własnym nadawcą – kształt ``iqo``."""
    other_competition.default_language = "en"
    other_competition.interface_languages = ["en", "es"]
    other_competition.from_email = IQO_SENDER
    other_competition.save(update_fields=["default_language", "interface_languages", "from_email"])
    return other_competition


@pytest.fixture
def post_reset(django_capture_on_commit_callbacks):
    def _post(client, email, path="/password-reset/"):
        with django_capture_on_commit_callbacks(execute=True):
            return client.post(path, {"email": email})

    return _post


def only_message():
    assert len(mail.outbox) == 1, [m.subject for m in mail.outbox]
    return mail.outbox[0]


def link_parts(message):
    match = FULL_RESET_LINK.search(message.body)
    assert match, message.body
    protocol, host, prefix, uid, token = match.groups()
    return protocol, host, prefix or "", uid, token


def complete_reset(client, prefix, uid, token):
    """Link z listu → formularz → nowe hasło. Zwraca odpowiedź po zapisie."""
    first = client.get(f"{prefix}/reset/{uid}/{token}/")
    # Django chowa token w sesji i przekierowuje na adres bez tokenu – pod tym samym hostem
    # i prefiksem (adres względny), inaczej sesja z tokenem zostałaby po drugiej stronie.
    assert first.status_code == 302
    assert first["Location"] == f"{prefix}/reset/{uid}/set-password/"
    form = client.get(first["Location"])
    assert form.status_code == 200 and form.context["validlink"]
    return client.post(first["Location"], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})


# --- host, język i nadawca ----------------------------------------------------------------------


def test_iqo_request_gets_an_english_message_from_its_own_sender_linking_to_its_own_host(
    client_for, iqo, post_reset
):
    user = UserFactory(email="student@example.test")
    client = client_for(iqo)

    page = client.get("/password-reset/")
    assert page.status_code == 200
    assert 'lang="en"' in page.content.decode()

    response = post_reset(client, user.email)

    assert response.status_code == 302 and response["Location"] == "/password-reset/sent/"
    message = only_message()
    assert message.from_email == IQO_SENDER
    assert message.subject.startswith("Password reset – ")
    assert "Someone" in message.body and "Ktoś" not in message.body
    _, host, prefix, uid, token = link_parts(message)
    assert host == HOST_OTHER_COMPETITION and prefix == ""
    html = message.alternatives[0].content
    assert f"://{HOST_OTHER_COMPETITION}/reset/{uid}/{token}/" in html

    done = complete_reset(client, prefix, uid, token)
    assert done.status_code == 302 and done["Location"] == "/reset/done/"
    login = client.post("/login/", {"username": user.email, "password": NEW_PASSWORD})
    assert login.status_code == 302
    assert client.session.get("_auth_user_id") == str(user.pk)


def test_polish_competition_keeps_polish_message_its_host_and_its_sender(client_for, competition, post_reset):
    user = UserFactory(email="uczen@example.test")

    post_reset(client_for(competition), user.email)

    message = only_message()
    assert message.subject.startswith("Reset hasła – ")
    assert message.from_email == (mail_from(competition) or message.from_email)
    _, host, prefix, _, _ = link_parts(message)
    assert host == HOST_COMPETITION and prefix == ""


def test_the_same_account_gets_a_link_to_whichever_host_it_asked_from(
    client_for, competition, iqo, post_reset
):
    """Konto jest jedno na instalację – link ma prowadzić tam, gdzie człowiek właśnie jest."""
    user = UserFactory(email="oba@example.test")

    post_reset(client_for(iqo), user.email)
    post_reset(client_for(competition), user.email)

    hosts = [link_parts(message)[1] for message in mail.outbox]
    assert hosts == [HOST_OTHER_COMPETITION, HOST_COMPETITION]
    assert [m.from_email for m in mail.outbox][0] == IQO_SENDER


def test_path_prefix_competition_keeps_its_prefix_through_the_whole_flow(competition, settings, post_reset):
    from apps.tenancy.provisioning import create_competition_from_template
    from apps.tenancy.templates_catalog import TEMPLATE_PUSTY
    from apps.tenancy.tests.factories import enforce_memberships_everywhere

    allow_test_hosts(settings)
    enforce_memberships_everywhere()
    create_competition_from_template(
        slug="druga",
        name="Olimpiada Druga",
        domain="druga.test",
        template=TEMPLATE_PUSTY,
        path_prefix="druga",
    )
    user = UserFactory(email="prefiks@example.test")
    client = Client(HTTP_HOST=HOST_COMPETITION, SERVER_NAME=HOST_COMPETITION)

    response = post_reset(client, user.email, path="/druga/password-reset/")

    assert response["Location"] == "/druga/password-reset/sent/"
    _, host, prefix, uid, token = link_parts(only_message())
    assert host == HOST_COMPETITION and prefix == "/druga"
    done = complete_reset(client, prefix, uid, token)
    assert done["Location"] == "/druga/reset/done/"
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)


# --- token ---------------------------------------------------------------------------------------


class _IssuedTwoDaysAgo(PasswordResetTokenGenerator):
    def _now(self):
        return super()._now() - timedelta(days=2)


def test_expired_and_made_up_tokens_show_the_invalid_link_page(client_for, iqo):
    user = UserFactory(email="stary@example.test")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    client = client_for(iqo)

    for token in (_IssuedTwoDaysAgo().make_token(user), "zmyslony-token"):
        first = client.get(f"/reset/{uid}/{token}/")
        assert first.status_code == 200
        assert first.context["validlink"] is False
    user.refresh_from_db()
    assert user.check_password(DEFAULT_PASSWORD)


# --- konta bez hasła platformy ------------------------------------------------------------------


def test_social_only_account_without_a_password_gets_a_link_and_can_set_one(
    client_for, competition, post_reset
):
    """Obietnica z ``register_social_participant``: hasło ustawia się przez „Nie pamiętasz hasła?”."""
    user = UserFactory(email="google@example.test")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    client = client_for(competition)

    post_reset(client, user.email)

    _, _, prefix, uid, token = link_parts(only_message())
    complete_reset(client, prefix, uid, token)
    user.refresh_from_db()
    assert user.has_usable_password() and user.check_password(NEW_PASSWORD)


# --- konta nieuruchomione, zablokowane, zanonimizowane -------------------------------------------


def _invited_student(competition, email="zaproszony@example.test"):
    """Uczeń z importu listy klasowej: bez hasła, nieaktywny, z ``invited_at``."""
    user = UserFactory(email=email, is_active=False, email_verified_at=None)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return ParticipantFactory(user=user, competition=competition, invited_at=timezone.now())


def test_invited_student_gets_the_invitation_again_and_the_account_stays_inactive(
    client_for, competition, post_reset
):
    participant = _invited_student(competition)
    unknown = post_reset(client_for(competition), "nikogo@example.test")
    mail.outbox.clear()

    response = post_reset(client_for(competition), participant.user.email)

    assert response.status_code == unknown.status_code and response["Location"] == unknown["Location"]
    message = only_message()
    assert f"://{HOST_COMPETITION}/zaproszenie/" in message.body
    assert not FULL_RESET_LINK.search(message.body)
    participant.user.refresh_from_db()
    assert not participant.user.is_active and not participant.user.has_usable_password()


def test_delegation_student_gets_the_delegation_invitation(client_for, competition, post_reset):
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.accounts.tests.test_delegations import (
        add,
        leader_for_country,
        make_delegations_competition,
    )

    iqo = make_delegations_competition(competition)
    leader = leader_for_country(iqo, CoordinatorFactory(), "lead@example.test")
    student = add(leader, email="kid@example.test")
    mail.outbox.clear()

    post_reset(client_for(iqo), student.user.email)

    message = only_message()
    assert message.to == ["kid@example.test"]
    assert not FULL_RESET_LINK.search(message.body)
    assert f"://{HOST_COMPETITION}/zaproszenie/" in message.body


def test_self_registered_unactivated_account_gets_the_activation_link(client_for, competition, post_reset):
    user = UserFactory(email="nowy@example.test", is_active=False, email_verified_at=None)

    post_reset(client_for(competition), user.email)

    message = only_message()
    assert f"://{HOST_COMPETITION}/activate/" in message.body
    assert not FULL_RESET_LINK.search(message.body)
    user.refresh_from_db()
    assert not user.is_active


@pytest.mark.parametrize("kind", ["blocked", "anonymised"])
def test_blocked_and_anonymised_accounts_get_nothing(client_for, competition, post_reset, kind):
    if kind == "blocked":
        user = UserFactory(email="zablokowany@example.test", is_active=False)
    else:
        user = UserFactory(
            email="deleted-7@invalid.olimpiadakwantowa.pl", is_active=False, email_verified_at=None
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

    response = post_reset(client_for(competition), user.email)

    assert response.status_code == 302 and response["Location"] == "/password-reset/sent/"
    assert mail.outbox == []


def test_resend_activation_sends_the_invitation_to_an_invited_student(
    competition, django_capture_on_commit_callbacks
):
    """Formularz „Wyślij link ponownie” nie może dać zaproszonemu uczniowi linku aktywacyjnego."""
    participant = _invited_student(competition)

    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation(participant.user.email) is True

    body = only_message().body
    assert "/zaproszenie/" in body and "/activate/" not in body


def test_activation_link_does_not_start_an_invited_account(competition):
    """Aktywacja z pominięciem ekranu zaproszenia dałaby konto bez zgód – link sprzed poprawki też."""
    from apps.core.api import DomainError

    participant = _invited_student(competition)

    with pytest.raises(DomainError):
        activate_with_token(make_activation_token(participant.user))
    participant.user.refresh_from_db()
    assert not participant.user.is_active


# --- pamięć stron, CSRF, motyw -----------------------------------------------------------------------


def test_reset_pages_never_come_from_the_page_cache(client_for, competition, settings):
    settings.PAGE_CACHE_ENABLED = True
    settings.PAGE_CACHE_SECONDS = 120
    user = UserFactory(email="cache@example.test")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    client = client_for(competition)

    for path in ("/password-reset/", "/password-reset/sent/", f"/reset/{uid}/zly-token/", "/reset/done/"):
        for _ in range(2):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.get("X-Page-Cache") in (None, "BYPASS"), path


def test_reset_form_requires_the_csrf_token(client_for, competition):
    client = client_for(competition, enforce_csrf_checks=True)

    assert client.post("/password-reset/", {"email": "x@example.test"}).status_code == 403


def test_iqo_theme_styles_reset_pages_with_application_slots(client_for, iqo, monkeypatch):
    """Strony resetu to formularze: motyw daje tokeny i arkusz, ale szablony slotów są aplikacji."""
    from apps.themes import services
    from apps.themes.rendering import forget_engines
    from apps.themes.runtime import forget_runtime
    from apps.themes.tests.helpers import IQO_ZIP

    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    forget_runtime()
    forget_engines()
    version, result = services.install_package(IQO_ZIP.read_bytes())
    assert result.errors == []
    services.activate(iqo, version)
    user = UserFactory(email="motyw@example.test")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    client = client_for(iqo)

    try:
        for path in ("/password-reset/", "/password-reset/sent/", f"/reset/{uid}/zly-token/", "/reset/done/"):
            response = client.get(path)
            assert response.status_code == 200, path
            html = response.content.decode()
            assert 'data-theme="iqo-quantum"' in html, path
            assert "data-theme-slot" not in html, path
    finally:
        forget_runtime()
        forget_engines()


def test_user_model_is_still_one_account_per_address():
    """Założenie, na którym stoi „link pod host żądania”: konto nie należy do jednego konkursu."""
    assert User._meta.get_field("email").unique
