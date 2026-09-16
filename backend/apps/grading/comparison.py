"""Porównanie ocen po odsłonięciu i krótki wątek notatek recenzentów przy jednej pracy.

Runda 1 jest ślepa **do chwili**, w której komplet ocen jest wystawiony – i ani sekundy dłużej.
Dopóki druga ocena nie padła, pokazanie cudzych punktów zamieniłoby niezależną ocenę w
przepisywanie; potem ukrywanie ich nie chroni już niczego, a odbiera recenzentom jedyną okazję,
żeby zobaczyć, gdzie się rozeszli, i dogadać się bez posiedzenia komisji (PROJEKT.md 2.2 i 2.4).

Co tu **nie** jest odsłaniane: tożsamość. Druga ocena podpisana jest literą („Recenzent B”), tak
jak materiał rozjemczy rundy 2 (``services.dispute_context``) – rozmowa ma dotyczyć pracy, a nie
osób. Litery przydzielamy po ``(punkty, id)``, a nie po samym ``id`` przydziału: numer recenzji
rośnie z kolejnością przydzielania, więc sortowanie po nim korelowałoby litery z kolejnością osób
w puli recenzentów.

Widzą to koordynator i recenzenci tej pracy – nikt inny, a uczestnik nigdy.
"""

from __future__ import annotations

from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import SubmissionStatus

from .models import (
    MAX_NOTE_LENGTH,
    ROUND_BLIND,
    Review,
    ReviewNote,
    ReviewStatus,
)

#: Stany pracy, w których oceny rundy 1 są już odsłonięte niezależnie od stanu pojedynczych
#: recenzji: rozjazd poszedł do moderacji albo praca ma ocenę uzgodnioną. W obu wypadkach komplet
#: ocen rundy 1 jest faktem, na którym system zbudował decyzję.
REVEALED_STATUSES = (
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
    SubmissionStatus.FINAL,
)

#: Podpisy recenzentów w porównaniu i w wątku. Litery, nie nazwiska.
LABELS = "ABCDEFGH"

#: Podpis dla recenzji rundy 2 – rozjemca nie jest „kolejną literą”, tylko inną rolą w procedurze.
TIEBREAK_LABEL = "Recenzent rozjemczy"


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def round_one_reviews(submission) -> list[Review]:
    """Recenzje rundy 1, które się liczą – bez anulowanych (odebranych albo unieważnionych)."""
    return list(
        Review.objects.filter(submission=submission, round=ROUND_BLIND)
        .exclude(status=ReviewStatus.CANCELLED)
        .select_related("reviewer")
        .order_by("score", "id")
    )


def is_revealed(submission, reviews=None) -> bool:
    """Czy oceny rundy 1 tej pracy są już odsłonięte dla jej recenzentów.

    Dwa warunki, bo opisują tę samą rzecz z dwóch stron: komplet wystawionych ocen (zwykły przebieg)
    oraz stan pracy, w którym system **już zbudował** coś na tych ocenach – moderację albo ocenę
    uzgodnioną. Drugi warunek ratuje sytuacje po korekcie koordynatora: praca bywa oceniona, choć
    recenzja została po drodze anulowana.
    """
    if submission.status in REVEALED_STATUSES:
        return True
    reviews = round_one_reviews(submission) if reviews is None else reviews
    return len(reviews) > 1 and all(review.status == ReviewStatus.SUBMITTED for review in reviews)


def reviewer_labels(submission) -> dict[int, str]:
    """Mapa „członek komitetu → podpis” dla jednej pracy: Recenzent A, B, … i rozjemca.

    Jedna mapa dla porównania i dla wątku notatek: gdyby każdy ekran liczył litery po swojemu,
    „Recenzent B” znaczyłby przy ocenach kogoś innego niż pod notatką.
    """
    labels: dict[int, str] = {}
    for index, review in enumerate(round_one_reviews(submission)):
        labels[review.reviewer_id] = f"Recenzent {LABELS[index]}" if index < len(LABELS) else "Recenzent"
    for review in Review.objects.filter(submission=submission).exclude(round=ROUND_BLIND):
        labels.setdefault(review.reviewer_id, TIEBREAK_LABEL)
    return labels


def comparison_context(review) -> dict | None:
    """Porównanie ocen dla recenzenta oglądającego **swoją** recenzję albo ``None``, gdy za wcześnie.

    Wiersze opisują wyłącznie cudze oceny: własną recenzent ma w formularzu obok. Różnica jest
    liczona względem jego punktów i podana ze znakiem – „+2” znaczy „druga ocena jest o dwa punkty
    wyższa”, co czyta się szybciej niż dwie liczby do odjęcia w pamięci.

    Komentarz pokazujemy **dla uczestnika**, a nie wewnętrzny: to jest ta część oceny, którą druga
    strona i tak zobaczy po ogłoszeniu wyników, a wątek notatek jest miejscem na resztę rozmowy.
    """
    submission = review.submission
    reviews = round_one_reviews(submission)
    if not is_revealed(submission, reviews):
        return None
    others = [
        item
        for item in reviews
        if item.pk != review.pk and item.status == ReviewStatus.SUBMITTED and item.score is not None
    ]
    labels = reviewer_labels(submission)
    rows = [
        {
            "label": labels.get(item.reviewer_id, "Recenzent"),
            "score": item.score,
            "comment_for_participant": item.comment_for_participant,
            "difference": (item.score - review.score) if review.score is not None else None,
        }
        for item in others
    ]
    blocked = note_block_reason(submission, revealed=True)
    return {
        "rows": rows,
        "own_score": review.score,
        "agreed": bool(rows) and all(row["difference"] == 0 for row in rows),
        "notes": note_rows(submission, viewer=review.reviewer),
        "can_post_note": blocked is None,
        "note_block_message": NOTE_BLOCK_MESSAGES[blocked] if blocked else "",
        "max_note_length": MAX_NOTE_LENGTH,
    }


