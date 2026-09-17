"""Zakres konkursu w warstwie WWW: po jednym reprezentatywnym ekranie z każdego panelu.

``apps/tenancy/tests/test_isolation.py`` sprawdza regułę krzyżową **po obszarach danych** (etap,
praca, recenzja, dyplom…). Ten moduł patrzy z drugiej strony – **po panelach** – bo to panele,
a nie modele, są tym, co zadanie T5 zmieniało:

- koordynator: pulpit, wyszukiwarka, lista kont, audyt,
- recenzent: kolejka przydziałów,
- uczestnik: pulpit i własne dokumenty,
- opiekun szkolny: lista uczniów,
- komisja odwoławcza: kolejka reklamacji,
- część publiczna: tabela wyników i statystyki.

Wspólny kształt każdego przypadku: **ta sama osoba** ma rolę w obu konkursach albo w jednym,
a żądanie idzie pod domenę konkursu A. Widać wtedy wyłącznie dane A. Ten układ nie jest
teoretyczny: nauczyciel bywa opiekunem w dwóch olimpiadach, a recenzent recenzuje w obu –
i wtedy kolejność „najpierw konkurs, potem rola” (§ 3.5) jest jedyną rzeczą, która je rozdziela.

Reguła odpowiedzi jest ta sama, co w suicie izolacji: **404** na cudzym obiekcie (nie 403),
**403** na złej roli we własnym konkursie, **302** dla anonima.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.models import CommitteeStatus, CompetitionRole, SchoolSupervisor
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.appeals.tests.factories import AppealFactory, AppealsCommitteeMemberFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.grading.tests.factories import ReviewFactory
from apps.results.tests.factories import CertificateFactory, ResultsPublicationFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def logged_in(client_for, competition, user):
    """Klient pod domeną konkursu, zalogowany na wskazane konto."""
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def world_b(other_competition):
    """Komplet danych drugiego konkursu: etap, zadanie, praca i wpis uczestnika.

    Jedna fikstura zamiast pięciu, bo w każdym z tych testów świat B jest tłem, a nie
    przedmiotem: chodzi o to, żeby **istniał** i żeby nie było go widać.
    """
    stage = StageFactory(competition=other_competition)
    problem = ProblemFactory(competition=other_competition, stage=stage)
    submission = SubmissionFactory(competition=other_competition, problem=problem, entry__stage=stage)
    return {
        "competition": other_competition,
        "stage": stage,
        "problem": problem,
        "submission": submission,
        "entry": submission.entry,
    }


# --- panel koordynatora ---------------------------------------------------------------------


@pytest.fixture
def coordinator_a(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return logged_in(client_for, competition, user)


def test_dashboard_counts_only_this_competition(coordinator_a, competition, world_b):
    """Pulpit pokazuje same liczby, więc błąd zakresu widać na nim **najpóźniej**.

    Karty etapów są kartami bieżącej edycji konkursu, a etap konkursu B ma własną edycję – więc
    jego nazwa nie ma prawa stanąć na tej stronie.
    """
    StageFactory(competition=competition)

    content = coordinator_a.get("/coordinator/").content.decode()

    assert world_b["stage"].display_name not in content or world_b["stage"].pk == 0
    assert f"/coordinator/stages/{world_b['stage'].pk}/" not in content


def test_search_does_not_reach_across_competitions(coordinator_a, competition, other_competition):
    """Wyszukiwarka panelu pyta naraz o uczestników, komisję, zadania, etapy i zgłoszenia.

    Asercja idzie po **kodzie publicznym**, a nie po nazwisku: fraza z adresu wraca na stronę
    w polu wyszukiwarki, więc „nazwiska nie ma w treści” byłoby asercją fałszywie negatywną.
    Kod publiczny stoi wyłącznie w wierszu wyniku i jest tym, co ten ekran naprawdę wydaje.
    """
    mine = ParticipantFactory(competition=competition, user__last_name="Nazwiskowski")
    stranger = ParticipantFactory(competition=other_competition, user__last_name="Nazwiskowski")

    content = coordinator_a.get("/coordinator/search/?q=Nazwiskowski").content.decode()

    assert mine.public_code in content
    assert stranger.public_code not in content


def test_accounts_list_shows_members_of_this_competition_only(coordinator_a, competition, other_competition):
    mine = ParticipantFactory(competition=competition, user__email="moj@example.invalid")
    grant_membership(mine.user, competition, CompetitionRole.PARTICIPANT)
    stranger = ParticipantFactory(competition=other_competition, user__email="obcy@example.invalid")
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    content = coordinator_a.get("/coordinator/accounts/").content.decode()

    assert mine.user.email in content
    assert stranger.user.email not in content


def test_account_of_another_competition_is_not_found(coordinator_a, other_competition):
    stranger = ParticipantFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    assert coordinator_a.get(f"/coordinator/accounts/{stranger.user.pk}/").status_code == 404


def test_audit_browser_hides_entries_about_objects_of_another_competition(
    coordinator_a, competition, world_b
):
    """Zakres audytu idzie **przez obiekt** wpisu – ``AuditLog`` nie ma własnej kolumny konkursu."""
    from apps.core.models import audit

    mine = StageFactory(competition=competition)
    audit(None, "stage.closed", mine)
    audit(None, "stage.rule_updated", world_b["stage"])

    content = coordinator_a.get("/coordinator/audit/").content.decode()

    assert f"competitions.stage#{mine.pk}" in content
    assert f"competitions.stage#{world_b['stage'].pk}" not in content


def test_audit_filter_choices_are_scoped_too(coordinator_a, world_b):
    """Sama nazwa akcji z cudzego konkursu bywa informacją („u kogoś padło ``stage.closed``”)."""
    from apps.core.models import audit

    audit(None, "results.published", world_b["stage"])

    content = coordinator_a.get("/coordinator/audit/").content.decode()

    assert 'value="results.published"' not in content


# --- panel recenzenta -----------------------------------------------------------------------


def test_reviewer_queue_is_scoped_before_the_assignment(client_for, competition, other_competition, world_b):
    """Ta sama osoba recenzuje w obu olimpiadach – rozdziela je wyłącznie kolejność z § 3.5."""
    reviewer_a = ActiveReviewerFactory(competition=competition)
    grant_membership(reviewer_a.user, competition, CompetitionRole.REVIEWER)
    review_b = ReviewFactory(
        competition=other_competition, submission=world_b["submission"], reviewer=reviewer_a
    )
    client = logged_in(client_for, competition, reviewer_a.user)

    listing = client.get("/review/").content.decode()

    assert f"/review/{review_b.pk}/" not in listing
    assert client.get(f"/review/{review_b.pk}/").status_code == 404


# --- panel uczestnika -----------------------------------------------------------------------


def test_participant_panel_shows_only_this_competitions_profile(client_for, competition, other_competition):
    """Pulpit uczestnika pokazuje profil spod domeny, na której stoimy – i tylko jego.

    Dwa **konta**, a nie jedno z dwoma profilami: w wydaniu B ``Participant.user`` jest wciąż
    ``OneToOne`` (kolumna konkursu dopiero powstała, unikalność zamyka się w wydaniu D, § 4.4),
    więc jedna osoba nie ma jeszcze jak wystartować w dwóch olimpiadach. Przedmiotem testu jest
    to, co obowiązuje **już teraz**: cudzy profil nie przecieka na ten pulpit.
    """
    here = ParticipantFactory(competition=competition, school="Szkoła Tutejsza")
    grant_membership(here.user, competition, CompetitionRole.PARTICIPANT)
    there = ParticipantFactory(competition=other_competition, school="Szkoła Obca")
    grant_membership(there.user, other_competition, CompetitionRole.PARTICIPANT)

    content = logged_in(client_for, competition, here.user).get("/me/").content.decode()

    assert here.public_code in content
    assert there.public_code not in content


def test_participant_certificate_of_another_competition_is_not_found(
    client_for, competition, other_competition, world_b
):
    here = ParticipantFactory(competition=competition)
    grant_membership(here.user, competition, CompetitionRole.PARTICIPANT)
    there = ParticipantFactory(competition=other_competition)
    entry_b = StageEntryFactory(competition=other_competition, participant=there, stage=world_b["stage"])
    certificate_b = CertificateFactory(competition=other_competition, entry=entry_b)
    client = logged_in(client_for, competition, here.user)

    assert client.get(f"/me/certificates/{certificate_b.pk}/").status_code == 404


# --- panel opiekuna -------------------------------------------------------------------------


def test_supervisor_sees_students_of_this_competition_only(
    client_for, competition, other_competition, world_b
):
    """Nauczyciel prowadzi klasę w dwóch olimpiadach; wiąże je **jeden** adres e-mail."""
    # ``SchoolSupervisor`` nie ma własnej fabryki (profil zakłada serwis rejestracji), więc
    # zakładamy go wprost – tak samo, jak robi to ``apps/web/tests/test_supervisor.py``.
    user = UserFactory(email="nauczyciel@example.invalid", groups=["supervisor"])
    SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=competition)
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    email = user.email
    mine = ParticipantFactory(competition=competition, supervisor_email=email, user__last_name="Tutejski")
    stranger = ParticipantFactory(
        competition=other_competition, supervisor_email=email, user__last_name="Obcy"
    )

    client = logged_in(client_for, competition, user)
    content = client.get("/supervisor/students/").content.decode()

    assert mine.public_code in content
    assert stranger.public_code not in content


# --- komisja odwoławcza ---------------------------------------------------------------------


def test_appeals_queue_is_scoped(client_for, competition, other_competition, world_b):
    member = AppealsCommitteeMemberFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.APPEALS)
    appeal_b = AppealFactory(competition=other_competition, submission=world_b["submission"])

    content = logged_in(client_for, competition, member.user).get("/appeals/").content.decode()

    assert world_b["submission"].entry.participant.public_code not in content
    assert f"/appeals/{appeal_b.pk}/" not in content


# --- część publiczna ------------------------------------------------------------------------


def test_public_results_of_another_competition_are_not_found(
    client_for, competition, other_competition, world_b
):
    """Adres bez logowania i z kolejnymi identyfikatorami – najtańsza droga do cudzych wyników."""
    stage_b = world_b["stage"]
    stage_b.results_published_at = timezone.now()
    stage_b.save(update_fields=["results_published_at"])
    publication = ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    assert client_for(competition).get(f"/results/{publication.stage_id}/").status_code == 404


def test_public_statistics_cover_this_competition_only(client_for, competition, other_competition, world_b):
    stage_b = world_b["stage"]
    stage_b.results_published_at = timezone.now()
    stage_b.save(update_fields=["results_published_at"])
    ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    content = client_for(competition).get("/statystyki/").content.decode()

    assert stage_b.edition.year_label not in content


# --- kontrakt odpowiedzi --------------------------------------------------------------------


def test_wrong_role_still_gets_403_not_404(client_for, competition):
    """Zła rola **we własnym konkursie** zostaje przy 403: tam nie ma czego ukrywać (§ 3.6)."""
    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)

    response = logged_in(client_for, competition, participant.user).get("/coordinator/")

    assert response.status_code == 403


def test_anonymous_still_gets_a_login_redirect(client_for, competition):
    response = client_for(competition).get("/coordinator/")

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_reviewer_of_another_competition_gets_403_on_this_panel(client_for, competition, other_competition):
    """Rola nadana u sąsiada nie jest rolą tutaj – ale odmowa dotyczy **roli**, więc 403."""
    reviewer_b = ActiveReviewerFactory(competition=other_competition, status=CommitteeStatus.ACTIVE)
    grant_membership(reviewer_b.user, other_competition, CompetitionRole.REVIEWER)
    competition.feature_flags = {**(competition.feature_flags or {}), "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])

    response = logged_in(client_for, competition, reviewer_b.user).get("/review/")

    assert response.status_code == 403
