"""Drugi składnik logowania a API: token nie może być drogą obok kodu z aplikacji.

``TwoFactorMiddleware`` pilnuje sesji, a żądanie z nagłówkiem ``Authorization: Token …`` dochodzi
do niej jako anonimowe. Te testy opisują obie zapory z ``apps.accounts.authentication`` i krok
drugiego składnika w ``POST /api/auth/login/``.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts import twofactor
from apps.accounts.models import GROUP_COORDINATOR
from apps.accounts.tests.factories import DEFAULT_PASSWORD, CoordinatorFactory, ParticipantFactory
from apps.accounts.tests.test_twofactor import enabled_device

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/auth/login/"
ME_URL = "/api/auth/me/"


@pytest.fixture(autouse=True)
def _feature_on(settings):
    settings.TWO_FACTOR_ENABLED = True
    settings.TWO_FACTOR_REQUIRED_ROLES = []


def login(client, user, **extra):
    return client.post(LOGIN_URL, {"email": user.email, "password": DEFAULT_PASSWORD, **extra}, format="json")


def with_token(key: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {key}")
    return client


def test_password_alone_gives_no_token_to_an_account_with_a_second_factor():
    user = ParticipantFactory().user
    enabled_device(user)
    client = APIClient()

    response = login(client, user)

    assert response.status_code == 400
    assert response.json()["code"] == "TWO_FACTOR_REQUIRED"
    assert not Token.objects.filter(user=user).exists()
    assert "_auth_user_id" not in client.session


def test_a_wrong_code_gives_no_token_and_no_session():
    user = ParticipantFactory().user
    enabled_device(user)
    client = APIClient()

    response = login(client, user, code="000000")

    assert response.status_code == 400
    assert response.json()["code"] == "TWO_FACTOR_INVALID"
    assert not Token.objects.filter(user=user).exists()
    assert "_auth_user_id" not in client.session


def test_a_valid_code_gives_a_working_token_and_a_verified_session():
    user = ParticipantFactory().user
    _device, backup_codes = enabled_device(user)
    client = APIClient()

    response = login(client, user, code=backup_codes[0])

    assert response.status_code == 200, response.content
    assert with_token(response.json()["token"]).get(ME_URL).status_code == 200
    # Sesja z tego logowania przeszła drugi składnik – warstwa wymuszająca jej nie zatrzymuje.
    assert client.get(ME_URL).status_code == 200


def test_enabling_the_second_factor_revokes_tokens_issued_before_it():
    user = ParticipantFactory().user
    old_key = login(APIClient(), user).json()["token"]
    assert with_token(old_key).get(ME_URL).status_code == 200

    enabled_device(user)

    assert not Token.objects.filter(user=user).exists()
    assert with_token(old_key).get(ME_URL).status_code == 401


def test_a_token_older_than_the_device_is_refused_even_if_it_exists():
    """Druga zapora: token, który powstał poza logowaniem z kodem (panel admina, powłoka)."""
    user = ParticipantFactory().user
    device, _codes = enabled_device(user)
    token = Token.objects.create(user=user)
    Token.objects.filter(pk=token.pk).update(created=device.confirmed_at - timedelta(minutes=1))

    assert with_token(token.key).get(ME_URL).status_code == 401


def test_logging_in_with_a_code_replaces_the_previous_token():
    user = ParticipantFactory().user
    _device, backup_codes = enabled_device(user)
    first = login(APIClient(), user, code=backup_codes[0]).json()["token"]

    second = login(APIClient(), user, code=backup_codes[1]).json()["token"]

    assert first != second
    assert with_token(first).get(ME_URL).status_code == 401
    assert with_token(second).get(ME_URL).status_code == 200


def test_a_role_that_must_have_a_second_factor_gets_no_token_without_one(settings):
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]
    coordinator = CoordinatorFactory()
    assert Group.objects.filter(name=GROUP_COORDINATOR, user=coordinator).exists()
    stale = Token.objects.create(user=coordinator)

    response = login(APIClient(), coordinator)

    assert response.status_code == 403
    assert response.json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"
    assert with_token(stale.key).get(ME_URL).status_code == 401


def test_an_account_without_a_second_factor_logs_in_as_before():
    user = ParticipantFactory().user

    response = login(APIClient(), user)

    assert response.status_code == 200
    assert with_token(response.json()["token"]).get(ME_URL).status_code == 200


def test_with_the_feature_off_a_stored_device_changes_nothing(settings):
    user = ParticipantFactory().user
    enabled_device(user)
    settings.TWO_FACTOR_ENABLED = False

    response = login(APIClient(), user)

    assert response.status_code == 200
    assert with_token(response.json()["token"]).get(ME_URL).status_code == 200
    assert twofactor.confirmed_device(user) is not None
