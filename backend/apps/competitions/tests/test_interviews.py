"""Etap w formie rozmowy kwalifikacyjnej: terminy koordynatora i zapisy uczestników.

Cztery rzeczy, na których ten moduł stoi:

- terminy powstają serią i wyłącznie w oknie etapu,
- termin z zapisem jest nieusuwalny, a miejsce zajęte – niedostępne,
- uczestnik ma w etapie **jeden** termin; „zmiana terminu” jest przeniesieniem zapisu,
- każda operacja zostawia ślad w audycie, a w ``diff`` nie ma ani jednej danej osobowej.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.interviews import (
    book_slot,
    cancel_booking,
    create_slots,
    delete_slot,
    slots_for_coordinator,
    slots_for_participant,
)
from apps.competitions.models import (
    InterviewBooking,
    InterviewSlot,
    Stage,
    StageEntryStatus,
    StageFormat,
)
from apps.competitions.services import update_stage
from apps.core.api import DomainError
from apps.core.models import AuditLog

from .factories import (
    CurrentEditionFactory,
    EditionFactory,
    InterviewSlotFactory,
    InterviewStageFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

#: Dane uczestnika użyte w asercjach „tego nie ma w temacie listu ani w audycie”. Muszą być
#: **rozpoznawalne**: przy „Jan Kowalski” asercja „nie ma nazwiska w diffie” przechodzi też wtedy,
#: gdy nazwisko tam jest, a przy trzyliterowym imieniu – przypadkowo wpada w każde inne słowo.
PII_FIRST_NAME = "Bartłomiejusz"
PII_LAST_NAME = "Wróblewski-Zadrożny"
PII_SCHOOL = "XLVII LO im. Testowej Kwantowej"

#: Wszystkie wpisy audytowe modułu – używane w asercji „w diffie nie ma danych osobowych”.
INTERVIEW_ACTIONS = (
    "interview.slots_created",
    "interview.slot_deleted",
    "interview.booked",
    "interview.booking_moved",
    "interview.cancelled",
)


@pytest.fixture
def coordinator_user():
    return CoordinatorFactory()


@pytest.fixture
def stage():
    """Etap rozmowy w bieżącej edycji: otwarty od wczoraj, zapisy do terminu za 14 dni."""
    return InterviewStageFactory(edition=CurrentEditionFactory())


@pytest.fixture
def qualified(stage):
    participant = ParticipantFactory(
        user__first_name=PII_FIRST_NAME, user__last_name=PII_LAST_NAME, school=PII_SCHOOL
    )
    StageEntryFactory(participant=participant, stage=stage, status=StageEntryStatus.QUALIFIED)
    return participant


def _tomorrow(hour: int = 9):
    """Godzina jutrzejsza – zawsze w przyszłości i wewnątrz okna etapu z fabryki."""
    return (timezone.now() + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)


# --- wyznaczanie terminów -------------------------------------------------------------------------


def test_create_slots_makes_a_consecutive_series(stage, coordinator_user):
    slots = create_slots(
        stage, coordinator_user, starts_at=_tomorrow(), duration_minutes=20, count=3, capacity=2
    )

    assert [slot.capacity for slot in slots] == [2, 2, 2]
    assert slots[0].ends_at == slots[1].starts_at
    assert slots[2].ends_at - slots[0].starts_at == timedelta(minutes=60)
    entry = AuditLog.objects.get(action="interview.slots_created")
    assert entry.diff["count"] == 3
    assert entry.diff["capacity"] == 2


def test_create_slots_refuses_on_a_written_stage(coordinator_user):
    written = StageFactory(edition=CurrentEditionFactory())

    with pytest.raises(DomainError) as exc:
        create_slots(written, coordinator_user, starts_at=_tomorrow(), duration_minutes=20)

    assert exc.value.machine_code == "STAGE_NOT_INTERVIEW"
    assert not InterviewSlot.objects.exists()


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"duration_minutes": 0}, "SLOT_DURATION_INVALID"),
        ({"duration_minutes": 481}, "SLOT_DURATION_INVALID"),
        ({"count": 0}, "SLOT_COUNT_INVALID"),
        ({"count": 51}, "SLOT_COUNT_INVALID"),
        ({"capacity": 0}, "SLOT_CAPACITY_INVALID"),
    ],
)
def test_create_slots_checks_the_numbers(stage, coordinator_user, kwargs, code):
    payload = {"starts_at": _tomorrow(), "duration_minutes": 20} | kwargs

    with pytest.raises(DomainError) as exc:
        create_slots(stage, coordinator_user, **payload)

    assert exc.value.machine_code == code


def test_create_slots_refuses_a_time_in_the_past(stage, coordinator_user):
    with pytest.raises(DomainError) as exc:
        create_slots(
            stage, coordinator_user, starts_at=timezone.now() - timedelta(hours=1), duration_minutes=20
        )

    assert exc.value.machine_code == "SLOT_IN_PAST"


def test_create_slots_must_fit_in_the_stage_window(stage, coordinator_user):
    """Okno rozmów **jest** oknem etapu – termin po jego zamknięciu byłby datą spoza harmonogramu."""
    after_deadline = stage.deadline_at + timedelta(days=1)

    with pytest.raises(DomainError) as exc:
        create_slots(stage, coordinator_user, starts_at=after_deadline, duration_minutes=20)

    assert exc.value.machine_code == "SLOT_OUTSIDE_STAGE"
    assert not InterviewSlot.objects.exists()


def test_create_slots_refuses_a_series_that_overflows_the_deadline(stage, coordinator_user):
    """Pierwszy termin mieści się w oknie, ostatni już nie – seria wchodzi albo nie wchodzi cała."""
    with pytest.raises(DomainError) as exc:
        create_slots(
            stage,
            coordinator_user,
            starts_at=stage.deadline_at - timedelta(minutes=30),
            duration_minutes=20,
            count=3,
        )

    assert exc.value.machine_code == "SLOT_OUTSIDE_STAGE"


def test_delete_slot_refuses_when_someone_is_booked(stage, qualified, coordinator_user):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, slot)

    with pytest.raises(DomainError) as exc:
        delete_slot(slot, coordinator_user)

    assert exc.value.machine_code == "SLOT_HAS_BOOKINGS"
    assert InterviewSlot.objects.filter(pk=slot.pk).exists()


def test_delete_slot_removes_an_empty_one_and_audits(stage, coordinator_user):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())

    delete_slot(slot, coordinator_user)

    assert not InterviewSlot.objects.exists()
    assert AuditLog.objects.filter(action="interview.slot_deleted").exists()


# --- zapisy uczestnika ------------------------------------------------------------------------------


def test_booking_requires_an_entry_in_the_stage(stage):
    stranger = ParticipantFactory()
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())

    with pytest.raises(DomainError) as exc:
        book_slot(stranger, slot)

    assert exc.value.machine_code == "NOT_QUALIFIED"
    assert not InterviewBooking.objects.exists()


def test_disqualified_participant_cannot_book(stage):
    participant = ParticipantFactory()
    StageEntryFactory(participant=participant, stage=stage, status=StageEntryStatus.DISQUALIFIED)
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())

    with pytest.raises(DomainError) as exc:
        book_slot(participant, slot)

    assert exc.value.machine_code == "NOT_QUALIFIED"


def test_booking_a_full_slot_is_refused(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(), capacity=1)
    book_slot(qualified, slot)
    other = ParticipantFactory()
    StageEntryFactory(participant=other, stage=stage, status=StageEntryStatus.QUALIFIED)

    with pytest.raises(DomainError) as exc:
        book_slot(other, slot)

    assert exc.value.machine_code == "SLOT_FULL"
    assert InterviewBooking.objects.count() == 1


def test_booking_the_same_slot_twice_is_refused(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, slot)

    with pytest.raises(DomainError) as exc:
        book_slot(qualified, slot)

    assert exc.value.machine_code == "ALREADY_BOOKED"


def test_booking_a_started_slot_is_refused(stage, qualified):
    started = InterviewSlotFactory(stage=stage)
    InterviewSlot.objects.filter(pk=started.pk).update(
        starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=1)
    )
    started.refresh_from_db()

    with pytest.raises(DomainError) as exc:
        book_slot(qualified, started)

    assert exc.value.machine_code == "SLOT_STARTED"


def test_booking_moves_between_slots_instead_of_duplicating(stage, qualified):
    first = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    second = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(11))
    book_slot(qualified, first)

    book_slot(qualified, second)

    booking = InterviewBooking.objects.get()
    assert booking.slot == second
    moved = AuditLog.objects.get(action="interview.booking_moved")
    assert moved.diff == {"from_slot": first.pk, "to_slot": second.pk}


def test_moving_after_the_interview_started_is_refused(stage, qualified):
    started = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, started)
    InterviewSlot.objects.filter(pk=started.pk).update(
        starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=1)
    )
    later = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(12))

    with pytest.raises(DomainError) as exc:
        book_slot(qualified, later)

    assert exc.value.machine_code == "BOOKING_LOCKED"
    assert InterviewBooking.objects.get().slot_id == started.pk


def test_cancel_frees_the_place(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(), capacity=1)
    book_slot(qualified, slot)

    cancel_booking(qualified, stage=stage)

    assert not InterviewBooking.objects.exists()
    assert AuditLog.objects.filter(action="interview.cancelled").exists()


def test_cancel_after_the_interview_started_is_refused(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, slot)
    InterviewSlot.objects.filter(pk=slot.pk).update(
        starts_at=timezone.now() - timedelta(minutes=5), ends_at=timezone.now() + timedelta(minutes=15)
    )

    with pytest.raises(DomainError) as exc:
        cancel_booking(qualified, stage=stage)

    assert exc.value.machine_code == "BOOKING_LOCKED"
    assert InterviewBooking.objects.exists()


def test_a_closed_stage_takes_neither_bookings_nor_cancellations(stage, qualified):
    """Zamknięty etap jest rozliczony: lista osób przy komisji nie może się już zmieniać."""
    booked = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    free = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(11))
    book_slot(qualified, booked)
    Stage.objects.filter(pk=stage.pk).update(closed_at=timezone.now())
    stage.refresh_from_db()

    with pytest.raises(DomainError) as booking:
        book_slot(qualified, free)
    with pytest.raises(DomainError) as cancellation:
        cancel_booking(qualified, stage=stage)

    assert booking.value.machine_code == "STAGE_CLOSED"
    assert cancellation.value.machine_code == "STAGE_CLOSED"
    assert InterviewBooking.objects.get().slot_id == booked.pk


def test_booking_in_an_archived_edition_says_so(qualified):
    """Etap archiwalny ma własny kod: „to nie rozmowa” byłoby nieprawdą na ekranie pełnym rozmów."""
    archived = InterviewStageFactory(edition=EditionFactory())
    participant = ParticipantFactory()
    StageEntryFactory(participant=participant, stage=archived, status=StageEntryStatus.QUALIFIED)
    slot = InterviewSlotFactory(stage=archived, starts_at=_tomorrow())

    with pytest.raises(DomainError) as exc:
        book_slot(participant, slot)

    assert exc.value.machine_code == "EDITION_NOT_CURRENT"
    assert exc.value.status_code == 403
    assert not InterviewBooking.objects.exists()


def test_booking_sends_a_confirmation_without_personal_data_in_the_subject(
    stage, qualified, django_capture_on_commit_callbacks
):
    """List wychodzi **po commicie** – stąd ``django_capture_on_commit_callbacks``.

    Kolejkowanie w środku transakcji dawałoby wiadomość także wtedy, gdy zapis ostatecznie
    nie wszedł: uczestnik dostawałby potwierdzenie terminu, którego nie ma w bazie.
    """
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(), meeting_url="https://meet.example/abc")

    with django_capture_on_commit_callbacks(execute=True):
        book_slot(qualified, slot)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [qualified.user.email]
    # Temat sprawdzamy co do znaku: „nazwiska nie ma” przechodzi także dla tematu, w którym nie ma
    # nic, a asercja ma pilnować również tego, że uczestnik w ogóle rozpozna, czego list dotyczy.
    assert message.subject == f"Termin rozmowy kwalifikacyjnej: {stage.display_name}"
    # List bywa czytany na cudzym ekranie (rzutnik w szkole, telefon na ławce): ani temat, ani
    # nagłówek treści nie mogą wypisywać, kto jest adresatem – on i tak wie.
    header = message.body.splitlines()[0]
    for value in (PII_FIRST_NAME, PII_LAST_NAME, PII_SCHOOL, qualified.user.email):
        assert value not in message.subject
        assert value not in header
    assert "https://meet.example/abc" in message.body


def test_audit_of_interviews_never_carries_personal_data(stage, qualified, coordinator_user):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    other = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(11))
    spare = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(13))
    create_slots(stage, coordinator_user, starts_at=_tomorrow(15), duration_minutes=20)
    book_slot(qualified, slot)
    book_slot(qualified, other)
    cancel_booking(qualified, stage=stage)
    delete_slot(spare, coordinator_user)

    entries = AuditLog.objects.filter(action__in=INTERVIEW_ACTIONS)
    assert entries.count() == 5
    # Tokeny są celowo nietypowe (patrz ``PII_*``): gdyby uczestnik nazywał się „Jan Kowalski”,
    # ta sama pętla przechodziłaby również nad diffem, w którym nazwisko naprawdę siedzi.
    forbidden = (
        PII_FIRST_NAME,
        PII_LAST_NAME,
        PII_SCHOOL,
        qualified.user.email,
        qualified.public_code,
    )
    for entry in entries:
        dumped = str(entry.diff)
        assert not any(value and value in dumped for value in forbidden)


# --- odczyt dla ekranów -----------------------------------------------------------------------------


def test_rows_for_participant_mark_own_and_full_slots(stage, qualified):
    mine = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9), capacity=1)
    full = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(11), capacity=1)
    book_slot(qualified, mine)
    other = ParticipantFactory()
    StageEntryFactory(participant=other, stage=stage, status=StageEntryStatus.QUALIFIED)
    book_slot(other, full)

    rows = {row["slot"].pk: row for row in slots_for_participant(stage, qualified)}

    assert rows[mine.pk]["is_mine"] is True
    assert rows[mine.pk]["bookable"] is False
    assert rows[full.pk]["free"] == 0
    assert rows[full.pk]["bookable"] is False
    assert rows[full.pk]["reason"] == "full"


def test_rows_explain_why_a_slot_is_not_bookable(stage, qualified):
    """Powód odmowy jest w wierszu, żeby panel nie pisał „Brak miejsc” pod każdym blokadą.

    Uczestnik, którego rozmowa właśnie trwa, ma pod pozostałymi terminami przeczytać, że nie da
    się jej już przenieść – a nie że w całej olimpiadzie zabrakło miejsc.
    """
    started = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    book_slot(qualified, started)
    InterviewSlot.objects.filter(pk=started.pk).update(
        starts_at=timezone.now() - timedelta(minutes=5), ends_at=timezone.now() + timedelta(minutes=15)
    )
    later = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(12))

    rows = {row["slot"].pk: row for row in slots_for_participant(stage, qualified)}

    assert rows[later.pk]["reason"] == "locked"
    assert rows[later.pk]["bookable"] is False
    # Własny, trwający termin zostaje na liście bez powodu odmowy – szablon podpisuje go „Twój
    # termin”, a nie komunikatem o blokadzie.
    assert rows[started.pk]["is_mine"] is True
    assert rows[started.pk]["reason"] is None


def test_rows_of_a_closed_stage_are_all_blocked(stage, qualified):
    InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    Stage.objects.filter(pk=stage.pk).update(closed_at=timezone.now())
    stage.refresh_from_db()

    row = slots_for_participant(stage, qualified)[0]

    assert row["reason"] == "closed"
    assert row["bookable"] is False


def test_rows_mark_a_slot_that_already_started(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    InterviewSlot.objects.filter(pk=slot.pk).update(
        starts_at=timezone.now() - timedelta(minutes=5), ends_at=timezone.now() + timedelta(minutes=15)
    )

    row = slots_for_participant(stage, qualified)[0]

    assert row["started"] is True
    assert row["reason"] == "started"
    assert row["bookable"] is False


def test_rows_for_coordinator_carry_the_bookings(stage, qualified):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, slot)

    rows = slots_for_coordinator(stage)

    assert rows[0].taken == 1
    booking = rows[0].bookings.all()[0]
    assert booking.entry.participant.public_code == qualified.public_code


# --- styk z resztą domeny ----------------------------------------------------------------------------


def test_uploading_to_an_interview_stage_is_refused(stage, qualified):
    """Zadania w takim etapie nie powstają, ale nawet gdyby powstały – upload odmawia."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.submissions.services import create_submission

    problem = ProblemFactory(stage=stage, number=1)

    with pytest.raises(DomainError) as exc:
        create_submission(
            user=qualified.user,
            stage=stage,
            problem_number=problem.number,
            upload=SimpleUploadedFile("praca.pdf", b"%PDF-1.4 tresc"),
        )

    assert exc.value.machine_code == "STAGE_NOT_ACCEPTING_FILES"


