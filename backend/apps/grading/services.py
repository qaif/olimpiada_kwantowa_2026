"""Logika domenowa oceniania: przydział recenzentów, wystawianie ocen, konsensus, moderacja.

Widoki tylko orkiestrują. Zasady wspólne dla całego modułu:

- każda zmiana stanu ``Submission`` idzie pod blokadą ``SELECT ... FOR UPDATE`` na tym zgłoszeniu.
  To jedyny punkt szeregowania: dwa równoczesne ``submit_review`` nie mogą utworzyć dwóch
  ``FinalGrade`` (relacja jeden-do-jednego w bazie jest dopiero drugą linią obrony),
- konflikt interesów na etapie okręgowym liczy się z ``CommitteeMember.district``, ale wyłącznie
  gdy okręg jest potwierdzony (``district_verified``). Okręg samodeklarowany nie jest dowodem
  braku konfliktu, więc taki recenzent nie dostaje przydziału na etapie okręgowym (dług T-02),
- czas zawsze przez ``timezone.now()``.
"""

from __future__ import annotations

import logging
from collections import Counter

from django.db import connection, models, transaction
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
)
from apps.accounts.services import active_reviewer_profile
from apps.competitions.models import Stage, StageKind
from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import Submission, SubmissionFile, SubmissionStatus

from .models import ROUND_BLIND, ROUND_TIEBREAK, FinalGrade, GradeMethod, Review, ReviewStatus

logger = logging.getLogger(__name__)

#: Stany zgłoszenia, w których wolno wystawić ocenę (moderacja obsługuje rundę rozjemczą).
REVIEWABLE_STATUSES = (SubmissionStatus.IN_REVIEW, SubmissionStatus.MODERATION)
#: Przestrzeń kluczy blokad doradczych Postgresa dla tego modułu (pg_advisory_xact_lock(int4, int4)).
#: Stała nie może kolidować z innymi modułami – każdy, kto doda blokadę doradczą, bierze własną.
ADVISORY_LOCK_NAMESPACE_ASSIGNMENT = 1005
#: Twarde limity adnotacji – JSON od recenzenta nie może urosnąć w nieskończoność.
MAX_ANNOTATIONS = 500
MAX_ANNOTATION_TEXT = 2000
MAX_COMMENT_LENGTH = 20000


# --- błędy domenowe ---------------------------------------------------------------------------


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


# --- pomocnicze -------------------------------------------------------------------------------


def _norm_district(value: str | None) -> str:
    return (value or "").strip().casefold()


def is_coordinator(user) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return user.groups.filter(name=GROUP_COORDINATOR).exists()


