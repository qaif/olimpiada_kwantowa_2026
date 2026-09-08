"""Reset hasła przez e-mail („Nie pamiętasz hasła?”).

Przepływ jest wspólny dla wszystkich ról – model konta jest jeden (``accounts.User``), loginem
zawsze jest adres e-mail – więc testy chodzą po koncie uczestnika i po koncie recenzenta tą samą
ścieżką i oczekują tego samego wyniku.

Regresje, których pilnują te testy:

- **enumeracja kont.** Odpowiedź na adres istniejący i nieistniejący musi być nie do odróżnienia:
  ten sam kod, ten sam adres docelowy, ta sama treść. Różni się wyłącznie to, czy poszedł list,
- **jednorazowość i termin ważności tokenu.** Ten sam link użyty drugi raz nie może dać formularza,
- **limit żądań.** Formularz wysyła list na adres podany przez nadawcę żądania, więc bez limitu
  jest wysyłaczem spamu na cudze skrzynki,
- **wyzerowanie licznika logowania.** Kto zapomniał hasła, zwykle najpierw wyczerpał limit prób
  zgadywaniem – reset, po którym nie da się zalogować, niczego nie załatwia,
- **treść listu.** W wiadomości nie ma hasła, a token nie wychodzi poza treść (temat go nie niesie).
"""

import re

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.tests.factories import (
    DEFAULT_PASSWORD,
    ActiveReviewerFactory,
    PendingReviewerFactory,
    UserFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

RESET_URL = "/password-reset/"
SENT_URL = "/password-reset/sent/"
LOGIN_URL = "/login/"

NEW_PASSWORD = "Nowe-Dlugie-Haslo-2026"  # noqa: S105 - hasło testowe

#: Adres formularza nowego hasła wyciągnięty z treści listu (``/reset/<uidb64>/<token>/``).
RESET_LINK = re.compile(r"/reset/[^/\s]+/[^/\s]+/")


def reset_link_from_outbox() -> str:
    """Ścieżka z jedynej wiadomości w skrzynce nadawczej. Sprawdza po drodze, że jest dokładnie jedna."""
    assert len(mail.outbox) == 1
    match = RESET_LINK.search(mail.outbox[0].body)
    assert match, mail.outbox[0].body
    return match.group(0)


def rest_framework_with(**rates) -> dict:
    """Kopia ``REST_FRAMEWORK`` z podmienionymi stawkami – jak w ``test_throttle.py``."""
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


# --- formularz i wysyłka -----------------------------------------------------------------------


def test_reset_form_is_public_and_linked_from_login(web_client):
    assert web_client.get(RESET_URL).status_code == 200
    # Bez linku na stronie logowania funkcja istniałaby wyłącznie dla tego, kto zna adres.
    assert RESET_URL in web_client.get(LOGIN_URL).content.decode()


def test_existing_account_gets_a_message_with_a_reset_link(web_client, participant):
    response = web_client.post(RESET_URL, {"email": participant.user.email})

    assert response.status_code == 302
    assert response["Location"] == SENT_URL
    link = reset_link_from_outbox()
    assert web_client.get(link).status_code in (200, 302)


def test_reviewer_uses_the_same_flow_as_a_participant(web_client):
    """Jeden model konta, jeden przepływ – recenzent nie ma osobnej ścieżki odzyskiwania hasła."""
    reviewer = ActiveReviewerFactory()

    response = web_client.post(RESET_URL, {"email": reviewer.user.email})

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [reviewer.user.email]


def test_pending_committee_member_can_reset_too(web_client):
    """Konto ``PENDING`` czeka na zatwierdzenie uprawnień, ale jest zwykłym, aktywnym kontem."""
    member = PendingReviewerFactory()

    web_client.post(RESET_URL, {"email": member.user.email})

    assert len(mail.outbox) == 1


# --- brak enumeracji kont ----------------------------------------------------------------------


def test_unknown_address_looks_exactly_like_a_known_one(web_client, participant):
    """Odpowiedzi muszą być nie do odróżnienia – inaczej formularz jest wyrocznią „czy to konto istnieje”."""
    known = web_client.post(RESET_URL, {"email": participant.user.email})
    mail.outbox.clear()
    unknown = web_client.post(RESET_URL, {"email": "nie-ma-takiego@example.test"})

    assert unknown.status_code == known.status_code == 302
    assert unknown["Location"] == known["Location"] == SENT_URL
    assert len(mail.outbox) == 0


def test_confirmation_page_speaks_conditionally(web_client):
    body = web_client.get(SENT_URL).content.decode()
    assert "Jeśli pod podanym adresem istnieje konto" in body


def test_inactive_account_gets_no_message(web_client, participant):
    """Domyślne zachowanie ``PasswordResetForm.get_users`` – konto wyłączone nie dostaje linku."""
    participant.user.is_active = False
    participant.user.save(update_fields=["is_active"])

    response = web_client.post(RESET_URL, {"email": participant.user.email})

    assert response.status_code == 302
    assert response["Location"] == SENT_URL
    assert len(mail.outbox) == 0


def test_address_matches_regardless_of_letter_case(web_client, participant):
    """Konta trzymamy małymi literami; adres z formularza jest normalizowany, a nie odrzucany."""
    response = web_client.post(RESET_URL, {"email": participant.user.email.upper()})

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [participant.user.email]


# --- treść wiadomości --------------------------------------------------------------------------


def test_message_carries_a_link_but_no_password_and_no_token_in_the_subject(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email})
    message = mail.outbox[0]
    link = reset_link_from_outbox()
    token = link.strip("/").split("/")[-1]

    assert message.subject == "Reset hasła – Olimpiada Kwantowa"
    # Temat listu przechodzi przez logi serwerów pocztowych po drodze – token nie ma tam czego szukać.
    assert token not in message.subject
    assert "\n" not in message.subject
    # Hasła nie znamy (w bazie jest hash), ale wiadomość nie może nieść żadnego z pól logowania.
    assert DEFAULT_PASSWORD not in message.body
    assert "24 godziny" in message.body
    assert "zignoruj" in message.body
    # Link jest absolutny – względny adres w liście jest nieklikalny.
    assert re.search(r"https?://[^/]+/reset/", message.body)


