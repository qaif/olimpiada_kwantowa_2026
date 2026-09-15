"""Logika domenowa oceniania: przydział recenzentów, wystawianie ocen, konsensus, moderacja.

Widoki tylko orkiestrują. Zasady wspólne dla całego modułu:

- każda zmiana stanu ``Submission`` idzie pod blokadą ``SELECT ... FOR UPDATE`` na tym zgłoszeniu.
  To jedyny punkt szeregowania: dwa równoczesne ``submit_review`` nie mogą utworzyć dwóch
  ``FinalGrade`` (relacja jeden-do-jednego w bazie jest dopiero drugą linią obrony),
- konflikt interesów na etapie wojewódzkim liczy się wyłącznie z ``CommitteeMember.district``:
  recenzent z województwa uczestnika nie dostaje jego pracy. Województwo członka komitetu jest
  opcjonalne (decyzja organizatora), więc jego brak nikogo nie wyklucza, a ``district_verified``
  jest tylko informacją dla koordynatora i niczego nie bramkuje,
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

from .models import (
    ROUND_BLIND,
    ROUND_TIEBREAK,
    FinalGrade,
    GradeMethod,
    ProblemReviewerRule,
    Review,
    ReviewStatus,
)

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
    """Czy recenzent jest w konflikcie województwa dla tego uczestnika na tym etapie.

    Poza etapem wojewódzkim konfliktu nie ma. Na etapie wojewódzkim konfliktowy jest wyłącznie
    recenzent z **tym samym** województwem co uczestnik. Województwo członka komitetu jest
    opcjonalne (decyzja organizatora): kto go nie ma, ocenia prace ze wszystkich województw.
    ``district_verified`` nie bierze tu udziału – województwo niepotwierdzone, ale równe
    województwu uczestnika, jest konfliktem, bo to bezpieczniejszy kierunek niż wpuszczenie
    recenzenta na pracę z jego własnego województwa.
    """
    if stage.kind != StageKind.DISTRICT:
        return False
    member_district = _norm_district(member.district)
    if not member_district:
        return False
    return member_district == _norm_district(participant_district)


def _assert_reviewer_eligible(reviewer: CommitteeMember) -> None:
    """Czy tę osobę w ogóle wolno przydzielić do oceniania.

    Ta sama bramka, co filtr ``reviewer_pool`` – wypisana osobno, bo przydział ręczny nie przechodzi
    przez pulę: koordynator wskazuje konkretną osobę z listy i musi dostać powód odmowy, a nie
    „tej osoby nie ma w puli”.
    """
    if reviewer.status != CommitteeStatus.ACTIVE or not reviewer.user.is_active:
        raise _bad_request("Recenzent nie jest aktywny.", "REVIEWER_NOT_ELIGIBLE")
    if not reviewer.user.groups.filter(name=GROUP_REVIEWER).exists():
        raise _bad_request("Wskazana osoba nie jest recenzentem.", "REVIEWER_NOT_ELIGIBLE")


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
        # ``entry__participant__user`` i ``problem`` są tu dla ekranu przydziałów ręcznych
        # (nazwisko w wyszukiwarce, numer zadania w tabeli) – bez nich lista robi zapytanie na wiersz.
        .select_related("entry", "entry__participant", "entry__participant__user", "problem")
        .order_by("entry_id", "problem_id", "-version")
    )
    best: dict[tuple[int, int], Submission] = {}
    for submission in rows:
        best.setdefault((submission.entry_id, submission.problem_id), submission)
    return sorted(best.values(), key=lambda item: item.pk)


def is_assignable(submission: Submission) -> bool:
    """Ten sam predykat, co ``_assignable_submissions``, ale dla jednego zgłoszenia.

    Przydział ręczny musi odpowiadać na pytanie „czy tę konkretną pracę wolno jeszcze komuś dać”,
    a nie budować listy całego etapu. Warunek jest dwuczłonowy, dokładnie jak tam: stan LOCKED albo
    IN_REVIEW **i** brak nowszej wersji tej samej pracy w tych stanach – starsza wersja nie może
    wejść do oceniania obok nowszej.
    """
    if submission.status not in (SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW):
        return False
    return not (
        Submission.objects.filter(
            entry_id=submission.entry_id,
            problem_id=submission.problem_id,
            status__in=(SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
            version__gt=submission.version,
        ).exists()
    )


def problem_rule_reviewers(problem_ids) -> dict[int, list[CommitteeMember]]:
    """Recenzenci z reguł „z góry”, pogrupowani po zadaniu, w kolejności utworzenia reguł.

    Pula reguł jest zawężona do osób, które nadal wolno przydzielać: recenzent zawieszony albo
    z wyłączonym kontem nie może dostać pracy dlatego, że kiedyś powstała dla niego reguła.
    """
    rules = (
        ProblemReviewerRule.objects.filter(problem_id__in=list(problem_ids))
        .select_related("reviewer", "reviewer__user")
        .order_by("created_at", "id")
    )
    eligible_ids = {member.pk for member in reviewer_pool()}
    grouped: dict[int, list[CommitteeMember]] = {}
    for rule in rules:
        if rule.reviewer_id in eligible_ids:
            grouped.setdefault(rule.problem_id, []).append(rule.reviewer)
    return grouped


def _skipped(submission: Submission, reason: str, reviewer_id: int | None = None) -> dict:
    """Wiersz listy ``skipped``. Uczestnik wyłącznie pseudonimem – audyt czytają też osoby bez RODO."""
    return {
        "submission_id": submission.pk,
        "public_code": submission.entry.participant.public_code,
        "reason": reason,
        "reviewer_id": reviewer_id,
    }


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

    Reguły „z góry” (``ProblemReviewerRule``) mają pierwszeństwo przed równoważeniem: recenzenci
    wskazani dla zadania wchodzą na miejsca ``per_submission`` w kolejności utworzenia reguł, a
    automat dobiera dopiero resztę. Gdy reguł jest więcej niż miejsc, przydzielani są **wszyscy** –
    świadoma decyzja organizatora wygrywa z liczbą, którą wpisał w formularzu przydziału. Reguła
    nie łamie jednak konfliktu interesów: recenzent skonfliktowany z tym uczestnikiem jest dla tej
    jednej pracy pomijany i trafia do ``skipped`` z powodem ``RULE_REVIEWER_CONFLICT``.
    """
    if per_submission < 1:
        raise _bad_request("Liczba recenzentów musi być dodatnia.", "INVALID_PER_SUBMISSION")

    _lock_stage_for_assignment(stage)
    submissions = _assignable_submissions(stage)
    pool = reviewer_pool()
    rules = problem_rule_reviewers({submission.problem_id for submission in submissions})
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
        participant_district = submission.entry.participant.district

        # Krok 1: recenzenci z reguł zadania. Konflikt interesów raportujemy per praca i per osoba –
        # koordynator musi wiedzieć, która reguła nie zadziałała i dla kogo, żeby załatwić to ręcznie.
        chosen: list[CommitteeMember] = []
        for member in rules.get(submission.problem_id, ()):
            if member.pk in already:
                continue
            if has_district_conflict(member, stage, participant_district):
                skipped.append(_skipped(submission, "RULE_REVIEWER_CONFLICT", member.pk))
                continue
            chosen.append(member)
            already.add(member.pk)

        # Krok 2: dopełnienie z puli. Reguły zajmują miejsca z ``per_submission``, więc gdy zajęły
        # wszystkie (albo więcej), automat nie dokłada już nikogo.
        missing = per_submission - len(already)
        if missing > 0:
            eligible = [
                member
                for member in pool
                if member.pk not in already and not has_district_conflict(member, stage, participant_district)
            ]
            if len(eligible) < missing:
                skipped.append(_skipped(submission, "NOT_ENOUGH_REVIEWERS"))
            else:
                chosen.extend(sorted(eligible, key=lambda member: (loads[member.pk], member.pk))[:missing])

        if not chosen:
            continue
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

    if created_total == 0 and any(item["reason"] == "NOT_ENOUGH_REVIEWERS" for item in skipped):
        # Nic nie dało się przydzielić – nie ma czego commitować, więc odpowiadamy błędem domenowym.
        # Warunek patrzy na *powód*, a nie na samą niepustość listy: przy pominiętej regule (konflikt
        # recenzenta wskazanego z góry) reszta przydziału mogła się udać albo nie być potrzebna,
        # a komunikat „za mało recenzentów” byłby wtedy nieprawdą.
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


