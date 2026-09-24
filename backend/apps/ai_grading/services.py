"""Reguły oceny AI: klucz, zlecenia, kolejka z ogranicznikiem, przebieg jednej oceny i odczyty.

Widoki i zadania Celery wyłącznie orkiestrują – każda decyzja („czy wolno zlecić”, „czy tę pracę
pominąć”, „czy ponowić”, „co zobaczy uczestnik”) stoi tutaj, w jednym miejscu dla panelu,
kolejki i eksportu danych.

**Kolejka z ogranicznikiem** (:func:`pump`). Prośba mówi „jedno zadanie na pracę”, a serwer
produkcyjny jest słaby: worker Celery ma dwa miejsca i dzieli je ze skanem antywirusowym i pocztą.
Wrzucenie czterystu zadań naraz zajęłoby oba miejsca na godziny (jedno wywołanie modelu trwa
minuty), a skan nowo oddanych prac stałby w kolejce. Dlatego zlecenie **nie** wrzuca zadań do
Celery: oznacza oceny jako ``PENDING``, a :func:`pump` wypuszcza ich tyle, ile mieści
``AI_GRADING_MAX_CONCURRENCY`` (domyślnie jedna). Każda zakończona ocena woła :func:`pump`
jeszcze raz, więc kolejka opróżnia się sama, bez odpytywania i bez zadań krążących w kółko.
Okresowy przebieg beatu (``tasks.pump_ai_assessments``) jest tylko siatką asekuracyjną na
restart workera.

**Idempotencja i koszt.** Podwójne kliknięcie nie płaci dwa razy: zlecenie pomija oceny
``PENDING`` i ``RUNNING`` (także przy „wygeneruj ponownie”), a przejście ``PENDING → RUNNING``
jest pojedynczym ``UPDATE … WHERE status = PENDING`` – drugie dostarczenie tego samego zadania
nie znajdzie już czego przejąć. Ponowienie po błędzie przejściowym (429, 5xx, sieć) następuje
wyłącznie wtedy, gdy API **nie oddało** odpowiedzi, czyli gdy nie było za co zapłacić; odpowiedź
oddana (także odmowa i ucięcie na ``max_tokens``) jest liczona i nie jest ponawiana automatycznie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import AvStatus, Submission, SubmissionFile, SubmissionStatus

from . import prompt
from .client import ApiFailure, CallResult, Usage, call_model, check_key
from .crypto import InvalidApiKey, decrypt_key, encrypt_key, last4, normalise_key
from .models import (
    AI_GRADING_FLAG,
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    PRICING_USD_PER_MTOK,
    AiAssessment,
    AiAssessmentStatus,
    AiGradingSettings,
    AiModel,
    AiStageVisibility,
)

logger = logging.getLogger(__name__)

#: Przestrzeń blokad doradczych Postgresa dla tej aplikacji (1005 – przydział recenzentów,
#: 1007 – zakładanie konkursu). Numer z zapasem, żeby nie zderzyć się z przestrzeniami
#: dokładanymi równolegle w innych modułach.
ADVISORY_LOCK_NAMESPACE_AI = 1042
#: Druga część klucza blokady kolejki – jedna kolejka na instalację (worker jest jeden).
PUMP_LOCK_KEY = 0

#: Odbiorca danych w eksporcie i w rejestrze czynności – jedno brzmienie.
PROCESSOR_NAME = "Anthropic PBC (dostawca modelu Claude) – podmiot przetwarzający"

#: Szacunek tokenów na stronę PDF-a (tekst strony plus jej obraz) i na jedno zdjęcie. Rząd
#: wielkości z dokumentacji API; na ekranie stoi wprost, że to szacunek.
TOKENS_PER_PDF_PAGE = 2500
TOKENS_PER_IMAGE = 1600
BYTES_PER_PDF_PAGE_GUESS = 60_000
#: Stała część żądania poza plikami: prompt systemowy, skala, rubryka, znaczniki.
TOKENS_FIXED_OVERHEAD = 2000
#: Myślenie adaptacyjne na ``effort: high`` plus JSON z uzasadnieniem – typowo kilka tysięcy.
TOKENS_OUTPUT_ESTIMATE = 8000


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _lock(key: int) -> None:
    """Blokada doradcza na czas transakcji. Na bazie innej niż Postgres – nic (testy jednostkowe)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [ADVISORY_LOCK_NAMESPACE_AI, int(key)])


# --- przełącznik i ustawienia -------------------------------------------------------------------


def is_enabled(competition) -> bool:
    """Czy konkurs ma włączoną ocenę AI. Bez zapytania – ``has_feature`` czyta pole wiersza."""
    return competition is not None and competition.has_feature(AI_GRADING_FLAG)


def settings_for(competition) -> AiGradingSettings:
    row, _ = AiGradingSettings.objects.get_or_create(competition=competition)
    return row


