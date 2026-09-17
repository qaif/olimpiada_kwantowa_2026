"""Drugi składnik logowania: protokół, jednorazowość, kody zapasowe i szyfrowanie sekretu.

Testy warstwy domenowej – bez ani jednego żądania HTTP. Ekrany i wymuszanie ma
``apps/web/tests/test_twofactor_web.py``; tutaj pilnujemy tego, co musi być prawdą niezależnie
od tego, którędy ktoś wejdzie na konto:

- **kod z aplikacji pasuje** – czyli nasza implementacja RFC 6238 zgadza się z wektorem z normy.
  To jest jedyny test, którego porażka znaczy „nikt nie zaloguje się na żadne konto z 2FA”,
- **kod działa raz** – kod podejrzany przez ramię albo wyłowiony z logu ma być bezużyteczny,
- **kod zapasowy znika po użyciu** – bo to jest cała jego wartość,
- **sekret nie leży w bazie jawnie** i konto z niemożliwym do odszyfrowania sekretem jest
  zepsute, a nie otwarte.
"""

from __future__ import annotations

import base64
import time

import pytest
from django.utils import timezone

from apps.accounts import twofactor
from apps.accounts.models import GROUP_COORDINATOR
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _feature_on(settings):
    """Cały ten plik opisuje działającą funkcję, więc włącza ją jawnie.

    Wyłącznik ``TWO_FACTOR_ENABLED`` jest domyślnie **wyłączony** (decyzja organizatora dla tej
    instalacji), a testy protokołu i reguł domenowych mają opisywać to, co się dzieje, gdy
    organizator go włączy. Stan wyłączony ma własne testy – niżej w tym pliku i w
    ``apps/web/tests/test_twofactor_web.py``, gdzie widać go na ekranach.
    """
    settings.TWO_FACTOR_ENABLED = True


def code_now(device) -> str:
    """Bieżący kod dla urządzenia – tak, jak pokazałaby go aplikacja w telefonie."""
    return twofactor.totp_code(device.plain_secret(), twofactor.current_counter())


def enabled_device(user):
    """Konto z włączonym i potwierdzonym drugim składnikiem. Zwraca (urządzenie, kody zapasowe)."""
    device = twofactor.begin_setup(user)
    codes = twofactor.confirm_setup(user, code_now(device))
    device.refresh_from_db()
    return device, codes


# --- protokół TOTP ---------------------------------------------------------------------------