# --- przydział ręczny -------------------------------------------------------------------------


def _create_blind_assignment(submission: Submission, reviewer: CommitteeMember) -> Review:
    """Tworzy (albo wskrzesza) przydział rundy 1 dla wskazanej pary praca–recenzent.

    Wskrzeszenie zamiast ``create``, bo unikalność w bazie obejmuje także recenzje ``CANCELLED``:
    po cofnięciu przydziału (``unassign_reviewer``) drugi ``create`` dla tej samej pary skończyłby
    się ``IntegrityError`` (500) zamiast zwykłym ponownym przydziałem. Anulowany rekord wraca więc
    do ``ASSIGNED`` ze świeżą datą – historia zmian zostaje w audycie, nie w duplikacie wiersza.
    """
    existing = Review.objects.filter(
        submission=submission, reviewer=reviewer, round=ROUND_BLIND, status=ReviewStatus.CANCELLED
    ).first()
    if existing is not None:
        existing.status = ReviewStatus.ASSIGNED
        existing.assigned_at = timezone.now()
        existing.save(update_fields=["status", "assigned_at"])
        return existing
    return Review.objects.create(
        submission=submission,
        reviewer=reviewer,
        round=ROUND_BLIND,
        status=ReviewStatus.ASSIGNED,
        assigned_at=timezone.now(),
    )


