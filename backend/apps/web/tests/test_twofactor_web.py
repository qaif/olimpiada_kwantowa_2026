"""Ekrany drugiego składnika i warstwa, która go wymusza.

Test warstwy WWW odpowiada na pytania, których nie da się zadać warstwie domenowej:

- czy sesja **po samym haśle** naprawdę nic nie może (bo to jest cały sens tego mechanizmu –
  reguła, która pilnuje jednego widoku i przepuszcza dwadzieścia innych, jest gorsza od braku),
- czy da się z tej poczekalni wyjść drogą inną niż podanie kodu (wylogowanie), bo bez tego
  człowiek bez telefonu zostaje z otwartą sesją na cudzym komputerze,
- czy konta **bez** drugiego składnika niczego nie zauważyły – domyślna konfiguracja nie może
  zmienić życia uczestnikowi, którego to nie dotyczy,
- czy koordynator może zdjąć zabezpieczenie z cudzego konta i czy zostaje po tym ślad.
"""

from __future__ import annotations

import pytest

from apps.accounts import twofactor
from apps.accounts.models import GROUP_COORDINATOR
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

SETUP_URL = "/account/2fa/"
CODES_URL = "/account/2fa/codes/"
DISABLE_URL = "/account/2fa/disable/"
VERIFY_URL = "/login/2fa/"


@pytest.fixture(autouse=True)
def _feature_on(settings):
    """Ten plik opisuje ekrany **działającej** funkcji, więc włącza ją jawnie.

    Wyłącznik ``TWO_FACTOR_ENABLED`` jest domyślnie wyłączony (decyzja organizatora dla tej
    instalacji). Jak serwis wygląda przy wyłączonej funkcji – czyli tak, jak wygląda naprawdę –
    sprawdza ``apps/web/tests/test_twofactor_disabled.py``.
    """
    settings.TWO_FACTOR_ENABLED = True


def code_for(user) -> str:
    device = twofactor.device_for(user)
    return twofactor.totp_code(device.plain_secret(), twofactor.current_counter())


def enable_for(user) -> list[str]:
    """Włącza drugi składnik poza interfejsem – dla testów, których przedmiotem jest coś dalej."""
    device = twofactor.begin_setup(user)
    code = twofactor.totp_code(device.plain_secret(), twofactor.current_counter())
    return twofactor.confirm_setup(user, code)


# --- konfiguracja z poziomu przeglądarki -------------------------------------------------------


def test_the_setup_page_needs_a_login(web_client):
    response = web_client.get(SETUP_URL)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


def test_the_setup_page_shows_a_qr_code_and_the_secret_to_type_in(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get(SETUP_URL).content.decode()

    device = twofactor.device_for(participant.user)
    assert device is not None and device.is_confirmed is False
    assert "<svg" in body
    # Klucz do przepisania jest obowiązkowy: część telefonów nie ma aparatu, a część czytników
    # nie radzi sobie z kodem na ekranie z filtrem prywatyzującym.
    assert device.plain_secret() in body


def test_confirming_with_a_valid_code_shows_the_backup_codes_exactly_once(web_client, participant):
    web_client.force_login(participant.user)
    web_client.get(SETUP_URL)

    response = web_client.post(SETUP_URL, {"code": code_for(participant.user)})

    assert response.status_code == 302
    assert response.headers["Location"] == CODES_URL
    body = web_client.get(CODES_URL).content.decode()
    assert body.count("<code>") == twofactor.BACKUP_CODE_COUNT
    # Drugie wejście na ten adres nie pokazuje już nic – kody są w bazie wyłącznie jako skróty.
    assert web_client.get(CODES_URL).headers["Location"] == SETUP_URL


def test_a_wrong_code_keeps_the_setup_page_open_with_an_error(web_client, participant):
    web_client.force_login(participant.user)
    web_client.get(SETUP_URL)

    response = web_client.post(SETUP_URL, {"code": "000000"})

    assert response.status_code == 400
    assert "nie pasuje" in response.content.decode()
    assert twofactor.confirmed_device(participant.user) is None


def test_after_enabling_the_session_is_not_asked_for_the_code_again(web_client, participant):
    """Człowiek właśnie przepisał kod z telefonu – żądanie kolejnego byłoby nauką, że to uciążliwe."""
    web_client.force_login(participant.user)
    web_client.get(SETUP_URL)
    web_client.post(SETUP_URL, {"code": code_for(participant.user)})

    assert web_client.get("/me/").status_code == 200


# --- poczekalnia: sesja po samym haśle ---------------------------------------------------------


def test_a_password_only_session_cannot_reach_any_panel(web_client, participant):
    enable_for(participant.user)
    web_client.force_login(participant.user)

    response = web_client.get("/me/")

    assert response.status_code == 302
    assert response.headers["Location"] == VERIFY_URL


def test_a_password_only_session_gets_403_on_the_api_not_a_redirect(web_client, participant):
    """Klient API ma dostać odpowiedź, którą rozumie – przekierowanie na HTML byłby dla niego 200."""
    enable_for(participant.user)
    web_client.force_login(participant.user)

    response = web_client.get("/api/auth/me/")

    assert response.status_code == 403
    assert "drugiego składnika" in response.json()["detail"]


@pytest.mark.parametrize("url", [VERIFY_URL, "/status/"])
def test_a_password_only_session_may_still_reach_the_waiting_room_and_the_status_page(
    web_client, participant, url
):
    enable_for(participant.user)
    web_client.force_login(participant.user)

    assert web_client.get(url).status_code == 200


def test_a_password_only_session_cannot_switch_the_second_factor_off(web_client, participant):
    """Najkrótsza droga do obejścia całego mechanizmu: znasz hasło, więc zdejmujesz zabezpieczenie.

    Warstwa wymuszająca przepuszcza wyłącznie adres **tego jednego** kroku, na który konto czeka.
    Ekran konfiguracji (a razem z nim ``/account/2fa/disable/``) nie jest tym krokiem dla konta,
    które ma już potwierdzone urządzenie.
    """
    enable_for(participant.user)
    web_client.force_login(participant.user)

    response = web_client.post(DISABLE_URL)

    assert response.status_code == 302
    assert response.headers["Location"] == VERIFY_URL
    assert twofactor.confirmed_device(participant.user) is not None


def test_a_password_only_session_cannot_reach_the_setup_screen_either(web_client, participant):
    enable_for(participant.user)
    web_client.force_login(participant.user)

    assert web_client.get(SETUP_URL).headers["Location"] == VERIFY_URL


def test_a_role_forced_to_configure_may_reach_the_setup_screen(web_client, coordinator, settings):
    """Odwrotna strona tej samej reguły: kto czeka na konfigurację, musi móc ją otworzyć."""
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]
    web_client.force_login(coordinator)

    assert web_client.get(SETUP_URL).status_code == 200
    assert web_client.get(VERIFY_URL).headers["Location"] == SETUP_URL


