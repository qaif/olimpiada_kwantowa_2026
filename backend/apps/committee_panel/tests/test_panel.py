"""Panel komisji (UX-PANEL-03): menu wg ról, Start, anonimowość, filtr kolejki, materiały, izolacja, koszt."""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import GROUP_APPEALS, CommitteeStatus, CompetitionRole, Voivodeship
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.appeals.tests.factories import AppealsCommitteeMemberFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.tests.query_budgets import budget
from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

START = "/committee/"
LAST_NAME = "Nieujawniona-Nazwiskowska"
EMAIL = "uczen-tajny@example.test"
SCHOOL = "XIV Liceum Tajemnicze"


def nav_keys(html: str) -> list[str]:
    return re.findall(r'data-nav="([a-z_]+)"', html)


def current_keys(html: str) -> list[str]:
    return re.findall(r'aria-current="page" data-nav="([a-z_]+)"', html)


def badge_of(html: str, key: str) -> str | None:
    match = re.search(rf'data-nav="{key}">.*?</a>', html, re.S)
    if match is None:
        return None
    badge = re.search(r'class="pp-badge">(\d+)', match.group(0))
    return badge.group(1) if badge else None


def page(client, path) -> str:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.content.decode()


@pytest.fixture
def stage(competition):
    return StageFactory(edition=CurrentEditionFactory(competition=competition), name="Etap szkolny próbny")


@pytest.fixture
def problems(stage):
    return [
        ProblemFactory(stage=stage, number=1, title="Splątanie dwóch kubitów"),
        ProblemFactory(stage=stage, number=2, title="Bramka Hadamarda"),
    ]


@pytest.fixture
def student(competition):
    return ParticipantFactory(
        competition=competition,
        school=SCHOOL,
        user=UserFactory(first_name="Ukryty", last_name=LAST_NAME, email=EMAIL, groups=["participant"]),
    )


@pytest.fixture
def entry(student, stage):
    return StageEntryFactory(participant=student, stage=stage)


@pytest.fixture
def reviewer(competition):
    member = ActiveReviewerFactory(competition=competition, district=Voivodeship.MALOPOLSKIE)
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    return member


def review_of(reviewer, entry, problem, **kwargs):
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.IN_REVIEW)
    return ReviewFactory(submission=submission, reviewer=reviewer, **kwargs)


@pytest.fixture
def logged(client_for, competition):
    def make(user):
        client = client_for(competition)
        client.force_login(user)
        return client

    return make


@pytest.fixture
def appeals_only(competition):
    """Członek **wyłącznie** komisji odwoławczej (bez roli recenzenta) – rzadkie, ale możliwe konto."""
    member = CommitteeMemberFactory(
        competition=competition,
        user=UserFactory(groups=[GROUP_APPEALS]),
        status=CommitteeStatus.ACTIVE,
        is_appeals_committee=True,
    )
    grant_membership(member.user, competition, CompetitionRole.APPEALS)
    return member


# --- menu wg ról ------------------------------------------------------------------------------------


def test_reviewer_menu_has_grading_but_no_appeals(logged, reviewer):
    html = page(logged(reviewer.user), START)

    keys = nav_keys(html)
    assert {
        "dashboard",
        "notifications",
        "todo",
        "graded",
        "materials",
        "support",
        "profile",
        "password",
    } <= set(keys)
    assert "appeals" not in keys
    # Druga ocena dopiero przy recenzji rundy 2.
    assert "tiebreak" not in keys
    assert current_keys(html) == ["dashboard"]
    # Podpis menu komisji – nie „Menu panelu” (koordynator) ani „Menu uczestnika”.
    assert "Menu komisji" in html
    assert "Menu panelu" not in html
    assert "Menu uczestnika" not in html


def test_appeals_only_member_sees_appeals_but_no_grading(logged, appeals_only):
    html = page(logged(appeals_only.user), START)

    keys = nav_keys(html)
    assert "appeals" in keys
    assert not {"todo", "graded", "materials"} & set(keys)
    # Materiały komitetu są ekranem recenzenta – bramka widoku, nie tylko menu.
    assert logged(appeals_only.user).get("/committee/materials/").status_code == 403


def test_appeals_member_who_reviews_has_both_sections(logged, competition):
    member = AppealsCommitteeMemberFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    grant_membership(member.user, competition, CompetitionRole.APPEALS)

    keys = nav_keys(page(logged(member.user), START))

    assert {"todo", "appeals"} <= set(keys)


def test_district_member_has_the_reviewer_menu_and_sees_the_conflict_rule(logged, reviewer):
    html = page(logged(reviewer.user), START)

    assert "małopolskie" in html
    assert "konflikt interesów" in html
    assert "appeals" not in nav_keys(html)


