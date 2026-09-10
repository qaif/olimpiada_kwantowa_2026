"""Okno rejestracji uczestników: reguła stanu, bramka serwisowa i ustawienia z panelu.

Cztery rzeczy, na których to stoi:

- stan liczy się z dwóch przełączników i dwóch dat, a nie z domysłu widoku,
- brak bieżącej edycji to „zamknięta”, nie „otwarta” – nie ma wtedy do czego zakładać konta,
- bramka jest **w serwisie**, więc obowiązuje formularz, API i logowanie społecznościowe naraz,
- zmiana okna z panelu zostawia ślad w audycie i nie przyjmuje odwróconego okna.
"""

from datetime import datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.models import (
    REGISTRATION_CLOSED,
    REGISTRATION_DISABLED,
    REGISTRATION_NOT_YET,
    REGISTRATION_OPEN,
    Edition,
    current_registration_status,
)
from apps.competitions.registration import ensure_registration_open, registration_message
from apps.competitions.services import update_registration_window
from apps.competitions.tests.factories import CurrentEditionFactory, EditionFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def now():
    return timezone.now()


# --- reguła stanu -------------------------------------------------------------------------------


def test_edition_without_a_window_is_open(now):
    """Domyślna edycja (włączona, bez terminów) przyjmuje konta – tak było przed tą zmianą."""
    state = EditionFactory().registration_status(now)

    assert state.is_open is True
    assert state.reason == REGISTRATION_OPEN
    assert state.opens_at is None and state.closes_at is None


@pytest.mark.parametrize(
    ("enabled", "opens_offset", "closes_offset", "is_open", "reason"),
    [
        # wyłącznik ma pierwszeństwo przed każdym terminem
        (False, None, None, False, REGISTRATION_DISABLED),
        (False, -1, 1, False, REGISTRATION_DISABLED),
        (False, 1, 2, False, REGISTRATION_DISABLED),
        # włączona, bez okna
        (True, None, None, True, REGISTRATION_OPEN),
        # przed otwarciem / w oknie / po zamknięciu
        (True, 1, None, False, REGISTRATION_NOT_YET),
        (True, 1, 2, False, REGISTRATION_NOT_YET),
        (True, -1, None, True, REGISTRATION_OPEN),
        (True, -1, 1, True, REGISTRATION_OPEN),
        (True, None, 1, True, REGISTRATION_OPEN),
        (True, -2, -1, False, REGISTRATION_CLOSED),
        (True, None, -1, False, REGISTRATION_CLOSED),
    ],
)
def test_status_matrix(now, enabled, opens_offset, closes_offset, is_open, reason):
    edition = EditionFactory(
        registration_enabled=enabled,
        registration_opens_at=None if opens_offset is None else now + timedelta(days=opens_offset),
        registration_closes_at=None if closes_offset is None else now + timedelta(days=closes_offset),
    )

    state = edition.registration_status(now)

    assert (state.is_open, state.reason) == (is_open, reason)


def test_window_boundaries_are_inclusive_on_open_and_exclusive_on_close(now):
    """Otwarcie „na styk” już wpuszcza, zamknięcie „na styk” już nie."""
    at_open = EditionFactory(registration_opens_at=now)
    at_close = EditionFactory(registration_closes_at=now)

    assert at_open.registration_status(now).is_open is True
    assert at_close.registration_status(now).is_open is False
    assert at_close.registration_status(now).reason == REGISTRATION_CLOSED


def test_no_current_edition_is_closed():
    """Brak bieżącej edycji: nie ma do czego się rejestrować, więc „wyłączona”, nie „otwarta”."""
    EditionFactory()  # edycja archiwalna z otwartą rejestracją – nie ma znaczenia

    state = current_registration_status()

    assert state.is_open is False
    assert state.reason == REGISTRATION_DISABLED


def test_current_registration_status_reads_the_current_edition(now):
    EditionFactory(registration_enabled=False)
    CurrentEditionFactory(registration_opens_at=now + timedelta(days=3))

    state = current_registration_status(now)

    assert (state.is_open, state.reason) == (False, REGISTRATION_NOT_YET)


# --- reguła okna w modelu i w bazie --------------------------------------------------------------


def test_reversed_window_is_rejected_by_full_clean(now):
    edition = EditionFactory.build(
        year_label="Edycja odwrócona",
        registration_opens_at=now + timedelta(days=2),
        registration_closes_at=now + timedelta(days=1),
    )

    with pytest.raises(ValidationError) as exc:
        edition.full_clean()

    assert "registration_closes_at" in exc.value.message_dict