def _lock_stage_for_assignment(stage: Stage) -> None:
    """Blokada doradcza na czas transakcji przydziału dla jednego etapu.

    Bez niej dwa równoczesne ``POST stages/{id}/assign/`` czytają ten sam obraz świata: każde widzi
    rozwiązanie bez recenzji i przydziela mu własnych recenzentów. Efektem jest albo czterech
    recenzentów zamiast dwóch, albo ``IntegrityError`` (500) na unikalności przydziału. Blokada
    doradcza, a nie ``select_for_update`` na ``Stage``: szeregujemy *operację*, a nie wiersz etapu,
    który sam się tu nie zmienia. Zwalnia ją koniec transakcji – także przy wyjątku.

    Jedyne surowe SQL w module i jedyne możliwe: to funkcja Postgresa bez odpowiednika w ORM.
    Parametry idą przez placeholdery sterownika, nie przez formatowanie napisu.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", [ADVISORY_LOCK_NAMESPACE_ASSIGNMENT, int(stage.pk)]
        )


def reviewer_pool() -> list[CommitteeMember]:
    """Aktywni recenzenci: status ACTIVE, konto aktywne, grupa ``reviewer``."""
    return list(
        CommitteeMember.objects.filter(
            status=CommitteeStatus.ACTIVE,
            user__is_active=True,
            user__groups__name=GROUP_REVIEWER,
        )
        .select_related("user")
        .distinct()
        .order_by("pk")
    )


def has_district_conflict(member: CommitteeMember, stage: Stage, participant_district: str | None) -> bool:
    """Czy recenzent jest w konflikcie okręgu dla tego uczestnika na tym etapie.

    Poza etapem okręgowym konfliktu nie ma. Na etapie okręgowym konfliktowy jest recenzent z okręgu
    uczestnika, a także **każdy** recenzent z okręgiem niepotwierdzonym lub pustym: dopóki
    koordynator nie potwierdzi okręgu, nie da się wykazać, że konfliktu nie ma (dług T-02).
    """
    if stage.kind != StageKind.DISTRICT:
        return False
    if not member.district_verified or not _norm_district(member.district):
        return True
    return _norm_district(member.district) == _norm_district(participant_district)


def validate_annotations(raw) -> list[dict]:
    """Sprowadza adnotacje do kanonicznego kształtu ``{page, rect[4], text, public}``.

    JSON przychodzi od recenzenta, więc jest walidowany strukturalnie i przycinany – nie trafia
    do bazy „jak leci”.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad_request("Adnotacje muszą być listą.", "INVALID_ANNOTATIONS")
    if len(raw) > MAX_ANNOTATIONS:
        raise _bad_request(f"Za dużo adnotacji (limit {MAX_ANNOTATIONS}).", "INVALID_ANNOTATIONS")
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _bad_request("Adnotacja musi być obiektem.", "INVALID_ANNOTATIONS")
        page = item.get("page")
        rect = item.get("rect")
        text = item.get("text", "")
        public = item.get("public", False)
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise _bad_request("Adnotacja wymaga numeru strony (page >= 1).", "INVALID_ANNOTATIONS")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise _bad_request("Adnotacja wymaga prostokąta rect=[x, y, w, h].", "INVALID_ANNOTATIONS")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in rect):
            raise _bad_request("Współrzędne rect muszą być liczbami.", "INVALID_ANNOTATIONS")
        if not isinstance(text, str):
            raise _bad_request("Treść adnotacji musi być tekstem.", "INVALID_ANNOTATIONS")
        if not isinstance(public, bool):
            raise _bad_request("Pole 'public' musi być logiczne.", "INVALID_ANNOTATIONS")
        cleaned.append(
            {
                "page": page,
                "rect": [float(value) for value in rect],
                "text": text[:MAX_ANNOTATION_TEXT],
                "public": public,
            }
        )
    return cleaned


def _clean_comment(value: str | None) -> str:
    return (value or "")[:MAX_COMMENT_LENGTH]


def allowed_scores(stage: Stage) -> set[int]:
    scale = getattr(stage, "scoring_scale", None)
    if scale is None:
        raise _conflict("Etap nie ma skali punktacji.", "SCORING_SCALE_MISSING")
    values = scale.allowed_values()
    if not values:
        raise _conflict("Skala punktacji etapu jest pusta.", "SCORING_SCALE_MISSING")
    return values


def _assert_score_in_scale(stage: Stage, score) -> int:
    if not isinstance(score, int) or isinstance(score, bool) or score not in allowed_scores(stage):
        raise _bad_request(
            f"Ocena {score} nie należy do skali {sorted(allowed_scores(stage))}.", "SCORE_NOT_IN_SCALE"
        )
    return score


def _locked_submission(submission_id: int) -> Submission:
    """Zgłoszenie pod blokadą wiersza – jedyny punkt szeregowania zapisów oceniania."""
    return (
        Submission.objects.select_for_update(of=("self",))
        .select_related("entry", "entry__participant", "entry__stage", "entry__stage__scoring_scale")
        .get(pk=submission_id)
    )


# --- przydział --------------------------------------------------------------------------------