def _start_review_if_locked(submission: Submission) -> None:
    """LOCKED → IN_REVIEW po pierwszym przydziale – dokładnie jak w ścieżce automatycznej."""
    if submission.status == SubmissionStatus.LOCKED:
        submission.status = SubmissionStatus.IN_REVIEW
        submission.save(update_fields=["status"])


@transaction.atomic
def add_problem_reviewer_rule(problem, reviewer: CommitteeMember, *, actor=None, request=None) -> dict:
    """Tworzy regułę „to zadanie recenzuje ta osoba” i stosuje ją od razu do prac już zablokowanych.

    Reguła obowiązująca dopiero od następnego przebiegu ``assign_reviewers`` byłaby pułapką:
    koordynator, który dodaje ją po zamknięciu etapu, zobaczyłby „zapisano” i żadnej zmiany na
    liście przydziałów. Dlatego serwis od razu dopisuje recenzje do wszystkich prac tego zadania,
    które nadaje się jeszcze przydzielać, i zwraca liczniki – ile dopisano, ile pominięto z powodu
    konfliktu województwa i ile miało tego recenzenta już wcześniej.
    """
    _assert_reviewer_eligible(reviewer)
    if ProblemReviewerRule.objects.filter(problem=problem, reviewer=reviewer).exists():
        raise _conflict("Ta reguła już istnieje.", "RULE_ALREADY_EXISTS")

    stage = problem.stage
    rule = ProblemReviewerRule.objects.create(
        problem=problem,
        reviewer=reviewer,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )

    assigned = 0
    conflicts = 0
    already = 0
    for submission in _assignable_submissions(stage):
        if submission.problem_id != problem.pk:
            continue
        if (
            Review.objects.filter(submission=submission, reviewer=reviewer, round=ROUND_BLIND)
            .exclude(status=ReviewStatus.CANCELLED)
            .exists()
        ):
            already += 1
            continue
        if has_district_conflict(reviewer, stage, submission.entry.participant.district):
            conflicts += 1
            continue
        _create_blind_assignment(submission, reviewer)
        _start_review_if_locked(submission)
        assigned += 1

    audit(
        actor,
        "review.rule_added",
        rule,
        {
            "problem_id": problem.pk,
            "reviewer_id": reviewer.pk,
            "assigned": assigned,
            "conflicts": conflicts,
            "already": already,
        },
        request=request,
    )
    logger.info(
        "Zadanie %s: reguła dla recenzenta %s, dopisano %s recenzji (konflikty: %s)",
        problem.pk,
        reviewer.pk,
        assigned,
        conflicts,
    )
    return {"rule": rule, "assigned": assigned, "conflicts": conflicts, "already": already}


