"""Logika domenowa reklamacji: złożenie, rozstrzygnięcie, finalizacja po zamknięciu okna.

Widoki tylko orkiestrują. Zasady wspólne dla modułu:

- każda zmiana stanu ``Submission`` idzie pod blokadą ``SELECT ... FOR UPDATE`` na tym zgłoszeniu
  (``_locked_submission``). To jedyny punkt szeregowania: dwie równoczesne decyzje komisji nie mogą
  utworzyć dwóch ``AppealDecision`` – relacja jeden-do-jednego w bazie jest dopiero drugą linią
  obrony, a użytkownik dostałby wtedy 500 zamiast czytelnego 409,
- konflikt interesów: autor ``Review`` rundy 1 lub 2 tego rozwiązania nie rozstrzyga reklamacji
  na nie (PROJEKT.md 2.4). Ta sama reguła filtruje kolejkę komisji, więc konfliktowy członek
  nie dowiaduje się nawet, że taka reklamacja istnieje,
- audyt nigdy nie zawiera danych osobowych – w ``diff`` idą wyłącznie identyfikatory i punkty,
- czas zawsze przez ``timezone.now()``.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import CommitteeMember, CommitteeStatus
from apps.competitions.models import Stage
from apps.core.api import DomainError
from apps.core.models import audit
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod, Review
from apps.grading.services import allowed_scores
from apps.submissions.models import Submission, SubmissionFile, SubmissionStatus

from .models import (
    CONFLICTING_ROUNDS,
    DECIDABLE_STATUSES,
    MAX_TEXT_LENGTH,
    MIN_ARGUMENT_LENGTH,
    SCORE_CHANGING_STATUSES,
    Appeal,
    AppealDecision,
    AppealStatus,
)

logger = logging.getLogger(__name__)

#: Ile zgłoszeń bierze jedna transakcja finalizacji. Etap z tysiącami prac nie może trzymać
#: blokad na wszystkich wierszach naraz przez cały przebieg zadania.
FINALIZE_BATCH_SIZE = 500


# --- błędy domenowe ---------------------------------------------------------------------------


def _not_found(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_404_NOT_FOUND)


def _forbidden(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_403_FORBIDDEN)


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


# --- pomocnicze -------------------------------------------------------------------------------


def _clean_text(value: str | None, *, label: str, code: str) -> str:
    """Normalizuje tekst od użytkownika i pilnuje twardego limitu długości.

    Za długi tekst jest **odrzucany**, a nie obcinany: obcięcie kasowałoby część odwołania albo
    uzasadnienia decyzji bez śladu, a oba te teksty są dokumentem procedury. Serializery mają ten
    sam limit (``max_length``), więc przez API leci czytelne 400 na polu; tutaj jest reguła domeny
    dla wywołań spoza HTTP (shell, zadania, import).
    """
    cleaned = (value or "").strip()
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise _bad_request(f"{label} nie może przekraczać {MAX_TEXT_LENGTH} znaków.", code)
    return cleaned


def _locked_submission(submission_id: int) -> Submission:
    """Zgłoszenie pod blokadą wiersza – jedyny punkt szeregowania zapisów reklamacji."""
    return (
        Submission.objects.select_for_update(of=("self",))
        .select_related("entry", "entry__participant", "entry__stage", "entry__stage__scoring_scale")
        .get(pk=submission_id)
    )


def appeals_committee_profile(user) -> CommitteeMember | None:
    """Profil członka komisji odwoławczej: aktywny ``CommitteeMember`` z ``is_appeals_committee``.

    Grupę ``appeals`` sprawdza klasa uprawnień (``IsAppealsCommittee``); serwis pilnuje samego
    profilu, żeby działał także wołany poza HTTP (shell, zadania).
    """
    if not user or not user.is_authenticated or not user.is_active:
        return None
    member = getattr(user, "committee_member", None)
    if member is None or member.status != CommitteeStatus.ACTIVE or not member.is_appeals_committee:
        return None
    return member


def has_conflict_of_interest(member: CommitteeMember, submission: Submission) -> bool:
    """Czy członek komisji recenzował to rozwiązanie (runda 1 lub 2)."""
    return Review.objects.filter(
        submission=submission, reviewer=member, round__in=CONFLICTING_ROUNDS
    ).exists()


# --- złożenie reklamacji ----------------------------------------------------------------------


@transaction.atomic
def file_appeal(user, submission: Submission, argument: str, *, request=None) -> Appeal:
    """Składa reklamację uczestnika na ocenę wstępną rozwiązania (T-06, kryteria 1–5).

    Kolejność sprawdzeń jest częścią kontraktu: cudze rozwiązanie to 404 (odpowiedź nie może
    potwierdzać, że dane zgłoszenie istnieje), a istniejąca reklamacja daje 409 **przed** kontrolą
    statusu – po pierwszej reklamacji zgłoszenie jest już APPEALED i komunikat o „niewłaściwym
    stanie” byłby mylący.
    """
    participant = getattr(user, "participant", None)
    locked = _locked_submission(submission.pk)
    if participant is None or locked.entry.participant_id != participant.pk:
        raise _not_found("Nie ma takiego rozwiązania.", "SUBMISSION_NOT_FOUND")

    cleaned = _clean_text(argument, label="Uzasadnienie reklamacji", code="ARGUMENT_TOO_LONG")
    if len(cleaned) < MIN_ARGUMENT_LENGTH:
        raise _bad_request(
            f"Uzasadnienie reklamacji musi mieć co najmniej {MIN_ARGUMENT_LENGTH} znaków.",
            "ARGUMENT_TOO_SHORT",
        )
    if Appeal.objects.filter(submission=locked).exists():
        raise _conflict("Reklamacja na to rozwiązanie została już złożona.", "APPEAL_ALREADY_FILED")
    if locked.status != SubmissionStatus.GRADED_PROVISIONAL:
        raise _conflict(
            f"Rozwiązanie w stanie {locked.status} nie podlega reklamacji.", "SUBMISSION_NOT_GRADED"
        )

    stage = locked.entry.stage
    now = timezone.now()
    if not stage.is_appeal_window_open(now):
        raise _forbidden("Okno reklamacji jest zamknięte.", "APPEAL_WINDOW_CLOSED")

    appeal = Appeal.objects.create(
        submission=locked,
        filed_by=participant,
        filed_at=now,
        argument=cleaned,
        status=AppealStatus.OPEN,
    )
    locked.status = SubmissionStatus.APPEALED
    locked.save(update_fields=["status"])
    audit(
        user,
        "appeal.filed",
        appeal,
        {"submission_id": locked.pk, "stage_id": stage.pk, "argument_length": len(cleaned)},
        request=request,
    )
    logger.info("Złożono reklamację %s na zgłoszenie %s", appeal.pk, locked.pk)
    appeal.submission = locked
    return appeal


# --- rozstrzygnięcie --------------------------------------------------------------------------


def _validate_decision(
    stage: Stage, decision_status: str, new_score, current_score: int | None
) -> int | None:
    """Sprawdza spójność rozstrzygnięcia z punktacją. Zwraca ``new_score`` po walidacji."""
    if decision_status not in DECIDABLE_STATUSES:
        raise _bad_request(
            f"Nieznane rozstrzygnięcie reklamacji: {decision_status}.", "INVALID_APPEAL_STATUS"
        )
    if decision_status not in SCORE_CHANGING_STATUSES:
        if new_score is not None:
            raise _bad_request("Odrzucenie reklamacji nie może zmieniać punktacji.", "UNEXPECTED_NEW_SCORE")
        return None
    if new_score is None:
        raise _bad_request("Uwzględnienie reklamacji wymaga podania nowej punktacji.", "NEW_SCORE_REQUIRED")
    values = allowed_scores(stage)
    if not isinstance(new_score, int) or isinstance(new_score, bool) or new_score not in values:
        raise _bad_request(f"Ocena {new_score} nie należy do skali {sorted(values)}.", "SCORE_NOT_IN_SCALE")
    if new_score == current_score:
        raise _bad_request(
            "Nowa punktacja jest taka sama jak dotychczasowa – to nie jest zmiana oceny.",
            "SCORE_UNCHANGED",
        )
    return new_score


@transaction.atomic
def decide_appeal(
    appeal: Appeal,
    actor_member: CommitteeMember,
    decision_status: str,
    new_score=None,
    justification: str = "",
    *,
    request=None,
) -> AppealDecision:
    """Rozstrzyga reklamację (T-06, kryteria 6–9).

    Niezależnie od wyniku rozwiązanie kończy w stanie ``FINAL`` – reklamacja jest ostatnim krokiem
    procedury. ``FinalGrade`` zmienia się tylko wtedy, gdy decyzja niesie nową punktację; wtedy
    dostaje ``method=APPEAL`` i ślad w audycie z diffem ``{old_score, new_score}``.
    """
    if actor_member is None or actor_member.status != CommitteeStatus.ACTIVE:
        raise _forbidden("Wymagany aktywny profil członka komisji.", "NOT_APPEALS_COMMITTEE")
    if not actor_member.is_appeals_committee:
        raise _forbidden("Konto nie należy do komisji odwoławczej.", "NOT_APPEALS_COMMITTEE")

    locked = _locked_submission(appeal.submission_id)
    appeal = Appeal.objects.select_related("filed_by").get(pk=appeal.pk)

    if has_conflict_of_interest(actor_member, locked):
        raise _forbidden(
            "Autor recenzji tego rozwiązania nie może rozstrzygać reklamacji na nie.",
            "CONFLICT_OF_INTEREST",
        )
    if AppealDecision.objects.filter(appeal=appeal).exists():
        raise _conflict("Reklamacja została już rozstrzygnięta.", "APPEAL_ALREADY_DECIDED")

    grade = FinalGrade.objects.filter(submission=locked).first()
    current_score = grade.score if grade is not None else None
    new_score = _validate_decision(locked.entry.stage, decision_status, new_score, current_score)
    cleaned_justification = _clean_text(
        justification, label="Uzasadnienie decyzji", code="JUSTIFICATION_TOO_LONG"
    )
    if not cleaned_justification:
        raise _bad_request("Decyzja komisji wymaga uzasadnienia.", "JUSTIFICATION_REQUIRED")
    if new_score is not None and grade is None:
        raise _conflict("Rozwiązanie nie ma oceny uzgodnionej do zmiany.", "GRADE_NOT_FOUND")

    now = timezone.now()
    decision = AppealDecision.objects.create(
        appeal=appeal,
        decided_by=actor_member.user,
        new_score=new_score,
        justification=cleaned_justification,
        decided_at=now,
    )
    decision.committee.add(actor_member)

    if new_score is not None:
        grade.score = new_score
        grade.method = GradeMethod.APPEAL
        grade.decided_by = actor_member.user
        grade.decided_at = now
        grade.rationale = cleaned_justification
        grade.save(update_fields=["score", "method", "decided_by", "decided_at", "rationale"])

    appeal.status = decision_status
    appeal.save(update_fields=["status"])
    # Reklamacja jest ostatnim krokiem procedury – także odrzucona domyka rozwiązanie.
    locked.status = SubmissionStatus.FINAL
    locked.save(update_fields=["status"])

    audit(
        actor_member.user,
        "appeal.decided",
        appeal,
        {
            "submission_id": locked.pk,
            "status": decision_status,
            "old_score": current_score,
            "new_score": new_score,
            "method": GradeMethod.APPEAL if new_score is not None else None,
        },
        request=request,
    )
    logger.info(
        "Reklamacja %s rozstrzygnięta (%s), punkty %s -> %s",
        appeal.pk,
        decision_status,
        current_score,
        new_score if new_score is not None else current_score,
    )
    return decision


# --- finalizacja po zamknięciu okna -----------------------------------------------------------


def finalize_unappealed(stage: Stage, *, now=None, actor=None, request=None) -> int:
    """Po zamknięciu okna reklamacji: GRADED_PROVISIONAL → FINAL (T-06, kryterium 10).

    Rozwiązania w stanie APPEALED zostają nietknięte – ich los rozstrzyga komisja. Idempotentny.

    Etap finału ma kilka tysięcy prac, więc przebieg idzie **partiami** po
    ``FINALIZE_BATCH_SIZE`` kluczy, każda we własnej transakcji i jednym ``UPDATE``. Jedna wielka
    transakcja trzymałaby blokady na wszystkich wierszach etapu przez cały przebieg zadania beata:
    każde równoczesne złożenie reklamacji czekałoby do końca finalizacji, a awaria w połowie
    cofałaby wszystko. Partia jest zamknięta sama w sobie – to, co się zacommitowało, zostaje.

    ``skip_locked=True``: wiersz zajęty właśnie przez ``file_appeal`` jest pomijany, a nie
    oczekiwany. To celowe – reklamacja złożona w ostatniej sekundzie okna wygrywa wyścig, jej
    zgłoszenie przestaje być GRADED_PROVISIONAL i nie ma go już czego finalizować. Filtr statusu
    powtórzony pod blokadą pilnuje, żeby nie nadpisać stanu ustawionego między odczytem a blokadą.

    W audycie idzie licznik i numer partii – nigdy lista identyfikatorów: wpis audytowy z tysiącami
    id nie jest śladem, tylko kopią tabeli.
    """
    now = now or timezone.now()
    if now < stage.appeal_window_closes_at:
        return 0
    pending = list(
        Submission.objects.filter(entry__stage=stage, status=SubmissionStatus.GRADED_PROVISIONAL)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    finalized = 0
    batches = 0
    for start in range(0, len(pending), FINALIZE_BATCH_SIZE):
        chunk = pending[start : start + FINALIZE_BATCH_SIZE]
        with transaction.atomic():
            locked = list(
                Submission.objects.select_for_update(of=("self",), skip_locked=True)
                .filter(pk__in=chunk, status=SubmissionStatus.GRADED_PROVISIONAL)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            if not locked:
                continue
            count = Submission.objects.filter(
                pk__in=locked, status=SubmissionStatus.GRADED_PROVISIONAL
            ).update(status=SubmissionStatus.FINAL)
            if not count:
                continue
            finalized += count
            batches += 1
            audit(
                actor,
                "submission.finalized",
                stage,
                {"submissions": count, "batch": batches, "batch_size": FINALIZE_BATCH_SIZE},
                request=request,
            )
    if finalized:
        logger.info(
            "Etap %s: sfinalizowano %s rozwiązań bez reklamacji w %s partiach",
            stage.pk,
            finalized,
            batches,
        )
    return finalized


def stages_with_closed_appeal_window(now=None):
    """Etapy, którym minęło okno reklamacji i które **mają jeszcze co finalizować**.

    Zawężenie do etapów z choć jednym GRADED_PROVISIONAL jest istotne dla beata: bez niego zadanie
    co 5 minut przemiatałoby wszystkie zamknięte etapy w historii olimpiady, żeby za każdym razem
    stwierdzić, że nie ma nic do zrobienia. ``distinct()``, bo złączenie idzie przez wpisy i ich
    rozwiązania (etap ma ich wiele).
    """
    now = now or timezone.now()
    return (
        Stage.objects.filter(
            appeal_window_closes_at__lte=now,
            entries__submissions__status=SubmissionStatus.GRADED_PROVISIONAL,
        )
        .distinct()
        .order_by("pk")
    )


# --- zapytania dla API ------------------------------------------------------------------------


def appeals_queue(member: CommitteeMember | None):
    """Kolejka komisji: reklamacje czekające na decyzję, bez tych z konfliktem interesów."""
    return (
        Appeal.objects.pending()
        .without_conflict_for(member)
        .select_related(
            "submission",
            "submission__entry",
            "submission__entry__stage",
            "submission__entry__stage__scoring_scale",
            "submission__problem",
            "submission__final_grade",
            "filed_by",
        )
        .prefetch_related(
            # Kolejka pokazuje wyłącznie oceny rundy 1 (T-06: „obie oceny rundy 1”), więc runda
            # rozjemcza nie musi w ogóle opuszczać bazy. Serializer i tak filtruje po rundzie –
            # to jest ta sama reguła zapisana o warstwę niżej, żeby nie wozić zbędnych wierszy.
            Prefetch(
                "submission__reviews",
                queryset=Review.objects.filter(round=ROUND_BLIND).order_by("id"),
            ),
            # Jawny Prefetch z posortowanym querysetem: ``Submission.latest_file`` korzysta wtedy
            # z cache'u prefetchu zamiast robić własne ``order_by`` per wiersz (N+1 na kolejce).
            Prefetch("submission__files", queryset=SubmissionFile.objects.order_by("-id")),
        )
        .order_by("filed_at", "id")
    )


def appeals_for_participant(user):
    """Reklamacje uczestnika. Filtr jest w queryseckie, nie w widoku (PROJEKT.md 2.3)."""
    participant = getattr(user, "participant", None)
    if participant is None:
        return Appeal.objects.none()
    return (
        Appeal.objects.filter(filed_by=participant)
        .select_related("submission", "submission__problem", "submission__entry", "decision")
        .order_by("-filed_at", "-id")
    )
