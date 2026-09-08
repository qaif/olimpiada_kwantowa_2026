"""Zadania etapu w panelu koordynatora (``/coordinator/stages/<id>/problems/``).

Pilnujemy pięciu rzeczy:

- zadanie z treścią PDF powstaje, a plik ląduje na **prywatnym** storage (nie na publicznym),
- o akceptacji pliku decyduje jego treść (``%PDF-``), nie nazwa ani ``Content-Type``,
- numer zadania jest unikalny w etapie, a komunikat stoi pod polem,
- podmiana treści po otwarciu etapu wymaga świadomego potwierdzenia,
- zadanie z rozwiązaniami nie znika z bazy (409), a treść przed otwarciem widzi wyłącznie
  koordynator.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.competitions.models import Problem
from apps.competitions.storage import PrivateMediaFileSystemStorage
from apps.competitions.tests.factories import ProblemFactory
from apps.core.models import AuditLog
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory

from .conftest import shift_stage

pytestmark = pytest.mark.django_db


def statement(name: str = "zadanie-1.pdf", data: bytes = PDF_BYTES) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def payload(**overrides) -> dict:
    data = {"number": 1, "title": "Nierówność ze średnimi", "allowed_formats": ["pdf"], "max_file_mb": 20}
    data.update(overrides)
    return data


# --- dodawanie ----------------------------------------------------------------------------------


def test_create_problem_with_statement_goes_to_private_storage(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/problems/",
        payload(number=7, statement_pdf=statement()),
    )

    problem = Problem.objects.get(stage=elim_stage, number=7)
    assert response.status_code == 302
    assert problem.title == "Nierówność ze średnimi"
    assert problem.allowed_formats == ["pdf"]
    assert problem.statement_pdf.read() == PDF_BYTES
    # Prywatny storage ma własny podkatalog – to on, a nie sama nazwa aliasu, jest tu dowodem.
    assert PrivateMediaFileSystemStorage.subdirectory in problem.statement_pdf.path.replace("\\", "/")
    assert AuditLog.objects.filter(action="problem.created", target_id=str(problem.pk)).exists()


def test_create_problem_rejects_a_file_that_is_not_a_pdf(web_client, coordinator, elim_stage):
    """Nazwa i ``Content-Type`` pochodzą od przesyłającego – liczy się nagłówek pliku."""
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/problems/",
        payload(statement_pdf=statement(data=b"PK\x03\x04 to jest zip")),
    )

    assert response.status_code == 400
    assert not Problem.objects.filter(stage=elim_stage).exists()
    assert "%PDF-" in response.context["form"].errors["statement_pdf"][0]


def test_create_problem_rejects_a_duplicate_number(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)
    before = Problem.objects.filter(stage=elim_stage).count()

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/problems/", payload(number=1))

    assert response.status_code == 400
    assert Problem.objects.filter(stage=elim_stage).count() == before
    assert "już jest w tym etapie" in response.context["form"].errors["number"][0]


def test_create_problem_requires_at_least_one_format(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim_stage.pk}/problems/", payload(allowed_formats=[]))

    assert response.status_code == 400
    assert response.context["form"].errors["allowed_formats"]


# --- edycja -------------------------------------------------------------------------------------


def test_edit_changes_title_and_formats(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)
    problem = problems[0]

    response = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        payload(number=problem.number, title="Nowy tytuł", allowed_formats=["pdf", "ipynb"], max_file_mb=30),
    )

    problem.refresh_from_db()
    assert response.status_code == 302
    assert problem.title == "Nowy tytuł"
    assert sorted(problem.allowed_formats) == ["ipynb", "pdf"]
    assert problem.max_file_mb == 30
    entry = AuditLog.objects.get(action="problem.updated", target_id=str(problem.pk))
    assert set(entry.diff) == {"title", "allowed_formats", "max_file_mb"}


def test_replacing_the_statement_after_opening_needs_confirmation(web_client, coordinator, elim_stage):
    """Etap otwarty: uczestnicy widzą treść, więc podmiana pliku nie może być cicha."""
    problem = ProblemFactory(stage=elim_stage, number=3, statement_pdf=statement("stare.pdf"))
    web_client.force_login(coordinator)
    body = payload(number=3, title=problem.title)

    refused = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        {**body, "statement_pdf": statement("nowe.pdf", b"%PDF-1.7 nowa tresc")},
    )
    problem.refresh_from_db()
    assert refused.status_code == 400
    assert problem.statement_pdf.read() == PDF_BYTES

    accepted = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        {
            **body,
            "statement_pdf": statement("nowe.pdf", b"%PDF-1.7 nowa tresc"),
            "confirm_open_stage": "on",
        },
    )
    problem.refresh_from_db()
    assert accepted.status_code == 302
    assert problem.statement_pdf.read() == b"%PDF-1.7 nowa tresc"


def test_statement_can_be_replaced_freely_before_the_stage_opens(web_client, coordinator, elim_stage):
    """Przed otwarciem nikt treści nie widział – potwierdzenia nie ma nawet w formularzu."""
    shift_stage(elim_stage, opens=2, deadline=10, review=24, appeal_opens=26, appeal_closes=33)
    problem = ProblemFactory(stage=elim_stage, number=4, statement_pdf=statement("stare.pdf"))
    web_client.force_login(coordinator)

    form_page = web_client.get(f"/coordinator/problems/{problem.pk}/edit/").content.decode()
    response = web_client.post(
        f"/coordinator/problems/{problem.pk}/edit/",
        {
            **payload(number=4, title=problem.title),
            "statement_pdf": statement("nowe.pdf", b"%PDF-1.7 przed otwarciem"),
        },
    )

    problem.refresh_from_db()
    assert 'name="confirm_open_stage"' not in form_page
    assert response.status_code == 302
    assert problem.statement_pdf.read() == b"%PDF-1.7 przed otwarciem"


# --- usuwanie -----------------------------------------------------------------------------------


def test_delete_removes_a_problem_without_submissions(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)
    problem = problems[1]

    response = web_client.post(f"/coordinator/problems/{problem.pk}/delete/")

    assert response.status_code == 302
    assert not Problem.objects.filter(pk=problem.pk).exists()
    assert AuditLog.objects.filter(action="problem.deleted", target_id=str(problem.pk)).exists()


def test_delete_refuses_when_the_problem_has_submissions(
    web_client, coordinator, elim_stage, entry, problems
):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/problems/{problems[0].pk}/delete/")

    assert response.status_code == 409
    assert Problem.objects.filter(pk=problems[0].pk).exists()
    assert "nie można go usunąć" in response.content.decode()


# --- lista --------------------------------------------------------------------------------------


def test_problem_list_shows_counts_and_formats(web_client, coordinator, elim_stage, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)

    content = web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").content.decode()

    assert problems[0].title in content
    assert problems[1].title in content
    assert ">.pdf<" in content
    assert f'href="/coordinator/problems/{problems[0].pk}/edit/"' in content
    # Zadanie z rozwiązaniem nie ma przycisku usuwania – odmowa jest też w interfejsie, nie tylko w serwisie.
    assert f'action="/coordinator/problems/{problems[0].pk}/delete/"' not in content
    assert f'action="/coordinator/problems/{problems[1].pk}/delete/"' in content


# --- widoczność treści --------------------------------------------------------------------------


def test_coordinator_sees_the_statement_before_the_stage_opens(web_client, coordinator, elim_stage):
    shift_stage(elim_stage, opens=2, deadline=10, review=24, appeal_opens=26, appeal_closes=33)
    problem = ProblemFactory(stage=elim_stage, number=5, statement_pdf=statement())
    web_client.force_login(coordinator)

    response = web_client.get(f"/api/competitions/problems/{problem.pk}/statement/")

    assert elim_stage.has_opened(timezone.now()) is False
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES


@pytest.mark.parametrize("role", ["anonymous", "participant"])
def test_others_still_get_404_before_the_stage_opens(web_client, participant, elim_stage, role):
    shift_stage(elim_stage, opens=2, deadline=10, review=24, appeal_opens=26, appeal_closes=33)
    problem = ProblemFactory(stage=elim_stage, number=6, statement_pdf=statement())
    if role == "participant":
        web_client.force_login(participant.user)

    assert web_client.get(f"/api/competitions/problems/{problem.pk}/statement/").status_code == 404


# --- uprawnienia --------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_manage_problems(web_client, participant, reviewer, elim_stage, problems, role):
    user = participant.user if role == "participant" else reviewer.user
    web_client.force_login(user)

    assert web_client.get(f"/coordinator/stages/{elim_stage.pk}/problems/").status_code == 403
    assert web_client.get(f"/coordinator/problems/{problems[0].pk}/edit/").status_code == 403
    assert web_client.post(f"/coordinator/problems/{problems[0].pk}/delete/").status_code == 403
    assert Problem.objects.filter(pk=problems[0].pk).exists()