def test_message_has_a_plain_text_and_an_html_part_without_remote_resources(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email})
    message = mail.outbox[0]

    assert message.content_subtype == "plain"
    assert len(message.alternatives) == 1
    html, mimetype = message.alternatives[0][0], message.alternatives[0][1]
    assert mimetype == "text/html"
    # Żadnego zdalnego zasobu: obrazek w liście to potwierdzenie odczytu i wyciek adresu IP czytelnika.
    assert "<img" not in html
    assert "http://cdn" not in html and "https://cdn" not in html


@override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
def test_link_uses_https_when_the_request_came_through_the_proxy(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email}, HTTP_X_FORWARDED_PROTO="https", secure=True)

    assert "https://" in mail.outbox[0].body
    assert "http://testserver" not in mail.outbox[0].body


# --- ustawienie nowego hasła -------------------------------------------------------------------


def test_link_leads_to_a_form_that_actually_changes_the_password(web_client, participant):
    email = participant.user.email
    web_client.post(RESET_URL, {"email": email})
    link = reset_link_from_outbox()

    # Pierwsze wejście przekierowuje na adres z ``set-password``: token wędruje do sesji, żeby nie
    # zostawał w pasku adresu ani w nagłówku ``Referer`` przy wysyłce formularza.
    landing = web_client.get(link, follow=True)
    assert landing.status_code == 200
    assert "Ustaw nowe hasło" in landing.content.decode()

    form_url = landing.request["PATH_INFO"]
    saved = web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert saved.status_code == 302
    assert saved["Location"] == "/reset/done/"
    assert web_client.get("/reset/done/").status_code == 200

    participant.user.refresh_from_db()
    assert participant.user.check_password(NEW_PASSWORD)
    assert not participant.user.check_password(DEFAULT_PASSWORD)


def test_new_password_logs_in_and_the_old_one_does_not(web_client, participant):
    email = participant.user.email
    web_client.post(RESET_URL, {"email": email})
    form_url = web_client.get(reset_link_from_outbox(), follow=True).request["PATH_INFO"]
    web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    assert web_client.post(LOGIN_URL, {"username": email, "password": DEFAULT_PASSWORD}).status_code == 200
    assert web_client.post(LOGIN_URL, {"username": email, "password": NEW_PASSWORD}).status_code == 302


def test_token_is_single_use(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email})
    link = reset_link_from_outbox()
    form_url = web_client.get(link, follow=True).request["PATH_INFO"]
    web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    # Ten sam link w świeżej sesji (nowy klient) – token miesza do skrótu hash hasła, więc po jego
    # zmianie przestaje pasować.
    again = Client().get(link, follow=True)
    assert again.status_code == 200
    body = again.content.decode()
    assert "Link jest nieważny" in body
    assert "Ustaw nowe hasło" not in body


def test_made_up_token_shows_the_invalid_link_page(web_client, participant):
    uid = urlsafe_base64_encode(force_bytes(participant.user.pk))

    response = web_client.get(f"/reset/{uid}/aaaaaa-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/", follow=True)

    assert response.status_code == 200
    assert "Link jest nieważny" in response.content.decode()


