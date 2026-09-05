"""Kryteria 3–4 z T-08: panel uczestnika, upload przez HTMX i zamknięcie po deadline."""

from datetime import UTC, datetime

import pytest

from apps.competitions.models import Stage
from apps.submissions.models import Submission
from apps.submissions.tests.factories import pdf_upload

from .conftest import close_submissions

pytestmark = pytest.mark.django_db


def test_dashboard_lists_problems_and_upload_form(web_client, participant, entry, problems):
    web_client.force_login(participant.user)
    response = web_client.get("/me/")
    content = response.content.decode()

    assert response.status_code == 200
    for problem in problems:
        assert problem.title in content
    assert 'name="file"' in content
    assert f"/me/stages/{entry.stage_id}/problems/1/upload/" in content
    # Odliczanie musi mieć czas serwera, nie zegar przeglądarki.
    assert "data-deadline=" in content
    assert "data-server-now=" in content


def test_dashboard_after_deadline_has_no_upload_form(web_client, participant, entry, problems):
    close_submissions(entry.stage)
    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()

    assert 'name="file"' not in content
    assert "Termin oddania rozwiązań minął" in content
    assert "Etap jest zamknięty" in content


def test_upload_returns_new_version_row(web_client, participant, entry, problems):
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"

    response = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true")
    content = response.content.decode()

    assert response.status_code == 200
    assert "wersja 1" in content
    assert Submission.objects.filter(entry=entry, problem=problems[0]).count() == 1

    second = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true")
    assert "wersja 2" in second.content.decode()
    assert Submission.objects.filter(entry=entry, problem=problems[0]).count() == 2


def test_upload_after_deadline_is_refused_with_message(web_client, participant, entry, problems):
    close_submissions(entry.stage)
    web_client.force_login(participant.user)

    response = web_client.post(
        f"/me/stages/{entry.stage_id}/problems/1/upload/",
        {"file": pdf_upload()},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert "Termin oddania rozwiązań minął" in response.content.decode()
    assert not Submission.objects.filter(entry=entry).exists()


def test_participant_can_register_for_open_elimination_stage(web_client, participant, elim_stage):
    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()
    assert f"/me/stages/{elim_stage.pk}/register/" in content

    response = web_client.post(f"/me/stages/{elim_stage.pk}/register/")

    assert response.status_code == 302
    assert elim_stage.entries.filter(participant=participant).exists()


def test_dashboard_shows_the_deadline_in_local_time(web_client, participant, entry, problems):
    """Etykiety czasu (przegląd T-08, ustalenie 4).

    W bazie deadline jest w UTC, ale szablon renderuje ``TIME_ZONE`` serwisu – dopisek „(UTC)”
    podawał uczestnikowi godzinę przesuniętą o dwie godziny względem tego, co widział obok.
    """
    # Deadline zostaje w przyszłości (inaczej panel pokazałby etap zamknięty), reszta osi czasu
    # przesuwa się za nim – kolejność dat pilnuje constraint w bazie.
    Stage.objects.filter(pk=entry.stage_id).update(
        deadline_at=datetime(2027, 7, 15, 10, 0, tzinfo=UTC),
        review_deadline_at=datetime(2027, 7, 20, 10, 0, tzinfo=UTC),
        appeal_window_opens_at=datetime(2027, 7, 21, 10, 0, tzinfo=UTC),
        appeal_window_closes_at=datetime(2027, 7, 28, 10, 0, tzinfo=UTC),
    )
    web_client.force_login(participant.user)

    content = web_client.get("/me/").content.decode()

    # 10:00 UTC w lipcu to 12:00 w Europe/Warsaw.
    assert "15 lipca 2027, 12:00 (czas polski)" in content
    assert "(UTC)" not in content
