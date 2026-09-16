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


def _assert_accepts_files(stage: Stage) -> None:
    """Etap w formie rozmowy nie przyjmuje plików – niezależnie od zegara.

    Reguła jest tutaj, a nie tylko w szablonie: brak przycisku w panelu nie jest zabezpieczeniem,
    a to samo żądanie wysłane wprost do API musi dostać odmowę z kodem, a nie utworzyć wersję
    rozwiązania do zadania, którego w tym etapie nie ma.
    """
    if stage.is_interview:
        raise DomainError(
            "Ten etap odbywa się w formie rozmowy kwalifikacyjnej – nie przyjmuje plików.",
            "STAGE_NOT_ACCEPTING_FILES",
            status.HTTP_409_CONFLICT,
        )


@transaction.atomic
def create_submission(
    user, stage: Stage, problem_number: int, upload, *, now=None, request=None
) -> Submission:
    """Przyjmuje rozwiązanie: forma etapu → blokada wpisu → deadline → walidacja → storage → skan.

    ``request`` jest wyłącznie po to, żeby wpisy audytowe unieważnionej oceny (upload nowej wersji
    pracy, która była już w ocenie) miały adres IP – logika nie czyta z niego niczego.
    """
    _assert_accepts_files(stage)
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

    # Nowa wersja unieważnia ocenę rozpoczętą na poprzedniej (ocenianie przed zamknięciem etapu).
    # Import lokalny, bo zależność idzie w drugą stronę: ``apps.grading`` zna ``apps.submissions``.
    # Wołane **przed** wrzuceniem pliku do storage: odmowa (praca finalna) ma zostawić transakcję
    # bez śladu, a obiekt w buckecie przeżyłby rollback bazy jako sierota.
    from apps.grading.services import supersede_earlier_versions

    supersede_earlier_versions(entry, problem, submission, request=request)

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
        # ``final_grade`` i ``appeals__decision`` są odwrotnymi stronami relacji z apps.grading
        # i apps.appeals – dociągane po nazwie, żeby lista własnych rozwiązań nie robiła N+1.
        .select_related("entry", "entry__participant", "entry__stage", "problem", "final_grade")
        .prefetch_related("files", "appeals__decision")
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

#: Statusy pracy, która jest już w obiegu oceniania – licznik „w ocenie” na karcie etapu.
UNDER_REVIEW_STATUSES = (
    SubmissionStatus.LOCKED,
    SubmissionStatus.IN_REVIEW,
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
)


