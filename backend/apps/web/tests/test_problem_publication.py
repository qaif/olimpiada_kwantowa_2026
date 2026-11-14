"""Planowana publikacja treści zadań (PUB-01).

Zadanie z ``publish_at`` w przyszłości **nie istnieje** dla uczestnika i świata: ani tytułem na
liście, ani PDF-em, ani w API, ani w panelu, ani w serwisie uploadu – a koordynator widzi je zawsze.
Chwila publikacji jest liczona zegarem serwera przy żądaniu, bez zadania okresowego.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.cms.live_data import problems_state
from apps.competitions.publication import next_publication, problem_visible
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.submissions.services import create_submission
from apps.submissions.tests.factories import pdf_upload
from apps.tenancy.tests.factories import grant_membership
from apps.web.forms import ProblemForm

pytestmark = pytest.mark.django_db

SECRET = "Tajny tytuł zadania trzeciego"
PDF = b"%PDF-1.4 tresc zadania"


@pytest.fixture
def scheduled(elim_stage):
    """Zadanie 3 z publikacją za godzinę – obok zadań 1 i 2 jawnych od otwarcia etapu."""
    return ProblemFactory(
        stage=elim_stage,
        number=3,
        title=SECRET,
        publish_at=timezone.now() + timedelta(hours=1),
        statement_pdf=SimpleUploadedFile("tresc.pdf", PDF, content_type="application/pdf"),
    )


def test_visibility_follows_the_server_clock(scheduled):
    now = timezone.now()

    assert not problem_visible(scheduled, now=now)
    assert problem_visible(scheduled, now=now + timedelta(hours=2))
    assert next_publication(scheduled.stage, now) == scheduled.publish_at


def test_publication_before_the_stage_opens_does_not_reveal_anything(elim_stage):
    elim_stage.opens_at = timezone.now() + timedelta(days=1)
    elim_stage.deadline_at = elim_stage.opens_at + timedelta(days=14)
    elim_stage.save()
    early = ProblemFactory(stage=elim_stage, publish_at=timezone.now() - timedelta(days=1))

    assert not problem_visible(early)
    assert early.effective_publish_at == elim_stage.opens_at


def test_statement_pdf_is_404_until_publication_but_coordinator_sees_it(
    web_client, participant, entry, scheduled, coordinator
):  # noqa: ARG001 - ``entry`` zapisuje uczestnika do etapu
    url = f"/api/competitions/problems/{scheduled.pk}/statement/"
    web_client.force_login(participant.user)
    assert web_client.get(url).status_code == 404

    web_client.force_login(coordinator)
    assert web_client.get(url).status_code == 200

    scheduled.publish_at = timezone.now() - timedelta(minutes=1)
    scheduled.save()
    web_client.force_login(participant.user)
    assert web_client.get(url).status_code == 200


def test_public_api_and_problems_page_skip_unpublished_problems(web_client, competition, problems, scheduled):  # noqa: ARG001 - zadania 1 i 2 z fikstury
    payload = web_client.get("/api/competitions/editions/current/").json()
    titles = [row["title"] for row in payload["problems"]]
    state = problems_state(competition)

    assert SECRET not in titles and len(titles) == 2
    assert [problem.number for problem in state.problems] == [1, 2]


def test_participant_panel_hides_the_title_and_announces_only_the_moment(
    web_client, participant, entry, problems, scheduled
):  # noqa: ARG001
    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()

    assert problems[0].title in content
    assert SECRET not in content
    assert "/problems/3/upload/" not in content
    assert "Kolejne zadania zostaną opublikowane" in content


def test_upload_to_an_unpublished_problem_is_404_in_view_and_service(
    web_client, participant, entry, scheduled
):
    web_client.force_login(participant.user)
    response = web_client.post(
        f"/me/stages/{entry.stage_id}/problems/3/upload/",
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 404
    assert SECRET not in response.content.decode()

    with pytest.raises(DomainError) as refused:
        create_submission(participant.user, scheduled.stage, 3, pdf_upload())
    assert refused.value.machine_code == "PROBLEM_NOT_FOUND"


def test_upload_works_after_publication(participant, entry, scheduled):  # noqa: ARG001
    later = scheduled.publish_at + timedelta(minutes=1)

    submission = create_submission(participant.user, scheduled.stage, 3, pdf_upload(), now=later)

    assert submission.problem_id == scheduled.pk


def test_problem_form_rejects_a_moment_outside_the_stage(elim_stage):
    before = timezone.localtime(elim_stage.opens_at - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    after = timezone.localtime(elim_stage.submission_deadline + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    base = {"number": 9, "title": "X", "allowed_formats": ["pdf"], "max_file_mb": 20}

    for value in (before, after):
        form = ProblemForm({**base, "publish_at": value}, stage=elim_stage)
        assert not form.is_valid()
        assert "publish_at" in form.errors


def _coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def test_coordinator_schedules_all_problems_at_once(client_for, competition, elim_stage, problems):
    client = _coordinator_client(client_for, competition)
    moment = timezone.localtime(timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)

    response = client.post(
        f"/coordinator/stages/{elim_stage.pk}/problems/publication/",
        {"publish_at": moment.strftime("%Y-%m-%dT%H:%M")},
    )

    assert response.status_code == 302
    for problem in problems:
        problem.refresh_from_db()
        assert problem.publish_at == moment
    assert AuditLog.objects.filter(action="stage.problems_publication_scheduled").count() == 1

    listing = client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").content.decode()
    assert "zaplanowane" in listing

    client.post(f"/coordinator/stages/{elim_stage.pk}/problems/publication/", {"publish_at": ""})
    problems[0].refresh_from_db()
    assert problems[0].publish_at is None


def test_problems_page_is_not_page_cached():
    """Chwila publikacji nie unieważnia cache'u – więc strony z zadaniami nie mogą w nim leżeć."""
    from apps.web.page_cache import is_cacheable_path

    assert not is_cacheable_path("/zadania/")
    assert not is_cacheable_path("/me/")
