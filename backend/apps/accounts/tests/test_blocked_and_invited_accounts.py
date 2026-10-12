"""Audyt 10.10.2026, S5 i S6: konto zablokowane i konto z zaproszenia nie uruchamiają się linkiem.

S5 – blokada była samym ``is_active=False``. Konto zablokowane bez potwierdzonego adresu wyglądało
jak świeża rejestracja: formularz „wyślij link ponownie” wysyłał mu link, a kliknięcie odblokowywało
konto. Od tej zmiany blokadę niesie ``User.blocked_at`` i odmawia każda droga aktywacji.

S6 – konto z importu listy (albo z delegacji) uruchamia się **wyłącznie** przyjęciem zaproszenia,
bo tam uczeń ustawia hasło i składa zgody. Link aktywacyjny ustawiał ``is_active`` bez zgód, a potem
logowanie Google'em wpuszczało do panelu. Ponowna wysyłka dla takiego konta wysyła zaproszenie.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.activation import (
    RESENT_INVITATION,
    activate_with_token,
    is_pending_activation,
    make_activation_token,
    mark_activated,
    resend_activation,
    resend_for_user,
)
from apps.accounts.adapters import REASON_INVITATION, SocialAccountAdapter, _invited_without_consents
from apps.accounts.bulk_registration import (
    INVITE_RESEND_COOLDOWN,
    accept_invitation,
    make_invite_token,
    read_invite_token,
    resend_invitation,
)
from apps.accounts.consents import CONSENT_FIELD_NAMES
from apps.accounts.models import Participant, User, Voivodeship
from apps.accounts.profile import update_account_by_coordinator
from apps.accounts.tasks import unactivated_accounts
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.services import register_for_stage
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.api import DomainError

pytestmark = pytest.mark.django_db

PASSWORD = "Poprawne-Haslo-2026"


def invited_participant(email: str = "zaproszony@example.test", **kwargs) -> Participant:
    """Profil w stanie „zaproszony” – tak, jak zostawia go import listy klasowej."""
    user = UserFactory(email=email, is_active=False, email_verified_at=None)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return ParticipantFactory(
        user=user,
        invited_at=timezone.now(),
        invitation_sent_at=timezone.now() - timedelta(hours=2),
        gdpr_consent_at=None,
        terms_accepted_at=None,
        **kwargs,
    )


def consents() -> dict:
    return dict.fromkeys(CONSENT_FIELD_NAMES, True)


# --- S5: ``blocked_at`` ustawiane przy przejściu ``is_active`` ----------------------------------


def test_blokada_punktowym_zapisem_ustawia_blocked_at_a_odblokowanie_je_czysci():
    user = UserFactory()
    user.is_active = False
    user.save(update_fields=["is_active"])
    user.refresh_from_db()
    assert user.blocked_at is not None

    user.is_active = True
    user.save(update_fields=["is_active"])
    user.refresh_from_db()
    assert user.blocked_at is None


def test_pelny_zapis_jak_w_adminie_tez_ustawia_blocked_at():
    user = User.objects.get(pk=UserFactory().pk)
    user.is_active = False
    user.save()
    assert User.objects.get(pk=user.pk).blocked_at is not None


def test_nowe_konto_nieaktywne_nie_jest_zablokowane():
    """Rejestracja zakłada konto nieaktywne, bo czeka na link – to nie jest blokada."""
    user = UserFactory(is_active=False, email_verified_at=None)
    assert User.objects.get(pk=user.pk).blocked_at is None
    assert is_pending_activation(user)


def test_zapis_innej_kolumny_nie_rusza_blocked_at():
    user = UserFactory()
    user.is_active = False
    user.save(update_fields=["is_active"])
    stamp = User.objects.get(pk=user.pk).blocked_at
    fresh = User.objects.get(pk=user.pk)
    fresh.last_login = timezone.now()
    fresh.save(update_fields=["last_login"])
    assert User.objects.get(pk=user.pk).blocked_at == stamp


def test_blokada_z_panelu_koordynatora_ustawia_blocked_at():
    coordinator = CoordinatorFactory()
    user = UserFactory(email="blokowany@example.test")
    update_account_by_coordinator(user, actor=coordinator, account={"is_active": False})
    assert User.objects.get(pk=user.pk).blocked_at is not None


def blocked_unverified() -> User:
    """Konto zablokowane bez potwierdzonego adresu – przypadek z audytu (sprzed ``accounts.0010``)."""
    user = UserFactory(email="zablokowany@example.test", email_verified_at=None)
    user.is_active = False
    user.save(update_fields=["is_active"])
    return User.objects.get(pk=user.pk)


def test_zablokowanego_konta_nie_aktywuje_ani_link_ani_reczna_aktywacja():
    user = blocked_unverified()
    with pytest.raises(DomainError) as exc:
        activate_with_token(make_activation_token(user))
    assert exc.value.machine_code == "ACCOUNT_BLOCKED"
    with pytest.raises(DomainError) as exc:
        mark_activated(user, actor=CoordinatorFactory())
    assert exc.value.machine_code == "ACCOUNT_BLOCKED"
    user.refresh_from_db()
    assert user.is_active is False


def test_zablokowane_konto_nie_dostaje_linku_i_nie_czeka_na_aktywacje(
    django_capture_on_commit_callbacks, mailoutbox
):
    user = blocked_unverified()
    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation(user.email) is False
    assert mailoutbox == []
    assert is_pending_activation(user) is False
    assert resend_for_user(user) is None


def test_kosiarka_nie_kasuje_zablokowanego_konta():
    user = blocked_unverified()
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=30))
    assert not unactivated_accounts().filter(pk=user.pk).exists()


def test_zablokowane_konto_z_zaproszenia_nie_przyjmie_zaproszenia_i_nie_dostanie_go_ponownie():
    participant = invited_participant()
    user = participant.user
    # Blokada konta, które nigdy nie było aktywne, nie ustawia ``blocked_at`` sama (nie ma przejścia
    # True → False), więc stan zapisujemy wprost – tak wygląda konto z migracji danych.
    User.objects.filter(pk=user.pk).update(blocked_at=timezone.now())
    token = make_invite_token(participant)
    with pytest.raises(DomainError):
        read_invite_token(token)
    participant = Participant.objects.select_related("user").get(pk=participant.pk)
    with pytest.raises(DomainError) as exc:
        resend_invitation(participant)
    assert exc.value.machine_code == "INVITE_BLOCKED"
    with pytest.raises(DomainError):
        accept_invitation(
            participant,
            password=PASSWORD,
            phone="600 100 200",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(),
        )
    assert User.objects.get(pk=user.pk).is_active is False


# --- S6: konto z zaproszenia ---------------------------------------------------------------------


def test_link_aktywacyjny_nie_uruchamia_konta_z_zaproszenia():
    participant = invited_participant()
    with pytest.raises(DomainError) as exc:
        activate_with_token(make_activation_token(participant.user))
    assert exc.value.machine_code == "INVITATION_REQUIRED"
    assert User.objects.get(pk=participant.user_id).is_active is False


def test_prosba_o_link_dla_konta_z_zaproszenia_wysyla_zaproszenie(
    django_capture_on_commit_callbacks, mailoutbox
):
    participant = invited_participant()
    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation(participant.user.email) is True
    assert len(mailoutbox) == 1
    # W liście jest link zaproszenia, a nie link aktywacyjny.
    assert "/zaproszenie/" in mailoutbox[0].body
    assert "/activate/" not in mailoutbox[0].body


def test_publiczna_prosba_szanuje_karencje_zaproszenia(django_capture_on_commit_callbacks, mailoutbox):
    participant = invited_participant()
    Participant.objects.filter(pk=participant.pk).update(invitation_sent_at=timezone.now())
    with django_capture_on_commit_callbacks(execute=True):
        assert resend_activation(participant.user.email) is False
    assert mailoutbox == []


def test_koordynator_ponawia_zaproszenie_bez_karencji(django_capture_on_commit_callbacks, mailoutbox):
    participant = invited_participant()
    Participant.objects.filter(pk=participant.pk).update(invitation_sent_at=timezone.now())
    with django_capture_on_commit_callbacks(execute=True):
        sent = resend_for_user(participant.user, actor=CoordinatorFactory(), enforce_cooldown=False)
    assert sent == RESENT_INVITATION
    assert len(mailoutbox) == 1


def test_logowanie_google_nie_wpuszcza_konta_z_zaproszenia_bez_zgod():
    """Konto uruchomione z pominięciem zaproszenia (stara dziura S6) – aktywne, ale bez zgody RODO."""
    participant = invited_participant()
    User.objects.filter(pk=participant.user_id).update(is_active=True, email_verified_at=timezone.now())
    user = User.objects.get(pk=participant.user_id)
    assert _invited_without_consents(user)
    sociallogin = SimpleNamespace(
        user=user,
        is_existing=True,
        email_addresses=[],
        account=SimpleNamespace(provider="google"),
        provider=SimpleNamespace(name="Google"),
    )
    from allauth.core.exceptions import ImmediateHttpResponse
    from django.test import RequestFactory

    with pytest.raises(ImmediateHttpResponse) as exc:
        SocialAccountAdapter().pre_social_login(RequestFactory().get("/"), sociallogin)
    assert exc.value.response.status_code == 401
    assert REASON_INVITATION == "invitation"


def test_zwykly_uczestnik_ze_zgodami_przechodzi_bramke_google():
    participant = ParticipantFactory(invited_at=timezone.now())
    assert not _invited_without_consents(participant.user)


def test_profil_bez_zgody_rodo_nie_zapisze_sie_do_etapu():
    participant = invited_participant()
    stage = StageFactory(edition=CurrentEditionFactory())
    with pytest.raises(DomainError) as exc:
        register_for_stage(participant, stage)
    assert exc.value.machine_code == "CONSENTS_MISSING"


# --- S12: karencja ponownego zaproszenia ----------------------------------------------------------


def test_drugie_zaproszenie_w_ciagu_godziny_jest_odmowa(django_capture_on_commit_callbacks, mailoutbox):
    participant = invited_participant()
    with django_capture_on_commit_callbacks(execute=True):
        resend_invitation(participant)
    participant = Participant.objects.select_related("user").get(pk=participant.pk)
    with pytest.raises(DomainError) as exc:
        resend_invitation(participant)
    assert exc.value.machine_code == "INVITE_COOLDOWN"
    assert exc.value.status_code == 429
    assert len(mailoutbox) == 1
    # Po karencji – znowu wolno.
    Participant.objects.filter(pk=participant.pk).update(
        invitation_sent_at=timezone.now() - INVITE_RESEND_COOLDOWN - timedelta(minutes=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        resend_invitation(Participant.objects.select_related("user").get(pk=participant.pk))
    assert len(mailoutbox) == 2


# --- niskie: drugie użycie tokenu zaproszenia -------------------------------------------------------


def test_drugie_przyjecie_tego_samego_zaproszenia_jest_odmowa():
    """Obiekt z pierwszego odczytu tokenu (jak w drugim, równoległym POST-cie) nie przyjmie zaproszenia."""
    participant = invited_participant()
    stale = Participant.objects.select_related("user").get(pk=participant.pk)
    accept_invitation(
        participant,
        password=PASSWORD,
        phone="600 100 200",
        district=Voivodeship.MAZOWIECKIE,
        given=consents(),
    )
    with pytest.raises(DomainError) as exc:
        accept_invitation(
            stale,
            password="Inne-Haslo-Napastnika-2026",
            phone="600 100 201",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(),
        )
    assert exc.value.machine_code == "INVITE_TOKEN_INVALID"
    user = User.objects.get(pk=participant.user_id)
    assert user.check_password(PASSWORD)