def test_tiebreak_item_and_counter_appear_with_a_round_two_review(logged, reviewer, entry, problems):
    review_of(reviewer, entry, problems[0], status=ReviewStatus.ASSIGNED)
    review_of(reviewer, entry, problems[1], status=ReviewStatus.ASSIGNED, round=ROUND_TIEBREAK)

    html = page(logged(reviewer.user), START)

    assert "tiebreak" in nav_keys(html)
    assert badge_of(html, "tiebreak") == "1"
    assert badge_of(html, "todo") == "2"


@pytest.mark.parametrize(
    "path,current",
    [
        ("/review/", "todo"),
        ("/review/?tab=submitted", "graded"),
        ("/review/?round=2", "tiebreak"),
        ("/committee/materials/", "materials"),
        ("/committee/notifications/", "notifications"),
        ("/account/profile/", "profile"),
    ],
)
def test_exactly_one_current_item(logged, reviewer, entry, problems, path, current):
    review_of(reviewer, entry, problems[0], round=ROUND_TIEBREAK)

    assert current_keys(page(logged(reviewer.user), path)) == [current]


def test_appeals_queue_is_framed_for_the_committee(logged, appeals_only):
    html = page(logged(appeals_only.user), "/appeals/")

    assert current_keys(html) == ["appeals"]
    assert "Reklamacje do rozpatrzenia" in html


def test_participant_and_anonymous_are_kept_out(client_for, competition, student):
    grant_membership(student.user, competition, CompetitionRole.PARTICIPANT)
    client = client_for(competition)

    anonymous = client.get(START)
    assert anonymous.status_code == 302
    assert "/login/" in anonymous["Location"]

    client.force_login(student.user)
    assert client.get(START).status_code == 403
    assert client.get("/committee/notifications/").status_code == 403
    # Panel uczestnika nie dostaje menu komisji.
    assert "Menu komisji" not in page(client, "/me/")


def test_pending_member_is_not_a_committee_member(logged, competition):
    member = CommitteeMemberFactory(competition=competition, user=UserFactory(groups=["reviewer"]))
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)

    assert logged(member.user).get(START).status_code == 403


