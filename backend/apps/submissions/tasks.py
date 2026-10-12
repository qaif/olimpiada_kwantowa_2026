"""Zadania Celery: skan antywirusowy pliku rozwiązania i cykliczne zamykanie etapów po deadline.

``scan_submission_file`` idzie na kolejkę ``scan`` (``CELERY_TASK_ROUTES``), ``close_due_stages``
uruchamia ``beat`` co minutę (``CELERY_BEAT_SCHEDULE``), a ``forward_submission_file`` – kolejką
``mail``, bo jest wysyłką listu z załącznikiem, a nie pracą domenową.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from .antivirus import ClamAVStreamTooLarge, ClamAVUnavailable, scan_stream
from .forwarding import forward_file
from .models import AvStatus, SubmissionFile, SubmissionStatus
from .services import apply_scan_error, apply_scan_verdict
from .services import close_due_stages as close_due_stages_service
from .storage import get_submission_storage

logger = logging.getLogger(__name__)

MAX_SCAN_RETRIES = 5
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 600
SCAN_TIMEOUT_SECONDS = 60

#: Ile razy ponawiamy przekazanie pracy organizatorowi. Trzy, tak samo jak przy zwykłym liście
#: (``apps.core.tasks.MAX_RETRIES``): typową awarię MTA przykrywają, a dalsze dobijanie się
#: trzymałoby w kolejce zadanie z kilkunastomegabajtowym załącznikiem.
MAX_FORWARD_RETRIES = 3

#: Kody błędu S3, którymi MinIO odpowiada na „nie ma takiego obiektu”.
_MISSING_OBJECT_CODES = {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


class MissingStorageObject(Exception):
    """Obiekt zniknął ze storage (albo nigdy tam nie dotarł). Błąd trwały – bez ponowień."""


def _open_object(storage, object_key: str):
    """Otwiera obiekt, sprowadzając „nie ma pliku” z obu backendów do jednego wyjątku.

    Lokalny backend rzuca ``FileNotFoundError``, S3 – ``ClientError`` z kodem ``NoSuchKey``.
    Bez tego zadanie kończyło się nieobsłużonym wyjątkiem: plik zostawał na zawsze ``PENDING``,
    a zgłoszenie w ``SCANNING``.
    """
    from botocore.exceptions import ClientError

    try:
        return storage.open(object_key)
    except FileNotFoundError as exc:
        raise MissingStorageObject(object_key) from exc
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _MISSING_OBJECT_CODES:
            raise MissingStorageObject(f"{object_key} ({code})") from exc
        raise


@shared_task(bind=True, max_retries=MAX_SCAN_RETRIES)
def scan_submission_file(self, file_id: int) -> str:
    """Skanuje plik przez ClamAV i zapisuje werdykt. Niedostępny ClamAV → retry z backoffem."""
    submission_file = SubmissionFile.objects.select_related("submission").filter(pk=file_id).first()
    if submission_file is None:
        logger.warning("Skan pominięty: SubmissionFile %s już nie istnieje.", file_id)
        return "MISSING"
    if submission_file.av_status != AvStatus.PENDING:
        logger.info("Skan pominięty: plik %s ma już status %s.", file_id, submission_file.av_status)
        return submission_file.av_status

    submission = submission_file.submission
    if submission.status == SubmissionStatus.SUBMITTED:
        submission.status = SubmissionStatus.SCANNING
        submission.save(update_fields=["status"])

    storage = get_submission_storage()
    try:
        stream = _open_object(storage, submission_file.object_key)
        try:
            verdict, signature = scan_stream(
                stream, timeout=SCAN_TIMEOUT_SECONDS, size=submission_file.size_bytes
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
    except MissingStorageObject as exc:
        # Retry nic nie da: obiekt nie pojawi się w buckecie sam z siebie. Plik zostaje z ERROR,
        # a zgłoszenie wraca ze SCANNING do stanu, w którym da się nim dalej administrować.
        logger.error("Brak obiektu w storage dla pliku %s (%s) – skan zamknięty błędem.", file_id, exc)
        apply_scan_error(submission_file, f"Brak obiektu w storage: {exc}")
        return AvStatus.ERROR
    except ClamAVStreamTooLarge as exc:
        logger.error("Plik %s ponad limit strumienia clamd (%s) – skan zamknięty błędem.", file_id, exc)
        apply_scan_error(submission_file, str(exc))
        return AvStatus.ERROR
    except ClamAVUnavailable as exc:
        countdown = min(RETRY_BASE_SECONDS * (2**self.request.retries), RETRY_MAX_SECONDS)
        logger.warning(
            "ClamAV niedostępny przy skanie pliku %s (próba %s/%s): %s",
            file_id,
            self.request.retries + 1,
            MAX_SCAN_RETRIES,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc

    apply_scan_verdict(submission_file, verdict, signature)
    if verdict == AvStatus.INFECTED:
        logger.warning("Plik %s zainfekowany (%s) – zgłoszenie odrzucone.", file_id, signature)
    return verdict


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=MAX_FORWARD_RETRIES,
)
def forward_submission_file(self, file_id: int) -> str:
    """Przekazuje przeskanowany plik rozwiązania na adresy organizatora (``forwarding.forward_file``).

    Ten sam kształt ponowień, co u ``apps.core.tasks.send_mail_task``, i z tego samego powodu:
    między nami a relayem stoi jeszcze DNS, gniazdo i TLS, więc ponawiamy **każdy** wyjątek,
    z rosnącym odstępem i rozrzutem. Rozrzut nie jest ozdobą – przy zamknięciu etapu skany kończą
    się setkami naraz, a bez niego wszystkie ponowienia uderzyłyby w MTA w tej samej sekundzie.

    Po wyczerpaniu prób wyjątek leci dalej i zostaje w logu workera. Dla uczestnika i dla skanu nie
    zmienia to nic: praca jest przyjęta, werdykt zapisany, a znacznik ``forwarded_at`` pusty, więc
    list da się wysłać ponownie (dziś ręcznie, z ``manage.py shell``) bez ryzyka duplikatu.

    Argumentem jest identyfikator, a nie obiekt: zadanie jedzie przez brokera jako JSON, a treść
    listu i tak powstaje z wiersza odczytanego pod blokadą.
    """
    return forward_file(file_id)


#: Twardy limit czasu liczenia metryk podglądu (liczba stron PDF-a). pypdf to czysty Python, więc
#: ``SoftTimeLimitExceeded`` przerywa go w miejscu; twardy limit (``time_limit``) jest kilka sekund
#: później, na wypadek pętli, której sygnał nie dosięgnie.
PREVIEW_SOFT_TIME_LIMIT_SECONDS = 30
PREVIEW_TIME_LIMIT_SECONDS = 40


@shared_task(
    soft_time_limit=PREVIEW_SOFT_TIME_LIMIT_SECONDS, time_limit=PREVIEW_TIME_LIMIT_SECONDS, ignore_result=True
)
def store_preview_metrics(file_id: int) -> None:
    """Liczba stron PDF-a i metryki podglądu po czystym skanie – **osobnym** zadaniem z limitem 30 s.

    Do audytu 10.10.2026 liczyło się to w środku ``scan_submission_file``, czyli w workerze, który
    obsługuje też kolejki ``scan`` i ``mail``, pod ogólnym limitem 30 minut. Spreparowany PDF
    (zapętlone odwołania, gigantyczna tablica stron) zajmował wtedy proces na pół godziny – skany
    kolejnych prac i poczta stały. Tutaj zadanie dostaje 30 s, a przekroczenie kończy się brakiem
    liczby stron (podgląd i tak nie jest warunkiem niczego – ``apps.submissions.preview``).

    Plik czytamy na nowo i tylko w stanie ``CLEAN``: zadanie jedzie przez brokera, a w międzyczasie
    plik mógł zostać ponownie przeskanowany albo usunięty.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    from .preview import store_page_count

    submission_file = SubmissionFile.objects.filter(pk=file_id, av_status=AvStatus.CLEAN).first()
    if submission_file is None:
        return
    try:
        store_page_count(submission_file)
    except SoftTimeLimitExceeded:
        logger.warning(
            "Metryki podglądu pliku %s przerwane po %s s.", file_id, PREVIEW_SOFT_TIME_LIMIT_SECONDS
        )