def test_short_password_is_rejected_by_the_validators(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email})
    form_url = web_client.get(reset_link_from_outbox(), follow=True).request["PATH_INFO"]

    response = web_client.post(form_url, {"new_password1": "Krot1!", "new_password2": "Krot1!"})

    assert response.status_code == 200
    # Walidator ``MinimumLengthValidator`` z ``AUTH_PASSWORD_VALIDATORS`` (min. 10 znaków) – ten sam,
    # który pilnuje haseł przy rejestracji.
    assert "new_password2" in response.context["form"].errors
    participant.user.refresh_from_db()
    assert participant.user.check_password(DEFAULT_PASSWORD)


def test_reset_does_not_log_the_user_in(web_client, participant):
    """Dostęp do skrzynki pocztowej nie może być jednym kliknięciem zamieniany w sesję."""
    web_client.post(RESET_URL, {"email": participant.user.email})
    form_url = web_client.get(reset_link_from_outbox(), follow=True).request["PATH_INFO"]
    web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    assert web_client.get("/me/").status_code == 302


# --- audyt -------------------------------------------------------------------------------------


def test_successful_reset_is_recorded_in_the_audit_log(web_client, participant):
    web_client.post(RESET_URL, {"email": participant.user.email})
    form_url = web_client.get(reset_link_from_outbox(), follow=True).request["PATH_INFO"]
    web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    entry = AuditLog.objects.get(action="password.reset")
    assert entry.actor_id == participant.user_id
    assert entry.target_type == "accounts.user"
    assert entry.target_id == str(participant.user_id)
    # W ``diff`` audytu nie ma danych osobowych ani tokenu – audyt czytają także osoby bez prawa do nich.
    assert participant.user.email not in str(entry.diff)


def test_requesting_a_link_alone_is_not_audited_as_a_password_change(web_client, participant):
    """Sam POST formularza niczego nie zmienia – wpis powstaje dopiero przy zapisaniu hasła."""
    web_client.post(RESET_URL, {"email": participant.user.email})

    assert not AuditLog.objects.filter(action="password.reset").exists()


# --- limit żądań -------------------------------------------------------------------------------


@override_settings(REST_FRAMEWORK=rest_framework_with(password_reset="5/hour"))
def test_sixth_request_within_the_window_is_throttled(web_client, participant):
    email = participant.user.email
    for attempt in range(5):
        assert web_client.post(RESET_URL, {"email": email}).status_code == 302, attempt

    blocked = web_client.post(RESET_URL, {"email": email})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert len(mail.outbox) == 5


@override_settings(REST_FRAMEWORK=rest_framework_with(password_reset="3/hour"))
def test_changing_the_target_address_does_not_dodge_the_limit(web_client):
    """Kubełek po samym adresie IP – inaczej formularz rozsyłałby listy po dowolnych skrzynkach."""
    for attempt in range(3):
        assert web_client.post(RESET_URL, {"email": f"ktos{attempt}@example.test"}).status_code == 302

    assert web_client.post(RESET_URL, {"email": "kolejny@example.test"}).status_code == 429
    # Inny klient ma własny kubełek i nie jest karany za cudze próby.
    other = web_client.post(RESET_URL, {"email": "zinnegoip@example.test"}, REMOTE_ADDR="10.9.9.9")
    assert other.status_code == 302


@override_settings(REST_FRAMEWORK=rest_framework_with(login="10/min", password_reset=None))
def test_successful_reset_clears_the_login_lockout(web_client, participant):
    """Po dziesięciu nietrafionych hasłach i skutecznym resecie logowanie musi znowu działać."""
    email = participant.user.email
    for _ in range(10):
        web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})
    assert web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"}).status_code == 429

    web_client.post(RESET_URL, {"email": email})
    form_url = web_client.get(reset_link_from_outbox(), follow=True).request["PATH_INFO"]
    web_client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})

    assert web_client.post(LOGIN_URL, {"username": email, "password": NEW_PASSWORD}).status_code == 302


# --- konfiguracja ------------------------------------------------------------------------------


def test_reset_timeout_is_a_day():
    assert settings.PASSWORD_RESET_TIMEOUT == 24 * 3600


def test_every_role_shares_one_account_model(web_client):
    """Sanity check dla założenia całego przepływu: nie ma osobnego modelu konta per rola."""
    user = UserFactory(email="ktos@example.test")

    web_client.post(RESET_URL, {"email": user.email})

    assert len(mail.outbox) == 1
