"""Kryterium 4 T-07: anonimizacja snapshotu (RODO) i ponowna publikacja."""

import json

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.models import QualificationMode, StageEntryStatus, StageKind
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.models import Anonymization, ResultsPublication
from apps.results.services import ADULT_AGE, MIN_SCHOOL_GROUP, publish_results

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

FIRST_NAME = "Zenobia"
LAST_NAME = "Niepublikowalna"
EMAIL = "zenobia.tajna@example.test"
SCHOOL = "XIV LO Warszawa"
CROWDED_SCHOOL = "I LO Gdańsk"
FULL_NAME = f"{FIRST_NAME} {LAST_NAME}"


def minor_year() -> int:
    """Rok urodzenia osoby na pewno niepełnoletniej w rozumieniu ``ADULT_AGE``."""
    return timezone.now().year - ADULT_AGE + 2


def adult_year() -> int:
    return timezone.now().year - ADULT_AGE - 1


def consenting_participant(*, publish_full_name: bool, guardian_consent: bool = True, **kwargs):
    """Uczestnik o rozpoznawalnych danych – łatwo sprawdzić, czy nie wyciekły do snapshotu."""
    user = UserFactory(email=EMAIL, first_name=FIRST_NAME, last_name=LAST_NAME)
    return ParticipantFactory(
        user=user,
        school=kwargs.pop("school", SCHOOL),
        district="mazowieckie",
        birth_year=kwargs.pop("birth_year", minor_year()),
        guardian_consent=guardian_consent,
        publish_full_name=publish_full_name,
        **kwargs,
    )


def make_final(**kwargs):
    """Finał – jedyny etap, w którym wolno w ogóle rozważać tryb ``FULL``."""
    return make_stage(kind=StageKind.FINAL, **kwargs)


def fill_school(stage, school: str, count: int = MIN_SCHOOL_GROUP - 1) -> None:
    """Dopełnia szkołę do progu k-anonimowości – inicjały ze szkołą wymagają grupy."""
    for _ in range(count):
        graded_entry(stage, [2], school=school)


def publish(stage, anonymization, actor=None):
    return publish_results(stage, actor, anonymization)


def test_code_snapshot_has_no_personal_data():
    """4. Snapshot CODE: wyłącznie pseudonim – bez imienia, nazwiska, e-maila, szkoły i id."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))

    publication = publish(stage, Anonymization.CODE)

    raw = json.dumps(publication.snapshot, ensure_ascii=False)
    for secret in (FIRST_NAME, LAST_NAME, EMAIL, SCHOOL, "2009"):
        assert secret not in raw
    assert publication.snapshot[0]["display"] == entry.participant.public_code
    assert set(publication.snapshot[0]) == {
        "rank",
        "display",
        "district",
        "points",
        "total",
        "qualified",
    }


def test_full_snapshot_needs_participant_consent():
    """4. FULL (finał): nazwisko tylko przy ``publish_full_name=True``; bez zgody zostaje pseudonim."""
    stage = make_final(problems=1)
    consenting = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    refusing = graded_entry(stage, [5], participant=ParticipantFactory(publish_full_name=False))

    publication = publish(stage, Anonymization.FULL)

    displays = {row["rank"]: row["display"] for row in publication.snapshot}
    assert displays[1] == FULL_NAME
    assert displays[2] == refusing.participant.public_code
    assert consenting.participant.public_code not in json.dumps(publication.snapshot)
    assert EMAIL not in json.dumps(publication.snapshot, ensure_ascii=False)


def test_full_anonymization_is_rejected_outside_the_final():
    """1 (przegląd). FULL poza finałem → 400 i żadnej publikacji: to nie jest tabela laureatów."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))

    with pytest.raises(DomainError) as error:
        publish(stage, Anonymization.FULL)

    assert error.value.machine_code == "ANONYMIZATION_NOT_ALLOWED_FOR_STAGE"
    assert error.value.status_code == 400
    assert not ResultsPublication.objects.exists()


