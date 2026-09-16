"""Pobieranie prac z paneli: odnośniki w tabelach i paczki ZIP koordynatora oraz recenzenta.

Prośby organizatora: „uczestnik powinien móc pobrać swoje rozwiązania linkiem, tak samo jak
koordynator”, „recenzent powinien móc pobrać paczkę ZIP ze wszystkimi swoimi rozwiązaniami” oraz
„koordynator też powinien móc zaznaczyć zadania i pobrać je jako zip”.
"""

import zipfile
from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.packaging import README_NAME
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db

PERSONAL_NAME = "Jan_Kowalski_LO5.pdf"


def stored_submission(entry, problem, *, av_status=AvStatus.CLEAN, status=SubmissionStatus.LOCKED):
    submission = SubmissionFactory(entry=entry, problem=problem, status=status)
    submission_file = SubmissionFileFactory(
        submission=submission, av_status=av_status, original_name=PERSONAL_NAME
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(PDF_BYTES), "application/pdf")
    return submission


def zip_names(response) -> list[str]:
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        return sorted(archive.namelist())


# --- uczestnik ---------------------------------------------------------------------------------


def test_karta_zadania_pokazuje_odnosnik_pobrania_wlasnej_wersji(web_client, participant, entry, problems):
    submission = stored_submission(entry, problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(participant.user)

    response = web_client.get(reverse("web:me"))

    assert response.status_code == 200
    expected = reverse("submissions:submission-download", kwargs={"pk": submission.pk})
    assert f'href="{expected}"' in response.content.decode()


def test_uczestnik_pobiera_wlasna_prace_pod_wlasna_nazwa(web_client, participant, entry, problems):
    """Właściciel dostaje swoją nazwę pliku – anonimowa jest dopiero dla pozostałych ról."""
    submission = stored_submission(entry, problems[0], av_status=AvStatus.PENDING)
    web_client.force_login(participant.user)

    response = web_client.get(reverse("submissions:submission-download", kwargs={"pk": submission.pk}))

    assert response.status_code == 200
    assert PERSONAL_NAME in response["Content-Disposition"]
    assert b"".join(response.streaming_content) == PDF_BYTES


# --- koordynator -------------------------------------------------------------------------------


def test_tabela_przydzialow_ma_odnosnik_w_wierszu_i_podpowiedz_przy_skanie(
    web_client, coordinator, elim_stage, problems
):
    clean = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    scanning = stored_submission(StageEntryFactory(stage=elim_stage), problems[1], av_status=AvStatus.PENDING)
    web_client.force_login(coordinator)

    response = web_client.get(reverse("web:coordinator-stage-assignments", args=[elim_stage.pk]))

    body = response.content.decode()
    assert f'href="{reverse("submissions:submission-download", kwargs={"pk": clean.pk})}"' in body
    assert f'href="{reverse("submissions:submission-download", kwargs={"pk": scanning.pk})}"' not in body
    assert "skan w toku" in body


def test_strona_przydzialow_daje_paczke_calego_etapu_i_zadania(web_client, coordinator, elim_stage, problems):
    stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    web_client.force_login(coordinator)

    response = web_client.get(reverse("web:coordinator-stage-assignments", args=[elim_stage.pk]))

    body = response.content.decode()
    download = reverse("web:coordinator-stage-download", args=[elim_stage.pk])
    assert f'href="{download}"' in body
    assert f'href="{download}?problem={problems[0].pk}"' in body
    assert 'name="submission_ids"' in body


def test_koordynator_pobiera_paczke_calego_etapu(web_client, coordinator, elim_stage, problems):
    first = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    second = stored_submission(StageEntryFactory(stage=elim_stage), problems[1])
    web_client.force_login(coordinator)

    response = web_client.get(reverse("web:coordinator-stage-download", args=[elim_stage.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert zip_names(response) == sorted(
        [
            README_NAME,
            f"{first.entry.participant.public_code}_zad1_v1.pdf",
            f"{second.entry.participant.public_code}_zad2_v1.pdf",
        ]
    )


def test_paczka_zaznaczonych_prac_obejmuje_tylko_zaznaczone(web_client, coordinator, elim_stage, problems):
    wanted = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    stored_submission(StageEntryFactory(stage=elim_stage), problems[1])
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-stage-download", args=[elim_stage.pk]),
        {"submission_ids": [wanted.pk]},
    )

    assert response.status_code == 200
    assert zip_names(response) == sorted([README_NAME, f"{wanted.entry.participant.public_code}_zad1_v1.pdf"])


def test_puste_zaznaczenie_wraca_z_komunikatem(web_client, coordinator, elim_stage, problems):
    stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    web_client.force_login(coordinator)

    response = web_client.post(
        reverse("web:coordinator-stage-download", args=[elim_stage.pk]), {}, follow=True
    )

    assert "Nie zaznaczono żadnej pracy." in response.content.decode()


def test_paczka_etapu_bez_prac_to_404_z_powodem(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.get(reverse("web:coordinator-stage-download", args=[elim_stage.pk]))

    assert response.status_code == 404
    assert "Brak prac do pobrania" in response.content.decode()


def test_paczki_etapu_nie_pobierze_nikt_poza_koordynatorem(
    web_client, participant, entry, elim_stage, problems
):
    stored_submission(entry, problems[0])
    web_client.force_login(participant.user)

    response = web_client.get(reverse("web:coordinator-stage-download", args=[elim_stage.pk]))

    assert response.status_code == 403


# --- recenzent ---------------------------------------------------------------------------------


def test_lista_recenzenta_ma_przycisk_paczki(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    submission = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    ReviewFactory(submission=submission, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web_client.force_login(reviewer.user)

    response = web_client.get(reverse("web:review-list"))

    assert f'href="{reverse("web:review-download")}"' in response.content.decode()


def test_recenzent_pobiera_paczke_wlasnych_prac(web_client, elim_stage, problems):
    reviewer = ActiveReviewerFactory()
    mine = stored_submission(StageEntryFactory(stage=elim_stage), problems[0])
    ReviewFactory(submission=mine, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    stranger = stored_submission(StageEntryFactory(stage=elim_stage), problems[1])
    ReviewFactory(submission=stranger, reviewer=ActiveReviewerFactory())
    web_client.force_login(reviewer.user)

    response = web_client.get(reverse("web:review-download"))

    assert response.status_code == 200
    assert zip_names(response) == sorted([README_NAME, f"{mine.entry.participant.public_code}_zad1_v1.pdf"])


def test_recenzent_bez_przydzialow_dostaje_404_z_powodem(web_client, elim_stage):
    reviewer = ActiveReviewerFactory()
    web_client.force_login(reviewer.user)

    response = web_client.get(reverse("web:review-download"))

    assert response.status_code == 404
    assert "Brak przydzielonych prac do pobrania." in response.content.decode()
