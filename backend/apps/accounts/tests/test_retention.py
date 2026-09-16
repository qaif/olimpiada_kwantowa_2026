"""Retencja danych osobowych: kiedy edycja przestaje być powodem do trzymania danych.

Przedmiotem tych testów są cztery rzeczy, z których każda, popsuta po cichu, kończy się
skasowaniem danych, których kasować nie było wolno – albo trzymaniem ich bez podstawy:

- **termin** liczy się od ostatniego deadline'u etapu plus ``Edition.data_retention_months``,
  a nie od daty utworzenia edycji ani od publikacji wyników,
- **edycja bieżąca nigdy nie wchodzi** do przebiegu, nawet gdyby jej retencja była krótsza
  niż jej własny kalendarz,
- **bramki**: konto z nierozstrzygniętą reklamacją, konto z etapem bez ogłoszonych wyników
  i konto startujące w edycji późniejszej zostają nietknięte,
- **skutek** jest dokładnie tą samą anonimizacją, co przy żądaniu z art. 17: dane osobowe znikają,
  pseudonimowy wiersz i dokumentacja zawodów zostają. Do tego wpis ``account.anonymised_by_retention``
  z identyfikatorem edycji – bez niego w aktach zostawałby sam skutek, bez powodu.

Dry-run (``plan``) jest sprawdzany tym samym kompletem danych, co przebieg: raport, który mówi
co innego niż to, co się za chwilę stanie, jest gorszy niż brak raportu.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.profile import ANONYMISED_EMAIL_DOMAIN, ANONYMISED_SCHOOL
from apps.accounts.retention import (
    BLOCKED_LATER_EDITION,
    BLOCKED_OPEN_APPEAL,
    BLOCKED_UNPUBLISHED_RESULTS,
    add_months,
    anonymise_expired_editions,
    candidates,
    expired_editions,
    plan,
    retention_deadline,
)
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.appeals.models import Appeal
from apps.competitions.models import Edition, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    EditionFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def old_edition(*, months_ago: int = 30, retention: int = 24, published: bool = True) -> Edition:
    """Edycja archiwalna z jednym etapem, którego deadline minął ``months_ago`` miesięcy temu.

    ``published`` steruje ogłoszeniem wyników, bo to jedna z bramek: etap bez
    ``results_published_at`` znaczy „zawody nie zostały domknięte”, a wtedy termin retencji
    policzony z jego deadline'u jest liczbą bez pokrycia.
    """
    edition = EditionFactory(data_retention_months=retention)
    now = timezone.now()
    past = now - timedelta(days=31 * months_ago)
    stage = StageFactory(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=past - timedelta(days=30),
        deadline_at=past,
        review_deadline_at=past + timedelta(days=14),
        appeal_window_opens_at=past + timedelta(days=16),
        appeal_window_closes_at=past + timedelta(days=23),
    )
    if published:
        stage.results_published_at = past + timedelta(days=24)
        stage.save(update_fields=["results_published_at"])
    return edition


def participant_of(edition, **kwargs):
    """Uczestnik z wpisem do (jedynego) etapu tej edycji."""
    participant = ParticipantFactory(**kwargs)
    StageEntryFactory(participant=participant, stage=edition.stages.first())
    return participant


# --- arytmetyka terminu -------------------------------------------------------------------------


def test_add_months_keeps_the_day_of_month():
    moment = timezone.make_aware(timezone.datetime(2026, 1, 15, 23, 59))

    assert add_months(moment, 24).date().isoformat() == "2028-01-15"


def test_add_months_clamps_a_day_that_the_target_month_does_not_have():
    """31 stycznia + 1 miesiąc to 28 lutego, a nie 3 marca – inaczej retencja by się wydłużała."""
    moment = timezone.make_aware(timezone.datetime(2026, 1, 31, 12, 0))

    assert add_months(moment, 1).date().isoformat() == "2026-02-28"


def test_the_deadline_counts_from_the_last_stage_deadline():
    edition = old_edition(months_ago=30, retention=24)
    last_deadline = edition.stages.first().deadline_at

    assert retention_deadline(edition) == add_months(last_deadline, 24)


def test_retention_switched_off_has_no_deadline():
    """Zero miesięcy jest świadomym wyłączeniem automatu dla rocznika, a nie „zero, czyli teraz”."""
    assert retention_deadline(old_edition(retention=0)) is None


def test_an_edition_without_stages_has_no_deadline():
    """Nic się w niej nie odbyło, więc nie ma od czego liczyć terminu."""
    assert retention_deadline(EditionFactory()) is None


# --- które edycje wchodzą do przebiegu -----------------------------------------------------------


def test_an_expired_edition_is_listed():
    edition = old_edition(months_ago=30, retention=24)

    assert [item.pk for item in expired_editions()] == [edition.pk]


def test_an_edition_within_its_retention_period_is_not_listed():
    old_edition(months_ago=6, retention=24)

    assert expired_editions() == []


def test_the_current_edition_never_expires_even_with_a_short_retention():
    """Bezwarunkowo, a nie przez sam termin: pomyłka w ustawieniu nie może anonimizować startujących."""
    edition = CurrentEditionFactory(data_retention_months=1)
    past = timezone.now() - timedelta(days=365)
    StageFactory(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=past - timedelta(days=30),
        deadline_at=past,
        review_deadline_at=past + timedelta(days=14),
        appeal_window_opens_at=past + timedelta(days=16),
        appeal_window_closes_at=past + timedelta(days=23),
    )

    assert expired_editions() == []


# --- bramki -------------------------------------------------------------------------------------


def test_an_account_of_an_expired_edition_is_due():
    edition = old_edition()
    participant = participant_of(edition)

    due = [item.participant.pk for item in candidates(edition) if item.is_due]

    assert due == [participant.pk]


def test_an_account_entered_in_a_later_edition_is_blocked():
    expired = old_edition()
    participant = participant_of(expired)
    current = CurrentEditionFactory()
    StageEntryFactory(participant=participant, stage=StageFactory(edition=current, kind=StageKind.ELIM))

    blocked = {item.participant.pk: item.blocked for item in candidates(expired)}

    assert blocked[participant.pk] == BLOCKED_LATER_EDITION


def test_an_account_with_a_pending_appeal_is_blocked():
    edition = old_edition()
    participant = participant_of(edition)
    Appeal.objects.create(
        submission=_submission_for(participant, edition),
        filed_by=participant,
        argument="Nie zgadzam się z oceną tego zadania i proszę o ponowne jej rozpatrzenie.",
    )

    blocked = {item.participant.pk: item.blocked for item in candidates(edition)}

    assert blocked[participant.pk] == BLOCKED_OPEN_APPEAL


def test_an_account_with_an_unpublished_stage_is_blocked():
    edition = old_edition(published=False)
    participant = participant_of(edition)

    blocked = {item.participant.pk: item.blocked for item in candidates(edition)}

    assert blocked[participant.pk] == BLOCKED_UNPUBLISHED_RESULTS


def _submission_for(participant, edition):
    """Praca uczestnika w etapie tej edycji – materiał dla reklamacji."""
    from apps.competitions.tests.factories import ProblemFactory
    from apps.submissions.models import Submission

    stage = edition.stages.first()
    entry = participant.stage_entries.get(stage=stage)
    return Submission.objects.create(entry=entry, problem=ProblemFactory(stage=stage, number=1))


# --- przebieg -----------------------------------------------------------------------------------


def test_the_run_anonymises_the_account_and_keeps_the_pseudonymous_row():
    edition = old_edition()
    participant = participant_of(
        edition, user=UserFactory(email="stary@example.test", first_name="Jan", last_name="Kowalski")
    )
    code = participant.public_code
    district = participant.district

    result = anonymise_expired_editions()

    participant.refresh_from_db()
    participant.user.refresh_from_db()
    assert result["anonymised"] == 1
    assert participant.user.email.endswith(f"@{ANONYMISED_EMAIL_DOMAIN}")
    assert participant.user.first_name == ""
    assert participant.school == ANONYMISED_SCHOOL
    # Pseudonim i województwo zostają: pod kodem uczestnik stoi w ogłoszonych tabelach, a lista
    # województw jest zamknięta, więc puste nie jest legalną wartością.
    assert participant.public_code == code
    assert participant.district == district


def test_the_run_leaves_an_audit_entry_with_the_reason():
    edition = old_edition()
    participant = participant_of(edition)

    anonymise_expired_editions()

    entry = AuditLog.objects.filter(action="account.anonymised_by_retention").get()
    assert entry.actor is None
    assert entry.diff["edition_id"] == edition.pk
    assert entry.diff["user_id"] == participant.user.pk


def test_the_run_skips_a_blocked_account():
    edition = old_edition(published=False)
    participant = participant_of(edition, user=UserFactory(email="zostaje@example.test"))

    result = anonymise_expired_editions()

    participant.user.refresh_from_db()
    assert result == {"editions": 1, "anonymised": 0, "blocked": 1}
    assert participant.user.email == "zostaje@example.test"


def test_committee_accounts_are_untouched():
    """Retencja dotyczy uczestników: konto recenzenta jest funkcyjne i żyje między edycjami."""
    from apps.accounts.tests.factories import ActiveReviewerFactory

    old_edition()
    member = ActiveReviewerFactory()

    anonymise_expired_editions()

    member.user.refresh_from_db()
    assert not member.user.email.endswith(f"@{ANONYMISED_EMAIL_DOMAIN}")


def test_a_second_run_does_nothing():
    """Konto już zanonimizowane nie wchodzi do przebiegu ponownie – ani do liczników, ani do audytu."""
    edition = old_edition()
    participant_of(edition)
    anonymise_expired_editions()

    result = anonymise_expired_editions()

    assert result["anonymised"] == 0
    assert AuditLog.objects.filter(action="account.anonymised_by_retention").count() == 1


# --- dry-run --------------------------------------------------------------------------------


def test_the_plan_changes_nothing():
    edition = old_edition()
    participant = participant_of(edition, user=UserFactory(email="nietkniete@example.test"))

    rows = plan()

    participant.user.refresh_from_db()
    assert [item.participant.pk for item in rows[0].due] == [participant.pk]
    assert participant.user.email == "nietkniete@example.test"


def test_the_report_command_lists_codes_and_never_e_mail_addresses():
    edition = old_edition()
    participant = participant_of(edition, user=UserFactory(email="tajny@example.test"))
    output = StringIO()

    call_command("retention_report", stdout=output)

    body = output.getvalue()
    assert participant.public_code in body
    assert "tajny@example.test" not in body
    assert edition.year_label in body
