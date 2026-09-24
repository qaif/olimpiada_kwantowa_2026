"""Wysyłka komunikatów (``apps.accounts.messaging``) – warstwa danych, bez ekranu.

Testy ekranu leżą w ``apps/web/tests/test_coordinator_messages.py``. Tutaj sprawdzamy reguły,
które decydują o tym, do kogo naprawdę pójdzie list: kto wchodzi do grupy, kto z niej wypada
i jak wysyłka dzieli się na porcje.
"""

import pytest
from django.core import mail

from apps.accounts.messaging import (
    RECIPIENT_CHUNK,
    parse_address_list,
    resolve_recipients,
    send_broadcast,
)
from apps.accounts.models import BroadcastGroup, BroadcastStatus, MessageBroadcast, Voivodeship
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    return StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)


def _participant(stage, email: str, *, status=StageEntryStatus.REGISTERED, **user_kwargs):
    participant = ParticipantFactory(user=UserFactory(email=email, **user_kwargs))
    StageEntryFactory(participant=participant, stage=stage, status=status)
    return participant


def test_address_list_accepts_commas_semicolons_and_newlines():
    text = " a@example.test ,b@example.test;\nc@example.test\n\n"

    assert parse_address_list(text) == ["a@example.test", "b@example.test", "c@example.test"]


def test_address_list_drops_duplicates_regardless_of_case():
    assert parse_address_list("A@example.test, a@example.test") == ["a@example.test"]


def test_edition_group_takes_participants_of_every_stage(stage):
    first = _participant(stage, "pierwszy@example.test")
    other_stage = StageFactory(edition=stage.edition, kind=StageKind.DISTRICT)
    second = _participant(other_stage, "drugi@example.test")

    recipients = resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition)

    assert recipients == sorted([first.user.email, second.user.email])


def test_blocked_and_unverified_accounts_are_excluded(stage):
    active = _participant(stage, "aktywny@example.test")
    _participant(stage, "zablokowany@example.test", is_active=False)
    _participant(stage, "niepotwierdzony@example.test", email_verified_at=None)

    recipients = resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition)

    assert recipients == [active.user.email]


def test_stage_group_ignores_other_stages(stage):
    here = _participant(stage, "tu@example.test")
    _participant(StageFactory(edition=stage.edition, kind=StageKind.FINAL), "gdzie-indziej@example.test")

    assert resolve_recipients(BroadcastGroup.STAGE_REGISTERED, stage=stage) == [here.user.email]


def test_qualified_group_takes_only_qualified_entries(stage):
    winner = _participant(stage, "awans@example.test", status=StageEntryStatus.QUALIFIED)
    _participant(stage, "bez-awansu@example.test", status=StageEntryStatus.NOT_QUALIFIED)

    assert resolve_recipients(BroadcastGroup.STAGE_QUALIFIED, stage=stage) == [winner.user.email]


def test_committee_group_skips_members_awaiting_approval():
    active = ActiveReviewerFactory()
    CommitteeMemberFactory()

    assert resolve_recipients(BroadcastGroup.COMMITTEE) == [active.user.email]


def test_committee_district_group_needs_a_voivodeship():
    ActiveReviewerFactory(district=Voivodeship.MAZOWIECKIE)

    assert resolve_recipients(BroadcastGroup.COMMITTEE_DISTRICT) == []


def test_group_without_its_context_resolves_to_nobody(stage):
    _participant(stage, "ktos@example.test")

    assert resolve_recipients(BroadcastGroup.STAGE_REGISTERED) == []
    assert resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS) == []


def test_unknown_group_resolves_to_nobody():
    assert resolve_recipients("NIE_MA_TAKIEJ") == []


def test_broadcast_is_split_into_chunks_and_every_letter_goes_out(django_capture_on_commit_callbacks):
    coordinator = CoordinatorFactory()
    recipients = [f"odbiorca{index}@example.test" for index in range(RECIPIENT_CHUNK + 5)]
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        broadcast = send_broadcast(
            group=BroadcastGroup.CUSTOM,
            subject="Temat",
            body="Treść",
            recipients=recipients,
            actor=coordinator,
        )

    broadcast.refresh_from_db()
    assert len(mail.outbox) == len(recipients)
    assert broadcast.sent_count == len(recipients)
    assert broadcast.status == BroadcastStatus.SENT
    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["chunks"] == 2