def test_reversed_window_is_rejected_by_the_database(now):
    """Ostatnia linia obrony: zapis z pominięciem ``full_clean()`` też nie przechodzi."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Edition.objects.create(
            year_label="Edycja odwrócona (baza)",
            registration_opens_at=now + timedelta(days=2),
            registration_closes_at=now + timedelta(days=1),
        )


# --- komunikaty ----------------------------------------------------------------------------------


def test_messages_name_the_reason_and_the_moment(now):
    opens_at = timezone.make_aware(datetime(2026, 9, 8, 0, 0))
    closes_at = timezone.make_aware(datetime(2026, 11, 7, 23, 59))

    not_yet = EditionFactory(registration_opens_at=opens_at).registration_status(
        now=opens_at - timedelta(days=1)
    )
    closed = EditionFactory(registration_closes_at=closes_at).registration_status(
        now=closes_at + timedelta(days=1)
    )
    disabled = EditionFactory(registration_enabled=False).registration_status(now)

    assert registration_message(not_yet) == "Rejestracja rusza 8 września 2026 o 00:00."
    assert registration_message(closed) == "Rejestracja została zamknięta 7 listopada 2026 o 23:59."
    assert registration_message(disabled) == "Rejestracja uczestników jest obecnie wyłączona."
    assert registration_message(EditionFactory().registration_status(now)) == ""


# --- bramka serwisowa -----------------------------------------------------------------------------


def test_guard_passes_when_registration_is_open():
    CurrentEditionFactory()

    assert ensure_registration_open().is_open is True


@pytest.mark.parametrize(
    ("field", "offset_days", "fragment"),
    [
        ("registration_enabled", None, "wyłączona"),
        ("registration_opens_at", 1, "rusza"),
        ("registration_closes_at", -1, "zamknięta"),
    ],
)
def test_guard_refuses_with_409_and_a_reason(field, offset_days, fragment):
    value = False if offset_days is None else timezone.now() + timedelta(days=offset_days)
    CurrentEditionFactory(**{field: value})

    with pytest.raises(DomainError) as exc:
        ensure_registration_open()

    assert exc.value.machine_code == "REGISTRATION_CLOSED"
    assert exc.value.status_code == 409
    assert fragment in str(exc.value.detail)


# --- ustawienia z panelu (serwis) -----------------------------------------------------------------


def test_update_saves_the_window_and_writes_audit(now):
    edition = CurrentEditionFactory()
    opens_at = now + timedelta(days=5)

    update_registration_window(edition, actor=CoordinatorFactory(), registration_opens_at=opens_at)

    edition.refresh_from_db()
    assert edition.registration_opens_at == opens_at
    entry = AuditLog.objects.get(action="edition.registration_updated")
    assert entry.target_id == str(edition.pk)
    assert entry.diff["registration_opens_at"]["to"] == opens_at.isoformat()


def test_update_rejects_a_reversed_window(now):
    edition = CurrentEditionFactory(registration_opens_at=now + timedelta(days=5))

    with pytest.raises(DomainError) as exc:
        update_registration_window(
            edition, actor=CoordinatorFactory(), registration_closes_at=now + timedelta(days=1)
        )

    edition.refresh_from_db()
    assert exc.value.machine_code == "REGISTRATION_WINDOW_INVALID"
    assert edition.registration_closes_at is None
    assert not AuditLog.objects.filter(action="edition.registration_updated").exists()


def test_update_without_changes_writes_nothing():
    edition = CurrentEditionFactory()

    update_registration_window(edition, actor=CoordinatorFactory(), registration_enabled=True)

    assert not AuditLog.objects.filter(action="edition.registration_updated").exists()


def test_disabling_does_not_clear_the_dates(now):
    opens_at = now + timedelta(days=5)
    edition = CurrentEditionFactory(registration_opens_at=opens_at)

    update_registration_window(edition, actor=CoordinatorFactory(), registration_enabled=False)

    edition.refresh_from_db()
    assert edition.registration_enabled is False
    assert edition.registration_opens_at == opens_at


# --- seed ------------------------------------------------------------------------------------------


def test_seed_sets_the_announced_opening_on_creation():
    from django.core.management import call_command

    from apps.competitions.management.commands.seed_edition_kwantowa import (
        EDITION_LABEL,
        REGISTRATION_OPENS,
    )

    call_command("seed_edition_kwantowa", verbosity=0)

    edition = Edition.objects.get(year_label=EDITION_LABEL)
    assert edition.registration_enabled is True
    assert edition.registration_opens_at == REGISTRATION_OPENS
    assert edition.registration_closes_at is None


def test_seed_does_not_touch_an_existing_edition(now):
    from django.core.management import call_command

    from apps.competitions.management.commands.seed_edition_kwantowa import EDITION_LABEL

    moved = now + timedelta(days=99)
    EditionFactory(year_label=EDITION_LABEL, registration_enabled=False, registration_opens_at=moved)

    call_command("seed_edition_kwantowa", verbosity=0)

    edition = Edition.objects.get(year_label=EDITION_LABEL)
    assert edition.registration_enabled is False
    assert edition.registration_opens_at == moved