@transaction.atomic
def remove_problem_reviewer_rule(rule: ProblemReviewerRule, *, actor=None, request=None) -> None:
    """Kasuje regułę. Recenzje, które z niej powstały, **zostają** – to już jest czyjaś praca.

    Kasowanie przydziałów razem z regułą kusi („cofnijmy wszystko”), ale znaczyłoby usuwanie
    rozpoczętych, a bywa że wystawionych ocen jednym kliknięciem w ekranie o regułach. Cofnięcie
    pojedynczego przydziału jest osobną, świadomą czynnością (``unassign_reviewer``).
    """
    problem = rule.problem
    diff = {"rule_id": rule.pk, "problem_id": rule.problem_id, "reviewer_id": rule.reviewer_id}
    rule.delete()
    # Celem wpisu jest zadanie, a nie skasowana reguła: po ``delete()`` jej identyfikator nie
    # wskazuje już niczego, a historia i tak jest czytana „po zadaniu”.
    audit(actor, "review.rule_removed", problem, diff, request=request)


@transaction.atomic
def assign_reviewer_to_submission(
    submission: Submission, reviewer: CommitteeMember, *, actor=None, request=None
) -> Review:
    """Przydziela wskazanego recenzenta do jednej, wskazanej pracy (runda ślepa).

    Furtka dla sytuacji, których automat nie ogarnia: praca pominięta jako ``skipped``, recenzent,
    który wypadł z obiegu, dociążenie konkretnej osoby. Odmowy są jawne i mają kody maszynowe –
    koordynator ma wiedzieć, *dlaczego* nie wolno, a nie dostać cichy brak efektu.

    Konflikt interesów obowiązuje tak samo, jak w przydziale automatycznym: ręczne wskazanie jest
    decyzją organizacyjną, a nie zwolnieniem z procedury (PROJEKT.md 2.2).
    """
    locked = _locked_submission(submission.pk)
    _assert_reviewer_eligible(reviewer)
    if not is_assignable(locked):
        raise _conflict(
            f"Rozwiązanie w stanie {locked.status} nie przyjmuje przydziałów.", "SUBMISSION_NOT_ASSIGNABLE"
        )
    if (
        Review.objects.filter(submission=locked, reviewer=reviewer)
        .exclude(status=ReviewStatus.CANCELLED)
        .exists()
    ):
        raise _conflict("Ten recenzent ma już tę pracę przydzieloną.", "ALREADY_ASSIGNED")
    if has_district_conflict(reviewer, locked.entry.stage, locked.entry.participant.district):
        raise _conflict("Recenzent ma konflikt interesów (województwo).", "REVIEWER_CONFLICT_OF_INTEREST")

    review = _create_blind_assignment(locked, reviewer)
    _start_review_if_locked(locked)
    audit(
        actor,
        "review.assigned_manually",
        review,
        {"submission_id": locked.pk, "reviewer_id": reviewer.pk, "round": ROUND_BLIND},
        request=request,
    )
    review.submission = locked
    return review


