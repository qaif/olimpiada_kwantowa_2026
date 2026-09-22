"""Serwis zarządzania cudzym kontem: ``update_account_by_coordinator``, ``delete_account_by_coordinator``.

Warstwa WWW ma własny komplet testów (``apps/web/tests/test_coordinator_accounts.py``); tutaj
stoją reguły, których z formularza nie da się wywołać, a które muszą obowiązywać każde wejście –
także przyszłe API i komendę CLI:

- konta koordynatora i superużytkownika są **nietykalne** z panelu,
- koordynator nie kasuje własnego konta tą drogą (do tego jest ``/account/delete/``
  z potwierdzeniem tożsamości),
- odwieszenie członka komitetu wraca do kompletu grup, choć ``approve_committee_member``
  świadomie odmawia takiego przejścia,
- ``delete_own_account`` zachowuje się dokładnie tak, jak przed wydzieleniem wspólnego rdzenia.
"""

import pytest

from apps.accounts.models import CommitteeStatus, User, Voivodeship
from apps.accounts.profile import (
    delete_account_by_coordinator,
    delete_own_account,
    update_account_by_coordinator,
)
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    return CoordinatorFactory(email="koordynator@example.test")


# --- konta chronione ----------------------------------------------------------------------------


def test_a_coordinator_account_cannot_be_edited_from_the_panel(actor):
    target = CoordinatorFactory(email="drugi@example.test")

    with pytest.raises(DomainError) as exc:
        update_account_by_coordinator(target, actor=actor, account={"first_name": "Podmiana"})

    assert exc.value.machine_code == "COORDINATOR_PROTECTED"
    target.refresh_from_db()
    assert target.first_name != "Podmiana"


def test_a_superuser_account_is_protected_even_without_the_coordinator_group(actor):
    target = UserFactory(is_superuser=True, is_staff=True)

    with pytest.raises(DomainError) as exc:
        delete_account_by_coordinator(target, actor=actor)

    assert exc.value.machine_code == "COORDINATOR_PROTECTED"
    assert User.objects.filter(pk=target.pk).exists()


def test_the_coordinator_does_not_delete_their_own_account_this_way(actor):
    with pytest.raises(DomainError) as exc:
        delete_account_by_coordinator(actor, actor=actor)

    assert exc.value.machine_code == "SELF_DELETE"
    assert User.objects.filter(pk=actor.pk).exists()


# --- zakres pól ---------------------------------------------------------------------------------


def test_participant_fields_are_refused_for_an_account_without_that_profile(actor):
    target = UserFactory()

    with pytest.raises(DomainError) as exc:
        update_account_by_coordinator(target, actor=actor, account={}, participant={"grade": 3})

    assert exc.value.machine_code == "NO_PARTICIPANT_PROFILE"


def test_committee_fields_are_refused_for_an_account_without_that_profile(actor):
    participant = ParticipantFactory()

    with pytest.raises(DomainError) as exc:
        update_account_by_coordinator(
            participant.user, actor=actor, account={}, committee={"status": CommitteeStatus.ACTIVE}
        )

    assert exc.value.machine_code == "NO_COMMITTEE_PROFILE"


def test_an_unknown_committee_status_is_refused(actor):
    member = ActiveReviewerFactory()

    with pytest.raises(DomainError) as exc:
        update_account_by_coordinator(
            member.user, actor=actor, account={}, committee={"status": "ZAWIESZONY"}
        )

    assert exc.value.machine_code == "STATUS_INVALID"


def test_suspending_and_reinstating_a_reviewer_restores_the_groups(actor):
    """Odwieszenie idzie poza ``approve_committee_member`` – ta odmawia wszystkiego poza PENDING.

    Grupy nadaje ten sam helper, co zatwierdzenie, więc „co dostaje recenzent” zostaje jedną
    definicją. Zawieszenie grup nie zdejmuje celowo: prawo do recenzowania rozstrzyga status
    profilu, a zdjęte grupy trzeba by przy odwieszeniu zgadywać z powrotem.
    """
    member = ActiveReviewerFactory(is_appeals_committee=True)

    update_account_by_coordinator(
        member.user, actor=actor, account={}, committee={"status": CommitteeStatus.SUSPENDED}
    )
    member.refresh_from_db()
    assert member.status == CommitteeStatus.SUSPENDED

    update_account_by_coordinator(
        member.user, actor=actor, account={}, committee={"status": CommitteeStatus.ACTIVE}
    )
    member.refresh_from_db()
    assert member.status == CommitteeStatus.ACTIVE
    assert set(member.user.groups.values_list("name", flat=True)) >= {"reviewer", "appeals"}


def test_the_voivodeship_of_a_pending_member_is_settable_and_marked_as_verified(actor):
    """Tu, inaczej niż w ``verify_committee_district``, profil nie musi być ACTIVE.

    Koordynator zapisuje **całe konto** jednym formularzem – blokada „tylko aktywnemu” znaczyłaby,
    że województwa nie da się uzupełnić przy tej samej okazji, co zatwierdzenie.
    """
    member = ActiveReviewerFactory(status=CommitteeStatus.PENDING, district=None, district_verified=False)

    update_account_by_coordinator(
        member.user, actor=actor, account={}, committee={"district": Voivodeship.OPOLSKIE}
    )

    member.refresh_from_db()
    assert member.district == Voivodeship.OPOLSKIE
    assert member.district_verified is True