@shared_task
def close_due_stages() -> list[int]:
    """Beat co 60 s: etapy po ``deadline_at + grace_seconds`` dostają LOCKED i znacznik ``closed_at``.

    Przebieg obchodzi wszystkie konkursy, każdy w jego kontekście. Zamknięcie etapu wywołuje
    zdarzenie integracyjne (``stage.closed``) i powiadomienia, a te budują adresy przez
    ``absolute_url`` – bez wiązania konkursu wyszłyby pod witrynę domyślną instalacji.
    """
    from apps.competitions.scoping import each_competition

    now = timezone.now()
    closed: list[int] = []
    for competition in each_competition():
        closed.extend(close_due_stages_service(now=now, competition=competition))
    return closed


@shared_task(ignore_result=True)
def finalise_closed_stage_quiz(stage_id: int) -> int:
    """Domyka trwające podejścia testu online etapu zamkniętego ręcznie – po tolerancji sieciowej.

    Kolejkuje je ``close_stage_now`` z opóźnieniem ``SUBMIT_GRACE_SECONDS`` (+ zapas): do tej
    chwili odpowiedź wysłana przed zamknięciem jeszcze się liczy (``accepts_answers_at``).
    ``now`` nie wcześniej niż koniec tolerancji, bo ``finalise_overdue`` domyka podejścia etapu
    zamkniętego dopiero za nią – a worker w trybie ``ALWAYS_EAGER`` (testy) albo po przestawieniu
    zegara mógłby uruchomić zadanie wcześniej i nie domknąć niczego. Idempotentne: podejście już
    zakończone ``finalise_overdue`` pomija.
    """
    from datetime import timedelta

    from apps.competitions.models import Stage
    from apps.quiz.models import SUBMIT_GRACE_SECONDS
    from apps.quiz.services import finalise_overdue

    stage = Stage.objects.filter(pk=stage_id).first()
    if stage is None or stage.closed_at is None:
        return 0
    after_grace = stage.closed_at + timedelta(seconds=SUBMIT_GRACE_SECONDS + 1)
    return finalise_overdue(stage=stage, now=max(timezone.now(), after_grace))