def test_register_keeps_the_content_but_never_the_addresses(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        send_broadcast(
            group=BroadcastGroup.CUSTOM,
            subject="Zmiana terminu",
            body="Etap rusza tydzień później.",
            recipients=["ktos@example.test"],
        )

    broadcast = MessageBroadcast.objects.get()
    log = AuditLog.objects.get(action="broadcast.sent")
    assert broadcast.body == "Etap rusza tydzień później."
    # Rejestr i audyt niosą liczniki, nigdy listy adresów.
    assert "ktos@example.test" not in str(log.diff)
    assert not hasattr(broadcast, "recipients")


# --- grupy z 24.09.2026: „wszyscy uczestnicy konkursu” i grupy z parametrem ------------------------
#
# Każda grupa ma tu dwa rodzaje testów: kto do niej **wchodzi** i kto z niej **wypada**. Drugi jest
# ważniejszy – list do niewłaściwej osoby jest nieodwracalny, a brak listu da się naprawić drugim.


#: Zdejmuje zawężenie do bieżącej edycji w testach, których przedmiotem jest coś innego (zakres
#: konkursu, szkoła, region) – edycja ma własne testy niżej.
ALL_EDITIONS = {"include_past_editions": True}


def _plain(email: str, **participant_kwargs):
    """Uczestnik bez żadnego wpisu do etapu – zarejestrowany i nic poza tym."""
    return ParticipantFactory(user=UserFactory(email=email), **participant_kwargs)


def test_all_participants_takes_profiles_without_any_stage_entry(competition, stage):
    with_entry = _participant(stage, "z-wpisem@example.test")
    without_entry = _plain("bez-wpisu@example.test")

    recipients = resolve_recipients(
        BroadcastGroup.ALL_PARTICIPANTS, competition=competition, edition=stage.edition
    )

    assert recipients == sorted([with_entry.user.email, without_entry.user.email])
    # Grupa edycyjna zostaje tym, czym była: wyłącznie osoby z wpisem.
    assert resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition) == [
        with_entry.user.email
    ]


