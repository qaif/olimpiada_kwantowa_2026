"""Zgłoszenia problemów z pracami: recenzent sygnalizuje, koordynator rozstrzyga.

Po co (prośba organizatora): recenzent, któremu trafi się skan nie do odczytania albo rozwiązanie
zupełnie innego zadania, nie miał dotąd dokąd z tym pójść poza pocztą do koordynatora. Zgłoszenie
jest drogą **wewnątrz systemu** – koordynator widzi je na własnym ekranie, z pracą i etapem w ręku,
a ślad decyzji zostaje przy recenzji, a nie w cudzej skrzynce.

Dwie zasady, na których stoi ten moduł:

- **zgłoszenie nie blokuje oceniania**. Recenzent może uważać pracę za nieczytelną i mimo to
  wystawić ocenę, jaką da się obronić. Zablokowanie formularza zamieniłoby sygnał w ultimatum
  i zmusiłoby część komitetu do omijania go pocztą – czyli do tego, po co ten model powstał,
- **rozstrzygnięcie jest wymagane**. „Rozwiąż” bez ani jednego zdania zostawiałoby w aktach
  zamknięte zgłoszenie, z którego nie wynika, co się z pracą stało. Zdanie bywa krótkie
  („poprosiliśmy o nowy skan”) i o to właśnie chodzi.

Jedno ograniczenie ilościowe: jeden recenzent nie może mieć dwóch **otwartych** zgłoszeń do tej
samej recenzji. Drugie zgłoszenie tej samej sprawy nie niesie informacji, a mnoży wiersze w kolejce
koordynatora. Po rozwiązaniu wolno zgłosić ponownie – to już inna sprawa.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from .models import MAX_NOTE_LENGTH, Review, WorkIssue, WorkIssueKind, WorkIssueStatus


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def open_issue(review: Review, kind: str, text: str, *, actor=None, request=None) -> WorkIssue:
    """Recenzent zgłasza problem z pracą. Zwraca utworzone zgłoszenie.

    Rodzaj musi pochodzić z zamkniętej listy (``WorkIssueKind``) – dowolny napis z formularza
    wylądowałby w kolumnie filtrowanej na ekranie koordynatora i rozsypałby jej grupowanie.
    Opis jest obowiązkowy także przy „inne”: bez zdania od recenzenta zgłoszenie mówi wyłącznie,
    że komuś coś się nie spodobało.
    """
    if kind not in WorkIssueKind.values:
        raise _bad_request("Wybierz rodzaj problemu z listy.", "ISSUE_KIND_INVALID")
    clean = (text or "").strip()
    if not clean:
        raise _bad_request("Opisz, co jest nie tak z pracą.", "ISSUE_TEXT_REQUIRED")
    if WorkIssue.objects.filter(review=review, status=WorkIssueStatus.OPEN).exists():
        raise _conflict(
            "Masz już otwarte zgłoszenie do tej pracy – koordynator jeszcze go nie rozstrzygnął.",
            "ISSUE_ALREADY_OPEN",
        )
    issue = WorkIssue.objects.create(
        review=review,
        submission=review.submission,
        kind=kind,
        text=clean[:MAX_NOTE_LENGTH],
    )
    audit(
        actor,
        "issue.opened",
        issue,
        {
            "submission_id": review.submission_id,
            "review_id": review.pk,
            "kind": kind,
            # Sama długość opisu, nie treść: dziennik zdarzeń nie jest drugą kopią zgłoszenia,
            # a opis bywa zdaniem o konkretnej pracy konkretnego uczestnika.
            "length": len(clean),
        },
        request=request,
    )
    return issue


@transaction.atomic
def resolve_issue(issue: WorkIssue, resolution: str, *, actor=None, request=None) -> WorkIssue:
    """Koordynator zamyka zgłoszenie z uzasadnieniem. Powtórzenie to odmowa, nie cicha zgoda.

    Odmowa przy zgłoszeniu już zamkniętym jest istotna przy dwóch koordynatorach pracujących na
    tej samej kolejce: drugi dowiaduje się, że sprawę rozstrzygnął ktoś inny, zamiast nadpisać
    cudze uzasadnienie własnym.
    """
    clean = (resolution or "").strip()
    if not clean:
        raise _bad_request("Napisz, jak sprawa została załatwiona.", "ISSUE_RESOLUTION_REQUIRED")
    locked = WorkIssue.objects.select_for_update().get(pk=issue.pk)
    if locked.status != WorkIssueStatus.OPEN:
        raise _conflict("To zgłoszenie jest już rozwiązane.", "ISSUE_ALREADY_RESOLVED")
    locked.status = WorkIssueStatus.RESOLVED
    locked.resolution = clean[:MAX_NOTE_LENGTH]
    locked.resolved_by = actor if getattr(actor, "is_authenticated", False) else None
    locked.resolved_at = timezone.now()
    locked.save(update_fields=["status", "resolution", "resolved_by", "resolved_at"])
    audit(
        actor,
        "issue.resolved",
        locked,
        {"submission_id": locked.submission_id, "kind": locked.kind, "length": len(clean)},
        request=request,
    )
    return locked


def open_issues_for_reviewer(member) -> dict[int, WorkIssue]:
    """Otwarte zgłoszenia recenzenta w postaci ``{review_id: zgłoszenie}``.

    Jedno zapytanie na całą listę przydziałów – marker „zgłoszony problem” stoi przy wierszach,
    a lista recenzenta bywa stustronicowa. ``dict`` zamiast listy, bo szablon pyta o pojedynczy
    wiersz, a nie przechodzi kolekcję.
    """
    if member is None:
        return {}
    rows = WorkIssue.objects.filter(review__reviewer=member, status=WorkIssueStatus.OPEN)
    return {issue.review_id: issue for issue in rows}


def issue_rows(stage=None):
    """Kolejka zgłoszeń dla koordynatora: otwarte najpierw, w każdej grupie od najnowszego.

    Filtr po etapie jest opcjonalny – ekran domyślnie pokazuje całą edycję, bo problem z pracą nie
    czeka na to, aż koordynator wybierze właściwy etap z listy. ``select_related`` obejmuje całą
    ścieżkę do uczestnika i zadania: każdy wiersz wypisuje kod pracy, numer zadania i recenzenta.

    Kolejność: ``status`` rosnąco dałaby „OPEN” przed „RESOLVED” alfabetycznie, ale to zbieg
    okoliczności nazw, a nie reguła – sortujemy więc po jawnie wyliczonej fladze.
    """
    rows = WorkIssue.objects.select_related(
        "review",
        "review__reviewer",
        "review__reviewer__user",
        "submission",
        "submission__problem",
        "submission__entry",
        "submission__entry__stage",
        "submission__entry__participant",
        "resolved_by",
    )
    if stage is not None:
        rows = rows.filter(submission__entry__stage=stage)
    return rows.annotate(
        is_resolved=Case(
            When(status=WorkIssueStatus.OPEN, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("is_resolved", "-created_at", "-id")


def open_issue_count(stage_ids=None) -> int:
    """Ile zgłoszeń czeka na koordynatora – licznik na pulpit.

    ``stage_ids=None`` liczy wszystkie; pulpit podaje etapy bieżącej edycji, bo to jego zakres
    i licznik obejmujący zeszłoroczne zgłoszenia prowadziłby na ekran, na którym ich nie widać.
    """
    rows = WorkIssue.objects.filter(status=WorkIssueStatus.OPEN)
    if stage_ids is not None:
        rows = rows.filter(submission__entry__stage_id__in=stage_ids)
    return rows.count()
