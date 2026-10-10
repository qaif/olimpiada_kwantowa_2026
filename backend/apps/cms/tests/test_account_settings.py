"""``/cms/account/`` nie zmienia adresu e-mail ani hasła (audyt 10.10.2026, W5).

Formularze konta Wagtaila zapisują nowy adres od razu – bez potwierdzenia na nowej skrzynce, listu
na starą, audytu i sprzątania ``allauth.EmailAddress`` – a zmiana hasła nie ma limitu prób. Obie
czynności mają w serwisie własne drogi (``/account/email/``, „Nie pamiętasz hasła?”), więc w panelu
redakcyjnym są wyłączone ustawieniami ``WAGTAIL_EMAIL_MANAGEMENT_ENABLED`` i
``WAGTAIL_PASSWORD_MANAGEMENT_ENABLED``.

Test zapisu sprawdza **oba** skutki jednego POST-a: imię się zmienia (czyli formularz naprawdę
przeszedł walidację i zapis), a adres – nie. Sama asercja „adres się nie zmienił” przechodziłaby
także wtedy, gdyby formularz odpadł na walidacji z zupełnie innego powodu.
"""

import pytest
from django.conf import settings

from apps.accounts.tests.factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db

ACCOUNT_URL = "/cms/account/"


def test_the_settings_switch_both_forms_off():
    assert settings.WAGTAIL_EMAIL_MANAGEMENT_ENABLED is False
    assert settings.WAGTAIL_PASSWORD_MANAGEMENT_ENABLED is False


def test_the_account_page_has_no_email_or_password_field(web_client, coordinator):
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    response = web_client.get(ACCOUNT_URL)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="name_email-email"' not in body
    assert 'name="password-old_password"' not in body
    assert 'name="password-new_password1"' not in body


def test_posting_an_email_does_not_change_the_account(web_client, coordinator):
    old_email = coordinator.email
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)
    panels_by_tab = web_client.get(ACCOUNT_URL).context["panels_by_tab"]
    data = {}
    # Komplet pól, które strona faktycznie wysyła (język, strefa czasowa, motyw…): bez nich POST
    # odpadłby na walidacji innego panelu, a test sprawdzałby wtedy co innego niż zamierza.
    for panels in panels_by_tab.values():
        for panel in panels:
            panel_form = getattr(panel, "get_form", lambda: None)()
            if panel_form is None:
                continue
            for name, field in panel_form.fields.items():
                value = panel_form.initial.get(name, field.initial)
                if not isinstance(value, (str, int, bool)):
                    continue
                if isinstance(value, bool):
                    if value:
                        data[panel_form.add_prefix(name)] = "on"
                    continue
                data[panel_form.add_prefix(name)] = value
    data["name_email-first_name"] = "Zmienione"
    data["name_email-last_name"] = coordinator.last_name or "Nazwisko"
    data["name_email-email"] = "napastnik@example.test"

    response = web_client.post(ACCOUNT_URL, data)

    assert response.status_code == 302, response.content.decode()[:2000]
    coordinator.refresh_from_db()
    assert coordinator.first_name == "Zmienione"
    assert coordinator.email == old_email
