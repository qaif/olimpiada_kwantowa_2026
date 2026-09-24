"""Tryb testowy oceny AI: praca testowa koordynatora i jej oceny (prośba organizatora z 24.09.2026).

„Włącz wszystkich dostawców dla testów” – organizator chce wypróbować każdego dostawcę, zanim
potwierdzi z nim umowę powierzenia. Umowa powierzenia dotyczy **danych osobowych uczestników**,
więc bramkę da się bezpiecznie pominąć wyłącznie dla pliku, który takich danych nie niesie:
własnego przykładu koordynatora. Trzy reguły to utrzymują i każda stoi tutaj:

- przy wgraniu koordynator **oświadcza**, że plik nie zawiera danych osobowych uczestników
  (oświadczenie trafia do dziennika zdarzeń razem z wgraniem),
- plik **identyczny** (ten sam SHA-256) z plikiem pracy uczestnika tego konkursu jest odrzucany –
  najprostsza droga obejścia bramki („wgram pracę Kowalskiego jako testową”) jest zamknięta,
  a obejście trudniejsze (przepisanie, zdjęcie ekranu) jest już świadomym złamaniem oświadczenia,
- ocena pracy testowej istnieje **wyłącznie** na karcie zadania koordynatora: nie ma jej
  w panelu recenzenta ani uczestnika, w eksporcie danych, w statystykach zgodności z oceną
  końcową ani w rejestrze czynności. Liczy się natomiast do zużycia i do limitu wydatków – bo
  kosztuje tyle samo, co każda inna.

Plik przechodzi tę samą walidację treści (``apps.submissions.validators``) i ten sam skan
antywirusowy (kolejka ``scan``), co praca uczestnika; dochodzi PNG, którego prace uczestników nie
przyjmują, a który jest najczęstszym formatem zrzutu ekranu z przykładowym rozwiązaniem.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import AvStatus, SubmissionFile
from apps.submissions.storage import get_submission_storage, sanitize_segment
from apps.submissions.validators import MEGABYTE, declared_extension, validate_upload

from . import prompt
from .models import AiAssessment, AiAssessmentStatus, AiTestWork

logger = logging.getLogger(__name__)

#: Formaty pracy testowej. PNG ponad formaty prac uczestników – patrz docstring modułu.
TEST_FORMATS = ("pdf", "jpg", "png", "py", "ipynb")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: Prefiks kluczy prac testowych w storage'u prac – osobny, żeby żaden mechanizm prac uczestników
#: (paczki ZIP, retencja, przekazywanie) nie wziął ich za pracę.
OBJECT_PREFIX = "ai-test"
HASH_CHUNK_SIZE = 1024 * 1024


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _sha256_of(upload) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(HASH_CHUNK_SIZE), b""):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def _validate(upload, max_file_mb: int) -> tuple[str, str]:
    """``(rozszerzenie, mime)`` po sprawdzeniu treści – reguły prac uczestników plus PNG."""
    ext = declared_extension(getattr(upload, "name", ""))
    if ext != "png":
        return validate_upload(upload, [item for item in TEST_FORMATS if item != "png"], max_file_mb)
    size = getattr(upload, "size", None) or 0
    if size > int(max_file_mb) * MEGABYTE:
        raise DomainError(
            f"Plik ma {size} B, limit dla tego zadania to {max_file_mb} MB.",
            "FILE_TOO_LARGE",
            http.HTTP_400_BAD_REQUEST,
        )
    if size == 0:
        raise _bad_request("Plik jest pusty.", "INVALID_FILE_TYPE")
    upload.seek(0)
    header = upload.read(len(PNG_MAGIC))
    upload.seek(0)
    if header != PNG_MAGIC:
        raise _bad_request("Treść pliku nie jest obrazem PNG (brak sygnatury PNG).", "INVALID_FILE_TYPE")
    return "png", "image/png"


@transaction.atomic
def upload_test_work(
    problem, upload, *, actor, no_personal_data: bool, label: str = "", request=None
) -> AiTestWork:
    """Przyjmuje pracę testową: oświadczenie → walidacja → odmowa pracy uczestnika → storage → skan."""
    from .services import is_enabled

    competition = problem.stage.edition.competition
    if not is_enabled(competition):
        raise _conflict("Ocena AI jest w tym konkursie wyłączona.", "AI_DISABLED")
    if not no_personal_data:
        raise _bad_request(
            "Potwierdź, że plik nie zawiera danych osobowych uczestników – praca testowa trafia do "
            "dostawców także bez umowy powierzenia.",
            "AI_TEST_DECLARATION",
        )
    ext, mime = _validate(upload, problem.max_file_mb)
    sha256 = _sha256_of(upload)
    if SubmissionFile.objects.filter(sha256=sha256, submission__competition=competition).exists():
        raise _bad_request(
            "Ten plik jest pracą uczestnika tego konkursu. Pracą testową może być wyłącznie własny "
            "przykład koordynatora – prace uczestników ocenia się zwykłym zleceniem, u dostawcy "
            "z potwierdzoną umową powierzenia.",
            "AI_TEST_IS_SUBMISSION",
        )
    pages = None
    if mime == prompt.PDF_MIME:
        upload.seek(0)
        pages = prompt.pdf_pages(upload.read())
        upload.seek(0)
    object_key = "/".join(
        [
            OBJECT_PREFIX,
            sanitize_segment(competition.pk),
            sanitize_segment(problem.pk),
            uuid.uuid4().hex,
            f"{sha256}.{ext}",
        ]
    )
    get_submission_storage().put(object_key, upload, mime)
    work = AiTestWork.objects.create(
        competition=competition,
        problem=problem,
        label=(label or "").strip()[:120],
        object_key=object_key,
        sha256=sha256,
        mime=mime,
        size_bytes=upload.size,
        page_count=pages,
        av_status=AvStatus.PENDING,
        uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
    )

    def _enqueue(pk: int = work.pk) -> None:
        from .tasks import scan_ai_test_work

        scan_ai_test_work.delay(pk)

    transaction.on_commit(_enqueue)
    audit(
        actor,
        "ai_grading.test_work_uploaded",
        work,
        {"problem": problem.pk, "mime": mime, "size": upload.size, "no_personal_data": True},
        request=request,
    )
    return work


def apply_scan_verdict(work_id: int, verdict: str) -> None:
    """Werdykt skanu. Zainfekowany plik znika ze storage'u od razu – nikt go już nie otworzy."""
    work = AiTestWork.objects.filter(pk=work_id).first()
    if work is None:
        return
    status = verdict if verdict in (AvStatus.CLEAN, AvStatus.INFECTED) else AvStatus.ERROR
    key = work.object_key
    fields = {"av_status": status}
    if status == AvStatus.INFECTED:
        fields["object_key"] = ""
    AiTestWork.objects.filter(pk=work_id).update(**fields)
    if status == AvStatus.INFECTED and key:
        _delete_object_on_commit(key)