def test_creating_a_problem_on_an_interview_stage_is_refused(stage, coordinator_user):
    from apps.competitions.services import create_problem

    with pytest.raises(DomainError) as exc:
        create_problem(stage=stage, actor=coordinator_user, number=1, title="Zadanie")

    assert exc.value.machine_code == "STAGE_NOT_ACCEPTING_PROBLEMS"


def test_format_cannot_change_once_there_are_bookings(stage, qualified, coordinator_user):
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow())
    book_slot(qualified, slot)

    with pytest.raises(DomainError) as exc:
        update_stage(stage, coordinator_user, format=StageFormat.SUBMISSIONS)

    stage.refresh_from_db()
    assert exc.value.machine_code == "STAGE_FORMAT_LOCKED"
    assert stage.format == StageFormat.INTERVIEW


def test_format_cannot_change_when_the_stage_already_has_problems(coordinator_user):
    """Etap w formie rozmowy zadań mieć nie może – zmiana formy zostawiłaby arkusz-sierotę.

    Zadanie zostałoby w bazie, zniknęło z panelu (ekran zadań ustępuje ekranowi terminów) i nie
    dałoby się go już usunąć przez interfejs.
    """
    written = StageFactory(edition=CurrentEditionFactory())
    ProblemFactory(stage=written, number=1)

    with pytest.raises(DomainError) as exc:
        update_stage(written, coordinator_user, format=StageFormat.INTERVIEW)

    written.refresh_from_db()
    assert exc.value.machine_code == "STAGE_FORMAT_LOCKED"
    assert written.format == StageFormat.SUBMISSIONS


