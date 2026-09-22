"""Kosiarka kont nieaktywowanych: co kasuje, czego nie tyka i co zostawia w audycie.

Dlaczego to w ogóle kasuje dane: adres e-mail jest unikalny, więc konto-widmo **blokuje adres**.
Uczeń, który nie doczekał listu i zamknął kartę, przy drugiej próbie dostawał „konto z tym adresem
już istnieje” i nie miał żadnej drogi dalej – aktywacji nie dostanie (link przepadł), a resetu hasła
nie użyje (konto nieaktywne nie dostaje listu resetu). Skasowanie wiersza zwalnia adres.

Najważniejszy test w tym pliku jest **negatywny**: konto, do którego odwołuje się dokumentacja
zawodów, zostaje. Świeża rejestracja takich obiektów mieć nie może, więc gdyby taki wiersz się
pojawił, znaczyłoby to, że stało się coś nieprzewidzianego – a wtedy kasowanie danych jest
najgorszą możliwą reakcją.
"""

from datetime import timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.activation import ACTIVATION_MAX_AGE
from apps.accounts.models import Participant, User
from apps.accounts.tasks import purge_unactivated_accounts
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.models import AuditLog

from .factories import ParticipantFactory, UserFactory

pytestmark = pytest.mark.django_db

WINDOW = timedelta(seconds=ACTIVATION_MAX_AGE)


def waiting_user(*, age: timedelta, **overrides) -> User:
    """Konto nieaktywowane, „założone” podaną chwilę temu."""
    user = UserFactory(is_active=False, email_verified_at=None, **overrides)
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - age)
    user.refresh_from_db()
    return user


def test_account_past_the_window_is_deleted():
    user = waiting_user(age=WINDOW + timedelta(minutes=1))

    result = purge_unactivated_accounts()

    assert result == {"deleted": 1, "skipped": 0}
    assert not User.objects.filter(pk=user.pk).exists()


def test_account_inside_the_window_is_kept():
    """Minuta przed terminem konto jeszcze żyje – list mógł właśnie dojść."""
    user = waiting_user(age=WINDOW - timedelta(minutes=1))

    assert purge_unactivated_accounts() == {"deleted": 0, "skipped": 0}
    assert User.objects.filter(pk=user.pk).exists()


def test_verified_account_is_never_touched():
    """``email_verified_at`` wypełnione znaczy „adres potwierdzony” – także dla konta zablokowanego.

    ``is_active=False`` niesie kilka znaczeń (zablokowane przez organizatora, zanonimizowane na
    żądanie RODO), więc sam ten warunek nie może rozstrzygać o skasowaniu danych.
    """
    blocked = UserFactory(is_active=False)
    User.objects.filter(pk=blocked.pk).update(date_joined=timezone.now() - WINDOW * 10)

    assert purge_unactivated_accounts() == {"deleted": 0, "skipped": 0}
    assert User.objects.filter(pk=blocked.pk).exists()


def test_active_account_is_never_touched():
    working = UserFactory()
    User.objects.filter(pk=working.pk).update(date_joined=timezone.now() - WINDOW * 10)

    assert purge_unactivated_accounts() == {"deleted": 0, "skipped": 0}
    assert User.objects.filter(pk=working.pk).exists()


def test_account_referenced_by_the_competition_is_skipped_and_logged(caplog):
    """Nieprzewidziany stan: konto nieaktywowane z wpisem do etapu. Zostaje i idzie do logu."""
    user = waiting_user(age=WINDOW + timedelta(hours=1))
    participant = ParticipantFactory(user=user)
    StageEntryFactory(participant=participant, stage=StageFactory())

    with caplog.at_level("WARNING"):
        result = purge_unactivated_accounts()

    assert result == {"deleted": 0, "skipped": 1}
    assert User.objects.filter(pk=user.pk).exists()
    assert Participant.objects.filter(pk=participant.pk).exists()
    assert f"#{user.pk}" in caplog.text
    # W logu nie ma adresu e-mail: identyfikator wystarcza, żeby koordynator odnalazł konto.
    assert user.email not in caplog.text