def test_login_lands_on_start(client_for, competition, reviewer):
    from apps.accounts.tests.factories import DEFAULT_PASSWORD

    response = client_for(competition).post(
        "/login/", {"username": reviewer.user.email, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 302
    assert response["Location"] == START


def test_profile_of_a_committee_member_is_framed(logged, reviewer):
    html = page(logged(reviewer.user), "/account/profile/")

    assert "Menu komisji" in html
    assert current_keys(html) == ["profile"]


# --- Start i anonimowość ------------------------------------------------------------------------------


def test_start_counts_work_per_stage_without_any_participant_data(logged, reviewer, entry, problems, student):
    review_of(reviewer, entry, problems[0], status=ReviewStatus.ASSIGNED)
    review_of(reviewer, entry, problems[1], status=ReviewStatus.SUBMITTED, score=5)

    html = page(logged(reviewer.user), START)

    assert 'data-tile="todo"' in html
    assert "1 z 2 ocenionych" in html
    assert "Etap szkolny próbny" in html
    assert f"/review/?stage={entry.stage_id}" in html
    for secret in (LAST_NAME, EMAIL, SCHOOL, student.public_code, "Ukryty"):
        assert secret not in html


def test_review_list_and_materials_never_show_participant_data(logged, reviewer, entry, problems, student):
    review_of(reviewer, entry, problems[0])
    client = logged(reviewer.user)

    queue = page(client, "/review/")
    materials = page(client, "/committee/materials/")

    assert student.public_code in queue  # jedyny identyfikator pracy
    for html in (queue, materials):
        for secret in (LAST_NAME, EMAIL, SCHOOL):
            assert secret not in html
    assert student.public_code not in materials


def test_start_shows_the_appeals_tile_with_the_queue_size(logged, appeals_only, entry, problems, student):
    from apps.appeals.models import Appeal

    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.APPEALED)
    Appeal.objects.create(
        submission=submission, filed_by=student, argument="Proszę o ponowne sprawdzenie " * 3
    )

    html = page(logged(appeals_only.user), START)

    assert 'data-tile="appeals"' in html
    assert badge_of(html, "appeals") == "1"
    assert student.public_code not in html


def test_start_query_count_does_not_grow_with_assignments(logged, reviewer, entry, problems, competition):
    client = logged(reviewer.user)
    review_of(reviewer, entry, problems[0])
    assert client.get(START).status_code == 200  # rozgrzewka (Site Wagtaila)
    with CaptureQueriesContext(connection) as one:
        assert client.get(START).status_code == 200

    other_stage = StageFactory(edition=entry.stage.edition, kind=StageKind.DISTRICT)
    for number in range(3, 7):
        review_of(reviewer, entry, ProblemFactory(stage=entry.stage, number=number))
    other_entry = StageEntryFactory(participant=entry.participant, stage=other_stage)
    review_of(reviewer, other_entry, ProblemFactory(stage=other_stage, number=1))
    with CaptureQueriesContext(connection) as many:
        assert client.get(START).status_code == 200

    assert len(many) == len(one), [query["sql"] for query in many.captured_queries]
    assert len(many) <= budget("committee/start")


# --- kolejka „Do oceny”: zakładka i filtr ---------------------------------------------------------------


def test_tab_parameter_opens_the_requested_tab(logged, reviewer, entry, problems):
    review_of(reviewer, entry, problems[0], status=ReviewStatus.ASSIGNED)
    client = logged(reviewer.user)

    assert 'data-queue-active="todo"' in page(client, "/review/")
    assert 'data-queue-active="submitted"' in page(client, "/review/?tab=submitted")
    assert 'data-queue-active="todo"' in page(client, "/review/?tab=nonsense")


def test_filter_narrows_the_tabs_to_one_problem(logged, reviewer, entry, problems):
    first = review_of(reviewer, entry, problems[0])
    second = review_of(reviewer, entry, problems[1])
    client = logged(reviewer.user)

    html = page(client, f"/review/?problem={problems[0].pk}")

    assert f"/review/{first.pk}/" in html
    assert f"/review/{second.pk}/" not in html
    assert "Wyczyść filtr" in html
    # Pasek podsumowania liczy całą kolejkę.
    assert re.search(r'queue-stat__value">\s*2\s*<', html)


def test_filter_ignores_foreign_and_malformed_values(logged, reviewer, entry, problems, other_competition):
    review = review_of(reviewer, entry, problems[0])
    foreign = ProblemFactory(competition=other_competition)
    client = logged(reviewer.user)

    for query in (f"?problem={foreign.pk}", "?stage=abc", "?round=-1", "?stage=999999"):
        assert f"/review/{review.pk}/" in page(client, f"/review/{query}"), query


def test_round_filter_shows_only_tiebreak_work(logged, reviewer, entry, problems):
    blind = review_of(reviewer, entry, problems[0])
    tiebreak = review_of(reviewer, entry, problems[1], round=ROUND_TIEBREAK)

    html = page(logged(reviewer.user), "/review/?round=2")

    assert f"/review/{tiebreak.pk}/" in html
    assert f"/review/{blind.pk}/" not in html


# --- materiały i izolacja konkursów -------------------------------------------------------------------


def test_materials_list_rubric_and_notes_of_assigned_problems_only(logged, reviewer, entry, problems):
    from apps.grading.models import RubricCriterion

    problems[0].reviewer_notes = "Uznajemy dowód bez podstawy indukcji."
    problems[0].save(update_fields=["reviewer_notes"])
    RubricCriterion.objects.create(problem=problems[0], order=1, title="Poprawny stan Bella", max_points=3)
    review_of(reviewer, entry, problems[0])

    html = page(logged(reviewer.user), "/committee/materials/")

    assert "Splątanie dwóch kubitów" in html
    assert "Uznajemy dowód bez podstawy indukcji." in html
    assert "Poprawny stan Bella" in html
    assert "Bramka Hadamarda" not in html  # zadanie bez przydziału


def test_reviewer_of_another_competition_gets_no_committee_panel(client_for, competition, other_competition):
    stranger = ActiveReviewerFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.REVIEWER)
    client = client_for(competition)
    client.force_login(stranger.user)

    assert client.get(START).status_code == 403
    assert client.get("/committee/materials/").status_code == 403


def test_start_counts_only_this_competition(logged, reviewer, entry, problems, other_competition):
    review_of(reviewer, entry, problems[0])
    foreign_stage = StageFactory(competition=other_competition, name="Etap cudzego konkursu")
    foreign = SubmissionFactory(
        competition=other_competition,
        problem=ProblemFactory(competition=other_competition, stage=foreign_stage),
        entry__stage=foreign_stage,
        status=SubmissionStatus.IN_REVIEW,
    )
    ReviewFactory(submission=foreign, reviewer=reviewer)

    html = page(logged(reviewer.user), START)

    assert badge_of(html, "todo") == "1"
    assert "Etap cudzego konkursu" not in html


def test_committee_strings_have_an_english_catalogue():
    """Komisja IQO bywa międzynarodowa – menu, kafle i formularz decyzji komisji odwoławczej po angielsku."""
    from django.utils import translation

    from apps.web.forms import AppealDecideForm

    with translation.override("en"):
        assert translation.gettext("Menu komisji") == "Committee menu"
        assert translation.gettext("Materiały dla komisji") == "Committee materials"
        assert str(AppealDecideForm().fields["justification"].label) == "Justification"