@pytest.mark.parametrize("field", ["opens_at", "deadline_at"])
def test_stage_window_cannot_shrink_under_an_existing_slot(stage, coordinator_user, field):
    """Okno rozmów **jest** oknem etapu, więc zawężenie go musi się rozbić o wyznaczone terminy.

    Inaczej koordynator przesuwałby deadline o dzień i zostawiał uczestnika z potwierdzeniem
    mailowym na godzinę, której nie ma już w harmonogramie.
    """
    slot = InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    shrunk = {
        "opens_at": {"opens_at": slot.starts_at + timedelta(hours=1)},
        "deadline_at": {"deadline_at": slot.starts_at - timedelta(hours=1)},
    }[field]

    with pytest.raises(DomainError) as exc:
        update_stage(stage, coordinator_user, **shrunk)

    stage.refresh_from_db()
    assert exc.value.machine_code == "STAGE_WINDOW_HAS_SLOTS"
    assert exc.value.status_code == 409
    assert getattr(stage, field) != shrunk[field]


def test_stage_window_may_still_be_widened_with_slots_in_place(stage, coordinator_user):
    """Rozszerzenie okna nikomu terminu nie zabiera – i ma przechodzić."""
    InterviewSlotFactory(stage=stage, starts_at=_tomorrow(9))
    widened = stage.deadline_at + timedelta(days=7)

    update_stage(stage, coordinator_user, deadline_at=widened)

    stage.refresh_from_db()
    assert stage.deadline_at == widened