# --- usunięcie ----------------------------------------------------------------------------------


def test_the_anonymisation_is_attributed_to_the_coordinator_not_to_the_deleted_account(actor):
    from apps.competitions.tests.factories import StageEntryFactory

    participant = ParticipantFactory()
    StageEntryFactory(participant=participant)

    assert delete_account_by_coordinator(participant.user, actor=actor) == "anonymised"

    anonymised = AuditLog.objects.get(action="account.anonymised")
    assert anonymised.actor == actor
    decision = AuditLog.objects.get(action="account.deleted_by_coordinator")
    assert decision.diff == {"result": "anonymised", "had_footprint": True}


def test_deleting_an_account_without_a_footprint_leaves_only_the_audit_trail(actor):
    target = UserFactory()
    pk = target.pk

    assert delete_account_by_coordinator(target, actor=actor) == "deleted"

    assert not User.objects.filter(pk=pk).exists()
    entry = AuditLog.objects.get(action="account.deleted")
    assert entry.actor == actor
    assert entry.diff == {"user_id": pk}


# --- opiekun szkolny (B2, 22.09.2026) ------------------------------------------------------------


def _supervisor_with_confirmed_participation(*, email: str):
    """Konto opiekuna z jedną zgodą i jednym potwierdzeniem udziału szkoły w edycji."""
    from django.contrib.auth.models import Group

    from apps.accounts.consents import ConsentKind
    from apps.accounts.models import GROUP_SUPERVISOR, ConsentRecord, SchoolParticipation, SchoolSupervisor
    from apps.competitions.tests.factories import CurrentEditionFactory
    from apps.tenancy.tests.factories import current_or_default_competition

    user = UserFactory(email=email)
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    competition = current_or_default_competition()
    supervisor = SchoolSupervisor.objects.create(
        user=user, school="XIV LO", phone="600100200", competition=competition
    )
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.TERMS, document_version="1.0", source="web"
    )
    edition = CurrentEditionFactory(competition=competition)
    SchoolParticipation.objects.create(supervisor=supervisor, edition=edition)
    return supervisor


def test_deleting_a_pure_supervisor_with_a_confirmed_participation_is_anonymised(actor):
    """B2: bez tego SchoolParticipation nie liczyło się do śladu, więc konto szłoby w kasację."""
    from apps.accounts.models import SchoolParticipation, SchoolSupervisor

    supervisor = _supervisor_with_confirmed_participation(email="opiekun.usuwany@szkola.test")
    user = supervisor.user

    assert delete_account_by_coordinator(user, actor=actor) == "anonymised"

    user.refresh_from_db()
    assert user.is_active is False
    assert user.first_name == ""
    assert SchoolSupervisor.objects.filter(pk=supervisor.pk).exists()
    supervisor.refresh_from_db()
    assert supervisor.school == ""
    assert supervisor.phone == ""
    assert SchoolParticipation.objects.filter(supervisor=supervisor).exists()
    assert not supervisor.consents.filter(withdrawn_at__isnull=True).exists()


def test_deleting_a_supervisor_without_any_footprint_is_a_hard_delete(actor):
    """Bez potwierdzenia udziału ani jednej edycji nie ma czego chronić – tak samo jak u uczestnika."""
    from django.contrib.auth.models import Group

    from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor
    from apps.tenancy.tests.factories import current_or_default_competition

    user = UserFactory(email="opiekun.bez-sladu@szkola.test")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=current_or_default_competition())
    pk = user.pk

    assert delete_account_by_coordinator(user, actor=actor) == "deleted"
    assert not User.objects.filter(pk=pk).exists()


def test_deleting_a_dual_role_account_anonymises_both_profiles(actor):
    """Uczestnik, który jest też opiekunem: oba profile czyszczą się w jednym przebiegu."""
    from apps.accounts.consents import ConsentKind
    from apps.accounts.models import ConsentRecord, SchoolSupervisor
    from apps.competitions.tests.factories import StageEntryFactory
    from apps.tenancy.tests.factories import current_or_default_competition

    participant = ParticipantFactory()
    supervisor = SchoolSupervisor.objects.create(
        user=participant.user,
        school="I LO",
        phone="600300400",
        competition=current_or_default_competition(),
    )
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.TERMS, document_version="1.0", source="web"
    )
    StageEntryFactory(participant=participant)

    assert delete_account_by_coordinator(participant.user, actor=actor) == "anonymised"

    participant.refresh_from_db()
    supervisor.refresh_from_db()
    assert participant.school == "—"
    assert supervisor.school == ""
    assert supervisor.phone == ""
    assert not supervisor.consents.filter(withdrawn_at__isnull=True).exists()


def test_deleting_your_own_account_still_has_no_actor_in_the_audit():
    """Zachowanie ``delete_own_account`` po wydzieleniu wspólnego rdzenia – bez zmian.

    Wykonawcą jest konto, które właśnie znika: wpis o skasowaniu wiersza nie ma do czego przypiąć
    ``actor`` (zgasłby na ``SET_NULL`` sekundę później), a anonimizację przypisuje sobie samo konto.
    """
    user = UserFactory()
    pk = user.pk

    assert delete_own_account(user) == "deleted"

    entry = AuditLog.objects.get(action="account.deleted")
    assert entry.actor is None
    assert entry.diff == {"user_id": pk}
