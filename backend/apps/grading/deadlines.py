"""Terminy pojedynczych recenzji i przypomnienia o nich.

Skąd bierze się termin (``Review.due_at``) – reguła jest jedna i stoi w ``review_due_at``:

1. **Punktem wyjścia jest chwila przydziału** powiększona o ``Stage.review_deadline_days``
   (domyślnie 14 dni, koordynator zmienia je na ekranie etapu). Termin jest osobisty, bo prace
   przydzielane są falami – zamknięcie etapu, potem dosyłki, zastępstwa za recenzenta, który
   wypadł. Jeden wspólny termin dawałby ostatnim recenzentom dwa dni, a pierwszym trzy tygodnie.
2. **Sufitem jest deadline recenzji całego etapu** (``Stage.review_deadline_at``), o ile wypada
   wcześniej: komplet ocen ma być gotowy przed nim, więc termin osobisty nie może go przekraczać.
   Sufit obejmuje zarazem okno reklamacji – ``review_deadline_at <= appeal_window_opens_at``
   pilnuje constraint w bazie, a reklamacje bez kompletu ocen nie miałyby czego dotyczyć.
3. **Sufit nie może cofnąć terminu w przeszłość.** Praca przydzielona po deadline etapu (dosyłka,
   zastępstwo, praca odzyskana z moderacji) dostaje pełne ``review_deadline_days`` – recenzent,
   który dostaje zadanie z terminem „wczoraj”, nie ma jak go dotrzymać, a przypominajka zasypałaby
   go listami od pierwszego dnia.

Termin jest **zapisywany**, a nie liczony przy każdym odczycie: recenzent zobaczył go razem
z pracą, więc zmiana ustawień etapu tydzień później nie może po cichu przesunąć czegoś, na co
ktoś już się umówił.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.submissions.models import SubmissionStatus

from .models import Review, ReviewStatus

#: Ile dni przed terminem wysyłamy pierwsze przypomnienie. Dwa dni to kompromis: tyle wystarczy,
#: żeby wieczorem obejrzeć kilka prac, a jeszcze nie jest to list „na zapas”, który przeczyta się
#: i odłoży.
REMINDER_LEAD_DAYS = 2

#: Minimalny odstęp między dwoma przypomnieniami dla tej samej recenzji. Dwadzieścia godzin, a nie
#: równe dwadzieścia cztery: beat chodzi codziennie o tej samej porze, a najmniejszy poślizg
#: (restart workera, wolniejszy przebieg) sprawiłby przy pełnej dobie, że dzień zostaje pominięty.
REMINDER_INTERVAL = timedelta(hours=20)

#: Stany recenzji, które jeszcze czekają na wykonanie – tylko one mają termin do pilnowania.
OPEN_STATUSES = (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT)

#: Stany pracy, w których recenzję da się jeszcze wystawić. Powtórzone tu zamiast importu
#: ``services.REVIEWABLE_STATUSES``, bo to serwisy wołają ten moduł, a nie odwrotnie – import
#: w drugą stronę zamykałby cykl.
REVIEWABLE_SUBMISSION_STATUSES = (SubmissionStatus.IN_REVIEW, SubmissionStatus.MODERATION)


def review_due_at(stage, assigned_at=None):
    """Termin oddania jednej recenzji przydzielonej w chwili ``assigned_at`` (patrz reguła wyżej)."""
    assigned_at = assigned_at or timezone.now()
    days = stage.review_deadline_days or 0
    if days < 1:  # pragma: no cover - constraint w bazie nie dopuszcza zera
        return None
    due = assigned_at + timedelta(days=days)
    ceiling = stage.review_deadline_at
    if ceiling is not None and assigned_at < ceiling < due:
        return ceiling
    return due


def is_overdue(review, now=None) -> bool:
    """Czy recenzja jest po terminie. Recenzja wystawiona albo anulowana nie jest już spóźniona."""
    if review.due_at is None or review.status not in OPEN_STATUSES:
        return False
    return review.due_at < (now or timezone.now())


def due_in_days(review, now=None):
    """Ile pełnych dni zostało do terminu (ujemne = po terminie). ``None``, gdy terminu nie ma."""
    if review.due_at is None:
        return None
    return (review.due_at - (now or timezone.now())).days


def reviews_needing_reminder(now=None):
    """Otwarte recenzje, o których trzeba dziś przypomnieć: termin za ≤ 2 dni albo już minął.

    Warunek ``reminded_at`` jest tym, co zamienia codzienny przebieg beatu w jeden list dziennie:
    recenzja przypomniana mniej niż ``REMINDER_INTERVAL`` temu nie wchodzi do przebiegu ponownie.
    Praca, która wyszła z oceniania (ocena rozstrzygnięta, etap sfinalizowany), odpada razem
    z filtrem po stanie zgłoszenia – przypomnienie o recenzji, której nie da się już wystawić,
    byłoby wezwaniem do czynności niemożliwej.
    """
    now = now or timezone.now()
    cutoff = now + timedelta(days=REMINDER_LEAD_DAYS)
    return (
        Review.objects.filter(
            status__in=OPEN_STATUSES,
            due_at__isnull=False,
            due_at__lte=cutoff,
            submission__status__in=REVIEWABLE_SUBMISSION_STATUSES,
        )
        .filter(_not_reminded_recently(now))
        .select_related(
            "reviewer",
            "reviewer__user",
            "submission",
            "submission__entry",
            "submission__entry__participant",
            "submission__entry__stage",
            "submission__problem",
        )
        .order_by("reviewer_id", "due_at", "id")
    )


def _not_reminded_recently(now) -> Q:
    """Warunek „nie przypominaliśmy o tej recenzji w ostatniej dobie”.

    Osobna funkcja, bo to jedyna część zapytania, którą trzeba zrozumieć, żeby wiedzieć, dlaczego
    recenzent nie dostaje dwóch listów dziennie.
    """
    return Q(reminded_at__isnull=True) | Q(reminded_at__lte=now - REMINDER_INTERVAL)


def group_by_reviewer(reviews) -> dict[int, list[Review]]:
    """Recenzje pogrupowane po recenzencie – jeden list na osobę, a nie jeden na pracę.

    Recenzent z ośmioma zaległościami dostaje jedną wiadomość z listą ośmiu prac. Osiem osobnych
    listów byłoby tą samą informacją podaną tak, że nikt jej nie przeczyta.
    """
    grouped: dict[int, list[Review]] = {}
    for review in reviews:
        grouped.setdefault(review.reviewer_id, []).append(review)
    return grouped


def reminder_message(reviews, now=None) -> tuple[str, str]:
    """Treść przypomnienia dla jednego recenzenta. Bez danych osobowych uczestników – kody prac.

    Ocenianie jest ślepe także w poczcie: w liście stoi kod publiczny pracy i numer zadania,
    dokładnie to, co recenzent widzi w panelu.
    """
    now = now or timezone.now()
    overdue = [review for review in reviews if review.due_at < now]
    subject = (
        f"Olimpiada Kwantowa: {len(overdue)} recenzji po terminie"
        if overdue
        else "Olimpiada Kwantowa: zbliża się termin recenzji"
    )
    lines = [
        "Przypomnienie o pracach, które czekają na Twoją ocenę:",
        "",
    ]
    for review in reviews:
        code = review.submission.entry.participant.public_code
        stage = review.submission.entry.stage.display_name
        marker = "PO TERMINIE" if review.due_at < now else "termin"
        lines.append(
            f"- {code}, zadanie {review.submission.problem.number} ({stage}) – "
            f"{marker}: {timezone.localtime(review.due_at):%d.%m.%Y %H:%M}"
        )
    lines += [
        "",
        "Prace znajdziesz po zalogowaniu w panelu recenzenta.",
        "Jeżeli nie zdążysz w terminie, napisz do koordynatora – lepiej przekazać pracę komuś "
        "innemu niż wstrzymywać ogłoszenie wyników etapu.",
    ]
    return subject, "\n".join(lines)
