"""Załączniki zadań (PUB-02): bramka jak dla treści, pobieranie jako dane, panel, komenda."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions import attachments as service
from apps.competitions.models import ProblemAttachment
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.competitions.tests.scope_helpers import api_client_factory
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

NPY = b"\x93NUMPY\x01\x00v\x00{'descr': '<f8', 'fortran_order': False, 'shape': (2,), }" + b" " * 40 + b"\n"


@pytest.fixture
def api_for(settings):
    return api_client_factory(settings)


def _add(problem, name="jony2.npy", data=NPY, **kwargs):
    return service.add_attachment(
        problem, SimpleUploadedFile(name, data), actor=None, competition=None, system=True, **kwargs
    )


def _url(attachment):
    return f"/api/competitions/problems/{attachment.problem_id}/attachments/{attachment.pk}/"


def test_clean_filename():
    assert service.clean_filename("../../etc/jony 2.NPY") == "jony_2.npy"
    assert service.clean_filename("C:\\dane\\wynik.csv") == "wynik.csv"
    assert service.clean_filename('a"b;c.txt') == "a_b_c.txt"
    assert service.clean_filename("zażółć.json") == "zażółć.json"
    with pytest.raises(DomainError):
        service.clean_filename(".npy")
    with pytest.raises(DomainError):
        service.clean_filename("bez_rozszerzenia")


@pytest.mark.parametrize("name", ["x.html", "x.svg", "x.exe", "x.js", "x.htm", "x.sh"])
def test_dangerous_extensions_are_refused(competition, name):
    problem = ProblemFactory()
    with pytest.raises(DomainError) as exc:
        _add(problem, name=name)
    assert exc.value.machine_code == "ATTACHMENT_TYPE_NOT_ALLOWED"


def test_size_limit_and_empty_file(competition, monkeypatch):
    problem = ProblemFactory()
    monkeypatch.setattr(service, "MAX_ATTACHMENT_BYTES", 10)
    with pytest.raises(DomainError):
        _add(problem, data=b"x" * 11)
    with pytest.raises(DomainError):
        _add(problem, name="pusty.txt", data=b"")


def test_attachment_metadata_and_private_storage(competition, settings):
    problem = ProblemFactory()
    attachment = _add(problem, description="dane do części a")
    assert attachment.size == len(NPY)
    assert len(attachment.sha256) == 64
    assert attachment.file.name.startswith("problems/attachments/")
    # Prywatny storage – podkatalog ``private/``, nie drzewo ``default``.
    assert "private" in attachment.file.path
    assert AuditLog.objects.filter(action="problem.attachment_added", target_id=str(attachment.pk)).exists()


def test_participant_downloads_published_attachment_as_octet_stream(api_for, competition):
    entry = StageEntryFactory()
    problem = ProblemFactory(stage=entry.stage)
    attachment = _add(problem)

    response = api_for(competition, entry.participant.user).get(_url(attachment))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/octet-stream"
    assert response["Content-Disposition"].startswith("attachment")
    assert 'filename="jony2.npy"' in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content) == NPY


def test_anonymous_download_follows_the_statement_rule(api_for, competition):
    """Treść zadania jest po otwarciu jawna także bez logowania – załącznik dokładnie tak samo."""
    problem = ProblemFactory()
    attachment = _add(problem)
    assert api_for(competition).get(_url(attachment)).status_code == 200


def test_hidden_before_stage_opens_except_coordinator(api_for, competition):
    stage = StageFactory(opens_at=timezone.now() + timedelta(days=1))
    problem = ProblemFactory(stage=stage)
    attachment = _add(problem)
    participant = ParticipantFactory()

    assert api_for(competition).get(_url(attachment)).status_code == 404
    assert api_for(competition, participant.user).get(_url(attachment)).status_code == 404
    assert api_for(competition, CoordinatorFactory()).get(_url(attachment)).status_code == 200


def test_hidden_before_publish_at(api_for, competition):
    problem = ProblemFactory(publish_at=timezone.now() + timedelta(hours=2))
    attachment = _add(problem)
    participant = ParticipantFactory()

    assert api_for(competition, participant.user).get(_url(attachment)).status_code == 404
    assert api_for(competition).get(_url(attachment)).status_code == 404


def test_other_competition_and_wrong_problem_are_404(api_for, competition, other_competition):
    problem_b = ProblemFactory(competition=other_competition)
    attachment_b = _add(problem_b)
    assert api_for(competition, CoordinatorFactory()).get(_url(attachment_b)).status_code == 404

    problem = ProblemFactory()
    other_problem = ProblemFactory(stage=problem.stage)
    attachment = _add(problem)
    url = f"/api/competitions/problems/{other_problem.pk}/attachments/{attachment.pk}/"
    assert api_for(competition).get(url).status_code == 404


def test_service_requires_coordinator(competition):
    problem = ProblemFactory()
    participant = ParticipantFactory()
    with pytest.raises(DomainError) as exc:
        service.add_attachment(
            problem, SimpleUploadedFile("a.csv", b"1,2\n"), actor=participant.user, competition=competition
        )
    assert exc.value.machine_code == "FORBIDDEN"
    attachment = service.add_attachment(
        problem, SimpleUploadedFile("a.csv", b"1,2\n"), actor=CoordinatorFactory(), competition=competition
    )
    with pytest.raises(DomainError):
        service.delete_attachment(attachment, actor=participant.user, competition=competition)


def test_duplicate_name_is_refused(competition):
    problem = ProblemFactory()
    _add(problem)
    with pytest.raises(DomainError) as exc:
        _add(problem, data=NPY + b"x")
    assert exc.value.machine_code == "ATTACHMENT_NAME_TAKEN"


def test_coordinator_panel_add_update_delete(client_for, competition):
    coordinator = CoordinatorFactory()
    problem = ProblemFactory()
    client = client_for(competition)
    client.force_login(coordinator)

    page = client.get(f"/coordinator/problems/{problem.pk}/edit/")
    assert page.status_code == 200
    assert "Załączniki" in page.content.decode()

    response = client.post(
        f"/coordinator/problems/{problem.pk}/attachments/",
        {"file": SimpleUploadedFile("jony3.npy", NPY), "description": "dane"},
    )
    assert response.status_code == 302
    attachment = ProblemAttachment.objects.get(problem=problem)
    assert attachment.filename == "jony3.npy"
    assert "jony3.npy" in client.get(f"/coordinator/problems/{problem.pk}/edit/").content.decode()

    client.post(
        f"/coordinator/problem-attachments/{attachment.pk}/", {"description": "nowy", "position": "3"}
    )
    attachment.refresh_from_db()
    assert (attachment.description, attachment.position) == ("nowy", 3)

    client.post(f"/coordinator/problem-attachments/{attachment.pk}/delete/")
    assert not ProblemAttachment.objects.filter(pk=attachment.pk).exists()
    assert AuditLog.objects.filter(action="problem.attachment_deleted").exists()


def test_participant_cannot_use_coordinator_actions(client_for, competition):
    problem = ProblemFactory()
    participant = ParticipantFactory()
    client = client_for(competition)
    client.force_login(participant.user)
    response = client.post(
        f"/coordinator/problems/{problem.pk}/attachments/", {"file": SimpleUploadedFile("a.csv", b"1\n")}
    )
    assert response.status_code in (302, 403, 404)
    assert not ProblemAttachment.objects.exists()


def test_participant_panel_lists_attachments(client_for, competition):
    from apps.accounts.tests.factories import UserFactory
    from apps.competitions.tests.factories import CurrentEditionFactory

    stage = StageFactory(edition=CurrentEditionFactory())
    participant = ParticipantFactory(user=UserFactory(groups=["participant"]))
    StageEntryFactory(stage=stage, participant=participant)
    problem = ProblemFactory(stage=stage)
    without = ProblemFactory(stage=stage)
    attachment = _add(problem, description="dane pomiarowe")
    client = client_for(competition)
    client.force_login(participant.user)

    body = client.get("/me/?tab=zadania").content.decode()

    assert f'id="problem-{without.pk}"' in body
    assert _url(attachment) in body
    assert "dane pomiarowe" in body


def test_problems_page_payload_for_djcms_lists_attachments(competition):
    from apps.cms.djcms_api.serializers import problem_dto

    problem = ProblemFactory()
    attachment = _add(problem)
    dto = problem_dto(problem)
    assert dto["attachments"][0]["filename"] == "jony2.npy"
    assert dto["attachments"][0]["url"].endswith(_url(attachment))


def test_command_is_idempotent(competition, tmp_path):
    problem = ProblemFactory()
    files = []
    for number in (2, 3, 4):
        path = tmp_path / f"jony{number}.npy"
        path.write_bytes(NPY + bytes([number]))
        files.append(str(path))
    args = [
        "--competition",
        competition.slug,
        "--stage",
        str(problem.stage_id),
        "--number",
        str(problem.number),
    ]

    call_command("add_problem_attachment", *args, *files)
    call_command("add_problem_attachment", *args, *files)

    assert sorted(ProblemAttachment.objects.filter(problem=problem).values_list("filename", flat=True)) == [
        "jony2.npy",
        "jony3.npy",
        "jony4.npy",
    ]
    (tmp_path / "jony2.npy").write_bytes(NPY + b"changed")
    with pytest.raises(CommandError):
        call_command("add_problem_attachment", *args, files[0])
    call_command("add_problem_attachment", *args, "--replace", files[0])
    attachment = ProblemAttachment.objects.get(problem=problem, filename="jony2.npy")
    assert attachment.size == len(NPY + b"changed")
    assert ProblemAttachment.objects.filter(problem=problem).count() == 3