@shared_task
def recompute_similarity(stage_id: int, actor_id: int | None = None) -> dict:
    """Przelicza podobieństwa rozwiązań w etapie (kolejka domyślna, uruchamiane przyciskiem).

    Zadanie, a nie synchroniczne żądanie, bo przy komplecie prac finału porównanie idzie
    w tysiącach par: przeglądarka odpadłaby na timeoucie, a koordynator nie miałby jak
    stwierdzić, czy przeliczenie trwa, czy padło. Kolejka jest domyślna – to praca rzadka
    i ręcznie wywołana, więc nie zasługuje na własnego workera, ale nie może też zająć
    kolejki skanów antywirusowych, od której zależy przyjmowanie prac.

    ``actor_id``, a nie obiekt użytkownika: argumenty zadania jadą przez brokera jako JSON,
    a wpis audytowy i tak potrzebuje wyłącznie tego, kto kliknął.
    """
    from apps.accounts.models import User
    from apps.competitions.models import Stage

    from .similarity import recompute_stage

    stage = Stage.objects.filter(pk=stage_id).first()
    if stage is None:
        logger.warning("Przeliczenie podobieństw pominięte: etap %s już nie istnieje.", stage_id)
        return {"stage_id": stage_id, "problems": 0, "compared": 0, "stored": 0, "truncated": []}
    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    return recompute_stage(stage, actor=actor)
