"""Reguła 13 z § 7 docs/tasks/DJ-01.md: 5 nieudanych logowań / 15 min na parę (IP, login)."""

from unittest import mock

import pytest

from apps.pages import auth

PASSWORD = "haslo-admina-123456"
LOCKED = "Zbyt wiele nieudanych prób logowania"


def _login(client, username, password, **extra):
    return client.post("/admin/login/?next=/admin/", {"username": username, "password": password}, **extra)


def _fail(client, username, times, **extra):
    for _ in range(times):
        response = _login(client, username, "zle-haslo", **extra)
        assert response.status_code == 200  # formularz z błędem, nie przekierowanie


@pytest.mark.django_db
def test_correct_password_works_before_limit(client, superuser):
    _fail(client, superuser.username, 4)
    response = _login(client, superuser.username, PASSWORD)
    assert response.status_code == 302


@pytest.mark.django_db
def test_fifth_failure_locks_even_correct_password(client, superuser):
    _fail(client, superuser.username, 5)
    response = _login(client, superuser.username, PASSWORD)
    assert response.status_code == 200
    assert LOCKED in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_lock_is_per_ip_and_login_pair(client, superuser, django_user_model):
    _fail(client, superuser.username, 5)
    # Inny adres – ten sam login działa (atakujący nie zablokuje redaktora z internetu).
    assert _login(client, superuser.username, PASSWORD, REMOTE_ADDR="198.51.100.7").status_code == 302
    client.logout()
    # Ten sam adres – inny login działa (redakcja za wspólnym NAT-em).
    other = django_user_model.objects.create_superuser("drugi@example.com", "drugi@example.com", PASSWORD)
    assert _login(client, other.username, PASSWORD).status_code == 302


@pytest.mark.django_db
def test_login_is_case_insensitive_for_the_counter(client, superuser):
    _fail(client, superuser.username.upper(), 5)
    assert LOCKED in _login(client, superuser.username, PASSWORD).content.decode()


@pytest.mark.django_db
def test_x_real_ip_ignored_from_untrusted_peer(client, superuser):
    # Połączenie spoza TRUSTED_PROXY_IPS – nagłówek jest od klienta, nie od Caddy'ego:
    # zmiana X-Real-IP przy każdej próbie nie może obchodzić limitu.
    for i in range(5):
        _login(client, superuser.username, "zle", REMOTE_ADDR="203.0.113.9", HTTP_X_REAL_IP=f"10.0.0.{i}")
    response = _login(
        client, superuser.username, PASSWORD, REMOTE_ADDR="203.0.113.9", HTTP_X_REAL_IP="10.0.0.99"
    )
    assert LOCKED in response.content.decode()


@pytest.mark.django_db
def test_x_real_ip_honoured_from_trusted_proxy(client, superuser):
    # Za Caddym (TRUSTED_PROXY_IPS = 172.30.1.0/24 w ustawieniach testowych) wszyscy mają ten sam
    # REMOTE_ADDR – liczy się adres klienta z X-Real-IP, inaczej jeden klient blokowałby wszystkich.
    proxy = {"REMOTE_ADDR": "172.30.1.2"}
    _fail(client, superuser.username, 5, HTTP_X_REAL_IP="203.0.113.10", **proxy)
    response = _login(client, superuser.username, PASSWORD, HTTP_X_REAL_IP="203.0.113.11", **proxy)
    assert response.status_code == 302


@pytest.mark.django_db
def test_success_resets_counter(client, superuser):
    _fail(client, superuser.username, 4)
    assert _login(client, superuser.username, PASSWORD).status_code == 302
    client.logout()
    _fail(client, superuser.username, 4)
    assert _login(client, superuser.username, PASSWORD).status_code == 302


@pytest.mark.django_db
def test_lock_expires_after_window_and_is_not_extended_while_locked(client, superuser, settings):
    start = 1_000_000.0
    with mock.patch("apps.pages.auth.time.time", return_value=start):
        _fail(client, superuser.username, 5)
    # Pukanie w czasie blokady nie przedłuża jej (porażki nie są dopisywane).
    with mock.patch("apps.pages.auth.time.time", return_value=start + 600):
        _fail(client, superuser.username, 3)
        assert LOCKED in _login(client, superuser.username, PASSWORD).content.decode()
    with mock.patch(
        "apps.pages.auth.time.time", return_value=start + settings.DJCMS_LOGIN_WINDOW_SECONDS + 1
    ):
        assert _login(client, superuser.username, PASSWORD).status_code == 302


@pytest.mark.django_db
def test_cms_toolbar_login_is_throttled_too(client, superuser):
    # Druga droga logowania django CMS (``cms_login``) – blokada siedzi w backendzie, nie w widoku.
    for _ in range(5):
        client.post("/cms_login/", {"username": superuser.username, "password": "zle", "next": "/"})
    client.post("/cms_login/", {"username": superuser.username, "password": PASSWORD, "next": "/"})
    assert "_auth_user_id" not in client.session


def test_backend_refuses_before_checking_password(rf):
    request = rf.post("/admin/login/")
    with (
        mock.patch.object(auth, "is_locked", return_value=True),
        mock.patch("django.contrib.auth.backends.ModelBackend.authenticate") as parent,
    ):
        with pytest.raises(auth.PermissionDenied):
            auth.ThrottledModelBackend().authenticate(request, username="x", password="y")
    parent.assert_not_called()


def test_counter_key_does_not_contain_login_or_ip(rf):
    request = rf.get("/", REMOTE_ADDR="203.0.113.5")
    key = auth._key(request, "redaktor@example.com")
    assert "redaktor" not in key
    assert "203.0.113.5" not in key
