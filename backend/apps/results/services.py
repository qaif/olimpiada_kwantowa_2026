"""Logika domenowa wyników etapu: przeliczenie, kwalifikacja i publikacja (T-07).

Widoki tylko orkiestrują. Zasady wspólne dla modułu:

- **snapshot kontra dane bieżące**: publikacja zamraża tabelę. Wszystko, co widzi publiczność,
  pochodzi z ``ResultsPublication.snapshot``; serwisy liczące dotykają bazy tylko w momencie
  publikacji albo podglądu koordynatora,
- **RODO**: snapshot przechodzi przez ``_display_name`` i zawiera wyłącznie ``rank``, ``display``,
  ``district``, ``points``, ``total`` i ``qualified``. Nigdy e-maila, roku urodzenia ani id
  użytkownika; imię i nazwisko wyłącznie przy ``FULL`` i tylko za zgodą uczestnika,
- **brak N+1**: przeliczenie etapu to stała liczba zapytań niezależnie od liczby wpisów – wpisy,
  zadania i zgłoszenia czytamy hurtem, a sumy składamy w Pythonie,
- audyt nigdy nie zawiera danych osobowych: w ``diff`` idą liczniki i identyfikatory,
- czas zawsze przez ``timezone.now()``.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status as http

from apps.competitions.models import (
    Problem,
    QualificationMode,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageKind,
)
from apps.core.api import DomainError
from apps.core.models import audit
from apps.grading.models import Review, ReviewStatus
from apps.submissions.models import Submission, SubmissionStatus

from .models import Anonymization, ResultsPublication

logger = logging.getLogger(__name__)

#: Kolejność etapów edycji. Kwalifikacja przenosi uczestnika do następnego – finał nie ma następcy.
STAGE_ORDER = (StageKind.ELIM, StageKind.DISTRICT, StageKind.FINAL)

#: Stany, w których ocena zgłoszenia jeszcze trwa. Etap z takim zgłoszeniem nie da się przeliczyć:
#: suma punktów byłaby chwilowa, a opublikowana tabela musi być ostateczna (PROJEKT.md 2.4).
UNFINISHED_STATUSES = (
    SubmissionStatus.IN_REVIEW,
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
)
#: Stany sprzed oceniania. Blokują przeliczenie tylko wtedy, gdy nie ma jeszcze ``FinalGrade`` –
#: praca z oceną uzgodnioną jest policzalna niezależnie od tego, w jakim stanie utknął jej rekord.
PREGRADING_STATUSES = (
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.SCANNING,
    SubmissionStatus.LOCKED,
)
#: Ile pseudonimów wchodzi do komunikatu błędu. Etap finału ma tysiące prac – lista bez limitu
#: zamieniłaby komunikat w zrzut tabeli.
MAX_REPORTED_CODES = 20


# --- błędy domenowe ---------------------------------------------------------------------------


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


# --- przeliczenie wyników ---------------------------------------------------------------------


def _latest_submissions(stage: Stage) -> dict[tuple[int, int], Submission]:
    """Najnowsza *nieodrzucona* wersja zgłoszenia dla każdej pary (wpis, zadanie).

    ``REJECTED_INFECTED`` jest pomijane: wersja odrzucona przez antywirusa nigdy nie weszła do
    oceniania, więc liczy się ostatnia wersja przed nią (a jeśli takiej nie ma – brak zgłoszenia,
    czyli 0 punktów). Jedno zapytanie na cały etap; ``final_grade`` przez ``select_related``
    (odwrotna strona relacji jeden-do-jednego), żeby suma nie robiła zapytania na wiersz.
    """
    rows = (
        Submission.objects.filter(entry__stage=stage)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("final_grade")
        .order_by("entry_id", "problem_id", "-version")
    )
    latest: dict[tuple[int, int], Submission] = {}
    for submission in rows:
        latest.setdefault((submission.entry_id, submission.problem_id), submission)
    return latest


def _blocks_finalization(submission: Submission) -> bool:
    """Czy to zgłoszenie nie pozwala jeszcze zamknąć tabeli wyników etapu."""
    if submission.status in UNFINISHED_STATUSES:
        return True
    grade = getattr(submission, "final_grade", None)
    return submission.status in PREGRADING_STATUSES and grade is None


def _assert_finalized(pending_codes: list[str]) -> None:
    if not pending_codes:
        return
    listed = sorted(set(pending_codes))
    shown = ", ".join(listed[:MAX_REPORTED_CODES])
    suffix = f" (+{len(listed) - MAX_REPORTED_CODES})" if len(listed) > MAX_REPORTED_CODES else ""
    error = _conflict(
        f"Ocenianie etapu nie jest zakończone. Nierozliczone prace: {shown}{suffix}.",
        "STAGE_NOT_FINALIZED",
    )
    # Lista pseudonimów także maszynowo – klient (panel koordynatora) nie musi parsować zdania.
    error.public_codes = listed
    raise error


def _rank_rows(rows: list[dict]) -> list[dict]:
    """Nadaje miejsca: malejąco po sumie, remis = to samo miejsce (1, 1, 3).

    Porządek wewnątrz remisu jest po ``public_code``, żeby tabela była powtarzalna – dwie
    publikacje tych samych danych muszą dać ten sam plik.
    """
    ordered = sorted(rows, key=lambda row: (-row["total"], row["public_code"]))
    rank = 0
    previous_total: int | None = None
    for index, row in enumerate(ordered, start=1):
        if previous_total is None or row["total"] != previous_total:
            rank = index
            previous_total = row["total"]
        row["rank"] = rank
    return ordered


def compute_stage_results(stage: Stage) -> list[dict]:
    """Tabela wyników etapu: suma ``FinalGrade.score`` po najnowszych wersjach zgłoszeń.

    Brak zgłoszenia do zadania = 0 punktów. Zwraca wiersze **pełne** (z danymi osobowymi) – to
    materiał dla koordynatora i wsad do anonimizacji w ``publish_results``, nigdy odpowiedź
    publiczna. Zapisuje ``StageEntry.total_points`` jednym ``bulk_update``.

    Rzuca ``STAGE_NOT_FINALIZED`` (409), gdy którakolwiek najnowsza wersja jest jeszcze
    w ocenianiu – z listą pseudonimów prac do dokończenia.
    """
    problems = list(stage.problems.order_by("number", "id"))
    entries = list(
        StageEntry.objects.filter(stage=stage)
        .select_related("participant", "participant__user")
        .order_by("id")
    )
    latest = _latest_submissions(stage)

    pending_codes: list[str] = []
    rows: list[dict] = []
    for entry in entries:
        participant = entry.participant
        points: dict[str, int] = {}
        total = 0
        for problem in problems:
            submission = latest.get((entry.pk, problem.pk))
            score = 0
            if submission is not None:
                if _blocks_finalization(submission):
                    pending_codes.append(participant.public_code)
                grade = getattr(submission, "final_grade", None)
                score = int(grade.score) if grade is not None else 0
            points[str(problem.number)] = score
            total += score
        rows.append(
            {
                "entry_id": entry.pk,
                "participant_id": participant.pk,
                "public_code": participant.public_code,
                "first_name": participant.user.first_name,
                "last_name": participant.user.last_name,
                "school": participant.school,
                "district": participant.district,
                "publish_full_name": participant.publish_full_name,
                "status": entry.status,
                "points": points,
                "total": total,
            }
        )

    _assert_finalized(pending_codes)

    changed = []
    by_entry = {row["entry_id"]: row for row in rows}
    for entry in entries:
        total = by_entry[entry.pk]["total"]
        if entry.total_points != total:
            entry.total_points = total
            changed.append(entry)
    if changed:
        StageEntry.objects.bulk_update(changed, ["total_points"])
    return _rank_rows(rows)


# --- kwalifikacja -----------------------------------------------------------------------------


def _rule_for(stage: Stage):
    rule = getattr(stage, "qualification_rule", None)
    if rule is None:
        raise _conflict("Etap nie ma progu kwalifikacji.", "QUALIFICATION_RULE_MISSING")
    if rule.requires_min_points and rule.min_points is None:
        raise _conflict(f"Próg {rule.mode} wymaga min_points.", "QUALIFICATION_RULE_INVALID")
    if rule.requires_top_n and (rule.top_n is None or rule.top_n < 1):
        raise _conflict(f"Próg {rule.mode} wymaga dodatniego top_n.", "QUALIFICATION_RULE_INVALID")
    return rule


def _top_n_cutoff(totals: list[int], top_n: int) -> int | None:
    """Najniższa suma mieszcząca się w pierwszej ``top_n`` – albo ``None``, gdy nie ma kandydatów.

    Remis na granicy rozstrzyga się na korzyść uczestników: progiem jest *wartość* zajmująca
    miejsce ``top_n``, więc wszyscy z takim samym wynikiem wchodzą, choćby było ich więcej niż N.
    """
    if not totals:
        return None
    ordered = sorted(totals, reverse=True)
    return ordered[min(top_n, len(ordered)) - 1]


def _qualified_entry_ids(rows: list[dict], rule) -> set[int]:
    """Identyfikatory wpisów spełniających próg. ``rows`` są już bez zdyskwalifikowanych."""
    mode = rule.mode
    if mode == QualificationMode.MIN_POINTS:
        return {row["entry_id"] for row in rows if row["total"] >= rule.min_points}
    if mode == QualificationMode.TOP_N:
        cutoff = _top_n_cutoff([row["total"] for row in rows], rule.top_n)
        if cutoff is None:
            return set()
        return {row["entry_id"] for row in rows if row["total"] >= cutoff}
    if mode == QualificationMode.TOP_N_PER_DISTRICT:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(_district_key(row["district"]), []).append(row)
        qualified: set[int] = set()
        for group in groups.values():
            cutoff = _top_n_cutoff([row["total"] for row in group], rule.top_n)
            if cutoff is None:
                continue
            qualified |= {row["entry_id"] for row in group if row["total"] >= cutoff}
        return qualified
    if mode == QualificationMode.HYBRID:
        cutoff = _top_n_cutoff([row["total"] for row in rows], rule.top_n)
        if cutoff is None:
            return set()
        return {row["entry_id"] for row in rows if row["total"] >= cutoff and row["total"] >= rule.min_points}
    raise _conflict(f"Nieznany tryb progu kwalifikacji: {mode}.", "QUALIFICATION_RULE_INVALID")


def _district_key(value: str | None) -> str:
    """Okręgi grupujemy po znormalizowanej nazwie – „Mazowiecki” i „mazowiecki” to jeden okręg."""
    return (value or "").strip().casefold()


def next_stage_of(stage: Stage) -> Stage | None:
    """Następny etap tej samej edycji (ELIM → DISTRICT → FINAL). Finał nie ma następnego."""
    try:
        index = STAGE_ORDER.index(stage.kind)
    except ValueError:  # pragma: no cover - kind pochodzi z choices
        return None
    for kind in STAGE_ORDER[index + 1 :]:
        found = Stage.objects.filter(edition_id=stage.edition_id, kind=kind).first()
        if found is not None:
            return found
    return None


@transaction.atomic
def apply_qualification(stage: Stage, *, actor=None, request=None) -> dict:
    """Przelicza wyniki i ustawia statusy kwalifikacji, tworząc wpisy w następnym etapie.

    ``DISQUALIFIED`` zostaje nietknięty i nie bierze udziału w progu – dyskwalifikacja jest
    decyzją proceduralną, a nie wynikiem punktowym, więc nie może zajmować miejsca w „top N”.
    Idempotentne: wpisy w następnym etapie powstają przez ``get_or_create``, więc drugie wywołanie
    niczego nie duplikuje ani nie cofa statusu już zarejestrowanego uczestnika.
    """
    rule = _rule_for(stage)
    rows = compute_stage_results(stage)

    candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    qualified_ids = _qualified_entry_ids(candidates, rule)

    entries = {
        entry.pk: entry
        for entry in StageEntry.objects.filter(pk__in=[row["entry_id"] for row in candidates]).select_related(
            "participant"
        )
    }
    changed: list[StageEntry] = []
    for row in candidates:
        entry = entries[row["entry_id"]]
        row["qualified"] = entry.pk in qualified_ids
        target = StageEntryStatus.QUALIFIED if row["qualified"] else StageEntryStatus.NOT_QUALIFIED
        if entry.status != target:
            entry.status = target
            changed.append(entry)
        row["status"] = target
    for row in rows:
        # Zdyskwalifikowany nie kwalifikuje się nigdy – w tabeli musi mieć jawne ``False``.
        row.setdefault("qualified", False)
    if changed:
        StageEntry.objects.bulk_update(changed, ["status"])

    following = next_stage_of(stage)
    created_entries = 0
    if following is not None:
        for row in candidates:
            if not row["qualified"]:
                continue
            _, created = StageEntry.objects.get_or_create(
                participant_id=row["participant_id"],
                stage=following,
                defaults={"status": StageEntryStatus.REGISTERED},
            )
            created_entries += int(created)

    summary = {
        "stage_id": stage.pk,
        "mode": rule.mode,
        "qualified": sum(1 for row in candidates if row["qualified"]),
        "not_qualified": sum(1 for row in candidates if not row["qualified"]),
        "disqualified": len(rows) - len(candidates),
        "next_stage_id": following.pk if following is not None else None,
        "created_entries": created_entries,
        "rows": rows,
    }
    audit(
        actor,
        "results.qualification_applied",
        stage,
        {key: value for key, value in summary.items() if key != "rows"},
        request=request,
    )
    logger.info(
        "Etap %s: kwalifikacja %s – %s zakwalifikowanych, %s nowych wpisów w etapie %s",
        stage.pk,
        rule.mode,
        summary["qualified"],
        created_entries,
        summary["next_stage_id"],
    )
    return summary


# --- publikacja -------------------------------------------------------------------------------


def _initials(first_name: str, last_name: str) -> str:
    parts = [part.strip()[:1].upper() for part in (first_name, last_name) if part and part.strip()]
    return "".join(f"{letter}." for letter in parts)


def _display_name(row: dict, anonymization: str) -> str:
    """Jedyne miejsce, w którym powstaje etykieta uczestnika w publikowanej tabeli.

    Reguła domyślnie zamknięta: każdy tryb, który nie ma kompletu danych do pokazania, spada do
    pseudonimu. Pełne imię i nazwisko wymaga jednocześnie trybu ``FULL`` i zgody uczestnika
    (``publish_full_name``) – bez zgody nawet finał pokazuje kod (RODO: minimalizacja + zgoda).
    """
    code = row["public_code"]
    if anonymization == Anonymization.FULL:
        if not row.get("publish_full_name"):
            return code
        full = " ".join(part for part in (row["first_name"], row["last_name"]) if part).strip()
        return full or code
    if anonymization == Anonymization.INITIALS_SCHOOL:
        initials = _initials(row["first_name"], row["last_name"])
        if not initials:
            return code
        school = (row.get("school") or "").strip()
        return f"{initials}, {school}" if school else initials
    return code


def build_snapshot(rows: list[dict], anonymization: str) -> list[dict]:
    """Zamrożona tabela: wyłącznie ``rank``, ``display``, ``district``, ``points``, ``total``,
    ``qualified``.

    Kształt jest budowany od zera z jawnie wypisanych pól, a nie przez usuwanie kluczy z wiersza
    roboczego – dopisanie kiedyś kolumny z danymi osobowymi do ``compute_stage_results`` nie może
    w żaden sposób „przeciec” do publikacji.
    """
    return [
        {
            "rank": row["rank"],
            "display": _display_name(row, anonymization),
            "district": row["district"],
            "points": dict(row["points"]),
            "total": row["total"],
            "qualified": bool(row.get("qualified")),
        }
        for row in rows
    ]


@transaction.atomic
def publish_results(stage: Stage, actor, anonymization: str, *, request=None) -> ResultsPublication:
    """Publikuje wyniki etapu: przelicza, kwalifikuje i zamraża zanonimizowaną tabelę.

    Ponowna publikacja nadpisuje snapshot tego samego rekordu (jeden etap = jedna tabela w mocy)
    i zostawia wpis w audycie. ``diff`` audytu ma wyłącznie liczniki – tabela wyników z nazwiskami
    nie może wylądować w logu czytanym przez osoby bez prawa do danych osobowych.
    """
    if anonymization not in Anonymization.values:
        raise _bad_request(f"Nieznany tryb anonimizacji: {anonymization}.", "INVALID_ANONYMIZATION")
    summary = apply_qualification(stage, actor=actor, request=request)
    snapshot = build_snapshot(summary["rows"], anonymization)
    now = timezone.now()

    publication, created = ResultsPublication.objects.update_or_create(
        stage=stage,
        defaults={
            "published_at": now,
            "published_by": actor if getattr(actor, "is_authenticated", False) else None,
            "anonymization": anonymization,
            "snapshot": snapshot,
        },
    )
    stage.results_published_at = now
    stage.save(update_fields=["results_published_at"])

    audit(
        actor,
        "results.published",
        publication,
        {
            "stage_id": stage.pk,
            "anonymization": anonymization,
            "rows": len(snapshot),
            "qualified": summary["qualified"],
            "republished": not created,
        },
        request=request,
    )
    logger.info(
        "Etap %s: opublikowano wyniki (%s wierszy, tryb %s, ponowna publikacja: %s)",
        stage.pk,
        len(snapshot),
        anonymization,
        not created,
    )
    return publication


# --- odczyt dla API ---------------------------------------------------------------------------


def published_results(stage_id: int) -> ResultsPublication | None:
    """Publikacja etapu albo ``None``. Publiczny widok nie dotyka poza tym żadnej innej tabeli."""
    return ResultsPublication.objects.filter(stage_id=stage_id).select_related("stage").first()


def _feedback_for(submission: Submission | None) -> list[dict]:
    """Informacja zwrotna dla uczestnika: komentarz i adnotacje **publiczne**.

    Nigdy ``comment_internal`` i nigdy tożsamości recenzenta (PROJEKT.md 2.4). Recenzje bez treści
    dla uczestnika są pomijane – pusta pozycja tylko zdradzałaby liczbę recenzentów.
    """
    if submission is None:
        return []
    feedback = []
    for review in submission.reviews.all():
        if review.status != ReviewStatus.SUBMITTED:
            continue
        comment = (review.comment_for_participant or "").strip()
        annotations = review.public_annotations()
        if not comment and not annotations:
            continue
        feedback.append({"comment_for_participant": comment, "annotations": annotations})
    return feedback


def results_for_participant(user) -> list[dict]:
    """Własne wyniki uczestnika – wyłącznie z etapów, których wyniki są już opublikowane.

    Dane są liczone na bieżąco (to własne punkty uczestnika, nie ogłoszona tabela), ale widoczne
    dopiero po ``Stage.results_published_at``: przed publikacją etap w ogóle nie jest zwracany.
    """
    participant = getattr(user, "participant", None)
    if participant is None:
        return []
    entries = list(
        StageEntry.objects.filter(participant=participant, stage__results_published_at__isnull=False)
        .select_related("stage", "stage__edition")
        .order_by("stage__opens_at", "stage_id")
    )
    if not entries:
        return []

    stage_ids = [entry.stage_id for entry in entries]
    problems: dict[int, list] = {stage_id: [] for stage_id in stage_ids}
    for problem in Problem.objects.filter(stage_id__in=stage_ids).order_by("number", "id"):
        problems[problem.stage_id].append(problem)

    submissions = (
        Submission.objects.filter(entry__in=entries)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("final_grade")
        .prefetch_related(Prefetch("reviews", queryset=Review.objects.order_by("round", "id")))
        .order_by("entry_id", "problem_id", "-version")
    )
    latest: dict[tuple[int, int], Submission] = {}
    for submission in submissions:
        latest.setdefault((submission.entry_id, submission.problem_id), submission)

    results = []
    for entry in entries:
        stage = entry.stage
        rows = []
        total = 0
        for problem in problems.get(stage.pk, []):
            submission = latest.get((entry.pk, problem.pk))
            grade = getattr(submission, "final_grade", None) if submission is not None else None
            score = int(grade.score) if grade is not None else 0
            total += score
            rows.append(
                {
                    "problem_id": problem.pk,
                    "number": problem.number,
                    "title": problem.title,
                    "score": score,
                    "feedback": _feedback_for(submission),
                }
            )
        results.append(
            {
                "stage_id": stage.pk,
                "stage_kind": stage.kind,
                "edition": stage.edition.year_label,
                "results_published_at": stage.results_published_at,
                "status": entry.status,
                "qualified": entry.status == StageEntryStatus.QUALIFIED,
                "total_points": entry.total_points if entry.total_points is not None else total,
                "problems": rows,
            }
        )
    return results
