"""Serwis z **wyłączonym** drugim składnikiem – czyli taki, jaki jest naprawdę.

Organizator zdecydował: „autoryzacja 2-etapowa wyłączona”. ``settings.TWO_FACTOR_ENABLED`` jest
więc domyślnie ``False``, a ten plik opisuje, co to znaczy w praktyce. **Nie ma tu ani jednego
nadpisania ustawienia** i to jest jego główna wartość: testy przechodzą wyłącznie wtedy, gdy
wartość domyślna naprawdę brzmi „wyłączone”. Gdyby ktoś kiedyś przestawił domyślną wartość
w ``config/settings/base.py``, ten plik zapali się pierwszy – zamiast wypuścić na produkcję
funkcję, której organizator nie zamawiał.

Sprawdzamy trzy rzeczy, bo na trzy sposoby można wyłączyć funkcję tylko pozornie:

1. **nie widać** – ani jednego odnośnika na ekranach, z których korzysta człowiek,
2. **nie da się dojść** – adresy odpowiadają 404, także ten, który zdejmuje zabezpieczenie
   z cudzego konta. Samo ukrycie przycisku zostawiłoby działający endpoint,
3. **nie przeszkadza** – konto, które ma w bazie potwierdzone urządzenie z czasów, gdy funkcja
   działała, loguje się samym hasłem i wchodzi na swój panel bez żadnego kroku pośredniego.

Osobno: wyłącznik **nie kasuje** zapisanych urządzeń (ostatni test). To jest różnica między
wyłączeniem a usunięciem funkcji – ponowne włączenie ma przywrócić stan, a nie kazać całemu
komitetowi konfigurować aplikacje od nowa.

Ekrany działającej funkcji opisuje ``apps/web/tests/test_twofactor_web.py`` (włącza ją fixture'em).
"""

from __future__ import annotations

import pytest

from apps.accounts import twofactor
from apps.accounts.models import GROUP_COORDINATOR

pytestmark = pytest.mark.django_db

SETUP_URL = "/account/2fa/"
CODES_URL = "/account/2fa/codes/"
DISABLE_URL = "/account/2fa/disable/"
VERIFY_URL = "/login/2fa/"


def stored_device(user):
    """Konto z potwierdzonym urządzeniem zapisanym w bazie – ślad po czasach, gdy 2FA działało.

    Zakładamy je **bez** interfejsu i bez wyłącznika, wprost przez warstwę domenową: interesuje
    nas wiersz w bazie, a nie droga, którą powstał. Potwierdzenie ustawiamy ręcznie, bo
    ``confirm_setup`` sprawdzałoby kod z aplikacji, a to jest przedmiot innego pliku.
    """
    from django.utils import timezone

    device = twofactor.TwoFactorDevice.objects.create(
        user=user,
        secret=twofactor.encrypt_secret(twofactor.generate_secret()),
        confirmed_at=timezone.now(),
        backup_codes=twofactor.generate_backup_codes()[1],
    )
    return device


# --- wyłącznik jest domyślnie wyłączony ---------------------------------------------------------


def test_the_feature_is_off_out_of_the_box():
    """Decyzja organizatora zapisana w kodzie: domyślna wartość ustawienia."""
    assert twofactor.is_enabled() is False


def test_no_role_is_forced_into_two_factor(settings):
    """Nawet lista ról zostawiona w ``.env`` z poprzedniej konfiguracji niczego nie wymusza."""
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_COORDINATOR]
    from apps.accounts.tests.factories import CoordinatorFactory

    assert twofactor.is_required_for(CoordinatorFactory()) is False


# --- nie widać -----------------------------------------------------------------------------------


def test_the_profile_page_has_no_link_to_the_second_factor(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get("/me/profile/").content.decode()

    assert "Logowanie dwuskładnikowe" not in body
    assert SETUP_URL not in body


def test_the_account_page_of_a_committee_member_has_no_link_either(web_client, reviewer):
    web_client.force_login(reviewer.user)

    body = web_client.get("/account/profile/").content.decode()

    assert "Logowanie dwuskładnikowe" not in body
    assert SETUP_URL not in body


def test_the_coordinator_account_screen_has_no_two_factor_section(web_client, coordinator, participant):
    """Także dla konta, które **ma** zapisane urządzenie: ekran pokazuje stan obowiązujący,
    a nie historię konfiguracji."""
    stored_device(participant.user)
    web_client.force_login(coordinator)

    body = web_client.get(f"/coordinator/accounts/{participant.user.pk}/").content.decode()

    assert "Logowanie dwuskładnikowe" not in body
    assert "Zdejmij drugi składnik" not in body


# --- nie da się dojść ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [SETUP_URL, CODES_URL, VERIFY_URL])
def test_the_screens_answer_404(web_client, participant, url):
    web_client.force_login(participant.user)

    assert web_client.get(url).status_code == 404


@pytest.mark.parametrize("url", [SETUP_URL, DISABLE_URL, VERIFY_URL])
def test_posting_to_the_screens_answers_404_too(web_client, participant, url):
    """POST osobno od GET: to on coś zmienia, więc to on musiałby zostać zapomniany."""
    stored_device(participant.user)
    web_client.force_login(participant.user)

    assert web_client.post(url, {"code": "000000"}).status_code == 404


def test_an_anonymous_visitor_gets_404_not_a_login_redirect(web_client):
    """Wyłączona funkcja ma wyglądać jak funkcja, której nigdy nie było – a nie jak coś,
    do czego trzeba się zalogować."""
    assert web_client.get(SETUP_URL).status_code == 404
    assert web_client.get(VERIFY_URL).status_code == 404


def test_the_coordinator_reset_endpoint_answers_404(web_client, coordinator, participant):
    """Ukrycie przycisku nie wystarcza: adres zdejmujący zabezpieczenie z cudzego konta nie może
    zostać osiągalny tylko dlatego, że nie ma do niego odnośnika."""
    device = stored_device(participant.user)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/accounts/{participant.user.pk}/2fa-reset/")

    assert response.status_code == 404
    assert twofactor.TwoFactorDevice.objects.filter(pk=device.pk).exists()


# --- nie przeszkadza ------------------------------------------------------------------------------


def test_an_account_with_a_stored_device_logs_in_with_the_password_alone(web_client, participant):
    """Sedno decyzji organizatora: urządzenie w bazie zostaje, ale nikt nie jest o nie pytany."""
    stored_device(participant.user)
    web_client.force_login(participant.user)

    assert web_client.get("/me/").status_code == 200


def test_the_api_is_not_blocked_for_such_an_account(web_client, participant):
    stored_device(participant.user)
    web_client.force_login(participant.user)

    assert web_client.get("/api/auth/me/").status_code == 200


def test_the_middleware_does_not_touch_the_session(web_client, participant):
    """Przy wyłączonej funkcji warstwa wychodzi przed dotknięciem sesji – żadnego znacznika."""
    stored_device(participant.user)
    web_client.force_login(participant.user)

    web_client.get("/me/")

    assert twofactor.SESSION_VERIFIED_KEY not in web_client.session


# --- wyłączenie to nie kasowanie ------------------------------------------------------------------


def test_stored_devices_survive_the_switch(web_client, participant):
    device = stored_device(participant.user)
    web_client.force_login(participant.user)

    web_client.get("/me/")
    web_client.get("/me/profile/")

    device.refresh_from_db()
    assert device.confirmed_at is not None
    assert device.backup_codes_left == twofactor.BACKUP_CODE_COUNT