@sensitive_variables("raw", "value")
def set_api_key(competition, raw: str, *, actor, request=None) -> AiGradingSettings:
    """Zapisuje (albo zastępuje) klucz. W audycie – wyłącznie fakt, nigdy wartość ani jej końcówka.

    ``sensitive_variables``: gdyby zapis padł (baza, blokada), raport błędu Django – strona
    ``DEBUG`` albo list do ``ADMINS`` – wypisuje zmienne lokalne ramek stosu. Tutaj jedyne dwie
    z jawnym kluczem są zasłonięte gwiazdkami.
    """
    try:
        value = normalise_key(raw)
    except InvalidApiKey as exc:
        raise _bad_request(str(exc), "AI_KEY_INVALID") from None
    row = settings_for(competition)
    replaced = row.has_key
    row.api_key_encrypted = encrypt_key(value)
    row.api_key_last4 = last4(value)
    row.api_key_set_at = timezone.now()
    row.api_key_set_by = actor if getattr(actor, "is_authenticated", False) else None
    row.api_key_checked_at = None
    row.api_key_check_ok = None
    row.api_key_check_message = ""
    row.updated_at = timezone.now()
    row.save()
    audit(actor, "ai_grading.key_set", row, {"replaced": replaced}, request=request)
    return row


def remove_api_key(competition, *, actor, request=None) -> AiGradingSettings:
    row = settings_for(competition)
    if not row.has_key:
        raise _conflict("Klucz API nie jest ustawiony.", "AI_KEY_MISSING")
    row.api_key_encrypted = ""
    row.api_key_last4 = ""
    row.api_key_set_at = None
    row.api_key_set_by = None
    row.api_key_checked_at = None
    row.api_key_check_ok = None
    row.api_key_check_message = ""
    row.updated_at = timezone.now()
    row.save()
    audit(actor, "ai_grading.key_removed", row, {}, request=request)
    return row


def check_api_key(competition, *, actor, request=None) -> tuple[bool, str]:
    """„Sprawdź klucz” – wynik zapisany przy ustawieniach i pokazany na ekranie."""
    row = settings_for(competition)
    key = decrypt_key(row.api_key_encrypted)
    if key is None:
        ok, message = (
            False,
            (
                "Klucz nie jest ustawiony."
                if not row.has_key
                else "Klucza nie da się odczytać – wpisz go ponownie."
            ),
        )
    else:
        ok, message = check_key(key, row.model)
    row.api_key_checked_at = timezone.now()
    row.api_key_check_ok = ok
    row.api_key_check_message = message[:300]
    row.save(update_fields=["api_key_checked_at", "api_key_check_ok", "api_key_check_message"])
    audit(actor, "ai_grading.key_checked", row, {"ok": ok}, request=request)
    return ok, message


def update_options(competition, *, model: str, spending_limit_usd, actor, request=None) -> AiGradingSettings:
    if model not in AiModel.values:
        raise _bad_request("Nieznany model.", "AI_MODEL_INVALID")
    if spending_limit_usd is not None and spending_limit_usd < 0:
        raise _bad_request("Limit wydatków nie może być ujemny.", "AI_LIMIT_INVALID")
    row = settings_for(competition)
    diff = {}
    if row.model != model:
        diff["model"] = [row.model, model]
    if row.spending_limit_usd != spending_limit_usd:
        diff["spending_limit_usd"] = [
            str(row.spending_limit_usd) if row.spending_limit_usd is not None else None,
            str(spending_limit_usd) if spending_limit_usd is not None else None,
        ]
    row.model = model
    row.spending_limit_usd = spending_limit_usd
    row.updated_at = timezone.now()
    row.save()
    if diff:
        audit(actor, "ai_grading.options_updated", row, diff, request=request)
    return row


def set_stage_visibility(stage, show: bool, *, actor, request=None) -> AiStageVisibility:
    """„Pokaż uczestnikom ocenę AI” dla jednego etapu. Ślad w audycie przy każdej zmianie."""
    row, _ = AiStageVisibility.objects.get_or_create(stage=stage)
    if row.show_to_participants != show:
        row.show_to_participants = show
        row.changed_at = timezone.now()
        row.changed_by = actor if getattr(actor, "is_authenticated", False) else None
        row.save()
        audit(actor, "ai_grading.visibility_changed", stage, {"show_to_participants": show}, request=request)
    return row


def visible_stage_ids(stages) -> set[int]:
    return set(
        AiStageVisibility.objects.filter(stage__in=stages, show_to_participants=True).values_list(
            "stage_id", flat=True
        )
    )


# --- koszt --------------------------------------------------------------------------------------


def _rates(served_model: str, requested_model: str) -> tuple[Decimal, Decimal]:
    return PRICING_USD_PER_MTOK.get(served_model) or PRICING_USD_PER_MTOK.get(
        requested_model, PRICING_USD_PER_MTOK[AiModel.OPUS.value]
    )


