"""Scalanie zdublowanych kont uczestnika (ACC-MERGE-01) – serwis ``apps.accounts.merge``.

Czego pilnują te testy poza samym „przeniosło”:

- **reguły z tabeli** w ``docs/tasks/ACC-MERGE-01.md`` § 1 – etap z pracą, pusty wpis po obu
  stronach, zgody (dowody i projekcje), zaświadczenie (wersje i bieżące), obecność na warsztatach,
  forum, rozmowa z organizatorem, należności,
- **konflikt blokuje i niczego nie zmienia** – podgląd mówi, wykonanie odmawia w całości,
- **transakcyjność** – błąd przy usuwaniu kopii cofa przepięcia,
- **odmowy** – inna rola, inny konkurs, konto koordynatora, własne konto,
- **adres e-mail** – wolno wziąć adres kopii tylko od konta, które swojego nie potwierdziło,
- **audyt** bez danych osobowych.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.merge import merge_participants, plan_merge
from apps.accounts.models import ConsentRecord, User
from apps.accounts.tests.factories import (
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.cms.models import WorkshopAttendance
from apps.competitions.models import StageEntry
from apps.competitions.tests.factories import EditionFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.submissions.models import Submission
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db


def person(competition, *, email, logged_in=False, **kw):
    return ParticipantFactory(
        competition=competition,
        user__email=email,
        user__first_name="Anna",
        user__last_name="Nowak",
        user__last_login=timezone.now() if logged_in else None,
        school="LO nr 5 w Bielsku-Białej",
        **kw,
    )


@pytest.fixture
def pair(competition):
    """Konto docelowe (logowało się) i kopia z literówką w adresie."""
    keep = person(competition, email="anna.nowak@gmail.com", logged_in=True)
    drop = person(competition, email="anna.nowak@gmail.con")
    return keep, drop


@pytest.fixture
def actor():
    return CoordinatorFactory()


def merged(keep, drop, actor, **kw):
    return merge_participants(keep, drop, actor=actor, **kw)


# --- etapy i prace -------------------------------------------------------------------------------


def test_stage_entry_with_work_moves_to_the_target(competition, pair, actor):
    keep, drop = pair
    entry = StageEntryFactory(competition=competition, participant=drop)
    work = SubmissionFactory(competition=competition, entry=entry)

    result = merged(keep, drop, actor)

    entry.refresh_from_db()
    assert entry.participant_id == keep.pk
    assert Submission.objects.get(pk=work.pk).entry_id == entry.pk
    assert result.outcome == "deleted"
    assert not User.objects.filter(pk=drop.user_id).exists()
    assert {line.key: line.count for line in result.plan.moved}["competitions.StageEntry.participant"] == 1


def test_an_empty_entry_of_the_target_gives_way_to_the_entry_with_work(competition, pair, actor):
    keep, drop = pair
    stage = StageFactory(competition=competition)
    empty = StageEntryFactory(competition=competition, participant=keep, stage=stage)
    full = StageEntryFactory(competition=competition, participant=drop, stage=stage)
    SubmissionFactory(competition=competition, entry=full)

    merged(keep, drop, actor)

    assert not StageEntry.objects.filter(pk=empty.pk).exists()
    assert StageEntry.objects.get(pk=full.pk).participant_id == keep.pk


def test_an_empty_entry_of_the_copy_is_dropped_and_the_copy_is_erased_not_anonymised(
    competition, pair, actor
):
    keep, drop = pair
    stage = StageFactory(competition=competition)
    full = StageEntryFactory(competition=competition, participant=keep, stage=stage)
    SubmissionFactory(competition=competition, entry=full)
    empty = StageEntryFactory(competition=competition, participant=drop, stage=stage)

    result = merged(keep, drop, actor)

    assert not StageEntry.objects.filter(pk=empty.pk).exists()
    assert StageEntry.objects.get(pk=full.pk).participant_id == keep.pk
    # Pusty wpis kopii nie zostaje śladem w zawodach – kopia znika w całości.
    assert result.outcome == "deleted"


def test_work_in_the_same_stage_on_both_accounts_blocks_and_changes_nothing(competition, pair, actor):
    keep, drop = pair
    stage = StageFactory(competition=competition, name="Etap I")
    mine = StageEntryFactory(competition=competition, participant=keep, stage=stage)
    theirs = StageEntryFactory(competition=competition, participant=drop, stage=stage)
    SubmissionFactory(competition=competition, entry=mine)
    SubmissionFactory(competition=competition, entry=theirs)
    ConsentRecord.objects.create(participant=drop, kind="PRIVACY", source="web")

    plan = plan_merge(keep, drop, actor=actor)
    assert plan.blocked
    assert any("Etap I" in text for text in plan.conflicts)

    with pytest.raises(DomainError) as excinfo:
        merged(keep, drop, actor)

    assert excinfo.value.machine_code == "MERGE_BLOCKED"
    assert StageEntry.objects.get(pk=theirs.pk).participant_id == drop.pk
    assert ConsentRecord.objects.get(participant__isnull=False, kind="PRIVACY").participant_id == drop.pk
    assert User.objects.filter(pk=drop.user_id).exists()
    assert not AuditLog.objects.filter(action="account.merged").exists()


# --- zgody, zaświadczenie, warsztaty, forum, czat, należności ------------------------------------


def test_consent_records_move_and_projections_take_the_person_not_the_account(competition, actor):
    earlier = timezone.now() - timedelta(days=30)
    keep = person(
        competition, email="a@example.test", logged_in=True, guardian_consent=False, gdpr_consent_at=earlier
    )
    drop = person(
        competition, email="b@example.test", guardian_consent=True, guardian_email="mama@example.test"
    )
    ConsentRecord.objects.create(
        participant=drop, kind="GUARDIAN", source="web", given_by_email="mama@example.test"
    )
    ConsentRecord.objects.create(participant=keep, kind="PRIVACY", source="web")

    result = merged(keep, drop, actor)

    keep.refresh_from_db()
    assert ConsentRecord.objects.filter(participant=keep).count() == 2
    assert keep.guardian_consent is True
    assert keep.guardian_email == "mama@example.test"
    assert keep.gdpr_consent_at > earlier
    assert {"guardian_consent", "guardian_email", "gdpr_consent_at"} <= set(result.plan.profile_fields)


def _certificate(participant, edition, *, version, status, current, uploaded_at):
    from apps.student_status.models import StudentStatusCertificate

    return StudentStatusCertificate.objects.create(
        participant=participant,
        edition=edition,
        version=version,
        is_current=current,
        status=status,
        sha256="c" * 64,
        mime="application/pdf",
        size_bytes=1,
        uploaded_at=uploaded_at,
    )


def test_student_status_versions_are_appended_and_the_better_current_wins(competition, pair, actor):
    from apps.student_status.models import StudentStatusCertificate

    keep, drop = pair
    edition = EditionFactory(competition=competition)
    other_edition = EditionFactory(competition=competition)
    now = timezone.now()
    rejected = _certificate(keep, edition, version=1, status="REJECTED", current=True, uploaded_at=now)
    accepted = _certificate(
        drop, edition, version=1, status="ACCEPTED", current=True, uploaded_at=now - timedelta(days=3)
    )
    only_copy = _certificate(drop, other_edition, version=1, status="PENDING", current=True, uploaded_at=now)

    merged(keep, drop, actor)

    rows = {row.pk: row for row in StudentStatusCertificate.objects.filter(participant=keep)}
    assert set(rows) == {rejected.pk, accepted.pk, only_copy.pk}
    assert (rows[rejected.pk].version, rows[rejected.pk].is_current) == (1, False)
    assert (rows[accepted.pk].version, rows[accepted.pk].is_current) == (2, True)
    assert (rows[only_copy.pk].version, rows[only_copy.pk].is_current) == (1, True)


def test_workshop_attendance_collapses_duplicates(competition, pair, actor):
    keep, drop = pair
    WorkshopAttendance.objects.create(participant=keep, workshop_key="2026-10-01-splatanie")
    WorkshopAttendance.objects.create(participant=drop, workshop_key="2026-10-01-splatanie")
    WorkshopAttendance.objects.create(participant=drop, workshop_key="2026-11-05-kubity")

    merged(keep, drop, actor)

    assert sorted(
        WorkshopAttendance.objects.filter(participant=keep).values_list("workshop_key", flat=True)
    ) == [
        "2026-10-01-splatanie",
        "2026-11-05-kubity",
    ]


def test_forum_threads_posts_and_subscriptions_follow_the_person(competition, pair, actor):
    from apps.forum.models import ForumSubscription
    from apps.forum.tests.factories import ForumPostFactory, ForumThreadFactory

    keep, drop = pair
    thread = ForumThreadFactory(competition=competition, author=drop.user)
    post = ForumPostFactory(competition=competition, thread=thread, author=drop.user)
    ForumSubscription.objects.create(competition=competition, thread=thread, user=drop.user)
    ForumSubscription.objects.create(competition=competition, thread=thread, user=keep.user)

    merged(keep, drop, actor)

    thread.refresh_from_db()
    post.refresh_from_db()
    assert thread.author_id == keep.user_id
    assert post.author_id == keep.user_id
    assert ForumSubscription.objects.filter(thread=thread).count() == 1


def test_organizer_conversations_of_both_accounts_are_joined(competition, pair, actor):
    from apps.chat.models import Conversation, Message

    keep, drop = pair
    mine = Conversation.objects.create(competition=competition, kind="ORGANIZER", participant=keep)
    theirs = Conversation.objects.create(competition=competition, kind="ORGANIZER", participant=drop)
    Message.objects.create(conversation=mine, sender=keep.user, sender_role="PARTICIPANT", body="Dzień dobry")
    copy_message = Message.objects.create(
        conversation=theirs, sender=drop.user, sender_role="PARTICIPANT", body="Nie mogę się zalogować"
    )

    merged(keep, drop, actor)

    copy_message.refresh_from_db()
    assert copy_message.conversation_id == mine.pk
    assert copy_message.sender_id == keep.user_id
    assert not Conversation.objects.filter(pk=theirs.pk).exists()


def test_a_settled_fee_of_the_copy_replaces_an_untouched_fee_of_the_target(competition, pair, actor):
    from apps.tenancy.fees import FeeSchedule, ParticipantFee

    keep, drop = pair
    edition = EditionFactory(competition=competition)
    schedule = FeeSchedule.objects.create(
        competition=competition, edition=edition, name="Wpisowe", amount=Decimal(50)
    )
    untouched = ParticipantFee.objects.create(schedule=schedule, participant=keep, amount=Decimal(50))
    paid = ParticipantFee.objects.create(
        schedule=schedule, participant=drop, amount=Decimal(50), status="PAID", paid_at=timezone.now()
    )

    merged(keep, drop, actor)

    assert list(ParticipantFee.objects.filter(participant=keep).values_list("pk", flat=True)) == [paid.pk]
    assert not ParticipantFee.objects.filter(pk=untouched.pk).exists()


def test_two_settled_fees_from_one_schedule_block(competition, pair, actor):
    from apps.tenancy.fees import FeeSchedule, ParticipantFee

    keep, drop = pair
    edition = EditionFactory(competition=competition)
    schedule = FeeSchedule.objects.create(
        competition=competition, edition=edition, name="Wpisowe", amount=Decimal(50)
    )
    for participant in (keep, drop):
        ParticipantFee.objects.create(
            schedule=schedule,
            participant=participant,
            amount=Decimal(50),
            status="PAID",
            paid_at=timezone.now(),
        )

    assert any("Wpisowe" in text for text in plan_merge(keep, drop, actor=actor).conflicts)


# --- odmowy --------------------------------------------------------------------------------------


def test_an_account_with_another_role_is_refused(competition, pair, actor):
    keep, drop = pair
    CommitteeMemberFactory(competition=competition, user=drop.user)

    plan = plan_merge(keep, drop, actor=actor)

    assert any("inną rolę" in text for text in plan.refusals)
    with pytest.raises(DomainError):
        merged(keep, drop, actor)
    assert User.objects.filter(pk=drop.user_id).exists()


def test_a_coordinator_account_is_refused(competition, pair, actor):
    keep, drop = pair
    coordinator = CoordinatorFactory()
    profile = ParticipantFactory(competition=competition, user=coordinator)

    assert plan_merge(keep, profile, actor=actor).refusals


def test_own_account_is_refused(competition, pair):
    keep, drop = pair

    assert any("własne konto" in text for text in plan_merge(keep, drop, actor=drop.user).refusals)


def test_accounts_from_different_competitions_are_refused(competition, other_competition, pair, actor):
    keep, _drop = pair
    stranger = ParticipantFactory(competition=other_competition, user=UserFactory(email="obcy@example.test"))

    with pytest.raises(DomainError) as excinfo:
        merged(keep, stranger, actor)

    assert excinfo.value.machine_code == "MERGE_OTHER_COMPETITION"


# --- transakcyjność ------------------------------------------------------------------------------


def test_a_failure_while_deleting_the_copy_rolls_everything_back(competition, pair, actor, monkeypatch):
    from apps.accounts import profile

    keep, drop = pair
    entry = StageEntryFactory(competition=competition, participant=drop)

    def boom(*args, **kwargs):
        raise RuntimeError("awaria w trakcie usuwania kopii")

    monkeypatch.setattr(profile, "delete_account_by_coordinator", boom)

    with pytest.raises(RuntimeError):
        merged(keep, drop, actor)

    assert StageEntry.objects.get(pk=entry.pk).participant_id == drop.pk
    assert User.objects.filter(pk=drop.user_id).exists()
    assert not AuditLog.objects.filter(action="account.merged").exists()


# --- adres e-mail --------------------------------------------------------------------------------


def test_the_target_takes_the_confirmed_address_of_the_copy(competition, actor):
    keep = ParticipantFactory(
        competition=competition,
        user=UserFactory(email="anna.nowak@gmail.con", email_verified_at=None, is_active=False),
    )
    drop = ParticipantFactory(competition=competition, user=UserFactory(email="anna.nowak@gmail.com"))
    StageEntryFactory(competition=competition, participant=keep)

    assert plan_merge(keep, drop, actor=actor).can_take_email
    result = merged(keep, drop, actor, take_email=True)

    keep.user.refresh_from_db()
    assert result.email_taken
    assert keep.user.email == "anna.nowak@gmail.com"
    assert keep.user.email_verified_at is not None
    assert keep.user.is_active
    assert not User.objects.filter(pk=drop.user_id).exists()


def test_a_confirmed_target_keeps_its_address(competition, pair, actor):
    keep, drop = pair

    assert not plan_merge(keep, drop, actor=actor).can_take_email
    with pytest.raises(DomainError) as excinfo:
        merged(keep, drop, actor, take_email=True)

    assert excinfo.value.machine_code == "MERGE_EMAIL_NOT_ALLOWED"
    assert User.objects.get(pk=keep.user_id).email == "anna.nowak@gmail.com"


# --- audyt ---------------------------------------------------------------------------------------


def test_the_merge_is_audited_with_codes_and_counts_only(competition, pair, actor):
    keep, drop = pair
    StageEntryFactory(competition=competition, participant=drop)
    drop_code, drop_user_id = drop.public_code, drop.user_id

    merged(keep, drop, actor)

    entry = AuditLog.objects.get(action="account.merged")
    assert entry.actor_id == actor.pk
    assert entry.target_id == str(keep.user_id)
    assert entry.diff["kept"] == keep.public_code
    assert entry.diff["merged"] == drop_code
    assert entry.diff["merged_user_id"] == drop_user_id
    assert entry.diff["moved"]["competitions.StageEntry.participant"] == 1
    serialized = str(entry.diff)
    for personal in ("anna.nowak", "Anna", "Nowak", "Bielsku"):
        assert personal not in serialized
    deleted = AuditLog.objects.get(action="account.deleted_by_coordinator", target_id=str(drop_user_id))
    assert deleted.diff["via"] == "merge"


def test_a_copy_removed_in_the_meantime_is_a_clear_refusal(competition, pair, actor):
    """Drugi koordynator usunął kopię między podglądem a kliknięciem – odmowa, nie błąd serwera."""
    keep, drop = pair
    User.objects.filter(pk=drop.user_id).delete()

    with pytest.raises(DomainError) as excinfo:
        merged(keep, drop, actor)

    assert excinfo.value.machine_code == "MERGE_GONE"
