"""Poprawki po przeglądzie STAT-01: zagnieżdżenie (H1), konta usunięte (M1), profil z innego
konkursu (M2), zamrożona przynależność (M3), uczniowie wszystkich opiekunów szkoły (M4) i L1–L5."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.models import GROUP_SUPERVISOR, Voivodeship
from apps.accounts.profile import ANONYMISED_SCHOOL
from apps.accounts.supervisors import supervisor_profile
from apps.accounts.tests.factories import UserFactory
from apps.competitions.tests.factories import EditionFactory, StageFactory
from apps.school_stats import services
from apps.school_stats.models import FrozenMembership
from apps.schools.tests.factories import SchoolFactory

from .conftest import make_supervisor, publish, student

pytestmark = pytest.mark.django_db


def lines_of(data, index: int = 0) -> dict:
    return {line["key"]: line for line in data["comparisons"][index]["lines"]}


# --- H1: zagnieżdżenie ------------------------------------------------------------------------


def test_probe_six_mine_five_others_plus_one_in_region_hides_the_school(
    competition, edition, stage, school, supervisor
):
    """Przypadek z przeglądu: szkoła 6+5, województwo = szkoła + 1 obcy.

    Bez reguły zagnieżdżenia obie średnie są widoczne (dopełnienia 5 i 6), a ich różnica wyznacza
    wynik jednej osoby spoza szkoły.
    """
    mine = [student(stage, school=school, mine=True) for _ in range(6)]
    others = [student(stage, school=school) for _ in range(5)]
    lonely = student(stage, school_text="Technikum spoza wykazu", district=Voivodeship.MAZOWIECKIE)
    publish(stage, {**dict.fromkeys(mine, 10), **dict.fromkeys(others, 4), lonely: 19})

    lines = lines_of(services.supervisor_statistics(supervisor, competition, edition))

    assert lines["school"]["visible"] is False
    assert lines["school"]["mean"] is None
    assert lines["region"]["visible"] is True
    assert lines["all"]["visible"] is True
    # Województwo i całość to ten sam zbiór – różnica zero, więc obie zostają.
    assert lines["region"]["aggregate"].entries == lines["all"]["aggregate"].entries == 12


def test_probe_also_applies_to_progress_and_school_report(competition, edition, stage, school, supervisor):
    mine = [student(stage, school=school, mine=True) for _ in range(6)]
    others = [student(stage, school=school) for _ in range(5)]
    lonely = student(stage, school_text="Inna szkoła", district=Voivodeship.MAZOWIECKIE)
    publish(stage, {**dict.fromkeys(mine, 10), **dict.fromkeys(others, 4), lonely: 19})

    data = services.supervisor_statistics(supervisor, competition, edition)
    report = services.school_report(
        school, edition, own_participant_ids=[entry.participant.pk for entry in mine]
    )

    assert data["progress"]["series"]["school"] == [None]
    school_line = next(line for line in report["stages"][0]["lines"] if line["key"] == "school")
    assert school_line["visible"] is False


def test_coordinator_region_minus_visible_schools_must_be_safe(competition, edition, stage):
    first = SchoolFactory(voivodeship=Voivodeship.MAZOWIECKIE)
    second = SchoolFactory(voivodeship=Voivodeship.MAZOWIECKIE)
    a = [student(stage, school=first) for _ in range(5)]
    c = [student(stage, school=second) for _ in range(6)]
    b = [student(stage, school_text="Mała szkoła", district=Voivodeship.MAZOWIECKIE) for _ in range(2)]
    publish(stage, {**dict.fromkeys(a, 3), **dict.fromkeys(c, 5), **dict.fromkeys(b, 9)})

    data = services.coordinator_statistics(edition)
    cells = {row["group"].school_id: row["cells"][0] for row in data["ranking"]}

    # Województwo (13) − pokazane szkoły (5 + 6) = 2 osoby małej szkoły → najmniejsza pokazana ukryta.
    assert cells[first.pk]["mean"] is None and cells[first.pk]["hidden"] is True
    assert cells[second.pk]["mean"] == Decimal(5)
    (region,) = data["regions"]
    assert region["cell"]["mean"] is not None


# --- M1: konta po anonimizacji --------------------------------------------------------------------


def erased(stage, **kw):
    """Uczeń po anonimizacji konta – znacznik adresu i kreska w szkole jak w ``anonymise_account``."""
    return student(
        stage,
        school_text=ANONYMISED_SCHOOL,
        user=UserFactory(email=f"usuniete-{uuid.uuid4().hex[:8]}@invalid.test"),
        **kw,
    )


def test_anonymised_accounts_do_not_form_a_school_and_are_not_counted(competition, edition, stage, school):
    student(stage, school=school)
    erased(stage)
    erased(stage)

    summary = services.edition_summary(edition)
    data = services.coordinator_statistics(edition)

    assert set(summary.groups) == {f"s{school.pk}"}
    assert summary.participants == 1
    assert [row["group"].school_id for row in data["ranking"]] == [school.pk]
    assert summary.stages[0].all.entries == 3  # w całości zostają – jako agregat


def test_school_whose_only_student_was_erased_is_not_a_lost_school(competition, school):
    previous = EditionFactory(competition=competition)
    previous_stage = StageFactory(edition=previous)
    current = EditionFactory(competition=competition)
    StageFactory(edition=current)
    entry = student(previous_stage, school=school)
    participant = entry.participant
    participant.user.email = "usuniete-x@invalid.test"
    participant.user.save(update_fields=["email"])
    participant.school, participant.school_ref = ANONYMISED_SCHOOL, None
    participant.save(update_fields=["school", "school_ref"])

    assert services.coordinator_statistics(current)["lost"] == []


# --- M2: profil opiekuna z innego konkursu ---------------------------------------------------------


def test_supervisor_profile_of_another_competition_is_not_a_profile_here(
    competition, other_competition, school
):
    foreign = make_supervisor(other_competition, school=school)
    foreign.user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])

    assert supervisor_profile(foreign.user, other_competition) == foreign
    assert supervisor_profile(foreign.user, competition) is None


def test_foreign_profile_cannot_open_statistics(flag_on, other_competition, edition, school, client_for):
    foreign = make_supervisor(other_competition, school=school)
    client = client_for(flag_on)
    client.force_login(foreign.user)

    assert client.get(reverse("web:supervisor-statistics")).status_code == 403
    assert client.get(reverse("web:supervisor-statistics-report")).status_code == 403


def test_scope_of_a_profile_from_another_competition_is_unverified(competition, other_competition, school):
    """Druga zapora (M2): nawet podany wprost, profil cudzego konkursu nie odsłania agregatu szkoły."""
    foreign = make_supervisor(other_competition, school=school)

    assert services.supervisor_scope(foreign, [], competition)["school_key"] is None
    assert services.supervisor_scope(foreign, [], other_competition)["school_key"] == f"s{school.pk}"


# --- M3: zamrożona przynależność ------------------------------------------------------------------


def test_publication_freezes_membership_and_later_school_change_does_not_move_the_aggregate(
    flag_on, edition, stage, school, other_school
):
    entries = [student(stage, school=school) for _ in range(5)]
    publish(stage, dict.fromkeys(entries, 4))
    assert FrozenMembership.objects.filter(stage=stage).count() == 5

    moved = entries[0].participant
    moved.school_ref = other_school
    moved.school = other_school.name
    moved.save(update_fields=["school_ref", "school"])
    summary = services.edition_summary(edition)

    assert summary.stages[0].groups[f"s{school.pk}"].entries == 5
    assert f"s{other_school.pk}" not in summary.stages[0].groups
    # Liczba osób szkoły (ranking, „do odzyskania”) jest bieżąca – uczeń już do niej nie należy.
    assert summary.groups[f"s{school.pk}"].participants == 4


def test_erased_participant_keeps_contributing_only_to_frozen_aggregates(flag_on, edition, stage, school):
    entries = [student(stage, school=school) for _ in range(5)]
    publish(stage, dict.fromkeys(entries, 6))
    participant = entries[0].participant
    participant.user.email = "usuniete-y@invalid.test"
    participant.user.save(update_fields=["email"])
    participant.school, participant.school_ref = ANONYMISED_SCHOOL, None
    participant.save(update_fields=["school", "school_ref"])

    summary = services.edition_summary(edition)

    assert summary.stages[0].groups[f"s{school.pk}"].entries == 5
    assert summary.groups[f"s{school.pk}"].participants == 4


def test_stage_published_before_deploy_is_frozen_lazily(competition, edition, stage, school):
    entries = [student(stage, school=school) for _ in range(3)]
    publish(stage, dict.fromkeys(entries, 1))  # flaga wyłączona – odbiornik nic nie zapisuje
    assert not FrozenMembership.objects.filter(stage=stage).exists()

    services.edition_summary(edition)

    assert FrozenMembership.objects.filter(stage=stage).count() == 3


def test_unpublishing_removes_the_frozen_rows(flag_on, edition, stage, school):
    entries = [student(stage, school=school) for _ in range(2)]
    publication = publish(stage, dict.fromkeys(entries, 1))
    publication.delete()

    assert not FrozenMembership.objects.filter(stage=stage).exists()


# --- M4: uczniowie wszystkich opiekunów szkoły --------------------------------------------------


def test_coordinator_hides_school_mean_against_union_of_its_supervisors(competition, edition, stage, school):
    make_supervisor(competition, school=school)  # SUPERVISOR_EMAIL
    second = make_supervisor(competition, school=school, email="drugi@szkola.test")
    known = [student(stage, school=school, mine=True) for _ in range(3)]
    known += [student(stage, school=school, supervisor_email=second.user.email) for _ in range(3)]
    stranger = student(stage, school=school)
    publish(stage, {**dict.fromkeys(known, 8), stranger: 1})

    row = services.coordinator_statistics(edition)["ranking"][0]
    report = services.school_report(
        school,
        edition,
        own_participant_ids=services.school_supervisor_participants(competition.pk, school.pk),
    )

    assert row["participants"] == 7
    assert row["cells"][0]["mean"] is None and row["cells"][0]["hidden"] is True
    assert next(line for line in report["stages"][0]["lines"] if line["key"] == "school")["visible"] is False


# --- L1, L2, L4, L5 -----------------------------------------------------------------------------------


def test_mean_requires_k_scored_entries(competition, edition, stage, school, supervisor):
    """Grupa dziewięciu wpisów, z których cztery mają sumę: liczby tak, średnia nie (L1)."""
    mine = [student(stage, school=school, mine=True) for _ in range(4)]
    for _ in range(5):
        student(stage, school=school)
    publish(stage, dict.fromkeys(mine, 5))

    lines = lines_of(services.supervisor_statistics(supervisor, competition, edition))
    cell = services.coordinator_statistics(edition)["ranking"][0]["cells"][0]

    assert lines["school"]["visible"] is True and lines["school"]["mean"] is None
    assert cell["mean"] is None


def test_history_count_hidden_when_subtraction_reveals_others(competition, edition, stage, school):
    mine = [student(stage, school=school, mine=True) for _ in range(5)]
    student(stage, school=school)

    report = services.school_report(school, edition, own_participant_ids=[e.participant.pk for e in mine])

    assert report["history"][-1]["visible"] is False
    assert report["history"][-1]["participants"] is None


def test_lost_schools_csv(flag_on, school, coordinator_client):
    previous = EditionFactory(competition=flag_on, year_label="2025/2026")
    student(StageFactory(edition=previous), school=school)
    current = EditionFactory(competition=flag_on, year_label="2026/2027", is_current=True)
    StageFactory(edition=current)

    response = coordinator_client.get(
        reverse("web:coordinator-school-stats-lost-export"), {"edition": current.pk}
    )

    content = b"".join(response.streaming_content).decode("utf-8-sig")
    assert response.status_code == 200
    assert str(school.rspo) in content and school.name in content


class RecordingCache:
    """Pamięć podręczna, która tylko zapisuje, z jakim czasem życia ją wołano."""

    def __init__(self):
        self.timeouts = []

    def get(self, key):
        return None

    def add(self, key, value, timeout=None):
        return True

    def set(self, key, value, timeout=None):
        self.timeouts.append(timeout)

    def delete(self, key):
        return None


def test_non_current_edition_gets_the_long_ttl(competition, monkeypatch):
    past = EditionFactory(competition=competition, is_current=False)
    StageFactory(edition=past)  # etap bez publikacji – mimo to edycja nie jest już bieżąca
    current = EditionFactory(competition=competition, is_current=True)
    StageFactory(edition=current)
    recorder = RecordingCache()
    monkeypatch.setattr(services, "cache", recorder)

    services.edition_summary(past)
    services.edition_summary(current)

    assert recorder.timeouts == [services.CACHE_TTL_FINAL, services.CACHE_TTL_LIVE]