def test_all_participants_skips_blocked_unverified_and_non_participants(competition):
    active = _plain("aktywny@example.test")
    ParticipantFactory(user=UserFactory(email="zablokowany@example.test", is_active=False))
    ParticipantFactory(user=UserFactory(email="niepotwierdzony@example.test", email_verified_at=None))
    # Konto bez profilu uczestnika (koordynator, recenzent) nie jest uczestnikiem.
    CoordinatorFactory(email="koordynator@example.test")
    ActiveReviewerFactory(user__email="recenzent@example.test")

    assert resolve_recipients(BroadcastGroup.ALL_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == [
        active.user.email
    ]


def test_all_participants_never_reaches_another_competition(competition, other_competition):
    here = _plain("tutaj@example.test")
    ParticipantFactory(user=UserFactory(email="sasiad@example.test"), competition=other_competition)

    assert resolve_recipients(BroadcastGroup.ALL_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == [
        here.user.email
    ]
    assert resolve_recipients(
        BroadcastGroup.ALL_PARTICIPANTS, competition=other_competition, **ALL_EDITIONS
    ) == ["sasiad@example.test"]


def _veteran(email: str, **participant_kwargs):
    """Uczestnik z konta założonego ponad rok temu – sprzed bieżącej edycji."""
    from datetime import timedelta

    from django.utils import timezone

    joined = timezone.now() - timedelta(days=400)
    return ParticipantFactory(user=UserFactory(email=email, date_joined=joined), **participant_kwargs)


def test_edition_scoped_groups_default_to_the_current_edition(competition, stage):
    """Domyślnie: wpis do etapu bieżącej edycji **albo** konto założone w tej edycji. Nic więcej."""
    from apps.competitions.tests.factories import EditionFactory

    returning = _veteran("wraca@example.test", school="LO nr 1")
    StageEntryFactory(participant=returning, stage=stage)
    newcomer = _plain("nowy@example.test", school="LO nr 1")
    _veteran("zeszloroczny@example.test", school="LO nr 1")
    old_stage = StageFactory(edition=EditionFactory(), kind=StageKind.ELIM)
    StageEntryFactory(
        participant=_veteran("tylko-dawny-etap@example.test", school="LO nr 1"), stage=old_stage
    )

    current = sorted([returning.user.email, newcomer.user.email])
    everyone = sorted([*current, "zeszloroczny@example.test", "tylko-dawny-etap@example.test"])
    for group, extra in (
        (BroadcastGroup.ALL_PARTICIPANTS, {}),
        (BroadcastGroup.REGION_PARTICIPANTS, {"district": Voivodeship.MAZOWIECKIE}),
        (BroadcastGroup.SCHOOL_PARTICIPANTS, {"school": "name:LO nr 1"}),
        (BroadcastGroup.GRADE_PARTICIPANTS, {"grade": 3}),
    ):
        kwargs = {"competition": competition, "edition": stage.edition, **extra}
        assert resolve_recipients(group, **kwargs) == current, group
        assert resolve_recipients(group, **kwargs, include_past_editions=True) == everyone, group


def test_without_a_current_edition_only_the_past_editions_switch_reaches_anyone(competition):
    person = _plain("ktos@example.test")

    assert resolve_recipients(BroadcastGroup.ALL_PARTICIPANTS, competition=competition) == []
    assert resolve_recipients(BroadcastGroup.ALL_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == [
        person.user.email
    ]


def test_one_person_in_several_participations_gets_one_letter(competition, other_competition, stage):
    """Jedno konto, profil w dwóch konkursach i wpisy do dwóch etapów – jeden adres, raz."""
    person = UserFactory(email="Wielokrotny@Example.test")
    mine = ParticipantFactory(user=person)
    ParticipantFactory(user=person, competition=other_competition)
    StageEntryFactory(participant=mine, stage=stage)
    StageEntryFactory(participant=mine, stage=StageFactory(edition=stage.edition, kind=StageKind.FINAL))

    assert resolve_recipients(
        BroadcastGroup.ALL_PARTICIPANTS, competition=competition, edition=stage.edition
    ) == ["wielokrotny@example.test"]
    assert resolve_recipients(BroadcastGroup.EDITION_PARTICIPANTS, edition=stage.edition) == [
        "wielokrotny@example.test"
    ]


def test_stage_from_another_competition_resolves_to_nobody(competition, other_competition):
    foreign_stage = StageFactory(competition=other_competition)
    StageEntryFactory(competition=other_competition, stage=foreign_stage)

    for group in (
        BroadcastGroup.STAGE_REGISTERED,
        BroadcastGroup.STAGE_QUALIFIED,
        BroadcastGroup.STAGE_NO_SUBMISSION,
    ):
        assert resolve_recipients(group, competition=competition, stage=foreign_stage) == []


def test_no_submission_group_takes_only_entries_without_a_valid_submission(competition, stage):
    from apps.submissions.models import SubmissionStatus
    from apps.submissions.tests.factories import SubmissionFactory

    idle = _participant(stage, "nic-nie-wyslal@example.test")
    done = _participant(stage, "wyslal@example.test")
    SubmissionFactory(entry=done.stage_entries.get(), problem__stage=stage)
    infected = _participant(stage, "tylko-wirus@example.test")
    SubmissionFactory(
        entry=infected.stage_entries.get(), problem__stage=stage, status=SubmissionStatus.REJECTED_INFECTED
    )
    _participant(stage, "zdyskwalifikowany@example.test", status=StageEntryStatus.DISQUALIFIED)
    # Praca w **innym** etapie nie zwalnia z przypomnienia o tym.
    elsewhere = _participant(stage, "gdzie-indziej@example.test")
    other_stage = StageFactory(edition=stage.edition, kind=StageKind.FINAL)
    SubmissionFactory(
        entry=StageEntryFactory(participant=elsewhere, stage=other_stage), problem__stage=other_stage
    )

    recipients = resolve_recipients(BroadcastGroup.STAGE_NO_SUBMISSION, competition=competition, stage=stage)

    assert recipients == sorted([idle.user.email, infected.user.email, elsewhere.user.email])


def test_region_group_by_voivodeship(competition):
    here = _plain("mazowsze@example.test", district=Voivodeship.MAZOWIECKIE)
    _plain("pomorze@example.test", district=Voivodeship.POMORSKIE)

    recipients = resolve_recipients(
        BroadcastGroup.REGION_PARTICIPANTS, competition=competition, district="Mazowieckie", **ALL_EDITIONS
    )

    assert recipients == [here.user.email]
    assert (
        resolve_recipients(BroadcastGroup.REGION_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == []
    )


def test_region_group_by_custom_region_includes_profiles_from_before_the_flag(competition, other_competition):
    from apps.accounts.models import Region

    north = Region.objects.create(competition=competition, code="okreg-polnoc", name="Okręg Północ")
    tagged = _plain("z-regionem@example.test", region=north, district=Voivodeship.POMORSKIE)
    # Profil sprzed flagi: samo ``district`` równe kodowi regionu.
    legacy = _plain("sprzed-flagi@example.test", district="okreg-polnoc")
    _plain("inny-region@example.test", district=Voivodeship.MAZOWIECKIE)
    foreign = Region.objects.create(competition=other_competition, code="okreg-polnoc", name="Cudzy")

    assert resolve_recipients(
        BroadcastGroup.REGION_PARTICIPANTS, competition=competition, region=north, **ALL_EDITIONS
    ) == sorted([tagged.user.email, legacy.user.email])
    # Region cudzego konkursu przy zakresie tego konkursu – nikt.
    assert (
        resolve_recipients(
            BroadcastGroup.REGION_PARTICIPANTS, competition=competition, region=foreign, **ALL_EDITIONS
        )
        == []
    )


def test_school_group_tells_registry_schools_from_hand_typed_names(competition, other_competition):
    from apps.accounts.messaging import school_choices
    from apps.schools.tests.factories import SchoolFactory

    school = SchoolFactory(name="LO nr 1", city="Gdańsk")
    registered = _plain("z-wykazu@example.test", school="LO nr 1", school_ref=school)
    typed = _plain("recznie@example.test", school="LO nr 1")
    _plain("inna-szkola@example.test", school="LO nr 2")
    ParticipantFactory(
        user=UserFactory(email="sasiad@example.test"), competition=other_competition, school_ref=school
    )

    assert resolve_recipients(
        BroadcastGroup.SCHOOL_PARTICIPANTS, competition=competition, school=f"sio:{school.pk}", **ALL_EDITIONS
    ) == [registered.user.email]
    assert resolve_recipients(
        BroadcastGroup.SCHOOL_PARTICIPANTS, competition=competition, school="name:LO nr 1", **ALL_EDITIONS
    ) == [typed.user.email]
    assert (
        resolve_recipients(
            BroadcastGroup.SCHOOL_PARTICIPANTS, competition=competition, school="sio:abc", **ALL_EDITIONS
        )
        == []
    )
    assert (
        resolve_recipients(BroadcastGroup.SCHOOL_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == []
    )

    choices = school_choices(competition)
    assert (f"sio:{school.pk}", "LO nr 1, Gdańsk", 1) in choices
    assert ("name:LO nr 1", "LO nr 1 (nazwa wpisana ręcznie)", 1) in choices
    # Uczeń z drugiego konkursu nie dokłada się do licznika szkoły w tym konkursie.
    assert all(count == 1 for _, _, count in choices)


def test_grade_group(competition):
    from apps.accounts.messaging import grade_choices

    third = _plain("trzecia@example.test", grade=3)
    _plain("pierwsza@example.test", grade=1)

    assert resolve_recipients(
        BroadcastGroup.GRADE_PARTICIPANTS, competition=competition, grade=3, **ALL_EDITIONS
    ) == [third.user.email]
    assert (
        resolve_recipients(BroadcastGroup.GRADE_PARTICIPANTS, competition=competition, **ALL_EDITIONS) == []
    )
    assert grade_choices(competition) == [(1, "klasa 1"), (3, "klasa 3")]


def test_workshop_group_takes_attendees_of_this_competition_only(competition, other_competition):
    from apps.cms.models import WorkshopAttendance

    key = "2026-11-12-kubity-i-bramki"
    present = _plain("byla@example.test")
    _plain("nie-byla@example.test")
    WorkshopAttendance.objects.create(participant=present, workshop_key=key)
    other_key = _plain("inny-warsztat@example.test")
    WorkshopAttendance.objects.create(participant=other_key, workshop_key="2026-12-01-splatanie")
    foreign = ParticipantFactory(user=UserFactory(email="sasiad@example.test"), competition=other_competition)
    WorkshopAttendance.objects.create(participant=foreign, workshop_key=key)

    assert resolve_recipients(BroadcastGroup.WORKSHOP_ATTENDEES, competition=competition, workshop=key) == [
        present.user.email
    ]
    assert resolve_recipients(BroadcastGroup.WORKSHOP_ATTENDEES, competition=competition) == []


def _supervisor(email: str, competition, *, role: bool = True, **user_kwargs):
    from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor

    user = UserFactory(email=email, groups=[GROUP_SUPERVISOR] if role else [], **user_kwargs)
    return SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=competition)


def test_supervisors_group_takes_teachers_of_this_competition_with_the_role(competition, other_competition):
    teacher = _supervisor("nauczyciel@example.test", competition)
    _supervisor("bez-roli@example.test", competition, role=False)
    _supervisor("zablokowany@example.test", competition, is_active=False)
    _supervisor("sasiad@example.test", other_competition)
    _plain("uczen@example.test")

    assert resolve_recipients(BroadcastGroup.SUPERVISORS, competition=competition) == [teacher.user.email]


def test_supervisors_group_follows_memberships_when_they_are_enforced(competition):
    from apps.accounts.models import GROUP_SUPERVISOR
    from apps.tenancy.tests.factories import grant_membership

    competition.feature_flags = {**(competition.feature_flags or {}), "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])
    member = _supervisor("z-czlonkostwem@example.test", competition, role=False)
    grant_membership(member.user, competition, GROUP_SUPERVISOR)
    # Sama globalna grupa przy włączonych członkostwach roli nie daje (``has_role``).
    _supervisor("tylko-grupa@example.test", competition)

    assert resolve_recipients(BroadcastGroup.SUPERVISORS, competition=competition) == [member.user.email]


def test_committee_group_never_reaches_another_competition(competition, other_competition):
    mine = ActiveReviewerFactory()
    ActiveReviewerFactory(competition=other_competition)

    assert resolve_recipients(BroadcastGroup.COMMITTEE, competition=competition) == [mine.user.email]


def test_broadcast_records_the_competition_and_the_group_parameter(
    competition, other_competition, django_capture_on_commit_callbacks
):
    target = {"school": "sio:7", "label": "LO nr 1, Gdańsk"}
    with django_capture_on_commit_callbacks(execute=True):
        broadcast = send_broadcast(
            group=BroadcastGroup.SCHOOL_PARTICIPANTS,
            subject="Temat",
            body="Treść",
            recipients=["ktos@example.test"],
            competition=other_competition,
            target=target,
        )

    broadcast.refresh_from_db()
    assert broadcast.competition == other_competition
    assert broadcast.target == target
    assert broadcast.target_label == "LO nr 1, Gdańsk"
    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["target"] == target
    assert "ktos@example.test" not in str(log.diff)


def test_recent_broadcasts_are_scoped_to_one_competition(competition, other_competition):
    from apps.accounts.messaging import recent_broadcasts
    from apps.tenancy.tests.factories import create_scoped

    mine = create_scoped(MessageBroadcast, competition, group=BroadcastGroup.CUSTOM, subject="Nasz", body=".")
    create_scoped(MessageBroadcast, other_competition, group=BroadcastGroup.CUSTOM, subject="Cudzy", body=".")

    assert recent_broadcasts(competition) == [mine]
    assert recent_broadcasts(None) == []