@transaction.atomic
def unassign_reviewer(review: Review, *, actor=None, request=None) -> Review:
    """Cofa przydział, którego recenzent jeszcze nie tknął (ASSIGNED → CANCELLED).

    Tylko ``ASSIGNED``: szkic znaczy, że ktoś już zaczął czytać pracę, a recenzja wystawiona jest
    częścią rozstrzygnięcia. Rekord zostaje (ślad po przydziale jest częścią historii) – zmienia
    się wyłącznie status, dokładnie jak przy anulowaniu wiszącej rundy rozjemczej.
    """
    if review.status != ReviewStatus.ASSIGNED:
        raise _conflict(
            "Cofnąć można wyłącznie przydział, którego recenzent jeszcze nie rozpoczął.",
            "REVIEW_NOT_ASSIGNED",
        )
    review.status = ReviewStatus.CANCELLED
    review.save(update_fields=["status"])
    audit(
        actor,
        "review.unassigned",
        review,
        {"submission_id": review.submission_id, "reviewer_id": review.reviewer_id},
        request=request,
    )
    return review


@transaction.atomic
def assign_third_reviewer(submission: Submission, reviewer: CommitteeMember, *, actor=None, request=None):
    """Wyznacza trzeciego recenzenta (runda 2) dla rozwiązania w moderacji."""
    locked = _locked_submission(submission.pk)
    if locked.status != SubmissionStatus.MODERATION:
        raise _conflict("Rozwiązanie nie jest w moderacji.", "NOT_IN_MODERATION")
    _assert_reviewer_eligible(reviewer)
    if Review.objects.filter(submission=locked, reviewer=reviewer, round=ROUND_BLIND).exists():
        raise _conflict(
            "Trzecim recenzentem nie może być autor oceny z rundy 1.", "REVIEWER_CONFLICT_OF_INTEREST"
        )
    if has_district_conflict(reviewer, locked.entry.stage, locked.entry.participant.district):
        raise _conflict("Recenzent ma konflikt interesów (województwo).", "REVIEWER_CONFLICT_OF_INTEREST")
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


# --- korekta ocen przez koordynatora -----------------------------------------------------------

#: Stany zgłoszenia, w których koordynator może wpisać albo poprawić ocenę końcową. Poza pracami
#: w obiegu oceniania są tu także prace już ocenione – korekta po fakcie jest właśnie tym, po co
#: ten tryb powstał. Nie ma tu stanów sprzed zamknięcia etapu: przed deadline uczestnik może
#: jeszcze podmienić plik, więc wpisana ocena dotyczyłaby wersji, której nikt nie widział.
GRADABLE_STATUSES = (
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
    SubmissionStatus.FINAL,
)
#: Minimalna długość uzasadnienia korekty. Krótsze nie jest uzasadnieniem, tylko kliknięciem –
#: a ten wpis czyta potem komisja odwoławcza.
MIN_OVERRIDE_RATIONALE = 10