def _assignable_submissions(stage: Stage) -> list[Submission]:
    """Zablokowane (i już przydzielane) rozwiązania etapu – po jednym, najnowszym, na zadanie.

    ``close_stage`` blokuje wyłącznie najnowszą nadającą się do oceny wersję, więc filtr po
    statusie zwykle wystarcza. Wybór maksymalnej wersji per (wpis, zadanie) jest zabezpieczeniem:
    starsza wersja nigdy nie może wejść do oceniania obok nowszej.
    """
    rows = (
        Submission.objects.filter(
            entry__stage=stage,
            status__in=(SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
        )
        .select_related("entry", "entry__participant")
        .order_by("entry_id", "problem_id", "-version")
    )
    best: dict[tuple[int, int], Submission] = {}
    for submission in rows:
        best.setdefault((submission.entry_id, submission.problem_id), submission)
    return sorted(best.values(), key=lambda item: item.pk)


@transaction.atomic
def assign_reviewers(stage: Stage, per_submission: int = 2, *, actor=None, request=None) -> dict:
    """Przydziela ``per_submission`` różnych recenzentów każdemu zablokowanemu rozwiązaniu etapu.

    Równoważenie: kolejny przydział dostaje recenzent z najmniejszą bieżącą liczbą recenzji w tym
    etapie. Idempotentny – rozwiązanie, które ma już komplet recenzentów rundy 1, jest pomijane.

    Rozwiązanie, dla którego nie da się skompletować recenzentów bez konfliktu interesów, **nie**
    wywraca całego etapu: trafia na listę ``skipped`` (z powodem), a reszta zostaje przydzielona
    i zacommitowana. Koordynator dostaje wtedy listę spraw do ręcznego załatwienia zamiast
    komunikatu „nic się nie udało”. Dopiero gdy nie udało się przydzielić **niczego**, leci 409 –
    wtedy nie ma czego commitować i cisza byłaby myląca.
    """
    if per_submission < 1:
        raise _bad_request("Liczba recenzentów musi być dodatnia.", "INVALID_PER_SUBMISSION")

    _lock_stage_for_assignment(stage)
    submissions = _assignable_submissions(stage)
    pool = reviewer_pool()
    loads: Counter[int] = Counter({member.pk: 0 for member in pool})
    for reviewer_id in Review.objects.filter(submission__entry__stage=stage).values_list(
        "reviewer_id", flat=True
    ):
        loads[reviewer_id] += 1

    created_total = 0
    touched: list[int] = []
    skipped: list[dict] = []
    for submission in submissions:
        already = set(
            Review.objects.filter(submission=submission, round=ROUND_BLIND)
            .exclude(status=ReviewStatus.CANCELLED)
            .values_list("reviewer_id", flat=True)
        )
        missing = per_submission - len(already)
        if missing <= 0:
            continue
        participant_district = submission.entry.participant.district
        eligible = [
            member
            for member in pool
            if member.pk not in already and not has_district_conflict(member, stage, participant_district)
        ]
        if len(eligible) < missing:
            skipped.append(
                {
                    "submission_id": submission.pk,
                    # Pseudonim, nie dane osobowe – koordynator musi wiedzieć, czyją pracę tknąć ręcznie.
                    "public_code": submission.entry.participant.public_code,
                    "reason": "NOT_ENOUGH_REVIEWERS",
                }
            )
            continue
        chosen = sorted(eligible, key=lambda member: (loads[member.pk], member.pk))[:missing]
        now = timezone.now()
        Review.objects.bulk_create(
            [
                Review(
                    submission=submission,
                    reviewer=member,
                    round=ROUND_BLIND,
                    status=ReviewStatus.ASSIGNED,
                    assigned_at=now,
                )
                for member in chosen
            ]
        )
        for member in chosen:
            loads[member.pk] += 1
        created_total += len(chosen)
        if submission.status == SubmissionStatus.LOCKED:
            submission.status = SubmissionStatus.IN_REVIEW
            submission.save(update_fields=["status"])
        touched.append(submission.pk)

    if created_total == 0 and skipped:
        # Nic nie dało się przydzielić – nie ma czego commitować, więc odpowiadamy błędem domenowym.
        raise _conflict("Za mało recenzentów bez konfliktu interesów dla tego etapu.", "NOT_ENOUGH_REVIEWERS")

    audit(
        actor,
        "review.assigned",
        stage,
        {
            "submissions": len(touched),
            "assignments": created_total,
            "per_submission": per_submission,
            "skipped": [item["submission_id"] for item in skipped],
        },
        request=request,
    )
    logger.info(
        "Etap %s: przydzielono %s recenzji dla %s rozwiązań, pominięto %s",
        stage.pk,
        created_total,
        len(touched),
        len(skipped),
    )
    return {"submissions": len(touched), "assignments": created_total, "skipped": skipped}


@transaction.atomic
def assign_third_reviewer(submission: Submission, reviewer: CommitteeMember, *, actor=None, request=None):
    """Wyznacza trzeciego recenzenta (runda 2) dla rozwiązania w moderacji."""
    locked = _locked_submission(submission.pk)
    if locked.status != SubmissionStatus.MODERATION:
        raise _conflict("Rozwiązanie nie jest w moderacji.", "NOT_IN_MODERATION")
    if reviewer.status != CommitteeStatus.ACTIVE or not reviewer.user.is_active:
        raise _bad_request("Recenzent nie jest aktywny.", "REVIEWER_NOT_ELIGIBLE")
    if not reviewer.user.groups.filter(name=GROUP_REVIEWER).exists():
        raise _bad_request("Wskazana osoba nie jest recenzentem.", "REVIEWER_NOT_ELIGIBLE")
    if Review.objects.filter(submission=locked, reviewer=reviewer, round=ROUND_BLIND).exists():
        raise _conflict(
            "Trzecim recenzentem nie może być autor oceny z rundy 1.", "REVIEWER_CONFLICT_OF_INTEREST"
        )
    if has_district_conflict(reviewer, locked.entry.stage, locked.entry.participant.district):
        raise _conflict("Recenzent jest w konflikcie okręgu.", "REVIEWER_CONFLICT_OF_INTEREST")
    if Review.objects.filter(submission=locked, round=ROUND_TIEBREAK).exists():
        raise _conflict("Trzeci recenzent jest już wyznaczony.", "THIRD_REVIEWER_ALREADY_ASSIGNED")

    review = Review.objects.create(
        submission=locked,
        reviewer=reviewer,
        round=ROUND_TIEBREAK,
        status=ReviewStatus.ASSIGNED,
        assigned_at=timezone.now(),
    )
    audit(
        actor,
        "review.third_assigned",
        review,
        {"submission_id": locked.pk, "reviewer_id": reviewer.pk},
        request=request,
    )
    return review


# --- wystawianie ocen -------------------------------------------------------------------------


def _assert_review_open(review: Review, submission_status: str) -> None:
    """Wspólna bramka zapisu recenzji: stan recenzji **i** stan zgłoszenia.

    Sam stan recenzji nie wystarcza: po rozstrzygnięciu rozjazdu przez koordynatora albo po
    finalizacji etapu praca jest zamknięta, a wiszący przydział nie może już do niej niczego dopisać
    (maszyna stanów PROJEKT.md 2.4). Bez tego szkic zapisywał się do zgłoszenia w stanie FINAL.
    """
    if review.status == ReviewStatus.SUBMITTED:
        raise _conflict("Recenzja została już wystawiona.", "REVIEW_ALREADY_SUBMITTED")
    if review.status == ReviewStatus.CANCELLED:
        raise _conflict("Ten przydział został anulowany.", "REVIEW_CANCELLED")
    if submission_status not in REVIEWABLE_STATUSES:
        raise _conflict(
            f"Rozwiązanie w stanie {submission_status} nie przyjmuje ocen.", "SUBMISSION_NOT_REVIEWABLE"
        )


def save_draft(
    review: Review, *, score=None, comment_internal=None, comment_for_participant=None, annotations=None
):
    """Zapis szkicu recenzji. Bez walidacji finalnej – ocena może być jeszcze niepełna."""
    _assert_review_open(review, review.submission.status)
    fields: list[str] = []
    if score is not None:
        review.score = score
        fields.append("score")
    if comment_internal is not None:
        review.comment_internal = _clean_comment(comment_internal)
        fields.append("comment_internal")
    if comment_for_participant is not None:
        review.comment_for_participant = _clean_comment(comment_for_participant)
        fields.append("comment_for_participant")
    if annotations is not None:
        review.annotations = validate_annotations(annotations)
        fields.append("annotations")
    if review.status != ReviewStatus.DRAFT:
        review.status = ReviewStatus.DRAFT
        fields.append("status")
    if fields:
        review.save(update_fields=fields)
    return review


def _create_final_grade(
    submission: Submission, *, score: int, method: str, decided_by=None, rationale: str = "", request=None
) -> FinalGrade:
    """Tworzy ocenę uzgodnioną i przenosi zgłoszenie do GRADED_PROVISIONAL.

    Wołane wyłącznie spod blokady ``select_for_update`` na ``submission`` – dzięki temu wyścig
    dwóch recenzji nie może utworzyć dwóch ocen. Relacja jeden-do-jednego zatrzymałaby drugi
    zapis w bazie, ale użytkownik dostałby wtedy 500 zamiast poprawnego przebiegu.
    """
    if FinalGrade.objects.filter(submission=submission).exists():
        raise _conflict("Rozwiązanie ma już ocenę uzgodnioną.", "ALREADY_GRADED")
    grade = FinalGrade.objects.create(
        submission=submission,
        score=score,
        method=method,
        decided_by=decided_by,
        decided_at=timezone.now(),
        rationale=_clean_comment(rationale),
    )
    submission.status = SubmissionStatus.GRADED_PROVISIONAL
    submission.save(update_fields=["status"])
    audit(
        decided_by,
        "grade.decided",
        grade,
        {"submission_id": submission.pk, "score": score, "method": method},
        request=request,
    )
    _cancel_pending_tiebreak(submission, actor=decided_by, request=request)
    return grade


def _cancel_pending_tiebreak(submission: Submission, *, actor=None, request=None) -> int:
    """Anuluje niewystawione recenzje rundy 2 po rozstrzygnięciu rozjazdu.

    Gdy rozjazd rozstrzygnie koordynator, przydział trzeciego recenzenta traci przedmiot. Zostawiony
    w ASSIGNED wisiałby na jego liście zadań i pozwalał dopisać ocenę do zamkniętej już pracy.
    Rekord zostaje (ślad po przydziale jest częścią historii), zmienia się tylko status.
    """
    pending = list(
        Review.objects.filter(submission=submission, round=ROUND_TIEBREAK).exclude(
            status__in=(ReviewStatus.SUBMITTED, ReviewStatus.CANCELLED)
        )
    )
    for review in pending:
        review.status = ReviewStatus.CANCELLED
        review.save(update_fields=["status"])
        audit(
            actor,
            "review.cancelled",
            review,
            {"submission_id": submission.pk, "round": review.round, "reason": "MODERATION_RESOLVED"},
            request=request,
        )
    return len(pending)


def _settle_round_one(submission: Submission, *, request=None) -> None:
    """Po komplecie ocen rundy 1: zgodne → ``FinalGrade(CONSENSUS)``, różne → moderacja.

    Rozstrzyga faktyczna liczba recenzji rundy 1, a nie założone „dwie”. Przy ``per_submission=1``
    (przydział awaryjny) jedna wystawiona ocena też domyka sprawę – inaczej praca zostawałaby
    na zawsze w IN_REVIEW, bo drugiej oceny nie miałby kto wystawić.
    """
    reviews = list(
        Review.objects.filter(submission=submission, round=ROUND_BLIND).exclude(status=ReviewStatus.CANCELLED)
    )
    if not reviews or not all(review.is_submitted for review in reviews):
        return
    scores = {review.score for review in reviews}
    if len(scores) == 1:
        # Konsensus nie ma człowieka rozstrzygającego – decyduje reguła, stąd decided_by=None.
        _create_final_grade(
            submission,
            score=scores.pop(),
            method=GradeMethod.CONSENSUS,
            decided_by=None,
            rationale=f"Zgodne oceny niezależnych recenzentów ({len(reviews)}).",
            request=request,
        )
        return
    if submission.status != SubmissionStatus.MODERATION:
        submission.status = SubmissionStatus.MODERATION
        submission.save(update_fields=["status"])
        audit(
            None,
            "submission.moderation",
            submission,
            {"scores": sorted(score for score in scores if score is not None)},
            request=request,
        )


@transaction.atomic
def submit_review(
    review: Review,
    score,
    comment_internal: str = "",
    comment_for_participant: str = "",
    annotations=None,
    *,
    request=None,
) -> Review:
    """Wystawia ocenę i rozstrzyga dalszy los zgłoszenia (PROJEKT.md 2.4).

    Kolejność blokad jest stała: najpierw ``Submission`` (``select_for_update``), potem recenzja.
    Dzięki temu dwa równoczesne wystawienia ocen szeregują się i tylko jedno z nich domyka rundę.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)

    _assert_review_open(review, submission.status)
    score = _assert_score_in_scale(submission.entry.stage, score)
    cleaned_annotations = validate_annotations(annotations)

    review.score = score
    review.comment_internal = _clean_comment(comment_internal)
    review.comment_for_participant = _clean_comment(comment_for_participant)
    review.annotations = cleaned_annotations
    review.status = ReviewStatus.SUBMITTED
    review.submitted_at = timezone.now()
    review.save(
        update_fields=[
            "score",
            "comment_internal",
            "comment_for_participant",
            "annotations",
            "status",
            "submitted_at",
        ]
    )
    audit(
        review.reviewer.user,
        "review.submitted",
        review,
        {"submission_id": submission.pk, "round": review.round, "score": score},
        request=request,
    )

    if review.round == ROUND_TIEBREAK:
        # ``rationale`` bierze się z ``comment_internal``, a nie z ``comment_for_participant``,
        # świadomie: to ślad procedury dla koordynatora i komisji odwoławczej – dlaczego rozjazd
        # rozstrzygnięto tak, a nie inaczej. Komentarz dla uczestnika bywa pusty (trzeci recenzent
        # pisze przede wszystkim do komisji), więc oparcie o niego zostawiałoby ocenę bez
        # uzasadnienia w aktach. Ochroną nie jest tu treść pola, tylko warstwa prezentacji:
        # ``submissions.serializers.SubmissionFinalGradeSerializer`` oddaje ``rationale``
        # wyłącznie dla ``method=APPEAL``, więc do uczestnika ten tekst nie trafia (PROJEKT.md 2.4).
        _create_final_grade(
            submission,
            score=score,
            method=GradeMethod.THIRD_REVIEW,
            decided_by=review.reviewer.user,
            rationale=review.comment_internal,
            request=request,
        )
    else:
        _settle_round_one(submission, request=request)
    review.submission = submission
    return review


# --- moderacja --------------------------------------------------------------------------------


def _resolution_method(submission: Submission, actor) -> tuple[str, Review | None]:
    """Tryb rozstrzygnięcia dostępny dla tego aktora: koordynator albo trzeci recenzent."""
    if is_coordinator(actor):
        return GradeMethod.MODERATION, None
    member = active_reviewer_profile(actor)
    if member is not None:
        third = (
            Review.objects.filter(submission=submission, reviewer=member, round=ROUND_TIEBREAK)
            .exclude(status=ReviewStatus.CANCELLED)
            .first()
        )
        if third is not None:
            return GradeMethod.THIRD_REVIEW, third
    raise DomainError(
        "Rozstrzygnąć rozjazd może koordynator albo wyznaczony trzeci recenzent.",
        "NOT_ALLOWED_TO_RESOLVE",
        http.HTTP_403_FORBIDDEN,
    )


@transaction.atomic
def resolve_moderation(
    submission: Submission, actor, score, method: str | None = None, rationale: str = "", *, request=None
) -> FinalGrade:
    """Rozstrzyga rozjazd ocen: koordynator (MODERATION) albo trzeci recenzent (THIRD_REVIEW)."""
    locked = _locked_submission(submission.pk)
    allowed_method, third_review = _resolution_method(locked, actor)
    if method is not None and method != allowed_method:
        raise DomainError(
            f"Ten tryb rozstrzygnięcia nie jest dostępny dla tego konta (dozwolony: {allowed_method}).",
            "NOT_ALLOWED_TO_RESOLVE",
            http.HTTP_403_FORBIDDEN,
        )
    if locked.status != SubmissionStatus.MODERATION:
        raise _conflict("Rozwiązanie nie jest w moderacji.", "NOT_IN_MODERATION")
    score = _assert_score_in_scale(locked.entry.stage, score)

    if third_review is not None:
        # Rozstrzygnięcie trzeciego recenzenta *jest* jego oceną, więc idzie tą samą drogą co każda
        # inna: ``submit_review`` zapisze recenzję, zostawi wpis ``review.submitted`` w audycie
        # i sam utworzy ``FinalGrade(THIRD_REVIEW)``. Osobna ścieżka dawała tu inny ślad audytowy
        # dla tej samej czynności, w zależności od wywołanego endpointu.
        submit_review(third_review, score, _clean_comment(rationale), "", None, request=request)
        return FinalGrade.objects.get(submission=locked)

    return _create_final_grade(
        locked,
        score=score,
        method=allowed_method,
        decided_by=actor if getattr(actor, "is_authenticated", False) else None,
        rationale=rationale,
        request=request,
    )


# --- zapytania dla API ------------------------------------------------------------------------


def reviews_for_reviewer(member: CommitteeMember | None):
    """Przydziały recenzenta. Filtr jest w queryseckie, nie w widoku (PROJEKT.md 2.3)."""
    return (
        Review.objects.for_reviewer(member)
        .select_related(
            "submission",
            "submission__entry",
            "submission__entry__participant",
            "submission__entry__stage",
            "submission__problem",
        )
        .prefetch_related(
            # Jawny Prefetch z posortowanym querysetem: ``Submission.latest_file`` korzysta wtedy
            # z cache'u prefetchu zamiast robić własne ``order_by`` per wiersz (N+1 na liście).
            models.Prefetch("submission__files", queryset=SubmissionFile.objects.order_by("-id"))
        )
        .order_by("submission__entry__stage_id", "submission__problem__number", "id")
    )


def moderation_queue():
    """Rozwiązania w moderacji wraz z ocenami rundy 1 – widok wyłącznie dla koordynatora."""
    return (
        Submission.objects.filter(status=SubmissionStatus.MODERATION)
        .select_related("entry", "entry__participant", "entry__stage", "problem")
        .prefetch_related("reviews__reviewer__user")
        .order_by("entry__stage_id", "problem__number", "id")
    )