def cost_of(usage: Usage, *, served_model: str, requested_model: str) -> Decimal:
    """Szacowany koszt jednego wywołania w USD. ``input_tokens`` nie obejmuje tokenów z cache."""
    rate_in, rate_out = _rates(served_model, requested_model)
    million = Decimal(1_000_000)
    total = (
        Decimal(usage.input_tokens) * rate_in
        + Decimal(usage.cache_write_tokens) * rate_in * CACHE_WRITE_MULTIPLIER
        + Decimal(usage.cache_read_tokens) * rate_in * CACHE_READ_MULTIPLIER
        + Decimal(usage.output_tokens) * rate_out
    ) / million
    return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _record_usage(assessment_id: int, competition, result: CallResult, requested_model: str) -> Decimal:
    """Dopisuje zużycie do oceny i do liczników konkursu – wyrażeniami ``F``, bez wyścigu."""
    usage = result.usage
    cost = cost_of(usage, served_model=result.model, requested_model=requested_model)
    AiAssessment.objects.filter(pk=assessment_id).update(
        input_tokens=F("input_tokens") + usage.input_tokens,
        output_tokens=F("output_tokens") + usage.output_tokens,
        cache_write_tokens=F("cache_write_tokens") + usage.cache_write_tokens,
        cache_read_tokens=F("cache_read_tokens") + usage.cache_read_tokens,
        cost_usd=F("cost_usd") + cost,
    )
    AiGradingSettings.objects.filter(competition=competition).update(
        total_calls=F("total_calls") + 1,
        total_input_tokens=F("total_input_tokens") + usage.input_tokens,
        total_output_tokens=F("total_output_tokens") + usage.output_tokens,
        total_cache_write_tokens=F("total_cache_write_tokens") + usage.cache_write_tokens,
        total_cache_read_tokens=F("total_cache_read_tokens") + usage.cache_read_tokens,
        total_cost_usd=F("total_cost_usd") + cost,
    )
    return cost


# --- materiały ----------------------------------------------------------------------------------


def scale_values(problem) -> list[int]:
    """Wartości skali w postaci **do pokazania** (z minusem, gdy skala go ma) – jak u recenzenta."""
    from apps.grading.services import scale_items

    return sorted(item["value"] for item in scale_items(problem.stage, problem))


def max_points_for(problem) -> Decimal | None:
    """Maksimum zadania w postaci do pokazania – z reguły oceny (``competitions.scoring``).

    Ta sama liczba, którą recenzent widzi przy polu punktów: najwyższa wartość skali zadania albo
    etapu, a w etapie z dowolnymi wartościami także samo maksimum zadania (12,5). Wcześniej
    funkcja składała ją sama z pól skali – i zadanie z samym maksimum dostałoby maksimum etapu.
    """
    from apps.competitions.scoring import problem_maximum

    return problem_maximum(problem.stage, problem)


def _rule(problem):
    from apps.competitions.scoring import safe_score_rule

    return safe_score_rule(problem.stage, problem)


def _read_field(field_file) -> bytes:
    """Plik zadania z prywatnego storage'u. Brak pliku = puste bajty (materiał opcjonalny)."""
    if not field_file:
        return b""
    try:
        field_file.open("rb")
        try:
            return field_file.read()
        finally:
            field_file.close()
    except Exception as exc:  # noqa: BLE001 - storage niedostępny to błąd materiałów, nie 500
        raise prompt.MaterialError(
            "storage", "Nie udało się odczytać materiałów zadania ze storage'u – spróbuj ponownie."
        ) from exc


def problem_materials(problem) -> prompt.ProblemMaterials:
    from apps.grading.rubric import criteria_for
    from apps.grading.services import scale_items

    maximum = max_points_for(problem)
    if maximum is None or maximum <= 0:
        raise prompt.MaterialError("no_scale", "Zadanie nie ma skali punktacji – nie ma czego proponować.")
    rule = _rule(problem)
    return prompt.ProblemMaterials(
        number=problem.number,
        title=problem.title,
        statement_pdf=_read_field(problem.statement_pdf),
        model_solution_pdf=_read_field(problem.model_solution_pdf),
        reviewer_notes=problem.reviewer_notes or "",
        scale_items=scale_items(problem.stage, problem),
        max_points=maximum,
        rubric=[
            {"title": item.title, "description": item.description, "max_points": item.max_points}
            for item in criteria_for(problem)
        ],
        free_values=bool(rule is not None and rule.free),
    )


def _clean_file(submission: Submission) -> SubmissionFile | None:
    submission_file = submission.latest_file
    if submission_file is None or submission_file.av_status != AvStatus.CLEAN:
        return None
    return submission_file


def _read_submission(submission_file: SubmissionFile) -> bytes:
    from apps.submissions.storage import get_submission_storage

    try:
        stream = get_submission_storage().open(submission_file.object_key)
    except Exception as exc:  # noqa: BLE001 - brak obiektu / storage niedostępny
        raise prompt.MaterialError("storage", "Nie udało się odczytać pliku pracy ze storage'u.") from exc
    try:
        return stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


# --- plan i szacunek ----------------------------------------------------------------------------


def latest_versions(problem) -> list[Submission]:
    """Najnowsza nieodrzucona wersja pracy każdego uczestnika w zadaniu (ta, którą się ocenia)."""
    rows = (
        Submission.objects.filter(problem=problem)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("entry", "entry__participant")
        .prefetch_related(Prefetch("files", queryset=SubmissionFile.objects.order_by("-id")))
        .order_by("entry_id", "-version", "-id")
    )
    latest: dict[int, Submission] = {}
    for submission in rows:
        latest.setdefault(submission.entry_id, submission)
    return sorted(latest.values(), key=lambda item: item.pk)


