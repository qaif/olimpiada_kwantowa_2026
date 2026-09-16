"""Karta członka komisji (``/coordinator/members/<id>/``) i lista członków (``/coordinator/members/``).

Sprawdzamy to, czego nie widać w testach serwisów: że karta zbiera w jedno miejsce dane rozsypane
po pięciu ekranach, że każdy formularz na niej celuje w **istniejący** adres akcji koordynatora
(karta nie ma własnych adresów zapisu), że nikt poza koordynatorem tu nie wejdzie i że koszt strony
nie rośnie z liczbą recenzji – bo to ostatnie jest jedyną rzeczą, której nie widać, dopóki komitet
ma trzy osoby, a boli, gdy ma trzydzieści.
"""

import pytest

from apps.accounts.models import CommitteeStatus, Voivodeship
from apps.accounts.tests.factories import ActiveReviewerFactory, CommitteeMemberFactory
from apps.core.models import audit
from apps.grading.models import ProblemReviewerRule, ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

MEMBERS_URL = "/coordinator/members/"


def member_url(member) -> str:
    return f"/coordinator/members/{member.pk}/"


@pytest.fixture
def locked(entry, problems):
    """Praca gotowa do przydziału – dokładnie taki stan zostawia zamknięcie etapu."""
    return SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.LOCKED)


@pytest.fixture
def review(locked, reviewer):
    return ReviewFactory(submission=locked, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=5)


# --- dostęp -------------------------------------------------------------------------------------


def test_non_coordinator_gets_403(web_client, reviewer):
    web_client.force_login(reviewer.user)

    assert web_client.get(MEMBERS_URL).status_code == 403
    assert web_client.get(member_url(reviewer)).status_code == 403


def test_participant_gets_403(web_client, participant, reviewer):
    web_client.force_login(participant.user)

    assert web_client.get(member_url(reviewer)).status_code == 403


def test_anonymous_is_redirected_to_login(web_client, reviewer):
    response = web_client.get(member_url(reviewer))

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- lista --------------------------------------------------------------------------------------