def test_the_generated_code_matches_the_rfc_6238_test_vector():
    """Wektor kontrolny z RFC 6238 (dodatek B): sekret „12345678901234567890”, SHA-1, 8 cyfr.

    Sprawdzamy sześć pierwszych cyfr naszego wyniku, bo tyle ich wystawiamy. Ten test nie ma
    prawa zależeć od żadnego kodu projektu poza samą funkcją – gdyby zaczął, przestałby być
    dowodem zgodności z normą i stałby się dowodem zgodności z nami samymi.
    """
    secret = base64.b32encode(b"12345678901234567890").decode("ascii")

    # T = 59 s → krok 1; norma podaje pełny kod 94287082.
    assert twofactor.totp_code(secret, 59 // 30) == "287082"
    # T = 1111111109 s → krok 37037036; norma podaje 07081804.
    assert twofactor.totp_code(secret, 1111111109 // 30) == "081804"


def test_the_code_is_accepted_within_the_time_window():
    """Zegar w telefonie spóźniony o pół minuty nie może odciąć nikogo od konta."""
    user = UserFactory()
    device, _ = enabled_device(user)
    device.last_counter = 0
    device.save(update_fields=["last_counter"])
    previous_step = twofactor.totp_code(device.plain_secret(), twofactor.current_counter() - 1)

    assert twofactor.verify(user, previous_step) is True


def test_a_code_from_outside_the_window_is_rejected():
    user = UserFactory()
    device, _ = enabled_device(user)
    stale = twofactor.totp_code(device.plain_secret(), twofactor.current_counter() - 10)

    assert twofactor.verify(user, stale) is False


def test_the_same_code_cannot_be_used_twice():
    """Sedno ochrony przed powtórzeniem: kod żyje 30 s, więc bez tego dałby się użyć drugi raz."""
    user = UserFactory()
    device, _ = enabled_device(user)
    device.last_counter = 0
    device.save(update_fields=["last_counter"])
    code = code_now(device)

    assert twofactor.verify(user, code) is True
    assert twofactor.verify(user, code) is False


def test_a_code_with_the_wrong_length_never_touches_the_database():
    user = UserFactory()
    device, _ = enabled_device(user)
    before = device.last_counter

    assert twofactor.verify(user, "12345") is False
    device.refresh_from_db()
    assert device.last_counter == before


# --- konfiguracja ----------------------------------------------------------------------------


def test_setup_stores_an_unconfirmed_device_that_login_ignores():
    """Sam sekret nie daje nic: dopóki nikt go nie potwierdził, logowanie o nim nie wie."""
    user = UserFactory()

    device = twofactor.begin_setup(user)

    assert device.is_confirmed is False
    assert twofactor.confirmed_device(user) is None
    assert twofactor.device_for(user) is not None


def test_opening_setup_again_does_not_destroy_a_working_device():
    """Przypadkowe wejście na ekran konfiguracji nie może odciąć człowieka od własnego konta."""
    user = UserFactory()
    device, _ = enabled_device(user)
    secret_before = device.plain_secret()

    again = twofactor.begin_setup(user)

    assert again.pk == device.pk
    assert again.plain_secret() == secret_before
    assert again.is_confirmed is True


def test_confirming_with_a_wrong_code_does_not_enable_anything():
    user = UserFactory()
    twofactor.begin_setup(user)

    with pytest.raises(DomainError):
        twofactor.confirm_setup(user, "000000")

    assert twofactor.confirmed_device(user) is None
    assert AuditLog.objects.filter(action="2fa.failed").count() == 1


def test_confirming_returns_ten_backup_codes_once():
    user = UserFactory()
    device = twofactor.begin_setup(user)

    codes = twofactor.confirm_setup(user, code_now(device))

    assert len(codes) == twofactor.BACKUP_CODE_COUNT
    assert len(set(codes)) == twofactor.BACKUP_CODE_COUNT
    device.refresh_from_db()
    assert device.backup_codes_left == twofactor.BACKUP_CODE_COUNT
    # W bazie są wyłącznie skróty – żaden z pokazanych kodów nie da się tam znaleźć.
    assert not set(codes) & set(device.backup_codes)


# --- kody zapasowe ---------------------------------------------------------------------------


def test_a_backup_code_works_and_is_consumed():
    user = UserFactory()
    device, codes = enabled_device(user)

    assert twofactor.verify(user, codes[0]) is True
    device.refresh_from_db()
    assert device.backup_codes_left == twofactor.BACKUP_CODE_COUNT - 1
    assert twofactor.verify(user, codes[0]) is False


def test_a_backup_code_is_accepted_regardless_of_dashes_and_case():
    """Kod przepisuje człowiek z kartki – myślnik i wielkość liter nie mogą o niczym decydować."""
    user = UserFactory()
    _, codes = enabled_device(user)
    messy = f" {codes[0].replace('-', '').lower()} "

    assert twofactor.verify(user, messy) is True


# --- szyfrowanie sekretu ---------------------------------------------------------------------


def test_the_secret_is_not_stored_in_plain_text():
    user = UserFactory()
    device = twofactor.begin_setup(user)

    assert device.plain_secret() not in device.secret
    assert device.plain_secret() == twofactor.decrypt_secret(device.secret)


def test_a_secret_encrypted_with_another_key_is_unreadable_not_open(settings):
    """Zmiana ``SECRET_KEY`` unieważnia drugi składnik – ma zamykać konto, a nie je otwierać."""
    user = UserFactory()
    device, _ = enabled_device(user)
    code = code_now(device)

    settings.SECRET_KEY = "zupelnie-inny-klucz-po-rotacji-sekretu-aplikacji"

    assert device.plain_secret() is None
    assert twofactor.verify(user, code) is False


# --- wyłączanie, reset i audyt ----------------------------------------------------------------


def test_disabling_removes_the_device_and_leaves_an_audit_entry():
    user = UserFactory()
    enabled_device(user)

    assert twofactor.disable(user) is True
    assert twofactor.device_for(user) is None
    assert AuditLog.objects.filter(action="2fa.disabled", target_id=str(user.pk)).exists()


def test_a_coordinator_reset_is_a_separate_audit_action():
    """``2fa.reset`` odróżnia „ktoś zgubił telefon” od „ktoś zdejmuje zabezpieczenia cudzych kont”."""
    coordinator = CoordinatorFactory()
    user = UserFactory()
    enabled_device(user)

    assert twofactor.reset_by_coordinator(user, actor=coordinator) is True

    entry = AuditLog.objects.get(action="2fa.reset")
    assert entry.actor_id == coordinator.pk
    assert entry.target_id == str(user.pk)
    assert twofactor.confirmed_device(user) is None


def test_successful_verification_is_audited_with_the_method_used():
    user = UserFactory()
    device, codes = enabled_device(user)
    device.last_counter = 0
    device.save(update_fields=["last_counter"])

    twofactor.verify(user, code_now(device))
    twofactor.verify(user, codes[0])

    methods = list(AuditLog.objects.filter(action="2fa.verified").values_list("diff", flat=True))
    assert {entry["method"] for entry in methods} == {"totp", "backup"}


# --- wymuszanie per rola -----------------------------------------------------------------------


def test_no_role_requires_two_factor_by_default(settings):
    """Domyślna konfiguracja niczego nie wymusza – inaczej wdrożenie zamknęłoby panel koordynatora."""
    settings.TWO_FACTOR_REQUIRED_ROLES = []

    assert twofactor.is_required_for(CoordinatorFactory()) is False


def test_a_listed_role_requires_two_factor(settings):
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]

    assert twofactor.is_required_for(CoordinatorFactory()) is True
    assert twofactor.is_required_for(UserFactory()) is False


# --- wyłącznik główny ---------------------------------------------------------------------------


# Że **domyślnie** funkcja jest wyłączona, sprawdza ``apps/web/tests/test_twofactor_disabled.py``
# – ten plik włącza ją fixture'em, więc nie umiałby tego udowodnić. Tam nie ma żadnego nadpisania
# ustawienia i testy przechodzą wyłącznie wtedy, gdy wartość domyślna naprawdę brzmi „wyłączone”.


def test_the_master_switch_wins_over_the_role_list(settings):
    """``TWO_FACTOR_REQUIRED_ROLES`` zostawione w ``.env`` z poprzedniej konfiguracji nie może po
    wyłączeniu funkcji odsyłać koordynatora na ekran, którego już nie ma."""
    settings.TWO_FACTOR_ENABLED = False
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]

    assert twofactor.is_required_for(CoordinatorFactory()) is False