def test_a_password_only_session_can_always_log_out(web_client, participant):
    """Bez tej drogi ktoś bez telefonu zostawiałby otwartą sesję na cudzym komputerze."""
    enable_for(participant.user)
    web_client.force_login(participant.user)

    response = web_client.post("/logout/")

    assert response.status_code == 302
    assert web_client.get("/me/").headers["Location"].startswith("/login/")


def test_a_correct_code_opens_the_session(web_client, participant):
    enable_for(participant.user)
    web_client.force_login(participant.user)
    twofactor.TwoFactorDevice.objects.filter(user=participant.user).update(last_counter=0)

    response = web_client.post(VERIFY_URL, {"code": code_for(participant.user)})

    assert response.status_code == 302
    assert web_client.get("/me/").status_code == 200


def test_a_wrong_code_keeps_the_session_in_the_waiting_room(web_client, participant):
    enable_for(participant.user)
    web_client.force_login(participant.user)

    response = web_client.post(VERIFY_URL, {"code": "000000"})

    assert response.status_code == 400
    assert web_client.get("/me/").headers["Location"] == VERIFY_URL
    assert AuditLog.objects.filter(action="2fa.failed").exists()


def test_a_backup_code_also_opens_the_session(web_client, participant):
    codes = enable_for(participant.user)
    web_client.force_login(participant.user)

    web_client.post(VERIFY_URL, {"code": codes[0]})

    assert web_client.get("/me/").status_code == 200


def test_the_next_parameter_is_honoured_but_only_for_our_own_host(web_client, participant):
    enable_for(participant.user)
    web_client.force_login(participant.user)
    twofactor.TwoFactorDevice.objects.filter(user=participant.user).update(last_counter=0)

    response = web_client.post(
        VERIFY_URL, {"code": code_for(participant.user), "next": "https://zlosliwy.example/"}
    )

    assert response.headers["Location"] == "/me/"


# --- konta bez drugiego składnika --------------------------------------------------------------


def test_an_account_without_a_device_notices_nothing(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get("/me/").status_code == 200
    assert web_client.get(VERIFY_URL).status_code == 302


def test_a_required_role_without_a_device_is_sent_to_the_setup_page(web_client, coordinator, settings):
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]
    web_client.force_login(coordinator)

    response = web_client.get("/coordinator/")

    assert response.status_code == 302
    assert response.headers["Location"] == SETUP_URL


# --- wyłączenie i reset przez koordynatora -----------------------------------------------------


def test_the_owner_can_switch_the_second_factor_off(web_client, participant):
    codes = enable_for(participant.user)
    web_client.force_login(participant.user)
    web_client.post(VERIFY_URL, {"code": codes[0]})

    response = web_client.post(DISABLE_URL)

    assert response.status_code == 302
    assert twofactor.device_for(participant.user) is None


def test_a_coordinator_can_take_a_lost_device_off_someone_elses_account(web_client, coordinator, participant):
    enable_for(participant.user)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/accounts/{participant.user.pk}/2fa-reset/")

    assert response.status_code == 302
    assert twofactor.confirmed_device(participant.user) is None
    entry = AuditLog.objects.get(action="2fa.reset")
    assert entry.actor_id == coordinator.pk


def test_the_reset_button_is_only_shown_for_accounts_that_have_a_device(web_client, coordinator, participant):
    web_client.force_login(coordinator)
    url = f"/coordinator/accounts/{participant.user.pk}/"

    without = web_client.get(url).content.decode()
    enable_for(participant.user)
    with_device = web_client.get(url).content.decode()

    assert "Zdejmij drugi składnik" not in without
    assert "Zdejmij drugi składnik" in with_device


def test_a_participant_cannot_reset_someone_elses_second_factor(web_client, participant, reviewer):
    enable_for(reviewer.user)
    codes = enable_for(participant.user)
    web_client.force_login(participant.user)
    web_client.post(VERIFY_URL, {"code": codes[0]})

    response = web_client.post(f"/coordinator/accounts/{reviewer.user.pk}/2fa-reset/")

    assert response.status_code == 403
    assert twofactor.confirmed_device(reviewer.user) is not None
