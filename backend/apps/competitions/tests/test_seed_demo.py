"""T-03, kryterium 9: `manage.py seed_demo` jest idempotentny."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
    InvitationCode,
    Participant,
    User,
)
from apps.competitions.models import (
    Edition,
    Problem,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageKind,
)

COUNTED_MODELS = (
    User,
    Participant,
    CommitteeMember,
    InvitationCode,
    Edition,
    Stage,
    ScoringScale,
    QualificationRule,
    Problem,
    StageEntry,
)


def _counts() -> dict[str, int]:
    return {model.__name__: model.objects.count() for model in COUNTED_MODELS}


def _run_seed() -> str:
    out = StringIO()
    # DEBUG jest wyłączony w ustawieniach testowych, więc komenda wymaga świadomego --force.
    call_command("seed_demo", "--force", stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_kryterium_9_seed_demo_uruchomiony_dwa_razy_nie_duplikuje_danych():
    """9. Liczby obiektów po drugim uruchomieniu są równe tym po pierwszym."""
    first_output = _run_seed()
    after_first = _counts()

    second_output = _run_seed()
    after_second = _counts()

    assert after_second == after_first
    assert "Kod zaproszenia" in first_output
    # Kodu nie da się odtworzyć, więc drugi przebieg go nie tworzy (inaczej rósłby licznik).
    assert "Kod zaproszenia" not in second_output


@pytest.mark.django_db
def test_kryterium_9_seed_demo_tworzy_komplet_danych_demonstracyjnych():
    """9. Zakres z T-03: edycja bieżąca + 3 etapy + 3 zadania + 5 uczestników + 3 recenzentów."""
    _run_seed()

    edition = Edition.objects.get(is_current=True)
    assert set(edition.stages.values_list("kind", flat=True)) == {
        StageKind.ELIM,
        StageKind.DISTRICT,
        StageKind.FINAL,
    }
    elim = edition.stages.get(kind=StageKind.ELIM)
    assert elim.is_open_for_submissions() is True
    assert elim.problems.count() == 3
    # Każdy etap dostał domyślną skalę i próg – bo etapy tworzy serwis `create_stage`.
    assert ScoringScale.objects.count() == 3
    assert QualificationRule.objects.count() == 3
    assert elim.scoring_scale.allowed_values() == {0, 2, 5, 6}

    assert Participant.objects.count() == 5
    assert StageEntry.objects.filter(stage=elim).count() == 5
    assert User.objects.filter(email="uczestnik1@example.com").exists()

    reviewers = CommitteeMember.objects.all()
    assert reviewers.count() == 3
    assert all(member.status == CommitteeStatus.ACTIVE for member in reviewers)
    assert sorted(reviewers.values_list("district", flat=True)) == sorted(
        ["mazowieckie", "malopolskie", "mazowieckie"]
    )
    assert all(member.user.groups.filter(name=GROUP_REVIEWER).exists() for member in reviewers)

    coordinator = User.objects.get(email="koordynator@example.com")
    assert coordinator.is_superuser is True
    assert coordinator.groups.filter(name=GROUP_COORDINATOR).exists()
    assert InvitationCode.objects.filter(created_by=coordinator).count() == 1


@pytest.mark.django_db
def test_seed_demo_bez_force_odmawia_przy_debug_false():
    """Konta demo mają jawne hasło – poza DEBUG komenda wymaga świadomej decyzji."""
    with pytest.raises(CommandError):
        call_command("seed_demo", stdout=StringIO())

    assert Edition.objects.count() == 0