@dataclass
class Plan:
    """Co zrobi zlecenie – pokazywane koordynatorowi **przed** potwierdzeniem."""

    problem: object
    model: str
    regenerate: bool
    to_generate: list[Submission] = field(default_factory=list)
    skipped_done: int = 0
    skipped_in_progress: int = 0
    without_file: int = 0
    estimate_usd: Decimal = Decimal("0")
    has_model_solution: bool = False
    has_statement: bool = False

    @property
    def count(self) -> int:
        return len(self.to_generate)


def _file_tokens(submission_file: SubmissionFile) -> int:
    mime = (submission_file.mime or "").lower()
    if mime == prompt.PDF_MIME:
        pages = submission_file.page_count or max(1, submission_file.size_bytes // BYTES_PER_PDF_PAGE_GUESS)
        return pages * TOKENS_PER_PDF_PAGE
    if mime in prompt.IMAGE_MIMES:
        return TOKENS_PER_IMAGE
    return max(1, submission_file.size_bytes // 3)


def _problem_tokens(problem) -> int:
    total = TOKENS_FIXED_OVERHEAD
    for field_file in (problem.statement_pdf, problem.model_solution_pdf):
        if not field_file:
            continue
        try:
            pages = prompt.pdf_pages(_read_field(field_file))
        except prompt.MaterialError:
            pages = None
        total += (pages or 2) * TOKENS_PER_PDF_PAGE
    return total


def estimate_cost(problem, files: list[SubmissionFile], model: str) -> Decimal:
    """Zgrubny szacunek w USD dla serii prac jednego zadania.

    Pierwsza praca płaci za zapis materiałów zadania do cache (1,25 stawki), kolejne – za odczyt
    (0,1 stawki), bo kolejka puszcza prace po kolei, w odstępach krótszych niż czas życia cache.
    """
    if not files:
        return Decimal("0")
    rate_in, rate_out = _rates(model, model)
    stable = Decimal(_problem_tokens(problem))
    million = Decimal(1_000_000)
    total = Decimal("0")
    for index, submission_file in enumerate(files):
        multiplier = CACHE_WRITE_MULTIPLIER if index == 0 else CACHE_READ_MULTIPLIER
        total += stable * rate_in * multiplier
        total += Decimal(_file_tokens(submission_file) + 300) * rate_in
        total += Decimal(TOKENS_OUTPUT_ESTIMATE) * rate_out
    return (total / million).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def plan_generation(problem, *, regenerate: bool, submission_ids: list[int] | None = None) -> Plan:
    competition = problem.stage.edition.competition
    row = settings_for(competition)
    plan = Plan(
        problem=problem,
        model=row.model,
        regenerate=regenerate,
        has_model_solution=bool(problem.model_solution_pdf),
        has_statement=bool(problem.statement_pdf),
    )
    candidates = latest_versions(problem)
    if submission_ids is not None:
        wanted = set(submission_ids)
        candidates = [item for item in candidates if item.pk in wanted]
    existing = {
        item.submission_id: item
        for item in AiAssessment.objects.filter(submission__in=[c.pk for c in candidates])
    }
    files: list[SubmissionFile] = []
    for submission in candidates:
        current = existing.get(submission.pk)
        if current is not None and current.is_in_progress:
            plan.skipped_in_progress += 1
            continue
        if current is not None and current.is_done and not regenerate:
            plan.skipped_done += 1
            continue
        submission_file = _clean_file(submission)
        if submission_file is None:
            plan.without_file += 1
            continue
        plan.to_generate.append(submission)
        files.append(submission_file)
    plan.estimate_usd = estimate_cost(problem, files, row.model)
    return plan


def assert_can_request(competition) -> AiGradingSettings:
    """Bramka zlecenia – ta sama dla ekranu podglądu i dla potwierdzenia."""
    if not is_enabled(competition):
        raise _conflict("Ocena AI jest w tym konkursie wyłączona.", "AI_DISABLED")
    row = settings_for(competition)
    if not row.has_key:
        raise _conflict("Najpierw dodaj klucz API w ustawieniach oceny AI.", "AI_KEY_MISSING")
    if row.limit_reached:
        raise _conflict(
            "Osiągnięto limit wydatków na ocenę AI ustawiony w ustawieniach – podnieś go albo zdejmij.",
            "AI_BUDGET_REACHED",
        )
    return row


@transaction.atomic
def request_generation(
    problem, *, regenerate: bool, submission_ids: list[int] | None = None, actor, request=None
) -> Plan:
    """Zlecenie: oznacza oceny jako ``PENDING`` i wypuszcza tyle, ile mieści kolejka.

    Blokada doradcza per zadanie szereguje dwa równoczesne zlecenia tego samego zadania (dwie
    karty przeglądarki, podwójne kliknięcie): drugie widzi już oceny ``PENDING`` i je pomija,
    zamiast zakładać drugi wiersz na tę samą pracę.
    """
    competition = problem.stage.edition.competition
    row = assert_can_request(competition)
    _lock(problem.pk)
    plan = plan_generation(problem, regenerate=regenerate, submission_ids=submission_ids)
    if plan.count > settings.AI_GRADING_MAX_BATCH:
        raise _bad_request(
            f"Jedno zlecenie obejmuje najwyżej {settings.AI_GRADING_MAX_BATCH} prac.", "AI_BATCH_TOO_LARGE"
        )
    now = timezone.now()
    ids = [submission.pk for submission in plan.to_generate]
    existing = {
        item.submission_id: item
        for item in AiAssessment.objects.select_for_update().filter(submission_id__in=ids)
    }
    queued: list[Submission] = []
    for submission in plan.to_generate:
        current = existing.get(submission.pk)
        fields = {
            "status": AiAssessmentStatus.PENDING,
            "model": row.model,
            "requested_at": now,
            "requested_by": actor if getattr(actor, "is_authenticated", False) else None,
            "dispatched_at": None,
            "started_at": None,
            "sent_at": None,
            "finished_at": None,
            "attempts": 0,
            "proposed_points": None,
            "max_points": None,
            "criteria": [],
            "summary": "",
            "errors": [],
            "confidence": "",
            "injection_suspected": False,
            "error_code": "",
            "error_message": "",
            "request_id": "",
        }
        if current is None:
            AiAssessment.objects.create(submission=submission, **fields)
        else:
            if current.is_in_progress or (current.is_done and not regenerate):
                continue
            for name, value in fields.items():
                setattr(current, name, value)
            current.save()
        queued.append(submission)
    plan.to_generate = queued
    audit(
        actor,
        "ai_grading.requested",
        problem,
        {"count": len(queued), "regenerate": regenerate, "model": row.model},
        request=request,
    )
    pump()
    return plan


# --- kolejka ------------------------------------------------------------------------------------


def _stale_before(now):
    return now - timedelta(minutes=int(settings.AI_GRADING_STALE_MINUTES))


def pump(*, now=None) -> list[int]:
    """Wypuszcza do Celery tyle czekających ocen, ile mieści ogranicznik. Zwraca ich identyfikatory.

    „W locie” jest ocena ``RUNNING`` oraz ``PENDING`` już przekazana do kolejki – obie nie starsze
    niż ``AI_GRADING_STALE_MINUTES``. Starsze uznajemy za zgubione (restart workera) i przestają
    blokować miejsce, a zgubione ``PENDING`` wracają do puli. Zadania trafiają do Celery dopiero
    po zatwierdzeniu transakcji (``on_commit``), żeby worker nie szukał wiersza, którego jeszcze
    nie widać.
    """
    from .tasks import run_ai_assessment

    now = now or timezone.now()
    stale = _stale_before(now)
    cap = max(1, int(settings.AI_GRADING_MAX_CONCURRENCY))
    with transaction.atomic():
        _lock(PUMP_LOCK_KEY)
        in_flight = AiAssessment.objects.filter(
            Q(status=AiAssessmentStatus.RUNNING, started_at__gte=stale)
            | Q(status=AiAssessmentStatus.PENDING, dispatched_at__gte=stale)
        ).count()
        free = cap - in_flight
        if free <= 0:
            return []
        chosen = list(
            AiAssessment.objects.filter(status=AiAssessmentStatus.PENDING)
            .filter(Q(dispatched_at__isnull=True) | Q(dispatched_at__lt=stale))
            .order_by("requested_at", "id")
            .values_list("pk", flat=True)[:free]
        )
        if not chosen:
            return []
        AiAssessment.objects.filter(pk__in=chosen).update(dispatched_at=now)
        for pk in chosen:
            transaction.on_commit(lambda pk=pk: run_ai_assessment.delay(pk))
    return chosen


def recover_stale(*, now=None) -> int:
    """Oceny ``RUNNING`` starsze niż próg kończą się błędem – worker zginął w trakcie wywołania.

    Błąd, a nie ponowne zlecenie: wywołanie mogło się zakończyć (i zostać policzone) po stronie
    Anthropic, zanim worker padł. Automatyczne ponowienie mogłoby więc zapłacić drugi raz – o tym
    decyduje koordynator przyciskiem „wygeneruj ponownie”.
    """
    now = now or timezone.now()
    return AiAssessment.objects.filter(
        status=AiAssessmentStatus.RUNNING, started_at__lt=_stale_before(now)
    ).update(
        status=AiAssessmentStatus.ERROR,
        error_code="interrupted",
        error_message="Ocena została przerwana (restart workera?). Wygeneruj ją ponownie.",
        finished_at=now,
    )


# --- przebieg jednej oceny ----------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """Wynik przebiegu dla zadania Celery: status końcowy albo prośba o ponowienie."""

    status: str
    retry_after: int | None = None


class _Fail(Exception):
    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def mark_failed(assessment_id: int, code: str, message: str, request_id: str = "") -> RunOutcome:
    AiAssessment.objects.filter(pk=assessment_id).update(
        status=AiAssessmentStatus.ERROR,
        error_code=code[:32],
        error_message=message[:500],
        request_id=(request_id or "")[:80],
        finished_at=timezone.now(),
    )
    return RunOutcome(AiAssessmentStatus.ERROR)


def _needles(submission: Submission) -> list[str]:
    """Dane osobowe autora pracy do wymazania z odpowiedzi modelu (``prompt.redact``)."""
    participant = submission.entry.participant
    user = participant.user
    local_part = (user.email or "").split("@")[0]
    return [user.first_name, user.last_name, user.get_full_name(), local_part, participant.school or ""]


def _refusal_message(category: str | None) -> str:
    suffix = f" (kategoria: {category})" if category else ""
    return (
        f"Model odmówił oceny tej pracy{suffix}, także po automatycznym przełączeniu na model "
        "zastępczy. Oceń ją bez sugestii AI."
    )


def run_assessment(assessment_id: int, *, final_attempt: bool = True) -> RunOutcome:
    """Jedna ocena od przejęcia do zapisu. Woła ją zadanie Celery – klucz czyta sama, z bazy.

    ``final_attempt`` mówi, czy przy błędzie przejściowym wolno jeszcze poprosić o ponowienie.
    """
    from apps.tenancy.context import competition_context

    now = timezone.now()
    claimed = AiAssessment.objects.filter(pk=assessment_id, status=AiAssessmentStatus.PENDING).update(
        status=AiAssessmentStatus.RUNNING, started_at=now, attempts=F("attempts") + 1
    )
    if not claimed:
        # Drugie dostarczenie tego samego zadania albo ocena już zakończona – nic do zrobienia.
        return RunOutcome("SKIPPED")
    assessment = AiAssessment.objects.select_related(
        "submission",
        "submission__competition",
        "submission__entry__participant__user",
        "submission__problem__stage__edition",
        "submission__problem__stage__scoring_scale",
    ).get(pk=assessment_id)
    competition = assessment.submission.competition
    with competition_context(competition):
        try:
            return _execute(assessment, competition, final_attempt=final_attempt)
        except _Fail as failure:
            return mark_failed(assessment_id, failure.code, failure.message, failure.request_id)
        except prompt.MaterialError as failure:
            return mark_failed(assessment_id, failure.code, failure.message)


def _execute(assessment: AiAssessment, competition, *, final_attempt: bool) -> RunOutcome:
    if not is_enabled(competition):
        raise _Fail("disabled", "Ocena AI została w tym konkursie wyłączona.")
    row = settings_for(competition)
    if not row.has_key:
        raise _Fail("no_key", "Brak klucza API – dodaj go w ustawieniach oceny AI.")
    if row.limit_reached:
        raise _Fail("budget", "Osiągnięto limit wydatków ustawiony przez koordynatora.")
    key = decrypt_key(row.api_key_encrypted)
    if key is None:
        raise _Fail("key_unreadable", "Klucza API nie da się odczytać – wpisz go ponownie w ustawieniach.")

    submission = assessment.submission
    submission_file = _clean_file(submission)
    if submission_file is None:
        raise _Fail("no_file", "Praca nie ma pliku po czystym skanie antywirusowym.")
    # Rozmiar sprawdzamy **przed** czytaniem pliku: base64 powiększa go o jedną trzecią, a worker
    # ma ograniczoną pamięć (``mem_limit``) – plik, który i tak nie zmieści się w żądaniu, nie ma
    # po co lądować w niej dwa razy.
    if submission_file.size_bytes * 4 // 3 > prompt.MAX_REQUEST_BYTES:
        raise _Fail(
            "too_large",
            "Plik pracy przekracza limit API (32 MB w jednym żądaniu po zakodowaniu). Tę pracę "
            "trzeba ocenić bez sugestii AI.",
        )
    problem = submission.problem
    materials = problem_materials(problem)
    request = prompt.build_request(
        model=assessment.model or row.model,
        competition_name=competition.name,
        problem=prompt.problem_blocks(materials),
        submission=prompt.submission_blocks(
            _read_submission(submission_file), submission_file.mime, page_count=submission_file.page_count
        ),
        max_tokens=int(settings.AI_GRADING_MAX_TOKENS),
    )

    AiAssessment.objects.filter(pk=assessment.pk).update(sent_at=timezone.now())
    try:
        result = call_model(key, request)
    except ApiFailure as failure:
        if failure.retryable and not final_attempt:
            AiAssessment.objects.filter(pk=assessment.pk).update(
                status=AiAssessmentStatus.PENDING,
                dispatched_at=timezone.now(),
                error_code=failure.code,
                error_message=f"{failure.message} (ponowienie)"[:500],
            )
            return RunOutcome("RETRY", retry_after=failure.retry_after)
        raise _Fail(failure.code, failure.message, failure.request_id) from None

    _record_usage(assessment.pk, competition, result, request["model"])
    if result.stop_reason == "refusal":
        raise _Fail("refusal", _refusal_message(result.refusal_category), result.request_id)
    if result.stop_reason == "max_tokens":
        raise _Fail(
            "max_tokens",
            "Odpowiedź modelu przekroczyła limit długości i została ucięta. Spróbuj ponownie albo "
            "oceń pracę bez sugestii AI.",
            result.request_id,
        )
    if result.stop_reason not in ("end_turn", "stop_sequence"):
        raise _Fail("api", f"Nieoczekiwane zakończenie odpowiedzi ({result.stop_reason}).", result.request_id)
    try:
        parsed = prompt.validate_output(result.text, max_points=materials.max_points)
    except prompt.InvalidOutput as exc:
        raise _Fail(
            "invalid_output", f"Odpowiedź modelu nie pasuje do schematu: {exc}", result.request_id
        ) from None
    parsed = prompt.redact_assessment(parsed, _needles(submission))

    AiAssessment.objects.filter(pk=assessment.pk).update(
        status=AiAssessmentStatus.DONE,
        model=result.model[:60],
        proposed_points=parsed.proposed_points,
        max_points=parsed.max_points,
        criteria=parsed.criteria,
        summary=parsed.summary,
        errors=parsed.errors,
        confidence=parsed.confidence,
        injection_suspected=parsed.injection_suspected,
        error_code="",
        error_message="",
        request_id=result.request_id[:80],
        finished_at=timezone.now(),
    )
    return RunOutcome(AiAssessmentStatus.DONE)


# --- odczyty: koordynator -----------------------------------------------------------------------


def _final_display(score, problem) -> Decimal:
    """Ocena końcowa w postaci do pokazania: skala etapu bywa w bazie przesunięta (``offset``).

    Przesunięcie zna reguła oceny (``ScoreRule.to_display``) – ta sama, która je nałożyła przy
    zapisie, więc zadanie z własnym zakresem nie dostanie cudzego offsetu.
    """
    rule = _rule(problem)
    return rule.to_display(score) if rule is not None else score


def problem_overview(problem) -> dict:
    """Sekcja „Ocena AI” na karcie zadania: liczniki, wiersze prac i zgodność z oceną końcową."""
    from apps.grading.models import FinalGrade

    submissions = latest_versions(problem)
    assessments = {
        item.submission_id: item
        for item in AiAssessment.objects.filter(submission__in=[s.pk for s in submissions])
    }
    finals = {
        item.submission_id: item.score
        for item in FinalGrade.objects.filter(submission__in=[s.pk for s in submissions])
    }
    counts = {choice: 0 for choice in AiAssessmentStatus.values}
    rows = []
    diffs: list[Decimal] = []
    for submission in submissions:
        assessment = assessments.get(submission.pk)
        if assessment is not None:
            counts[assessment.status] += 1
        final = finals.get(submission.pk)
        final_display = _final_display(final, problem) if final is not None else None
        if assessment is not None and assessment.is_done and final_display is not None:
            diffs.append(abs(Decimal(final_display) - assessment.proposed_points))
        rows.append(
            {
                "submission": submission,
                "assessment": assessment,
                "final": final_display,
                "has_file": _clean_file(submission) is not None,
            }
        )
    agreement = None
    if diffs:
        agreement = {
            "count": len(diffs),
            "mean_abs_diff": (sum(diffs) / len(diffs)).quantize(Decimal("0.01")),
            "exact_share": round(100 * sum(1 for d in diffs if d < Decimal("0.5")) / len(diffs)),
            "within_one_share": round(100 * sum(1 for d in diffs if d <= 1) / len(diffs)),
        }
    return {
        "rows": rows,
        "counts": counts,
        "missing": sum(1 for row in rows if row["assessment"] is None),
        "in_progress": counts[AiAssessmentStatus.PENDING] + counts[AiAssessmentStatus.RUNNING],
        "agreement": agreement,
        "cost_usd": sum((row["assessment"].cost_usd for row in rows if row["assessment"]), Decimal("0")),
    }


# --- odczyty: recenzent i uczestnik -------------------------------------------------------------


def reviewer_context(review, competition, *, editable: bool) -> dict | None:
    """Panel „Ocena AI” przy recenzji albo ``None``, gdy nie ma czego pokazać.

    Wołający (``ReviewDetailView``) dostaje recenzję wyłącznie z własnych przydziałów recenzenta,
    więc sugestia cudzej pracy nie ma tu jak trafić. Pokazujemy wyłącznie ocenę ``DONE`` **tej**
    wersji pracy – sugestia do starszej wersji opisywałaby inny plik.
    """
    if not is_enabled(competition):
        return None
    assessment = AiAssessment.objects.filter(
        submission_id=review.submission_id, status=AiAssessmentStatus.DONE
    ).first()
    if assessment is None:
        return None
    from apps.grading.rubric import criteria_for

    has_rubric = bool(list(criteria_for(review.submission.problem)))
    suggested = suggested_points(assessment.proposed_points, review.submission.problem)
    return {
        "assessment": assessment,
        # Przycisk tylko wypełnia formularz – i tylko tam, gdzie formularz ma jedno pole punktów.
        # Przy rubryce punkty rozdziela recenzent kryterium po kryterium, a sugestia AI dzieli
        # rozwiązanie po swojemu – przeniesienie jej do rubryki byłoby zgadywaniem.
        "prefill_value": suggested if editable and not has_rubric else None,
    }


def suggested_points(proposed: Decimal | None, problem):
    """Punkty do wstawienia przyciskiem „punkty AI jako punkt wyjścia” – albo ``None``.

    W etapie „tylko ze skali” – najbliższa wartość skali (``prompt.nearest_scale_value``), jak
    dotąd. W etapie z dowolnymi wartościami (wydanie 0.35.0) – sama propozycja, przycięta do
    zakresu zadania i sprowadzona do 0,01 (połówka w górę, ``apps.core.points.round_points``):
    formularz przyjmuje wtedy każdą taką liczbę, więc zaokrąglanie do skali odbierałoby
    recenzentowi informację, którą AI już podało.
    """
    if proposed is None:
        return None
    rule = _rule(problem)
    if rule is not None and rule.free:
        from apps.core.points import round_points

        value = round_points(proposed)
        return min(max(value, rule.display_minimum), rule.display_maximum)
    return prompt.nearest_scale_value(proposed, scale_values(problem))


def participant_ai_feedback(participant, stage) -> list[dict]:
    """Sugestie AI dla uczestnika – **wyłącznie** gdy koordynator to włączył i wyniki są ogłoszone.

    Trzy warunki naraz: flaga konkursu, przełącznik etapu i publikacja wyników. Brak któregoś
    daje pustą listę, a szablon bez listy nie ma o czym wspomnieć – uczestnik nie dowiaduje się
    nawet, że ocena AI w ogóle istnieje.
    """
    from apps.competitions.models import StageEntry
    from apps.results.models import ResultsPublication

    competition = stage.edition.competition
    if participant is None or not is_enabled(competition):
        return []
    if not AiStageVisibility.objects.filter(stage=stage, show_to_participants=True).exists():
        return []
    if not ResultsPublication.objects.filter(stage=stage).exists():
        return []
    entry = StageEntry.objects.filter(participant=participant, stage=stage).first()
    if entry is None:
        return []
    latest: dict[int, Submission] = {}
    for submission in (
        Submission.objects.filter(entry=entry)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("problem")
        .order_by("problem__number", "problem_id", "-version")
    ):
        latest.setdefault(submission.problem_id, submission)
    assessments = {
        item.submission_id: item
        for item in AiAssessment.objects.filter(
            submission__in=[s.pk for s in latest.values()], status=AiAssessmentStatus.DONE
        )
    }
    items = []
    for submission in latest.values():
        assessment = assessments.get(submission.pk)
        if assessment is None:
            continue
        items.append(
            {
                "number": submission.problem.number,
                "title": submission.problem.title,
                "proposed_points": assessment.proposed_points,
                "max_points": assessment.max_points,
                "summary": assessment.summary,
            }
        )
    return items


def export_section(participant) -> list[dict]:
    """Oceny AI w eksporcie danych uczestnika (art. 15 i 20 RODO).

    Zawsze: **fakt** przetwarzania – która praca, kiedy i do kogo wyszła (odbiorca danych jest
    informacją, do której uczestnik ma prawo z art. 15 ust. 1 lit. c, niezależnie od ustawień
    ekranu). Treść sugestii – wyłącznie tam, gdzie uczestnik i tak ją widzi w panelu
    (:func:`participant_ai_feedback`: przełącznik etapu i ogłoszone wyniki). Eksport nie może być
    drugą, luźniejszą drogą do danych, których ekran uczestnikowi nie pokazuje.
    """
    if participant is None:
        return []
    from apps.results.models import ResultsPublication

    rows = (
        AiAssessment.objects.filter(submission__entry__participant=participant, sent_at__isnull=False)
        .select_related("submission__problem", "submission__entry__stage__edition__competition")
        .order_by("submission__entry__stage__opens_at", "submission__problem__number", "submission__version")
    )
    stage_ids = {row.submission.entry.stage_id for row in rows}
    visible = visible_stage_ids(stage_ids) & set(
        ResultsPublication.objects.filter(stage_id__in=stage_ids).values_list("stage_id", flat=True)
    )
    items = []
    for row in rows:
        stage = row.submission.entry.stage
        shown = stage.pk in visible and row.is_done and is_enabled(stage.edition.competition)
        items.append(
            {
                "etap": stage.display_name,
                "zadanie_numer": row.submission.problem.number,
                "wersja_pracy": row.submission.version,
                "przekazano_do": PROCESSOR_NAME,
                "przekazano": timezone.localtime(row.sent_at).isoformat(),
                "model": row.model,
                "charakter": (
                    "sugestia oceny dla komitetu, niewiążąca – ocenę wystawia recenzent; "
                    "decyzja nie zapada w sposób zautomatyzowany"
                ),
                "tresc": (
                    {
                        "proponowane_punkty": str(row.proposed_points),
                        "maksimum": str(row.max_points),
                        "podsumowanie": row.summary,
                    }
                    if shown
                    else None
                ),
            }
        )
    return items


def erase_for_participants(participants) -> int:
    """Kasuje oceny AI prac tych uczestników – przy anonimizacji konta (art. 17 RODO).

    Praca i jej oficjalne oceny zostają (są dokumentacją zawodów, patrz ``apps.accounts.profile``),
    ale sugestia AI dokumentacją nie jest: nikt na niej nie opiera kwalifikacji, a po
    anonimizacji nie ma już komu jej pokazać. Liczniki zużycia w ustawieniach konkursu zostają.
    """
    deleted, _ = AiAssessment.objects.filter(submission__entry__participant__in=participants).delete()
    return deleted