def test_full_name_only_for_laureates_of_the_final():
    """1 (przegląd). Nazwisko dostaje laureat; finalista poniżej progu zostaje pod pseudonimem."""
    stage = make_final(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    laureate = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    beaten = graded_entry(
        stage,
        [2],
        participant=ParticipantFactory(
            user=UserFactory(first_name="Bogdan", last_name="Przegrany"),
            publish_full_name=True,
            guardian_consent=True,
        ),
    )

    publication = publish(stage, Anonymization.FULL)

    displays = {row["rank"]: row["display"] for row in publication.snapshot}
    assert displays[1] == FULL_NAME
    assert displays[2] == beaten.participant.public_code
    assert "Przegrany" not in json.dumps(publication.snapshot, ensure_ascii=False)
    assert laureate.participant.public_code not in json.dumps(publication.snapshot)


def test_minor_without_guardian_consent_stays_anonymous():
    """1 (przegląd). Zgoda małoletniego sama nie wystarcza – potrzebna zgoda opiekuna."""
    stage = make_final(problems=1)
    minor = graded_entry(
        stage,
        [6],
        participant=consenting_participant(
            publish_full_name=True, guardian_consent=False, birth_year=minor_year()
        ),
    )

    publication = publish(stage, Anonymization.FULL)

    assert publication.snapshot[0]["display"] == minor.participant.public_code
    assert LAST_NAME not in json.dumps(publication.snapshot, ensure_ascii=False)


def test_adult_participant_does_not_need_a_guardian():
    """1 (przegląd). Pełnoletni (wg roku urodzenia) decyduje o publikacji nazwiska sam."""
    stage = make_final(problems=1)
    graded_entry(
        stage,
        [6],
        participant=consenting_participant(
            publish_full_name=True, guardian_consent=False, birth_year=adult_year()
        ),
    )

    publication = publish(stage, Anonymization.FULL)

    assert publication.snapshot[0]["display"] == FULL_NAME


def test_initials_school_snapshot():
    """4. INITIALS_SCHOOL: „Z.N., XIV LO Warszawa” – bez pełnego imienia i nazwiska."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    fill_school(stage, SCHOOL)

    publication = publish(stage, Anonymization.INITIALS_SCHOOL)

    assert publication.snapshot[0]["display"] == f"Z.N., {SCHOOL}"
    raw = json.dumps(publication.snapshot, ensure_ascii=False)
    assert FIRST_NAME not in raw
    assert LAST_NAME not in raw
    assert EMAIL not in raw


def test_initials_school_falls_back_to_the_code_below_the_k_anonymity_threshold():
    """7 (przegląd). Inicjały ze szkołą w grupie mniejszej niż trzy osoby wskazują konkretną osobę."""
    stage = make_stage(problems=1)
    alone = graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    # Razem z „alone” w XIV LO są dwie osoby – o jedną za mało, żeby inicjały cokolwiek ukryły.
    fill_school(stage, SCHOOL, count=MIN_SCHOOL_GROUP - 2)
    for _ in range(MIN_SCHOOL_GROUP):
        graded_entry(stage, [5], school=CROWDED_SCHOOL)

    publication = publish(stage, Anonymization.INITIALS_SCHOOL)

    displays = {row["total"]: row["display"] for row in publication.snapshot}
    assert displays[6] == alone.participant.public_code
    assert displays[5] == f"J.K., {CROWDED_SCHOOL}"
    assert SCHOOL not in json.dumps(publication.snapshot, ensure_ascii=False)


def test_district_is_published_only_next_to_pseudonyms():
    """7 (przegląd). Okręg zostaje wyłącznie w tabeli CODE; przy inicjałach i nazwiskach znika."""
    stage = make_final(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    fill_school(stage, SCHOOL)

    by_code = publish(stage, Anonymization.CODE).snapshot
    by_initials = publish(stage, Anonymization.INITIALS_SCHOOL).snapshot
    by_name = publish(stage, Anonymization.FULL).snapshot

    assert by_code[0]["district"] == "mazowieckie"
    assert "district" not in by_initials[0]
    assert "district" not in by_name[0]


def test_snapshot_carries_points_ranking_and_qualification():
    """Snapshot ma komplet danych tabeli: miejsce, punkty per zadanie, sumę i kwalifikację."""
    stage = make_stage(problems=2, mode=QualificationMode.MIN_POINTS, min_points=8)
    graded_entry(stage, [6, 5])
    graded_entry(stage, [2, 2])

    publication = publish(stage, Anonymization.CODE)

    first, second = publication.snapshot
    assert first["rank"] == 1
    assert first["points"] == {"1": 6, "2": 5}
    assert first["total"] == 11
    assert first["qualified"] is True
    assert second["qualified"] is False


def test_publishing_sets_stage_marker_and_qualification_statuses():
    """Publikacja przelicza, kwalifikuje i stawia znacznik ``Stage.results_published_at``."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    entry = graded_entry(stage, [6])

    publication = publish(stage, Anonymization.CODE)

    stage.refresh_from_db()
    entry.refresh_from_db()
    assert stage.results_published_at == publication.published_at
    assert entry.status == StageEntryStatus.QUALIFIED
    assert entry.total_points == 6


def test_republish_overwrites_snapshot_and_leaves_audit_without_personal_data():
    """Ponowna publikacja nadpisuje ten sam rekord i zostawia ślad w audycie bez danych osobowych."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], participant=consenting_participant(publish_full_name=True))
    fill_school(stage, SCHOOL)

    publish(stage, Anonymization.CODE)
    publication = publish(stage, Anonymization.INITIALS_SCHOOL)

    assert ResultsPublication.objects.count() == 1
    assert publication.anonymization == Anonymization.INITIALS_SCHOOL
    assert publication.snapshot[0]["display"] == f"Z.N., {SCHOOL}"
    entries = AuditLog.objects.filter(action="results.published").order_by("id")
    assert entries.count() == 2
    assert entries.last().diff["republished"] is True
    raw = json.dumps([entry.diff for entry in entries], ensure_ascii=False)
    for secret in (FIRST_NAME, LAST_NAME, EMAIL, SCHOOL):
        assert secret not in raw


def test_unknown_anonymization_is_rejected():
    """Tryb anonimizacji spoza listy → 400, zanim cokolwiek zostanie opublikowane."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    with pytest.raises(DomainError) as error:
        publish(stage, "PESEL")

    assert error.value.machine_code == "INVALID_ANONYMIZATION"
    assert error.value.status_code == 400
    assert not ResultsPublication.objects.exists()


def test_publishing_before_the_appeal_window_closes_is_rejected():
    """4 (przegląd). Publikacja przed zamknięciem reklamacji → 409 i nic w bazie."""
    stage = make_stage(problems=1, appeals_open=True)
    graded_entry(stage, [6])

    with pytest.raises(DomainError) as error:
        publish(stage, Anonymization.CODE)

    assert error.value.machine_code == "APPEAL_WINDOW_OPEN"
    assert error.value.status_code == 409
    assert not ResultsPublication.objects.exists()
    stage.refresh_from_db()
    assert stage.results_published_at is None