def test_an_unactivated_supervisor_account_is_deleted_past_the_window():
    """Ta sama kosiarka, ten sam powód: opiekun też nie ma jeszcze konta chronionego dowodem.

    Rejestracja opiekuna przechodzi przez ten sam ``register_supervisor`` → aktywacja, co
    uczestnik – konto powstaje ``is_active=False``, ``email_verified_at=None`` i czeka na ten sam
    link. H2 (22.09.2026): dopóki ta rola nie prowadziła nigdzie, nikt nie sprawdzał, czy kosiarka
    w ogóle ją widzi.
    """
    from apps.accounts.supervisors import register_supervisor

    register_supervisor(
        email="porzucony.opiekun@szkola.test",
        password="Poprawne-Haslo-2026",
        first_name="Jan",
        last_name="Nauczyciel",
        terms_consent=True,
        gdpr_consent=True,
    )
    user = User.objects.get(email="porzucony.opiekun@szkola.test")
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - WINDOW - timedelta(minutes=1))

    result = purge_unactivated_accounts()

    assert result == {"deleted": 1, "skipped": 0}
    assert not User.objects.filter(pk=user.pk).exists()


def test_an_activated_supervisor_account_is_kept():
    """Aktywacja odcina konto od kosiarki dokładnie tak samo, jak u uczestnika."""
    from apps.accounts.activation import activate_with_token, make_activation_token
    from apps.accounts.supervisors import register_supervisor

    register_supervisor(
        email="aktywny.opiekun@szkola.test",
        password="Poprawne-Haslo-2026",
        first_name="Jan",
        last_name="Nauczyciel",
        terms_consent=True,
        gdpr_consent=True,
    )
    user = User.objects.get(email="aktywny.opiekun@szkola.test")
    activate_with_token(make_activation_token(user))
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - WINDOW - timedelta(minutes=1))

    result = purge_unactivated_accounts()

    assert result == {"deleted": 0, "skipped": 0}
    assert User.objects.filter(pk=user.pk).exists()


def test_an_unactivated_supervisor_with_a_confirmed_participation_is_skipped_and_logged(caplog):
    """Nieprzewidziany stan, tak samo jak u uczestnika: potwierdzenie istnieje, kasowanie by je zabrało."""
    from apps.accounts.models import SchoolParticipation
    from apps.accounts.supervisors import register_supervisor
    from apps.competitions.tests.factories import CurrentEditionFactory
    from apps.tenancy.tests.factories import current_or_default_competition

    register_supervisor(
        email="niedokonczone.szkola@szkola.test",
        password="Poprawne-Haslo-2026",
        first_name="Jan",
        last_name="Nauczyciel",
        terms_consent=True,
        gdpr_consent=True,
    )
    user = User.objects.get(email="niedokonczone.szkola@szkola.test")
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - WINDOW - timedelta(hours=1))
    edition = CurrentEditionFactory(competition=current_or_default_competition())
    SchoolParticipation.objects.create(supervisor=user.school_supervisor, edition=edition)

    with caplog.at_level("WARNING"):
        result = purge_unactivated_accounts()

    assert result == {"deleted": 0, "skipped": 1}
    assert User.objects.filter(pk=user.pk).exists()


def test_deleting_the_account_frees_the_address_for_a_new_registration():
    """Sens całej kosiarki: ten sam adres da się zarejestrować ponownie."""
    user = waiting_user(age=WINDOW + timedelta(minutes=1), email="drugie-podejscie@example.test")

    purge_unactivated_accounts()

    assert not User.objects.filter(email="drugie-podejscie@example.test").exists()
    again = UserFactory(email="drugie-podejscie@example.test")
    assert again.pk != user.pk


def test_purge_leaves_one_audit_entry_with_counts_only():
    waiting_user(age=WINDOW + timedelta(minutes=1))
    waiting_user(age=WINDOW + timedelta(minutes=2), email="drugi@example.test")

    purge_unactivated_accounts()

    entry = AuditLog.objects.get(action="account.purged_unactivated")
    assert entry.diff == {"deleted": 2, "skipped": 0}
    # Wpis per konto byłby listą adresów, które ktoś kiedyś próbował zarejestrować – czyli
    # dokładnie tą daną osobową, którą ten przebieg właśnie usunął.
    assert "example.test" not in str(entry.diff)


def test_nothing_to_do_leaves_no_audit_entry():
    assert purge_unactivated_accounts() == {"deleted": 0, "skipped": 0}
    assert not AuditLog.objects.filter(action="account.purged_unactivated").exists()


def test_management_command_runs_the_same_function():
    """Komenda istnieje dla koordynatora, który musi zwolnić adres **teraz**, nie w kwadransie."""
    user = waiting_user(age=WINDOW + timedelta(minutes=1))

    call_command("purge_unactivated_accounts")

    assert not User.objects.filter(pk=user.pk).exists()


def test_beat_runs_the_purge_every_quarter():
    entry = settings.CELERY_BEAT_SCHEDULE["purge-unactivated-accounts"]

    assert entry["task"] == "apps.accounts.tasks.purge_unactivated_accounts"
    assert entry["schedule"] == 900.0