def _cancel_pending_reviews(submission: Submission, *, reason: str, actor=None, request=None) -> int:
    """Anuluje wszystkie niedokończone recenzje pracy (ASSIGNED i DRAFT), zostawiając wystawione.

    Ta sama reguła, co przy rozstrzygniętym rozjeździe (``_cancel_pending_tiebreak``), tylko dla
    obu rund: praca z wpisaną oceną końcową nie ma już czego dostarczyć recenzentowi, a wiszący
    przydział zostawiałby mu na liście zadanie bez przedmiotu. Szkic anulujemy razem z przydziałem –
    jest tak samo niedokończony i tak samo stoi w kolejce.
    """
    pending = list(
        Review.objects.filter(submission=submission).exclude(
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
            {"submission_id": submission.pk, "round": review.round, "reason": reason},
            request=request,
        )
    return len(pending)


def _results_published(stage: Stage) -> bool:
    """Czy etap ma już ogłoszoną tabelę wyników.

    Import lokalny: ``apps.results.services`` woła ocenianie, więc import na poziomie modułu
    zamykałby cykl.
    """
    from apps.results.models import ResultsPublication

    return ResultsPublication.objects.filter(stage=stage).exists()


@transaction.atomic
def set_review_score(review: Review, score, *, actor=None, request=None, rationale: str = "") -> Review:
    """Koordynator wpisuje albo poprawia punkty pojedynczej recenzji.

    Różnica wobec ``submit_review``: nie ma tu bramki stanu recenzji ani zgłoszenia. Koordynator
    poprawia także ocenę już wystawioną i wpisuje ocenę za recenzenta, który jej nie oddał – to
    jest cel tego trybu. Jedyne, co obowiązuje bez wyjątku, to skala punktacji etapu: ocena spoza
    skali rozjeżdżałaby tabelę wyników i próg kwalifikacji.

    Ślad po tym, że punkty pochodzą od koordynatora, zostaje w dwóch miejscach: w audycie
    (``review.score_set_by_coordinator`` z wartością przed i po) oraz – dla recenzji, której nikt
    nie wystawił – w komentarzu wewnętrznym. Recenzent musi wiedzieć, skąd w jego recenzji wzięła
    się ocena, której nie wpisał.

    Po zmianie serwis ponawia rozstrzygnięcie rundy 1 (``_settle_round_one``), żeby konsensus
    i kolejka moderacji zgadzały się z nowym stanem ocen. Praca, która ma już ocenę uzgodnioną,
    jest z tego wyłączona: zmiana oceny końcowej po fakcie to osobna, świadoma czynność
    (``override_final_grade``) – wymaga uzasadnienia i ma własny tryb w tabeli wyników.
    """
    submission = _locked_submission(review.submission_id)
    review = Review.objects.select_related("reviewer", "reviewer__user").get(pk=review.pk)
    score = _assert_score_in_scale(submission.entry.stage, score)

    previous = review.score
    fields = ["score"]
    review.score = score
    if review.status != ReviewStatus.SUBMITTED:
        note = "[koordynator] Punkty wpisane przez koordynatora."
        if rationale:
            note = f"{note} {rationale}"
        review.comment_internal = _clean_comment(
            f"{review.comment_internal}\n{note}".strip() if review.comment_internal else note
        )
        review.status = ReviewStatus.SUBMITTED
        review.submitted_at = timezone.now()
        fields += ["comment_internal", "status", "submitted_at"]
    review.save(update_fields=fields)

    audit(
        actor,
        "review.score_set_by_coordinator",
        review,
        {
            "submission_id": submission.pk,
            "round": review.round,
            "from": previous,
            "to": score,
            "rationale": _clean_comment(rationale),
        },
        request=request,
    )
    if not FinalGrade.objects.filter(submission=submission).exists():
        _settle_round_one(submission, request=request)
    review.submission = submission
    return review


@transaction.atomic
def override_final_grade(submission: Submission, score, *, rationale: str, actor=None, request=None) -> dict:
    """Koordynator wpisuje albo zmienia ocenę końcową pracy – także pracy bez ani jednej recenzji.

    Tryb ostatniej instancji: praca, której nikt nie zrecenzował (brak chętnych, awaria, decyzja
    komisji o ocenie na posiedzeniu), oraz korekta oceny już ustalonej – konsensusem, moderacją
    czy trzecim recenzentem. Ocena dostaje własny tryb ``COORDINATOR_OVERRIDE``, żeby w aktach
    było widać, że wzięła się z decyzji organizatora, a nie z procedury oceniania.

    Uzasadnienie jest **obowiązkowe**: to jedyna rzecz, po której da się później odtworzyć, czemu
    ocena wygląda tak, a nie inaczej – a czyta ją komisja odwoławcza.

    Ogłoszona tabela wyników **nie jest** ruszana. Snapshot jest dokumentem z chwili publikacji;
    zmiana oceny po ogłoszeniu wchodzi do wyników dopiero przez ponowne przeliczenie i publikację.
    Zamiast cicho rozjeżdżać jedno z drugim, serwis zwraca ``results_stale=True`` i to panel mówi
    koordynatorowi, co jeszcze zostało do zrobienia.
    """
    locked = _locked_submission(submission.pk)
    stage = locked.entry.stage
    rationale = (rationale or "").strip()
    if len(rationale) < MIN_OVERRIDE_RATIONALE:
        raise _bad_request(
            f"Korekta oceny wymaga uzasadnienia (co najmniej {MIN_OVERRIDE_RATIONALE} znaków).",
            "RATIONALE_REQUIRED",
        )
    if not is_assignable(locked) and locked.status not in GRADABLE_STATUSES:
        raise _conflict(
            f"Rozwiązanie w stanie {locked.status} nie przyjmuje oceny końcowej.",
            "SUBMISSION_NOT_GRADABLE",
        )
    score = _assert_score_in_scale(stage, score)

    grade = FinalGrade.objects.filter(submission=locked).first()
    previous = grade.score if grade is not None else None
    previous_method = grade.method if grade is not None else None
    if grade is None:
        grade = FinalGrade(submission=locked)
    grade.score = score
    grade.method = GradeMethod.COORDINATOR_OVERRIDE
    grade.decided_by = actor if getattr(actor, "is_authenticated", False) else None
    grade.decided_at = timezone.now()
    grade.rationale = _clean_comment(rationale)
    grade.save()

    cancelled = _cancel_pending_reviews(locked, reason="COORDINATOR_OVERRIDE", actor=actor, request=request)
    # Praca już finalna zostaje finalna: cofnięcie jej do oceny wstępnej otwierałoby z powrotem
    # okno reklamacji dla sprawy, którą etap ma dawno za sobą.
    if locked.status != SubmissionStatus.FINAL:
        locked.status = SubmissionStatus.GRADED_PROVISIONAL
        locked.save(update_fields=["status"])

    results_stale = _results_published(stage)
    audit(
        actor,
        "grade.overridden",
        grade,
        {
            "submission_id": locked.pk,
            "from": previous,
            "from_method": previous_method,
            "to": score,
            "cancelled_reviews": cancelled,
            "results_stale": results_stale,
        },
        request=request,
    )
    logger.info(
        "Korekta oceny koordynatora: zgłoszenie %s %s → %s (wyniki nieaktualne: %s)",
        locked.pk,
        previous,
        score,
        results_stale,
    )
    return {"grade": grade, "results_stale": results_stale, "cancelled_reviews": cancelled}


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


def stage_problem_rules(stage: Stage) -> list[dict]:
    """Zadania etapu razem z regułami „z góry” – materiał na tabelę w panelu koordynatora."""
    rules: dict[int, list[ProblemReviewerRule]] = {}
    for rule in (
        ProblemReviewerRule.objects.filter(problem__stage=stage)
        .select_related("reviewer", "reviewer__user")
        .order_by("created_at", "id")
    ):
        rules.setdefault(rule.problem_id, []).append(rule)
    return [
        {"problem": problem, "rules": rules.get(problem.pk, [])}
        for problem in stage.problems.order_by("number", "id")
    ]


def stage_assignment_rows(stage: Stage, query: str = "") -> list[dict]:
    """Prace etapu w obiegu oceniania: recenzenci, oceny cząstkowe i ocena końcowa w jednym wierszu.

    Lista jest szersza niż sam przydział (``_assignable_submissions``) i celowo: ekran odpowiada
    także za korektę ocen, a poprawia się najczęściej pracę **już ocenioną**. Wiersz niosący
    ``assignable=False`` pokazuje więc oceny, ale nie formularz przydziału – recenzenta do pracy
    zamkniętej i tak nie da się dopisać.

    Filtr ``query`` działa na kodzie publicznym **i** na nazwisku: koordynator jest jedyną rolą,
    która zna jedno i drugie, a szuka raz tak, raz tak (kod z listy pominiętych prac, nazwisko
    z telefonu od uczestnika). Sortowanie po kodzie uczestnika, a potem po numerze zadania –
    prace jednej osoby mają stać obok siebie.

    Recenzje ``CANCELLED`` są w wierszu widoczne: cofnięty przydział jest informacją („próbowaliśmy,
    cofnięto”), a nie stanem do ukrycia.
    """
    rows_qs = (
        Submission.objects.filter(
            entry__stage=stage,
            status__in=(
                SubmissionStatus.LOCKED,
                SubmissionStatus.IN_REVIEW,
                *GRADABLE_STATUSES,
            ),
        )
        .select_related("entry", "entry__participant", "entry__participant__user", "problem")
        .order_by("entry_id", "problem_id", "-version")
    )
    # Po jednej, najnowszej wersji na (wpis, zadanie) – ta sama zasada, co przy przydziale: starsza
    # wersja nie może stanąć w tabeli obok nowszej i kusić do wpisania oceny nie tam, gdzie trzeba.
    best: dict[tuple[int, int], Submission] = {}
    for submission in rows_qs:
        best.setdefault((submission.entry_id, submission.problem_id), submission)
    submissions = list(best.values())

    text = (query or "").strip()
    if text:
        needle = text.casefold()
        submissions = [
            submission
            for submission in submissions
            if needle in submission.entry.participant.public_code.casefold()
            or needle in (submission.entry.participant.user.last_name or "").casefold()
        ]
    submission_ids = [item.pk for item in submissions]
    reviews: dict[int, list[Review]] = {}
    for review in (
        Review.objects.filter(submission_id__in=submission_ids)
        .select_related("reviewer", "reviewer__user")
        .order_by("round", "id")
    ):
        reviews.setdefault(review.submission_id, []).append(review)
    grades = {
        grade.submission_id: grade for grade in FinalGrade.objects.filter(submission_id__in=submission_ids)
    }
    rows = [
        {
            "submission": submission,
            "reviews": reviews.get(submission.pk, []),
            "final_grade": grades.get(submission.pk),
            # Bez zapytania na wiersz: ``best`` trzyma już najnowszą wersję pracy, więc wystarczy
            # sam stan – nowszej wersji w LOCKED/IN_REVIEW z definicji nie ma.
            "assignable": submission.status in (SubmissionStatus.LOCKED, SubmissionStatus.IN_REVIEW),
        }
        for submission in submissions
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["submission"].entry.participant.public_code,
            row["submission"].problem.number,
            row["submission"].pk,
        ),
    )


