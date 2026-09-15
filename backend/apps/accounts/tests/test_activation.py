"""Aktywacja konta linkiem z listu: token, jednorazowość, ponowna wysyłka, drogi rejestracji.

Czego te testy pilnują, po kolei:

- **konto z rejestracji nie loguje się bez potwierdzenia adresu.** Adres e-mail jest u nas loginem
  i jedyną drogą odzyskania konta, więc przyjmowanie go na słowo znaczyło konto bez powrotu przy
  literówce i możliwość zajęcia cudzego adresu,
- **token jest jednorazowy i krótki.** Cztery godziny, bo tyle samo żyje nieaktywowane konto,
- **ponowna wysyłka nie zdradza, kto ma konto.** Odpowiedź jest identyczna dla adresu istniejącego
  i nieistniejącego – inaczej publiczny formularz byłby wyszukiwarką kont,
- **dostawca, który potwierdził adres, zastępuje nasz list.** Google podaje ``email_verified``;
  Facebook nie potwierdza adresu wcale, więc jego konto przechodzi zwykłą aktywację.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.activation import (
    ACTIVATION_MAX_AGE,
    ACTIVATION_SUBJECT,
    make_activation_token,
    mark_activated,
    resend_activation,
)
from apps.accounts.models import User
from apps.accounts.services import register_committee, register_participant, register_social_participant
from apps.core.api import DomainError
from apps.core.models import AuditLog

from .factories import DEFAULT_PASSWORD, InvitationCodeFactory

PASSWORD = "Poprawne-Haslo-2026"


def register(**overrides):
    data = {
        "email": "aktywacja@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Nowak",
        "school": "LO nr 1",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 1990,
        "phone": "600 100 200",
        "terms_consent": True,
        "gdpr_consent": True,
    }
    data.update(overrides)
    return register_participant(**data)


def social(**overrides):
    data = {
        "email": "spolecznosciowy@example.test",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school": "LO nr 1",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 1990,
        "phone": "600 100 200",
        "terms_consent": True,
        "gdpr_consent": True,
    }
    data.update(overrides)
    return register_social_participant(**data)


# --- rejestracja zostawia konto nieaktywne ------------------------------------------------------


@pytest.mark.django_db
def test_registration_creates_an_inactive_account_and_sends_the_link(
    open_registration, django_capture_on_commit_callbacks
):
    # List wychodzi **po commicie** (``transaction.on_commit``), żeby worker nie czytał stanu,
    # którego jeszcze nie ma – stąd ``django_capture_on_commit_callbacks``.
    with django_capture_on_commit_callbacks(execute=True):
        participant = register()

    user = participant.user
    assert user.is_active is False
    assert user.email_verified_at is None

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == ACTIVATION_SUBJECT
    assert message.to == ["aktywacja@example.test"]
    assert "/activate/" in message.body
    # List mówi o spamie i o oknie ważności – bez tego uczestnik nie wie, gdzie szukać i ile ma czasu.
    # Zdanie o spamie stoi na stronie po rejestracji, nie w liście (kto go czyta, ten go dostał).
    assert "spam" not in message.body
    assert "4 godziny" in message.body
    # Poza adresem odbiorcy (i tak w nagłówku ``To:``) w liście nie ma danych osobowych.
    assert "Nowak" not in message.body


@pytest.mark.django_db
def test_inactive_account_cannot_authenticate(open_registration):
    """``ModelBackend`` odrzuca konto nieaktywne – i to jest cała reguła, bez drugiej kopii w widoku."""
    from django.contrib.auth import authenticate

    participant = register()

    assert authenticate(username=participant.user.email, password=PASSWORD) is None


@pytest.mark.django_db
def test_committee_registration_also_waits_for_the_link(django_capture_on_commit_callbacks):
    """Kod zaproszenia dowodzi zaproszenia, a nie tego, że wpisany adres należy do tej osoby."""
    InvitationCodeFactory(plain_code="kod-testowy-0001")

    with django_capture_on_commit_callbacks(execute=True):
        member = register_committee(
            email="recenzent@example.test",
            password=PASSWORD,
            first_name="Jan",
            last_name="Kowalski",
            invitation_code="kod-testowy-0001",
        )

    assert member.user.is_active is False
    assert member.user.email_verified_at is None
    # Uprawnienia z kodu są nadane od razu – to osobna rzecz niż wpuszczenie do logowania.
    assert member.status == "ACTIVE"
    assert len(mail.outbox) == 1


# --- token --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_link_activates_the_account_exactly_once(open_registration, client):
    participant = register()
    token = make_activation_token(participant.user)

    first = client.get(f"/activate/{token}/")

    assert first.status_code == 200
    participant.user.refresh_from_db()
    assert participant.user.is_active is True
    assert participant.user.email_verified_at is not None

    # Drugie kliknięcie nie jest błędem serwera, ale nie jest też drugą aktywacją: strona mówi
    # „konto jest już aktywne”, a nie „zaktywowano”.
    second = client.get(f"/activate/{token}/")
    assert second.status_code == 400
    assert AuditLog.objects.filter(action="account.activated").count() == 1


@pytest.mark.django_db
def test_a_token_older_than_the_window_is_refused(open_registration):
    from apps.accounts.activation import activate_with_token

    with freeze_time(timezone.now() - timedelta(seconds=ACTIVATION_MAX_AGE + 60)):
        participant = register()
        token = make_activation_token(participant.user)

    with pytest.raises(DomainError) as exc:
        activate_with_token(token)

    assert exc.value.machine_code == "ACTIVATION_INVALID"
    participant.user.refresh_from_db()
    assert participant.user.email_verified_at is None


@pytest.mark.django_db
def test_a_token_stops_matching_after_the_address_changes(open_registration):
    """Token niesie adres, więc link z listu sprzed zmiany nie potwierdza adresu, którego już nie ma."""
    from apps.accounts.activation import activate_with_token

    participant = register()
    token = make_activation_token(participant.user)
    User.objects.filter(pk=participant.user.pk).update(email="inny@example.test")

    with pytest.raises(DomainError) as exc:
        activate_with_token(token)

    assert exc.value.machine_code == "ACTIVATION_INVALID"


@pytest.mark.django_db
def test_a_made_up_token_is_refused(db):
    from apps.accounts.activation import activate_with_token

    with pytest.raises(DomainError):
        activate_with_token("to-nie-jest-podpisany-token")


@pytest.mark.django_db
def test_activation_also_tells_allauth_that_the_address_is_verified(open_registration):
    """Zamyka dziurę z checklisty § 3.2.8: auto-connect Google nie ma już czego czyścić.

    Bez tego wiersza allauth uznawałby adres za niepotwierdzony i przy pierwszym logowaniu
    Google'em czyścił prawidłowo ustawione hasło uczestnika (``wipe_password``).
    """
    from allauth.account.models import EmailAddress

    participant = register()
    mark_activated(participant.user)

    address = EmailAddress.objects.get(user=participant.user)
    assert address.email == participant.user.email
    assert address.verified is True


@pytest.mark.django_db
def test_activation_window_is_four_hours():
    """Okno aktywacji i życie nieaktywowanego konta to **ta sama** liczba – patrz activation.py."""
    assert ACTIVATION_MAX_AGE == 4 * 3600


# --- ponowna wysyłka ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_resend_sends_only_for_an_account_that_waits(open_registration, django_capture_on_commit_callbacks):
    register()
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation("aktywacja@example.test") is True
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_resend_sends_nothing_for_an_unknown_or_already_active_account(
    open_registration, django_capture_on_commit_callbacks
):
    participant = register()
    mark_activated(participant.user)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation("nie-ma-takiego@example.test") is False
        assert resend_activation("aktywacja@example.test") is False
    assert mail.outbox == []


# --- logowanie społecznościowe ------------------------------------------------------------------


@pytest.mark.django_db
def test_provider_verified_address_means_an_active_account_without_our_link(
    open_registration, django_capture_on_commit_callbacks
):
    """Google potwierdza adres – drugie potwierdzenie tego samego adresu byłoby pytaniem o znane."""
    with django_capture_on_commit_callbacks(execute=True):
        participant = social(email_verified=True)

    assert participant.user.is_active is True
    assert participant.user.email_verified_at is not None
    assert mail.outbox == []


@pytest.mark.django_db
def test_provider_without_a_verified_address_goes_through_activation(
    open_registration, django_capture_on_commit_callbacks
):
    """Facebook nie potwierdza adresu (``VERIFIED_EMAIL: False``), więc konto czeka na nasz link."""
    with django_capture_on_commit_callbacks(execute=True):
        participant = social(email_verified=False)

    assert participant.user.is_active is False
    assert participant.user.email_verified_at is None
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == ACTIVATION_SUBJECT


# --- migracja danych ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_manual_activation_marks_both_fields_and_audits():
    """Jedna funkcja dla obu dróg aktywacji – różni je wyłącznie akcja w audycie i wykonawca."""
    from .factories import CoordinatorFactory, UserFactory

    coordinator = CoordinatorFactory()
    waiting = UserFactory(is_active=False, email_verified_at=None, password=DEFAULT_PASSWORD)

    mark_activated(waiting, actor=coordinator, action="account.activated_by_coordinator")

    waiting.refresh_from_db()
    assert waiting.is_active is True
    assert waiting.email_verified_at is not None
    entry = AuditLog.objects.get(action="account.activated_by_coordinator")
    assert entry.actor == coordinator
    assert entry.diff == {"email_verified": True}
