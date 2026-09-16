"""Paczka ZIP z pracami etapu: zawartość, anonimowe nazwy, zakresy i ślad audytowy.

Prośba organizatora brzmiała „koordynator powinien móc pobrać prace”, ale tym, co trzeba tu
pilnować, jest **co** wychodzi z serwisu: nazwy plików są daną osobową, a paczka wędruje dalej
do komitetu, więc żaden test nie sprawdza samego kodu odpowiedzi.
"""

import zipfile
from io import BytesIO

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.packaging import README_NAME
from apps.submissions.services import build_stage_zip
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import PDF_BYTES, SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db

#: Nazwa, jaką uczestnicy nadają plikom w rzeczywistości – i której paczka nie może przepuścić.
PERSONAL_NAME = "Jan_Kowalski_LO5.pdf"


def stored_submission(entry, problem, *, version=1, av_status=AvStatus.CLEAN, content=PDF_BYTES):
    """Rozwiązanie z prawdziwym plikiem w storage testowym (backend lokalny)."""
    submission = SubmissionFactory(
        entry=entry, problem=problem, version=version, status=SubmissionStatus.LOCKED
    )
    submission_file = SubmissionFileFactory(
        submission=submission, av_status=av_status, original_name=PERSONAL_NAME
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(content), "application/pdf")
    return submission


@pytest.fixture
def stage():
    return StageFactory()


@pytest.fixture
def problems(stage):
    return [ProblemFactory(stage=stage, number=1), ProblemFactory(stage=stage, number=2)]


def names_in(package) -> list[str]:
    with zipfile.ZipFile(package.stream) as archive:
        return sorted(archive.namelist())


def test_zip_ma_po_jednym_pliku_na_prace_i_anonimowe_nazwy(stage, problems):
    """Nazwa w paczce to kod uczestnika, numer zadania i wersja – nigdy nazwa od uczestnika."""
    entry = StageEntryFactory(stage=stage)
    stored_submission(entry, problems[0])
    stored_submission(entry, problems[1])
    code = entry.participant.public_code

    package = build_stage_zip(stage)

    assert package.count == 2
    assert names_in(package) == sorted([README_NAME, f"{code}_zad1_v1.pdf", f"{code}_zad2_v1.pdf"])
    with zipfile.ZipFile(package.stream) as archive:
        readme = archive.read(README_NAME).decode("utf-8")
        assert archive.read(f"{code}_zad1_v1.pdf") == PDF_BYTES
    assert "Prac w paczce: 2." in readme
    # README jest spisem treści, a nie listą autorów: nazwa od uczestnika nie ma tu wstępu.
    assert PERSONAL_NAME not in readme


def test_zip_bierze_najnowsza_czysta_wersje(stage, problems):
    """Wersja po skanie wygrywa z nowszą, która skanu jeszcze nie przeszła."""
    entry = StageEntryFactory(stage=stage)
    stored_submission(entry, problems[0], version=1, content=b"%PDF-1.7 stara\n")
    stored_submission(entry, problems[0], version=2, av_status=AvStatus.PENDING)

    package = build_stage_zip(stage)

    assert names_in(package) == sorted([README_NAME, f"{entry.participant.public_code}_zad1_v1.pdf"])


def test_praca_bez_czystego_pliku_nie_wchodzi_do_paczki(stage, problems):
    entry = StageEntryFactory(stage=stage)
    stored_submission(entry, problems[0], av_status=AvStatus.CLEAN)
    stored_submission(entry, problems[1], av_status=AvStatus.INFECTED)

    package = build_stage_zip(stage)

    assert package.count == 1
    assert f"{entry.participant.public_code}_zad2_v1.pdf" not in names_in(package)


def test_audyt_notuje_liczbe_a_nie_nazwy(stage, problems):
    entry = StageEntryFactory(stage=stage)
    stored_submission(entry, problems[0])

    build_stage_zip(stage, actor=CoordinatorFactory())

    entry_log = AuditLog.objects.get(action="stage.downloaded_zip")
    assert entry_log.target_id == str(stage.pk)
    assert entry_log.diff == {"count": 1, "scope": "stage", "problem": None}
    assert entry.participant.public_code not in str(entry_log.diff)


def test_filtr_po_zadaniu_zawezza_paczke(stage, problems):
    entry = StageEntryFactory(stage=stage)
    stored_submission(entry, problems[0])
    stored_submission(entry, problems[1])

    package = build_stage_zip(stage, problem=problems[1])

    assert names_in(package) == sorted([README_NAME, f"{entry.participant.public_code}_zad2_v1.pdf"])


def test_zaznaczenie_ogranicza_paczke_a_cudze_id_odpada(stage, problems):
    """Identyfikator z innego etapu nie wchodzi do paczki – zawężenie po etapie jest walidacją."""
    entry = StageEntryFactory(stage=stage)
    wanted = stored_submission(entry, problems[0])
    stored_submission(entry, problems[1])
    other_stage = StageFactory()
    foreign = stored_submission(
        StageEntryFactory(stage=other_stage), ProblemFactory(stage=other_stage, number=1)
    )

    package = build_stage_zip(stage, submission_ids=[wanted.pk, foreign.pk])

    assert package.count == 1
    assert names_in(package) == sorted([README_NAME, f"{entry.participant.public_code}_zad1_v1.pdf"])


def test_pusty_zakres_to_404(stage, problems):
    with pytest.raises(DomainError) as excinfo:
        build_stage_zip(stage)

    assert excinfo.value.status_code == 404
    assert excinfo.value.machine_code == "NO_SUBMISSIONS"