def _delete_object_on_commit(key: str) -> None:
    def _delete() -> None:
        try:
            get_submission_storage().delete(key)
        except Exception:  # noqa: BLE001 - sierota w storage'u nie może wywrócić operacji
            logger.exception("Nie udało się usunąć pracy testowej %s ze storage'u.", key)

    transaction.on_commit(_delete)


@transaction.atomic
def delete_test_work(work: AiTestWork, *, actor, request=None) -> None:
    """Kasuje pracę testową z jej ocenami. Nie w trakcie liczenia – ocena w locie straciłaby wiersz."""
    if work.assessments.filter(status__in=(AiAssessmentStatus.PENDING, AiAssessmentStatus.RUNNING)).exists():
        raise _conflict(
            "Ocena tej pracy testowej jeszcze się liczy – poczekaj na wynik.", "AI_TEST_IN_PROGRESS"
        )
    key = work.object_key
    audit(actor, "ai_grading.test_work_deleted", work, {"problem": work.problem_id}, request=request)
    work.delete()
    if key:
        _delete_object_on_commit(key)


@transaction.atomic
def request_test_assessment(
    work: AiTestWork, *, provider: str, model: str, actor, request=None
) -> AiAssessment:
    """Zleca ocenę pracy testowej jednym dostawcą i modelem – **bez** bramki umowy powierzenia.

    Pozostałe bramki są te same co przy pracach uczestników (flaga, pakiet, klucz, limit wydatków,
    cena modelu przy limicie) – tryb testowy wydaje prawdziwe pieniądze.
    """
    from .services import _lock, _reset_fields, assert_can_request, pump, resolve_target

    row = assert_can_request(work.competition, provider, model, for_tests=True)
    provider, model = resolve_target(row, provider, model)
    if not work.is_clean:
        raise _conflict("Praca testowa nie ma jeszcze czystego skanu antywirusowego.", "AI_TEST_NOT_CLEAN")
    _lock(work.problem_id)
    current = (
        AiAssessment.objects.select_for_update()
        .filter(test_work=work, provider=provider, requested_model=model)
        .first()
    )
    fields = _reset_fields(provider=provider, model=model, now=timezone.now(), actor=actor)
    if current is None:
        current = AiAssessment.objects.create(competition=work.competition, test_work=work, **fields)
    else:
        if current.is_in_progress:
            raise _conflict("Ocena tym modelem jeszcze się liczy.", "AI_TEST_IN_PROGRESS")
        for name, value in fields.items():
            setattr(current, name, value)
        current.save()
    audit(
        actor,
        "ai_grading.test_requested",
        work,
        {"provider": provider, "model": model},
        request=request,
    )
    pump()
    return current


def delete_test_assessment(assessment: AiAssessment, *, actor, request=None) -> None:
    if not assessment.is_test:
        raise _conflict("Z tego miejsca kasuje się wyłącznie oceny prac testowych.", "AI_NOT_TEST")
    if assessment.is_in_progress:
        raise _conflict("Ocena jeszcze się liczy – poczekaj na wynik.", "AI_TEST_IN_PROGRESS")
    audit(
        actor,
        "ai_grading.test_assessment_deleted",
        assessment.test_work,
        {"provider": assessment.provider, "model": assessment.requested_model},
        request=request,
    )
    assessment.delete()


def test_works_overview(problem) -> dict:
    """Prace testowe zadania z ocenami (najnowsze pierwsze) – wyłącznie dla karty koordynatora."""
    works = list(AiTestWork.objects.filter(problem=problem).select_related("uploaded_by"))
    by_work: dict[int, list[AiAssessment]] = {}
    for item in AiAssessment.objects.filter(test_work__in=[work.pk for work in works]).order_by(
        "-requested_at", "-id"
    ):
        by_work.setdefault(item.test_work_id, []).append(item)
    rows = [{"work": work, "assessments": by_work.get(work.pk, [])} for work in works]
    return {
        "test_works": rows,
        "test_in_progress": sum(1 for row in rows for item in row["assessments"] if item.is_in_progress),
    }
