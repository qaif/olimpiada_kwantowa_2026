"""Logika domenowa rozwiązań: upload z egzekwowaniem deadline'u i blokada etapu po deadline.

Widoki tylko orkiestrują. Deadline jest liczony wyłącznie po stronie serwera (``timezone.now()``),
w transakcji z ``SELECT ... FOR UPDATE`` na ``StageEntry`` – ta sama blokada nadaje numer wersji,
więc dwa równoległe uploady dostają wersje 1 i 2, a nie kolizję unikalności (PROJEKT.md 1.3).
"""

from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import status

from apps.competitions.models import Problem, Stage, StageEntry, StageEntryStatus
from apps.core.api import DomainError

from .models import AvStatus, Submission, SubmissionFile, SubmissionStatus
from .storage import build_object_key, get_submission_storage
from .validators import validate_upload

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 1024 * 1024


def _forbidden(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_403_FORBIDDEN)


def _sha256_of(upload) -> str:
    """Suma kontrolna liczona strumieniowo – plik nigdy nie musi zmieścić się w pamięci naraz."""
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(HASH_CHUNK_SIZE), b""):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def get_problem(stage: Stage, problem_number: int) -> Problem:
    problem = Problem.objects.filter(stage=stage, number=problem_number).first()
    if problem is None:
        raise DomainError(
            "Nie ma takiego zadania w tym etapie.", "PROBLEM_NOT_FOUND", status.HTTP_404_NOT_FOUND
        )
    return problem


def _locked_entry(user, stage: Stage) -> StageEntry:
    """Wpis uczestnika wyłącznie z ``request.user`` – identyfikator z body nigdy tu nie dociera."""
    entry = (
        StageEntry.objects.select_for_update(of=("self",))
        .filter(participant__user=user, stage=stage)
        .select_related("participant", "stage", "stage__edition")
        .first()
    )
    if entry is None:
        raise _forbidden("Nie jesteś zarejestrowany do tego etapu.", "NOT_REGISTERED")
    if entry.status == StageEntryStatus.DISQUALIFIED:
        raise _forbidden("Wpis do etapu jest zdyskwalifikowany.", "ENTRY_DISQUALIFIED")
    return entry


