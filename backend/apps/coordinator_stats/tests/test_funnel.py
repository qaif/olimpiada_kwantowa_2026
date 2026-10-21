"""Lejek uczestnika (STATS-02 § 1): każdy krok niezależnie, filtry, izolacja, budżet zapytań, ekran i CSV."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CompetitionRole, Voivodeship
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.cms.models import WorkshopAttendance
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    EditionFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.coordinator_stats import domains, funnel
from apps.core.models import AuditLog
from apps.schools.tests.factories import SchoolFactory
from apps.student_status.models import CertificateStatus, StudentStatusCertificate
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import ProblemFactory, SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

ADULT = 2000
MINOR = 2012


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(
        competition=competition, year_label="2026/2027", created_at=timezone.now() - timedelta(days=30)
    )


@pytest.fixture
def elim(edition):
    return StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I – eliminacje")


@pytest.fixture
def training(edition):
    return StageFactory(edition=edition, kind=StageKind.TRAINING, name="Trening")


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def person(
    competition, *, activated=True, logged_in=True, consents=True, birth_year=ADULT, guardian=True, **kw
):
    """Uczestnik z kontem w zadanym stanie; ``user__…`` w ``kw`` idzie do fabryki konta."""
    now = timezone.now()
    return ParticipantFactory(
        competition=competition,
        user__email_verified_at=now if activated else None,
        user__last_login=now if logged_in else None,
        gdpr_consent_at=now if consents else None,
        birth_year=birth_year,
        guardian_consent=guardian,
        **kw,
    )


def certificate(participant, edition, status=CertificateStatus.ACCEPTED):
    return StudentStatusCertificate.objects.create(
        participant=participant,
        edition=edition,
        version=1,
        is_current=True,
        status=status,
        sha256="c" * 64,
        mime="application/pdf",
        size_bytes=10,
    )


def submit(entry, status=SubmissionStatus.SUBMITTED):
    return SubmissionFactory(entry=entry, problem=ProblemFactory(stage=entry.stage), status=status)


@pytest.fixture
def world(competition, edition, elim, training):
    """Siedmioro uczestników, każde odpada na innym kroku (opis przy każdym)."""
    people = {
        "not_activated": person(competition, activated=False, logged_in=False),
        "never_logged": person(competition, logged_in=False),
        "no_gdpr": person(competition, consents=False),
        "minor_no_guardian": person(competition, birth_year=MINOR, guardian=False),
        "training_only": person(competition),
        "complete": person(competition),
        "elim_workshop": person(competition),
    }
    StageEntryFactory(stage=training, participant=people["training_only"])
    complete = StageEntryFactory(stage=elim, participant=people["complete"])
    submit(complete)
    certificate(people["complete"], edition)
    StageEntryFactory(stage=elim, participant=people["elim_workshop"])
    for key in ("complete", "elim_workshop"):
        WorkshopAttendance.objects.create(participant=people[key], workshop_key="2026-11-05-kwanty")
    return people


def counts(result) -> dict:
    return {step["key"]: step["count"] for step in result.steps}


# --- kroki ---------------------------------------------------------------------------------------


def test_each_step_is_counted_independently(competition, edition, world):
    result = funnel.compute(competition, edition, funnel.Filters())

    assert counts(result) == {
        "registered": 7,
        "activated": 6,
        "logged_in": 5,
        # Zgody: wszyscy poza brakiem RODO i małoletnim bez zgody opiekuna – także bez aktywacji
        # i bez logowania (krok nie zależy od poprzednich).
        "consents": 5,
        "enrolled": 3,
        "enrolled_training": 1,
        "enrolled_first": 2,
        "submitted": 1,
        "student_status": 1,
        # Obecność bez zaakceptowanego zaświadczenia też się liczy.
        "workshop": 2,
    }
    steps = {step["key"]: step for step in result.steps}
    assert steps["activated"]["vs_registered"] == 85.7
    assert steps["consents"]["vs_registered"] == 71.4
    # Podwiersz „w tym Etap I” – też względem rejestracji; odsetka „względem poprzedniego” nie ma.
    assert steps["enrolled_first"]["vs_registered"] == 28.6 and steps["enrolled_first"]["sub"] is True
    assert all("vs_previous" not in step for step in result.steps)
    assert result.first_stage_name == "Etap I – eliminacje" and result.has_training


def test_accepted_certificate_counts_before_anyone_sent_work(competition, edition, elim):
    # Sytuacja z produkcji (21.10.2026): Etap I się nie zaczął, prac 0, a zaświadczenia są
    # zaakceptowane – lejek ścisły pokazywał przy nich 0.
    for _index in range(3):
        profile = person(competition)
        StageEntryFactory(stage=elim, participant=profile)
        certificate(profile, edition)

    got = counts(funnel.compute(competition, edition, funnel.Filters()))

    assert got["submitted"] == 0 and got["student_status"] == 3


def test_minor_with_guardian_consent_and_adult_without_it_pass_consents(competition, edition, elim):
    person(competition, birth_year=MINOR, guardian=True)
    person(competition, birth_year=ADULT, guardian=False)
    person(competition, birth_year=MINOR, guardian=False, birth_date=None)  # sam rocznik, małoletni

    assert counts(funnel.compute(competition, edition, funnel.Filters()))["consents"] == 2


def test_infected_submission_is_not_a_sent_work_and_pending_certificate_is_not_accepted(
    competition, edition, elim
):
    profile = person(competition)
    entry = StageEntryFactory(stage=elim, participant=profile)
    submit(entry, status=SubmissionStatus.REJECTED_INFECTED)
    certificate(profile, edition, status=CertificateStatus.PENDING)

    got = counts(funnel.compute(competition, edition, funnel.Filters()))

    assert got["enrolled_first"] == 1 and got["submitted"] == 0 and got["student_status"] == 0


def test_edition_without_contest_stage_has_no_first_stage_row(competition, edition):
    person(competition)

    result = funnel.compute(competition, edition, funnel.Filters())

    assert counts(result)["enrolled_first"] is None and result.first_stage_name == ""


# --- izolacja ------------------------------------------------------------------------------------


def test_other_competition_and_anonymised_accounts_are_not_counted(
    competition, other_competition, edition, world
):
    person(other_competition)
    # Konto po anonimizacji: adres ze znacznikiem ``ANONYMISED_EMAIL_MARKER`` (``@invalid.``).
    person(competition, user__email="usuniete-1@invalid.anonymised")

    assert counts(funnel.compute(competition, edition, funnel.Filters()))["registered"] == 7


def test_past_edition_counts_its_own_participants_only(competition, edition):
    old = EditionFactory(
        competition=competition, year_label="2025/2026", created_at=edition.created_at - timedelta(days=365)
    )
    old_stage = StageFactory(edition=old, kind=StageKind.ELIM)
    veteran = person(competition, user__date_joined=old.created_at + timedelta(days=3))
    StageEntryFactory(stage=old_stage, participant=veteran)
    person(competition)  # zarejestrowany w bieżącej edycji – nie należy do zeszłorocznej

    assert counts(funnel.compute(competition, old, funnel.Filters()))["registered"] == 1
    # W bieżącej edycji weteran bez wpisu i bez nowego konta nie jest uczestnikiem.
    assert counts(funnel.compute(competition, edition, funnel.Filters()))["registered"] == 1


def test_foreign_edition_is_404(coordinator_client, other_competition, edition):
    foreign = EditionFactory(competition=other_competition)
    url = reverse("web:coordinator-stats-funnel")

    assert coordinator_client.get(url, {"edition": foreign.pk}).status_code == 404
    assert coordinator_client.get(url, {"edition": "x"}).status_code == 404
    assert (
        coordinator_client.get(
            reverse("web:coordinator-stats-funnel-export"), {"edition": foreign.pk}
        ).status_code
        == 404
    )


# --- filtry --------------------------------------------------------------------------------------


def test_region_school_domain_and_period_filters(competition, edition):
    school = SchoolFactory(name="XIV LO", city="Warszawa", voivodeship=Voivodeship.MAZOWIECKIE)
    a = person(competition, school=school.name, school_ref=school, user__email="a@gmail.com")
    person(competition, district=Voivodeship.MALOPOLSKIE, user__email="b@outlook.com")
    late = person(competition, user__email="c@wp.pl")
    late.user.date_joined = timezone.now() + timedelta(days=2)
    late.user.save(update_fields=["date_joined"])

    def registered(**kw):
        return counts(funnel.compute(competition, edition, funnel.Filters(**kw)))["registered"]

    assert registered() == 3
    assert registered(region=Voivodeship.MALOPOLSKIE) == 1
    assert registered(region="nie-ma-takiego") == 0
    assert registered(school=f"sio:{school.pk}") == 1
    assert registered(school="śmieci") == 0
    assert registered(domain=domains.GMAIL) == 1
    assert registered(domain=domains.MICROSOFT) == 1
    assert registered(domain=domains.OTHER) == 0
    today = timezone.localdate()
    assert registered(registered_from=today + timedelta(days=1)) == 1
    assert registered(registered_to=today) == 2
    assert a.pk  # profil ze szkołą z wykazu istnieje


def test_domain_groups_in_sql_match_python(competition, edition):
    emails = [
        "a@gmail.com",
        "b@googlemail.com",
        "c@wp.pl",
        "d@interia.eu",
        "e@hotmail.co.uk",
        "f@live.pl",
        "g@zsp1.edu.pl",
        "h@lo5.eduwarszawa.pl",
        "i@lo5.krakow.pl",
        "j@live.szkola.pl",
        "k@gmail.com.evil.org",
    ]
    for email in emails:
        person(competition, user__email=email)
    expected = {group: sum(domains.group_of(email) == group for email in emails) for group in domains.GROUPS}

    for group in domains.GROUPS:
        got = counts(funnel.compute(competition, edition, funnel.Filters(domain=group)))["registered"]
        assert got == expected[group], group


def test_form_rejects_reversed_period_and_unknown_choices(coordinator_client, edition):
    url = reverse("web:coordinator-stats-funnel")

    response = coordinator_client.get(url, {"registered_from": "2026-10-10", "registered_to": "2026-10-01"})
    assert response.status_code == 200 and response.context["result"] is None
    assert coordinator_client.get(url, {"domain": "yahoo"}).context["result"] is None
    assert coordinator_client.get(url, {"domain": domains.GMAIL}).context["result"] is not None


# --- „gdzie odpadają” ----------------------------------------------------------------------------


def test_dropoff_tables_sort_by_lowest_activation_and_hide_small_groups(competition, edition):
    for index in range(5):
        person(competition, district=Voivodeship.SLASKIE, activated=index < 1, school="LO Śląskie")
    for index in range(6):
        person(competition, district=Voivodeship.POMORSKIE, activated=index < 5, school="LO Pomorskie")
    for _index in range(2):
        person(competition, district=Voivodeship.LUBUSKIE, activated=False, school="LO Lubuskie")

    result = funnel.compute(competition, edition, funnel.Filters())

    assert [item["label"] for item in result.regions] == [
        Voivodeship.SLASKIE.label,
        Voivodeship.POMORSKIE.label,
    ]
    assert result.regions[0]["activation"] == 20.0 and result.regions[0]["registered"] == 5
    assert [item["label"].split(" (")[0] for item in result.schools] == ["LO Śląskie", "LO Pomorskie"]


# --- koszt ----------------------------------------------------------------------------------------


def _queries(competition, edition) -> int:
    with CaptureQueriesContext(connection) as captured:
        funnel.compute(competition, edition, funnel.Filters())
    return len(captured.captured_queries)


def test_query_count_does_not_grow_with_participants(competition, edition, elim, training):
    for _index in range(2):
        StageEntryFactory(stage=elim, participant=person(competition))
    small = _queries(competition, edition)
    for _index in range(15):
        profile = person(competition, district=Voivodeship.OPOLSKIE)
        submit(StageEntryFactory(stage=elim, participant=profile))
        WorkshopAttendance.objects.create(participant=profile, workshop_key="k")

    assert _queries(competition, edition) == small
    assert small <= 6


def _screen_queries(client) -> int:
    from django.core.cache import cache

    cache.clear()  # wynik lejka leży w pamięci podręcznej – liczymy pełne przeliczenie
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("web:coordinator-stats-funnel"))
    assert response.status_code == 200
    return len(captured.captured_queries)


def test_screen_query_count_does_not_grow_with_participants(
    coordinator_client, competition, edition, world, elim
):
    _screen_queries(coordinator_client)  # rozgrzewka: sesja, konkurs, menu
    before = _screen_queries(coordinator_client)
    for _index in range(12):
        StageEntryFactory(stage=elim, participant=person(competition))

    assert _screen_queries(coordinator_client) == before


# --- ekran, CSV, bramki ---------------------------------------------------------------------------


def test_screen_shows_steps_and_bars(coordinator_client, world):
    response = coordinator_client.get(reverse("web:coordinator-stats-funnel"))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Pierwsze logowanie" in html and "stat-bar__fill--w" in html
    assert "Etap I – eliminacje" in html
    # Bez przełącznika trybu i bez kolumny „względem poprzedniego kroku”.
    assert 'name="mode"' not in html and "względem poprzedniego" not in html
    assert response.context["result"].steps[0]["vs_registered"] == 100.0
    # Ekran lejka nie pokazuje żadnej osoby.
    assert world["complete"].user.email not in html and world["complete"].public_code not in html


def test_old_strict_mode_link_is_ignored(coordinator_client, world):
    url = reverse("web:coordinator-stats-funnel")

    response = coordinator_client.get(url, {"mode": "strict"})

    assert response.status_code == 200
    assert counts(response.context["result"]) == counts(coordinator_client.get(url).context["result"])
    assert counts(response.context["result"])["workshop"] == 2  # niezależnie, nie ściśle
    export = coordinator_client.get(reverse("web:coordinator-stats-funnel-export"), {"mode": "strict"})
    assert export.status_code == 200


def test_csv_has_aggregates_only_and_is_audited(coordinator_client, edition, world):
    response = coordinator_client.get(
        reverse("web:coordinator-stats-funnel-export"), {"domain": domains.OTHER}
    )
    body = b"".join(response.streaming_content).decode("utf-8-sig")

    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    assert "Pierwsze logowanie" in body and "2026/2027" in body
    assert "poprzedniego" not in body and "Sposób liczenia" not in body
    header = body.splitlines()[0]
    assert "Odsetek względem rejestracji (%)" in header
    for profile in world.values():
        assert profile.user.email not in body and profile.public_code not in body
    entry = AuditLog.objects.filter(action="export.generated").latest("id")
    assert entry.diff["kind"] == "participant_funnel"


def test_participant_and_anonymous_cannot_open_the_screen(client_for, competition, edition):
    profile = ParticipantFactory(competition=competition)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    client = client_for(competition)
    url = reverse("web:coordinator-stats-funnel")

    assert client.get(url).status_code == 302
    client.force_login(profile.user)
    assert client.get(url).status_code == 403
    assert client.get(reverse("web:coordinator-stats-funnel-export")).status_code == 403


def test_menu_has_the_funnel(coordinator_client, edition):
    html = coordinator_client.get(reverse("web:coordinator-stats-funnel")).content.decode()
    assert reverse("web:coordinator-stats-funnel") in html and "Lejek uczestnika" in html
