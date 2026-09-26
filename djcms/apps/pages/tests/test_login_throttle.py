"""Reguła 13 z § 7 docs/tasks/DJ-01.md: 5 nieudanych logowań / 15 min na parę (IP, login)."""

from unittest import mock

import pytest

from apps.pages import auth

PASSWORD = "haslo-admina-123456"
LOCKED = "Zbyt wiele nieudanych prób logowania"


def _login(client, username, password, **extra):
    return client.post(
        "/djcms/admin/login/?next=/admin/", {"username": username, "password": password}, **extra
    )


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
    request = rf.post("/djcms/admin/login/")
    with (
        mock.patch.object(auth, "reserve_attempt", return_value=None),
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


# --- poprawki po przeglądzie: licznik w bazie, rezerwacja przed hasłem, sufit na login -----------


def _backend_fail(rf, username, times, remote="203.0.113.50"):
    """Porażki wprost przez backend (szybciej niż formularz, ta sama ścieżka licznika)."""
    backend = auth.ThrottledModelBackend()
    for _ in range(times):
        request = rf.post("/djcms/admin/login/", REMOTE_ADDR=remote)
        try:
            assert backend.authenticate(request, username=username, password="zle-haslo") is None
        except auth.PermissionDenied:
            pass


@pytest.mark.django_db
def test_flood_of_junk_logins_does_not_reset_victims_lock(client, superuser, rf):
    """Zalew porażek na setki wymyślonych loginów z tego samego adresu nie kasuje licznika ofiary.

    Poprzedni licznik w buforze plikowym (``MAX_ENTRIES`` = 300) wyrzucał wtedy losową trzecią
    część wpisów – w tym, z dużym prawdopodobieństwem, wpis zablokowanej pary.
    """
    _fail(client, superuser.username, 5)
    for i in range(400):
        _backend_fail(rf, f"smiec-{i}@example.com", 1, remote="127.0.0.1")
    response = _login(client, superuser.username, PASSWORD)
    assert LOCKED in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_distributed_guessing_hits_per_login_ceiling(client, superuser, rf, settings):
    """Sufit na login: porażki z wielu adresów (po 4 z każdego – pod progiem pary) też blokują."""
    settings.DJCMS_LOGIN_USER_MAX_FAILURES = 12
    for n in range(3):
        _backend_fail(rf, superuser.username, 4, remote=f"198.51.100.{n + 1}")
    # Nowy adres, dobre hasło – a jednak blokada: login wyczerpał sufit.
    response = _login(client, superuser.username, PASSWORD, REMOTE_ADDR="192.0.2.77")
    assert LOCKED in response.content.decode()
    # Inny login z tego samego adresu działa – sufit dotyczy loginu, nie adresu.
    assert not auth.is_locked(rf.post("/", REMOTE_ADDR="192.0.2.77"), "inny@example.com")


@pytest.mark.django_db
def test_success_resets_pair_but_not_per_login_ceiling(client, superuser, rf, settings):
    settings.DJCMS_LOGIN_USER_MAX_FAILURES = 8
    _backend_fail(rf, superuser.username, 4, remote="198.51.100.1")
    assert _login(client, superuser.username, PASSWORD, REMOTE_ADDR="198.51.100.1").status_code == 302
    client.logout()
    # Para wyzerowana, ale 4 porażki liczą się dalej do sufitu na login: 4 kolejne go wyczerpują.
    _backend_fail(rf, superuser.username, 4, remote="198.51.100.2")
    assert auth.is_locked(rf.post("/", REMOTE_ADDR="198.51.100.3"), superuser.username)


@pytest.mark.django_db
def test_rejected_and_successful_attempts_leave_no_rows(client, superuser, rf):
    from apps.pages.models import LoginAttempt

    _fail(client, superuser.username, 5)
    assert LoginAttempt.objects.count() == 5
    _backend_fail(rf, superuser.username, 3, remote="127.0.0.1")  # odrzucone w czasie blokady
    assert LoginAttempt.objects.count() == 5
    other = rf.post("/djcms/admin/login/", REMOTE_ADDR="192.0.2.1")
    assert auth.ThrottledModelBackend().authenticate(other, username=superuser.username, password=PASSWORD)
    assert LoginAttempt.objects.count() == 5  # udane sprawdzenie hasła nie jest porażką


@pytest.mark.django_db
def test_old_rows_are_pruned(rf):
    from apps.pages.models import LoginAttempt

    start = 2_000_000.0
    with mock.patch("apps.pages.auth.time.time", return_value=start):
        _backend_fail(rf, "ktos@example.com", 3)
    with mock.patch("apps.pages.auth.time.time", return_value=start + 2 * 3600):
        _backend_fail(rf, "ktos@example.com", 1)
    assert LoginAttempt.objects.count() == 1


def test_counter_keys_are_keyed_hashes(rf):
    request = rf.get("/", REMOTE_ADDR="203.0.113.5")
    import hashlib

    plain = hashlib.sha256(b"203.0.113.5|redaktor@example.com").hexdigest()
    assert auth._key(request, "redaktor@example.com") != plain
    assert auth._user_key("Redaktor@Example.com") == auth._user_key(" redaktor@example.com ")


@pytest.mark.django_db(transaction=True)
def test_concurrent_attempts_never_exceed_the_limit(superuser, rf):
    """Dwanaście równoległych prób z jednej pary: do sprawdzenia hasła dochodzi najwyżej pięć.

    Każdy wątek ma własne połączenie z bazą (jak procesy/wątki gunicorna). Sprawdzenie hasła
    (``ModelBackend.authenticate``) jest podmienione na wolne i zawsze nieudane – okno wyścigu
    między „sprawdź blokadę” a „zapisz porażkę” jest więc szerokie; stara wersja (odczyt licznika
    przed hasłem, zapis po) przepuszczała tu praktycznie wszystkie próby. Wątków jest tyle, żeby
    nie wyczerpać puli połączeń Postgresa współdzielonej w devie z aplikacją główną.
    """
    import threading
    import time as time_module

    from django.db import connection

    threads_count = 12
    barrier = threading.Barrier(threads_count)
    checked, errors = [], []
    lock = threading.Lock()

    def slow_parent(self, request, username=None, password=None, **kwargs):
        with lock:
            checked.append(username)
        time_module.sleep(0.05)
        return None

    def worker():
        try:
            request = rf.post("/djcms/admin/login/", REMOTE_ADDR="203.0.113.99")
            barrier.wait(timeout=10)
            try:
                auth.ThrottledModelBackend().authenticate(
                    request, username=superuser.username, password="zle"
                )
            except auth.PermissionDenied:
                pass
        except Exception as exc:  # noqa: BLE001 - błąd wątku ma wywrócić test, a nie zniknąć
            with lock:
                errors.append(repr(exc))
        finally:
            connection.close()

    with mock.patch("django.contrib.auth.backends.ModelBackend.authenticate", slow_parent):
        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert 1 <= len(checked) <= 5
    request = rf.post("/djcms/admin/login/", REMOTE_ADDR="203.0.113.99")
    assert auth.is_locked(request, superuser.username) == (len(checked) == 5)


# --- IPv6: klucz pary po sieci /64 (poprawka po przeglądzie DJ-02f) --------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("203.0.113.5", "203.0.113.5"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.5", "203.0.113.5"),
        ("unknown", "unknown"),
    ],
)
def test_throttle_address_collapses_ipv6_to_64(address, expected):
    assert auth.throttle_address(address) == expected