def test_switching_the_feature_off_does_not_delete_stored_devices(settings):
    """Wyłącznik jest wyłącznikiem, a nie kasowaniem: ponowne włączenie ma przywrócić stan,
    a nie kazać całemu komitetowi konfigurować aplikacje od nowa."""
    user = UserFactory()
    device, _ = enabled_device(user)

    settings.TWO_FACTOR_ENABLED = False

    assert twofactor.TwoFactorDevice.objects.filter(pk=device.pk).exists()
    device.refresh_from_db()
    assert device.confirmed_at is not None
    assert device.backup_codes_left == twofactor.BACKUP_CODE_COUNT


# --- adres otpauth i kod QR --------------------------------------------------------------------


def test_the_provisioning_uri_carries_the_secret_and_the_issuer():
    user = UserFactory(email="komisja@example.test")
    device = twofactor.begin_setup(user)

    uri = twofactor.provisioning_uri(user, device.plain_secret())

    assert uri.startswith("otpauth://totp/")
    assert f"secret={device.plain_secret()}" in uri
    assert "issuer=" in uri
    assert f"digits={twofactor.CODE_DIGITS}" in uri


def test_the_qr_code_is_a_self_contained_svg():
    """Bez zasobu z zewnątrz i bez ``data:`` – inaczej trzeba by poszerzyć politykę CSP."""
    svg = twofactor.qr_svg("otpauth://totp/test?secret=JBSWY3DPEHPK3PXP")

    assert svg.startswith("<svg")
    assert "viewBox" in svg
    assert "http" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_the_counter_follows_unix_time_not_the_server_timezone():
    """Krok czasu liczony ze strefy serwera dawałby kody działające tylko w Polsce."""
    assert twofactor.current_counter(0) == 0
    assert twofactor.current_counter(twofactor.TIME_STEP_SECONDS) == 1
    assert abs(twofactor.current_counter() - int(time.time() // twofactor.TIME_STEP_SECONDS)) <= 1
    # Sanity: znacznik „ostatnie użycie” zapisujemy zegarem Django, a nie tym powyżej.
    user = UserFactory()
    device, _ = enabled_device(user)
    assert (timezone.now() - device.last_used_at).total_seconds() < 60