def note_block_reason(submission, *, revealed: bool | None = None) -> str | None:
    """Dlaczego nie wolno dopisać notatki (albo ``None``, gdy wolno).

    Wątek zamyka się razem ze sprawą: praca z oceną ostateczną i etap z ogłoszonymi wynikami są
    faktem, do którego nic się już nie dopisuje – akta mają odpowiadać temu, co widziała komisja
    w chwili decyzji. Przed odsłonięciem ocen wątku nie ma w ogóle: notatka byłaby wtedy furtką do
    uzgodnienia punktów przed ich wystawieniem, czyli obejściem ślepej oceny.

    ``revealed`` pozwala podać już policzone odsłonięcie – wywołujący, który właśnie je sprawdził
    (``comparison_context``), nie powtarza wtedy tych samych zapytań.
    """
    if not (is_revealed(submission) if revealed is None else revealed):
        return "NOT_REVEALED"
    if submission.status == SubmissionStatus.FINAL:
        return "SUBMISSION_FINAL"
    if _results_published(submission.entry.stage):
        return "RESULTS_PUBLISHED"
    return None


#: Zdania dla recenzenta – jedno na każdy powód odmowy z ``note_block_reason``.
NOTE_BLOCK_MESSAGES = {
    "NOT_REVEALED": "Notatki otwierają się dopiero, gdy wszystkie oceny tej pracy są wystawione.",
    "SUBMISSION_FINAL": "Ta praca ma ocenę ostateczną – wątek notatek jest zamknięty.",
    "RESULTS_PUBLISHED": "Wyniki tego etapu są już ogłoszone – wątek notatek jest zamknięty.",
}


def _results_published(stage) -> bool:
    """Czy etap ma ogłoszoną tabelę wyników. Import lokalny – ``apps.results`` woła ocenianie."""
    from apps.results.models import ResultsPublication

    return ResultsPublication.objects.filter(stage=stage).exists()


def note_rows(submission, *, viewer=None) -> list[dict]:
    """Wątek notatek jednej pracy: podpis literowy, treść i czas. Bez tożsamości autorów.

    ``viewer`` (członek komitetu oglądający wątek) dostaje przy swoich wpisach ``own=True``, żeby
    panel mógł je wyróżnić – to jedyna informacja o tożsamości, jaką ten widok zdradza, i dotyczy
    wyłącznie samego czytelnika.
    """
    labels = reviewer_labels(submission)
    return [
        {
            "label": labels.get(note.author_id, "Recenzent"),
            "text": note.text,
            "created_at": note.created_at,
            "own": viewer is not None and note.author_id == viewer.pk,
        }
        for note in ReviewNote.objects.filter(submission=submission).order_by("created_at", "id")
    ]


def can_read_notes(submission, member) -> bool:
    """Czy dany członek komitetu jest recenzentem tej pracy – tylko oni czytają wątek.

    Koordynator ma własną drogę (pulpit, ``notes_by_submission``), a nie tę: jego uprawnienie
    wynika z roli, a nie z przydziału, więc sprawdzanie go tutaj mieszałoby dwie reguły w jednej
    funkcji.
    """
    if member is None:
        return False
    return (
        Review.objects.filter(submission=submission, reviewer=member)
        .exclude(status=ReviewStatus.CANCELLED)
        .exists()
    )


def add_review_note(submission, author, text: str, *, request=None) -> ReviewNote:
    """Dopisuje notatkę do wątku pracy. Pisać mogą wyłącznie jej recenzenci.

    Koordynator wątku nie pisze świadomie: od decyzji organizatora jest rozstrzygnięcie moderacji
    z uzasadnieniem, które trafia do akt oceny. Głos w dyskusji recenzentów byłby czymś pomiędzy –
    wpływałby na ocenę, nie zostawiając śladu w jej uzasadnieniu.
    """
    if not can_read_notes(submission, author):
        raise _conflict("Nie recenzujesz tej pracy.", "NOT_A_REVIEWER_OF_SUBMISSION")
    reason = note_block_reason(submission)
    if reason is not None:
        raise _conflict(NOTE_BLOCK_MESSAGES[reason], reason)
    cleaned = (text or "").strip()
    if not cleaned:
        raise _bad_request("Notatka nie może być pusta.", "EMPTY_NOTE")
    note = ReviewNote.objects.create(submission=submission, author=author, text=cleaned[:MAX_NOTE_LENGTH])
    audit(
        author.user,
        "review.note_added",
        note,
        {"submission_id": submission.pk, "length": len(note.text)},
        request=request,
    )
    return note


def notes_by_submission(submissions) -> dict[int, list[dict]]:
    """Wątki notatek dla listy prac – jedno zapytanie na cały ekran moderacji koordynatora.

    Kolejka moderacji bywa długa, a notatki są przy niej dopiskiem, nie treścią główną: liczenie
    ich osobno dla każdego wiersza byłoby zapytaniem na pracę tylko po to, żeby zwykle nie pokazać
    niczego.
    """
    ids = [submission.pk for submission in submissions]
    if not ids:
        return {}
    labels: dict[int, dict[int, str]] = {}
    grouped: dict[int, list[dict]] = {}
    for submission in submissions:
        labels[submission.pk] = reviewer_labels(submission)
    for note in ReviewNote.objects.filter(submission_id__in=ids).order_by("created_at", "id"):
        grouped.setdefault(note.submission_id, []).append(
            {
                "label": labels.get(note.submission_id, {}).get(note.author_id, "Recenzent"),
                "text": note.text,
                "created_at": note.created_at,
            }
        )
    return grouped