def lockable_submission_ids(stage: Stage) -> list[int]:
    """Identyfikatory wersji, które wolno zablokować do oceny – po jednej na parę (wpis, zadanie).

    Jedna reguła wyboru dla obu dróg blokowania: zamknięcia etapu (``close_stage``) i blokady
    w otwartym etapie (``lock_for_review``). Gdyby każda miała własną, koordynator, który kliknął
    „Zablokuj oddane prace do oceny”, a potem zamknął etap, mógłby dostać dwa różne zbiory prac –
    i nie miałby jak zgadnąć, która z nich jest tą ocenianą.

    Dla każdej pary schodzimy po wersjach od najnowszej i zatrzymujemy się na pierwszej, która nie
    jest ``REJECTED_INFECTED`` – wersja odrzucona przez antywirusa nie może zjeść uczestnikowi
    całego etapu, więc do oceny wchodzi ostatnia wcześniejsza wersja. Znaleziona wersja:

    - ``SUBMITTED`` → nadaje się do zablokowania i trafia na listę,
    - ``SCANNING`` → zostawiamy skanowi (``apply_scan_verdict`` domknie sprawę),
    - cokolwiek innego (m.in. ``LOCKED``) → nie ruszamy.

    Zatrzymanie się na pierwszej nieodrzuconej wersji daje idempotencję obu operacjom: po blokadzie
    ta wersja ma ``LOCKED`` i lista jest pusta, zamiast schodzić niżej i blokować starszą historię.
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
    return to_lock


@transaction.atomic
def close_stage(stage: Stage) -> int:
    """Blokuje najnowszą *nadającą się do oceny* wersję każdej pary (wpis, zadanie).

    Wybór wersji należy do ``lockable_submission_ids`` – tam jest opisana cała reguła. Tutaj
    zostaje sama konsekwencja zamknięcia etapu: wersje ``SCANNING`` dokończy skan, bo etap ma już
    (albo zaraz będzie miał) ``closed_at`` i ``_status_after_scan`` zwróci dla nich ``LOCKED``.

    Idempotentny. Zwraca liczbę faktycznie zablokowanych rozwiązań.
    """
    to_lock = lockable_submission_ids(stage)
    if not to_lock:
        return 0
    return Submission.objects.filter(pk__in=to_lock).update(status=SubmissionStatus.LOCKED)


@transaction.atomic
def lock_for_review(stage: Stage, *, actor=None, request=None) -> int:
    """Blokuje oddane prace do oceny **bez zamykania etapu** (prośba organizatora).

    Komitet chce zacząć czytać prace, zanim minie deadline – przy kilkuset zgłoszeniach czekanie do
    ostatniego dnia oznacza, że ocenianie startuje wtedy, kiedy powinno się kończyć. Blokada jest
    więc oddzielona od zamknięcia etapu: prace wchodzą do obiegu oceniania, a okno uploadu zostaje
    otwarte. Uczestnik nic nie traci – nowa wersja unieważnia rozpoczętą ocenę
    (``grading.services.supersede_earlier_versions``) i praca wraca do kolejki.

    Wersji ``SCANNING`` nie ruszamy: ich stan po skanie ustala ``_status_after_scan``, które w
    otwartym etapie zwraca ``SUBMITTED``. Praca, której skan skończył się już po kliknięciu
    przycisku, zostanie więc zwykłym „oddane” i wejdzie do oceniania przy następnym kliknięciu –
    świadomie, bo skan nie ma prawa wnioskować o decyzji koordynatora sprzed kilku sekund.

    Na etapie zamkniętym wszystko jest już zablokowane, więc serwis nie odmawia, tylko zwraca 0:
    ``STAGE_ALREADY_CLOSED`` sugerowałoby, że coś poszło nie tak, a nie „nie ma tu nic do zrobienia”.

    Wpisy etapu są blokowane ``SELECT ... FOR UPDATE`` na czas transakcji – to ta sama blokada,
    którą bierze ``_locked_entry`` przy uploadzie, więc obie operacje się szeregują. Bez niej upload
    trwający w chwili kliknięcia mógłby wcisnąć nową wersję między odczyt kandydatów a ich blokadę:
    do oceniania weszłaby wtedy wersja przedostatnia, a najnowsza czekałaby jako „oddana”.
    ``close_stage`` tej blokady nie potrzebuje, bo działa na etapie z zamkniętym oknem uploadu.
    """
    from apps.core.models import audit

    list(StageEntry.objects.select_for_update(of=("self",)).filter(stage=stage).values_list("pk", flat=True))
    locked = close_stage(stage)
    audit(actor, "stage.locked_for_review", stage, {"locked": locked}, request=request)
    logger.info("Etap %s: zablokowano %s prac do oceny bez zamykania etapu", stage.pk, locked)
    return locked


@transaction.atomic
def lock_submission_for_review(submission: Submission, *, actor=None, request=None) -> Submission:
    """Wciąga do oceniania **jedną** wskazaną pracę – furtka dla ekranu przydziałów.

    Ten sam warunek, co w blokadzie całego etapu, tylko wypisany dla pojedynczej pracy i z jawnymi
    odmowami: koordynator, który klika przy konkretnym wierszu tabeli, ma dostać powód, a nie cichy
    brak efektu. Starsza wersja to ``NOT_LATEST_VERSION`` (do oceniania wchodzi wyłącznie najnowsza
    nieodrzucona), a każdy inny stan – łącznie ze ``SCANNING``, które należy do skanu – to
    ``SUBMISSION_NOT_LOCKABLE``.
    """
    from apps.core.models import audit

    locked = Submission.objects.select_for_update(of=("self",)).get(pk=submission.pk)
    newer = (
        Submission.objects.filter(
            entry_id=locked.entry_id, problem_id=locked.problem_id, version__gt=locked.version
        )
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .exists()
    )
    if newer:
        raise DomainError(
            "Ta praca ma nowszą wersję – do oceny wchodzi wyłącznie najnowsza.",
            "NOT_LATEST_VERSION",
            status.HTTP_409_CONFLICT,
        )
    if locked.status != SubmissionStatus.SUBMITTED:
        raise DomainError(
            f"Rozwiązanie w stanie {locked.status} nie nadaje się do zablokowania.",
            "SUBMISSION_NOT_LOCKABLE",
            status.HTTP_409_CONFLICT,
        )
    locked.status = SubmissionStatus.LOCKED
    locked.save(update_fields=["status"])
    audit(
        actor,
        "submission.locked_for_review",
        locked,
        {"submission_id": locked.pk, "version": locked.version},
        request=request,
    )
    submission.status = locked.status
    return locked


def review_counters(stage: Stage) -> dict:
    """Liczniki na kartę etapu: ile prac czeka na blokadę i ile jest już w ocenie.

    „Oddane (niezablokowane)” liczy dokładnie to, co zablokuje przycisk – nie wszystkie zgłoszenia
    ze statusem ``SUBMITTED``, bo starsze wersje i historia po nowym uploadzie też tam siedzą,
    a ich nikt nie ocenia.
    """
    return {
        "submitted": len(lockable_submission_ids(stage)),
        "under_review": Submission.objects.filter(
            entry__stage=stage, status__in=UNDER_REVIEW_STATUSES
        ).count(),
    }


@transaction.atomic
def close_stage_now(stage: Stage, *, actor=None, request=None) -> int:
    """Ręczne zamknięcie etapu przez koordynatora (panel WWW, T-08).

    Ta sama treść, co przebieg beata (``close_due_stages``): blokada najnowszych wersji plus
    znacznik ``closed_at``. Różnica jest jedna – koordynator może zamknąć etap przed deadline'em
    (np. po awarii albo decyzją komitetu), więc nie ma tu warunku na zegar. Idempotencja jest
    twarda: powtórne zamknięcie to ``STAGE_ALREADY_CLOSED``, a nie ciche „nic się nie stało”.
    """
    from apps.core.models import audit

    locked_stage = Stage.objects.select_for_update().get(pk=stage.pk)
    if locked_stage.closed_at is not None:
        raise DomainError("Etap jest już zamknięty.", "STAGE_ALREADY_CLOSED", status.HTTP_409_CONFLICT)
    locked = close_stage(locked_stage)
    locked_stage.closed_at = timezone.now()
    locked_stage.save(update_fields=["closed_at"])
    audit(actor, "stage.closed", locked_stage, {"locked": locked, "manual": True}, request=request)
    logger.info("Etap %s zamknięty ręcznie, zablokowanych rozwiązań: %s", locked_stage.pk, locked)
    stage.closed_at = locked_stage.closed_at
    return locked


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

    Blokada do oceny w **otwartym** etapie (``lock_for_review``) świadomie nie ma tu odpowiednika:
    warunkiem jest ``closed_at``, a nie „koordynator kiedyś kliknął przycisk”. Skan kończący się
    już po kliknięciu zwraca więc pracę do ``SUBMITTED``, a do oceniania wciąga ją dopiero następne
    kliknięcie. Alternatywa – znacznik „ten etap jest już oceniany” – blokowałaby każdą nową wersję
    natychmiast po skanie, czyli odbierała uczestnikowi otwarte okno uploadu, o które w tej zmianie
    właśnie chodzi.
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