def test_ipv6_addresses_in_one_64_share_the_pair_key(rf):
    first = rf.get("/", REMOTE_ADDR="2001:db8:1:2::1")
    second = rf.get("/", REMOTE_ADDR="2001:db8:1:2:ffff:ffff:ffff:fffe")
    other_net = rf.get("/", REMOTE_ADDR="2001:db8:1:3::1")
    login = "redaktor@example.com"
    assert auth._key(first, login) == auth._key(second, login)
    assert auth._key(first, login) != auth._key(other_net, login)


def test_ipv4_mapped_ipv6_shares_the_key_with_ipv4(rf):
    login = "redaktor@example.com"
    mapped = rf.get("/", REMOTE_ADDR="::ffff:203.0.113.5")
    plain = rf.get("/", REMOTE_ADDR="203.0.113.5")
    assert auth._key(mapped, login) == auth._key(plain, login)


@pytest.mark.django_db
def test_rotating_ipv6_addresses_in_one_64_do_not_escape_the_lock(client, superuser):
    # Pięć porażek z pięciu różnych adresów tej samej /64 – szósta próba (inny adres, dobre hasło)
    # jest zablokowana. Przed poprawką każdy adres miał własny licznik.
    for host in range(1, 6):
        response = _login(client, superuser.username, "zle-haslo", REMOTE_ADDR=f"2001:db8:5:6::{host:x}")
        assert response.status_code == 200
    response = _login(client, superuser.username, PASSWORD, REMOTE_ADDR="2001:db8:5:6::abcd")
    assert LOCKED in response.content.decode()
    # Inna /64 – licznik osobny, dobre hasło działa.
    assert _login(client, superuser.username, PASSWORD, REMOTE_ADDR="2001:db8:5:7::1").status_code == 302