def sanitize_original_name(name: str | None) -> str:
    """Sprowadza nazwę od użytkownika do samej nazwy pliku, bez znaków sterujących, do 255 znaków.

    Nazwa jest wyłącznie metadaną (ścieżkę w storage buduje ``build_object_key``), ale trafia do
    ``Content-Disposition``, do adminki i do eksportów – więc żadnych separatorów ścieżki ani
    bajtów < 0x20 / 0x7F, które mogłyby łamać nagłówek HTTP albo wiersz CSV.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in base if char >= " " and char != "\x7f")
    return cleaned.strip()[:255] or "plik"


def _assert_window_open(stage: Stage, now) -> None:
    """Okno uploadu to przedział półotwarty ``[opens_at, submission_deadline)``.

    Chwila równa ``submission_deadline`` (czyli ``deadline_at + grace_seconds``) jest już po
    terminie – tak samo jak w ``Stage.is_open_for_submissions``, żeby API i model nigdy nie
    odpowiadały inaczej na to samo pytanie. ``closed_at`` zamyka okno niezależnie od zegara.
    """
    if now < stage.opens_at:
        raise _forbidden("Etap jeszcze się nie otworzył.", "STAGE_NOT_OPEN")
    if now >= stage.submission_deadline or stage.closed_at is not None:
        raise _forbidden("Termin oddania rozwiązań minął.", "DEADLINE_PASSED")


@transaction.atomic
def create_submission(user, stage: Stage, problem_number: int, upload, *, now=None) -> Submission:
    """Przyjmuje rozwiązanie: blokada wpisu → deadline → walidacja treści → storage → skan."""
    problem = get_problem(stage, problem_number)
    entry = _locked_entry(user, stage)
    now = now or timezone.now()
    _assert_window_open(stage, now)

    ext, mime = validate_upload(upload, problem.allowed_formats, problem.max_file_mb)
    sha256 = _sha256_of(upload)

    previous = Submission.objects.filter(entry=entry, problem=problem).aggregate(top=Max("version"))["top"]
    submission = Submission.objects.create(
        entry=entry,
        problem=problem,
        version=(previous or 0) + 1,
        submitted_at=now,
        is_late=now > stage.deadline_at,
        status=SubmissionStatus.SUBMITTED,
    )

    object_key = build_object_key(
        edition_id=stage.edition_id,
        stage_id=stage.pk,
        participant_code=entry.participant.public_code,
        submission_uuid=submission.uuid,
        sha256=sha256,
        ext=ext,
    )
    get_submission_storage().put(object_key, upload, mime)

    submission_file = SubmissionFile.objects.create(
        submission=submission,
        object_key=object_key,
        sha256=sha256,
        original_name=sanitize_original_name(getattr(upload, "name", "")),
        mime=mime,
        size_bytes=upload.size,
        av_status=AvStatus.PENDING,
        created_at=now,
    )

    # Kolejkowanie dopiero po commicie: worker nie może zobaczyć id, którego jeszcze nie ma w bazie.
    def _enqueue_scan(file_id: int = submission_file.pk) -> None:
        from .tasks import scan_submission_file

        scan_submission_file.delay(file_id)

    transaction.on_commit(_enqueue_scan)
    return submission


def submissions_for_user(user):
    """Rozwiązania widoczne dla użytkownika. Filtr roli jest w queryseckie, nie w widoku."""
    return (
        Submission.objects.for_user(user)
        .select_related("entry", "entry__participant", "entry__stage", "problem")
        .prefetch_related("files")
        .order_by("entry__stage_id", "problem__number", "-version")
    )


def grouped_submissions_for_user(user) -> list[dict]:
    """Własne rozwiązania pogrupowane per zadanie: najnowsza wersja + historia starszych."""
    groups: dict[tuple[int, int], dict] = {}
    for submission in submissions_for_user(user):
        key = (submission.entry_id, submission.problem_id)
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "stage_id": submission.entry.stage_id,
                "problem_id": submission.problem_id,
                "problem_number": submission.problem.number,
                "latest": submission,
                "history": [],
            }
        else:
            group["history"].append(submission)
    return list(groups.values())


#: Statusy, w których wersja nadaje się do oceny i którą wypada zablokować przy zamknięciu etapu.
LOCKABLE_STATUSES = (SubmissionStatus.SUBMITTED, SubmissionStatus.SCANNING)


@transaction.atomic
def close_stage(stage: Stage) -> int:
    """Blokuje najnowszą *nadającą się do oceny* wersję każdej pary (wpis, zadanie).

    Dla każdej pary schodzimy po wersjach od najnowszej i zatrzymujemy się na pierwszej, która nie
    jest ``REJECTED_INFECTED`` – wersja odrzucona przez antywirusa nie może zjeść uczestnikowi
    całego etapu, więc do oceny wchodzi ostatnia wcześniejsza wersja. Znaleziona wersja:

    - ``SUBMITTED`` → blokujemy tutaj,
    - ``SCANNING`` → zostawiamy skanowi; ``apply_scan_verdict`` ustawi LOCKED, bo etap ma już
      ``closed_at`` (gdyby okazała się zainfekowana, zablokuje zamiast niej wersję wcześniejszą),
    - cokolwiek innego (m.in. ``LOCKED``) → nie ruszamy.

    Idempotentny: po pierwszym przebiegu pierwsza nieodrzucona wersja ma ``LOCKED``, a zatrzymanie
    się na niej nie pozwala zejść niżej i zablokować dodatkowo starszej historii.
    Zwraca liczbę faktycznie zablokowanych rozwiązań.
    """
    rows = (
        Submission.objects.filter(entry__stage=stage)
        .order_by("entry_id", "problem_id", "-version")
        .values_list("pk", "entry_id", "problem_id", "status")
    )
    to_lock: list[int] = []
    decided: set[tuple[int, int]] = set()
    for pk, entry_id, problem_id, status_value in rows:
        key = (entry_id, problem_id)
        if key in decided:
            continue
        if status_value == SubmissionStatus.REJECTED_INFECTED:
            continue  # zainfekowana wersja nie liczy się do oceny – patrzymy na wcześniejszą
        decided.add(key)
        if status_value == SubmissionStatus.SUBMITTED:
            to_lock.append(pk)
    if not to_lock:
        return 0
    return Submission.objects.filter(pk__in=to_lock).update(status=SubmissionStatus.LOCKED)


def due_stages(now=None):
    """Etapy po deadline (z tolerancją) i jeszcze niezamknięte.

    ``submission_deadline`` to ``deadline_at + grace_seconds``, więc ``deadline_at < now`` jest
    warunkiem koniecznym – ORM zawęża zbiór, a tolerancja jest sprawdzana na obiekcie.
    """
    now = now or timezone.now()
    candidates = Stage.objects.filter(closed_at__isnull=True, deadline_at__lt=now).order_by("id")
    return [stage for stage in candidates if stage.submission_deadline <= now]


def close_due_stages(now=None) -> list[int]:
    """Zamyka wszystkie etapy, którym minął deadline. Zwraca listę id zamkniętych etapów."""
    now = now or timezone.now()
    closed: list[int] = []
    for stage in due_stages(now):
        with transaction.atomic():
            locked = close_stage(stage)
            stage.closed_at = now
            stage.save(update_fields=["closed_at"])
        logger.info("Etap %s zamknięty, zablokowanych rozwiązań: %s", stage.pk, locked)
        closed.append(stage.pk)
    return closed


def _status_after_scan(submission: Submission) -> str:
    """Stan, do którego wraca zgłoszenie po zakończonym skanie.

    Zwykle ``SUBMITTED``. Jeśli w międzyczasie ``close_due_stages`` zamknęło etap, wersja nie może
    wrócić do stanu edytowalnego – blokada należy się jej tak samo, jakby skan zdążył przed
    deadline'em. Bez tego zgłoszenie skanowane w chwili zamknięcia etapu wypadałoby z oceniania.
    """
    if submission.entry.stage.closed_at is not None:
        return SubmissionStatus.LOCKED
    return SubmissionStatus.SUBMITTED


def _lock_fallback_version(submission: Submission) -> None:
    """Po odrzuceniu zainfekowanej wersji w zamkniętym etapie do oceny wraca ostatnia wcześniejsza."""
    fallback = (
        Submission.objects.filter(
            entry_id=submission.entry_id, problem_id=submission.problem_id, version__lt=submission.version
        )
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .order_by("-version")
        .first()
    )
    if fallback is not None and fallback.status == SubmissionStatus.SUBMITTED:
        fallback.status = SubmissionStatus.LOCKED
        fallback.save(update_fields=["status"])


def _scanned_submission_file(pk: int) -> SubmissionFile:
    return (
        SubmissionFile.objects.select_for_update(of=("self",))
        .select_related("submission", "submission__entry", "submission__entry__stage")
        .get(pk=pk)
    )


def apply_scan_verdict(submission_file: SubmissionFile, verdict: str, signature: str = "") -> SubmissionFile:
    """Zapisuje wynik skanu i przenosi zgłoszenie w odpowiedni stan (PROJEKT.md 2.4)."""
    with transaction.atomic():
        locked = _scanned_submission_file(submission_file.pk)
        infected = verdict == AvStatus.INFECTED
        locked.av_status = AvStatus.INFECTED if infected else AvStatus.CLEAN
        locked.av_signature = (signature or "")[:200]
        locked.scanned_at = timezone.now()
        locked.save(update_fields=["av_status", "av_signature", "scanned_at"])
        submission = locked.submission
        stage_closed = submission.entry.stage.closed_at is not None
        if infected:
            submission.status = SubmissionStatus.REJECTED_INFECTED
            submission.save(update_fields=["status"])
            if stage_closed:
                _lock_fallback_version(submission)
        elif submission.status == SubmissionStatus.SCANNING:
            # CLEAN wraca do SUBMITTED (albo od razu do LOCKED, jeśli etap zdążył się zamknąć);
            # statusów dalszych w cyklu życia skan nie cofa.
            submission.status = _status_after_scan(submission)
            submission.save(update_fields=["status"])
    return locked


def apply_scan_error(submission_file: SubmissionFile, reason: str) -> SubmissionFile:
    """Zamyka skan błędem trwałym: plik dostaje ``ERROR``, zgłoszenie wychodzi ze ``SCANNING``.

    Używane, gdy ponowienie nic nie da – nie ma obiektu w storage albo plik przekracza
    ``StreamMaxLength`` clamd. Zgłoszenie wraca do stanu sprzed skanu (``SUBMITTED``, a przy
    zamkniętym etapie ``LOCKED``), żeby nie wisiało w ``SCANNING`` do końca świata.
    """
    with transaction.atomic():
        locked = _scanned_submission_file(submission_file.pk)
        locked.av_status = AvStatus.ERROR
        locked.av_signature = (reason or "")[:200]
        locked.scanned_at = timezone.now()
        locked.save(update_fields=["av_status", "av_signature", "scanned_at"])
        submission = locked.submission
        if submission.status == SubmissionStatus.SCANNING:
            submission.status = _status_after_scan(submission)
            submission.save(update_fields=["status"])
    return locked