def moderation_queue():
    """Rozwiązania w moderacji wraz z ocenami rundy 1 – widok wyłącznie dla koordynatora."""
    return (
        Submission.objects.filter(status=SubmissionStatus.MODERATION)
        .select_related("entry", "entry__participant", "entry__stage", "problem")
        .prefetch_related("reviews__reviewer__user")
        .order_by("entry__stage_id", "problem__number", "id")
    )


def dispute_context(review: Review) -> list[dict]:
    """Materiał rozjemczy dla trzeciego recenzenta: obie oceny rundy 1 **bez tożsamości autorów**.

    Rozjemca musi wiedzieć, na czym polega rozjazd (punkty i argumentacja wewnętrzna), ale nie
    może wiedzieć, kto co napisał – inaczej runda 2 przestaje być niezależna, a staje się
    arbitrażem między nazwiskami (PROJEKT.md 2.4, przegląd T-05).

    Dostępne wyłącznie dla recenzji rundy 2; dla rundy 1 leci 404, bo materiał rozjemczy dla
    zwykłego recenzenta nie istnieje (a 403 potwierdzałoby, że coś takiego jest).

    Kolejność jest po ``score``, nie po ``id`` przydziału: numer recenzji rośnie z kolejnością
    przydzielania, więc sortowanie po nim korelowałoby wiersze z pulą recenzentów.
    """
    if review.round != ROUND_TIEBREAK:
        raise DomainError(
            "Materiał rozjemczy jest dostępny wyłącznie dla recenzji rundy 2.",
            "NOT_A_TIEBREAK_REVIEW",
            http.HTTP_404_NOT_FOUND,
        )
    rows = Review.objects.filter(
        submission_id=review.submission_id,
        round=ROUND_BLIND,
        status=ReviewStatus.SUBMITTED,
    ).order_by("score", "id")
    return [{"score": row.score, "comment_internal": row.comment_internal} for row in rows]