def test_list_shows_every_member_with_workload(web_client, coordinator, reviewer, review):
    idle = CommitteeMemberFactory(status=CommitteeStatus.SUSPENDED)
    web_client.force_login(coordinator)

    response = web_client.get(MEMBERS_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert reviewer.user.email in content
    # Osoba bez ani jednej recenzji też ma wiersz: zero przydziałów jest informacją o rozkładzie
    # obciążenia, a nie brakiem wiersza.
    assert idle.user.email in content
    assert member_url(reviewer) in content


def test_list_status_filter_narrows_rows(web_client, coordinator, reviewer):
    suspended = CommitteeMemberFactory(status=CommitteeStatus.SUSPENDED)
    web_client.force_login(coordinator)

    response = web_client.get(MEMBERS_URL, {"status": CommitteeStatus.SUSPENDED})
    content = response.content.decode()

    assert suspended.user.email in content
    assert reviewer.user.email not in content


def test_list_stage_filter_keeps_people_and_zeroes_counts(
    web_client, coordinator, reviewer, review, interview_stage
):
    """Etap zawęża liczniki, a nie listę osób – „zero w tym etapie” jest właśnie odpowiedzią."""
    web_client.force_login(coordinator)

    response = web_client.get(MEMBERS_URL, {"stage": interview_stage.pk})

    assert response.status_code == 200
    assert reviewer.user.email in response.content.decode()
    row = next(row for row in response.context["rows"] if row["member"].pk == reviewer.pk)
    assert row["submitted"] == 0


def test_list_ignores_unknown_filter_values(web_client, coordinator, reviewer):
    """Parametr z ręcznie skróconego adresu znaczy „bez zawężenia”, a nie 404."""
    web_client.force_login(coordinator)

    response = web_client.get(MEMBERS_URL, {"status": "NIE-MA-TAKIEGO", "stage": "abc"})

    assert response.status_code == 200
    assert response.context["status"] == ""
    assert response.context["stage_id"] is None
    assert reviewer.user.email in response.content.decode()


# --- karta: sekcje ------------------------------------------------------------------------------


def test_card_renders_all_sections(web_client, coordinator, reviewer, review, locked):
    web_client.force_login(coordinator)

    response = web_client.get(member_url(reviewer))
    content = response.content.decode()

    assert response.status_code == 200
    for heading in ("Dane", "Obciążenie", "Recenzje", "Przydziel pracę", "Reguły", "Historia"):
        assert heading in content
    assert reviewer.user.email in content
    # Recenzja jest w tabeli razem z pseudonimem uczestnika – nazwiska tu nie ma, bo tabela mówi
    # o pracy recenzenta, a nie o uczestniku.
    assert locked.entry.participant.public_code in content


def test_card_reviews_table_posts_to_existing_endpoints(web_client, coordinator, reviewer, review):
    web_client.force_login(coordinator)

    content = web_client.get(member_url(reviewer)).content.decode()

    assert f'action="/coordinator/reviews/{review.pk}/score/"' in content
    assert f'action="/coordinator/reviews/{review.pk}/unassign/"' in content


def test_card_offers_assignment_of_free_work(web_client, coordinator, entry, problems):
    """Praca zablokowana, której ta osoba nie recenzuje, jest do przydzielenia z karty."""
    free = SubmissionFactory(entry=entry, problem=problems[1], status=SubmissionStatus.LOCKED)
    member = ActiveReviewerFactory()
    web_client.force_login(coordinator)

    content = web_client.get(member_url(member)).content.decode()

    assert f'action="/coordinator/submissions/{free.pk}/assign-reviewer/"' in content
    assert f'name="reviewer_id" value="{member.pk}"' in content


def test_card_hides_work_the_member_already_reviews(web_client, coordinator, reviewer, review, locked):
    web_client.force_login(coordinator)

    content = web_client.get(member_url(reviewer)).content.decode()

    assert f'action="/coordinator/submissions/{locked.pk}/assign-reviewer/"' not in content


def test_card_rule_section_lists_and_offers_rules(web_client, coordinator, reviewer, problems):
    rule = ProblemReviewerRule.objects.create(problem=problems[0], reviewer=reviewer)
    web_client.force_login(coordinator)

    content = web_client.get(member_url(reviewer)).content.decode()

    assert f'action="/coordinator/reviewer-rules/{rule.pk}/delete/"' in content
    # Zadanie, które reguły jeszcze nie ma, stoi w formularzu dodania.
    assert f'action="/coordinator/problems/{problems[1].pk}/reviewer-rules/"' in content
    # Zadanie z regułą znika z listy kandydatów – druga taka sama reguła jest niemożliwa.
    assert f'action="/coordinator/problems/{problems[0].pk}/reviewer-rules/"' not in content


def test_card_district_and_approval_use_existing_actions(web_client, coordinator, problems):
    pending = CommitteeMemberFactory(status=CommitteeStatus.PENDING, district=Voivodeship.SLASKIE)
    web_client.force_login(coordinator)

    content = web_client.get(member_url(pending)).content.decode()

    assert f'action="/coordinator/committee/{pending.pk}/verify-district/"' in content
    assert f'action="/coordinator/committee/{pending.pk}/approve/"' in content


def test_card_shows_audit_entries_about_this_person(web_client, coordinator, reviewer):
    audit(coordinator, "committee.approved", reviewer, {"status": "ACTIVE"})
    web_client.force_login(coordinator)

    content = web_client.get(member_url(reviewer)).content.decode()

    assert "committee.approved" in content


def test_card_reminder_only_when_there_is_something_to_remind_about(
    web_client, coordinator, reviewer, locked
):
    """Przypomnienie o pustej liście byłoby spamem – przycisk stoi tylko przy realnej robocie."""
    web_client.force_login(coordinator)

    idle = web_client.get(member_url(reviewer)).content.decode()
    assert "Przypomnij e-mailem" not in idle

    ReviewFactory(submission=locked, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    busy = web_client.get(member_url(reviewer)).content.decode()
    assert "Przypomnij e-mailem" in busy
    assert f'action="/coordinator/stages/{locked.entry.stage_id}/progress/remind/"' in busy


def test_card_of_member_without_reviews_still_renders(web_client, coordinator):
    """Profil bez ani jednej recenzji: sekcje opcjonalne znikają, strona zostaje."""
    fresh = CommitteeMemberFactory()
    web_client.force_login(coordinator)

    response = web_client.get(member_url(fresh))
    content = response.content.decode()

    assert response.status_code == 200
    assert response.context["calibration"] is None
    assert "Kalibracja" not in content
    assert "Ta osoba nie ma jeszcze ani jednej recenzji." in content


def test_card_calibration_appears_after_submitted_reviews(web_client, coordinator, reviewer, review):
    web_client.force_login(coordinator)

    response = web_client.get(member_url(reviewer))

    # Moduł kalibracji jest w tej instalacji obecny, a recenzent ma wystawioną ocenę rundy 1.
    assert response.context["calibration"] is not None
    assert "Kalibracja" in response.content.decode()


def test_card_missing_member_is_404(web_client, coordinator):
    web_client.force_login(coordinator)

    assert web_client.get("/coordinator/members/999999/").status_code == 404


# --- koszt --------------------------------------------------------------------------------------


def _add_reviews(entry, problems, reviewer, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        submission = SubmissionFactory(
            entry=entry, problem=problems[number % 2], status=SubmissionStatus.LOCKED, version=number + 1
        )
        ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=2)


def test_card_query_count_does_not_grow_with_reviews(
    web_client, coordinator, reviewer, entry, problems, django_assert_max_num_queries
):
    """Dwadzieścia recenzji ma kosztować tyle, co dwie: karta liczy wszystko z jednego pobrania.

    Sprawdzamy **niezmienność**, a nie okrągłą liczbę: stały narzut (sesja, ustawienia serwisu,
    menu panelu) bywa zmieniany przez sąsiednie ekrany i nie o nim jest ta asercja. Sufit stoi
    obok wyłącznie jako bezpiecznik przed regresją rzędu wielkości.
    """
    _add_reviews(entry, problems, reviewer, 2)
    web_client.force_login(coordinator)
    # Pierwsze żądanie po zalogowaniu jest droższe od każdego następnego: wypełnia pamięci
    # podręczne procesu (ustawienia serwisu Wagtaila, definicje uprawnień). Ta asercja jest o tym,
    # jak koszt zależy od **danych**, więc rozgrzewka odbywa się przed pomiarem.
    web_client.get(member_url(reviewer))

    with django_assert_max_num_queries(45) as few:
        assert web_client.get(member_url(reviewer)).status_code == 200

    _add_reviews(entry, problems, reviewer, 18, start=2)
    with django_assert_max_num_queries(45) as many:
        assert web_client.get(member_url(reviewer)).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)


def test_list_query_count_does_not_grow_with_members(web_client, coordinator, django_assert_max_num_queries):
    """Dziesięciu recenzentów kosztuje tyle, co jeden: liczniki dokłada jeden agregat."""
    ActiveReviewerFactory()
    web_client.force_login(coordinator)
    # Pierwsze żądanie po zalogowaniu jest droższe od każdego następnego: wypełnia pamięci
    # podręczne procesu (ustawienia serwisu Wagtaila, definicje uprawnień). Ta asercja jest o tym,
    # jak koszt zależy od **danych**, więc rozgrzewka odbywa się przed pomiarem.
    web_client.get(MEMBERS_URL)

    with django_assert_max_num_queries(30) as few:
        assert web_client.get(MEMBERS_URL).status_code == 200

    for _ in range(9):
        ActiveReviewerFactory()
    with django_assert_max_num_queries(30) as many:
        assert web_client.get(MEMBERS_URL).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)
