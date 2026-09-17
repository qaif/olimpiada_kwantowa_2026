"""Dodatki panelu recenzenta: szablony komentarzy, pomiar czasu, uwagi do linii i zgłoszenia.

Osobny moduł od ``reviewer.py`` i ``reviewer_tools.py`` z tego samego powodu, dla którego tamte
dwa są rozdzielone: w ``reviewer.py`` jest ocenianie jednej pracy (formularz, podgląd, adnotacje),
w ``reviewer_tools.py`` – materiały i rozmowa wokół niej, a tutaj cztery narzędzia, z których żadne
nie zmienia oceny: notatnik szablonów, licznik czasu, uwaga przypięta do linii kodu i zgłoszenie
problemu z pracą.

Wspólna reguła dostępu jest jedna i stoi w querysecie: recenzent widzi **własne przydziały**, więc
cudza recenzja jest 404, a nie 403 (``reviews_for_reviewer``). Reguły domenowe są w ``apps.grading``
(``snippets``, ``worklog``, ``code_view``, ``issues``) – widoki wyłącznie orkiestrują i zamieniają
``DomainError`` na komunikat.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import View

from apps.core.api import DomainError
from apps.grading.code_view import add_line_note
from apps.grading.issues import open_issue
from apps.grading.models import CommentSnippet
from apps.grading.services import reviews_for_reviewer
from apps.grading.snippets import add_own_snippet, delete_own_snippet
from apps.grading.worklog import HEARTBEAT_SECONDS, format_duration, heartbeat
from apps.web.mixins import ReviewerRequiredMixin


class ReviewerScopedMixin(ReviewerRequiredMixin):
    """Własne przydziały zalogowanego recenzenta – cudza recenzja to 404, nie 403."""

    def get_review(self, pk: int):
        return get_object_or_404(reviews_for_reviewer(self.reviewer, self.competition), pk=pk)


def _back_to_review(pk) -> str:
    """Powrót na stronę oceny. Bez ``pk`` (szablon dodany spoza kontekstu pracy) – na listę."""
    if pk:
        return reverse("web:review-detail", kwargs={"pk": pk})
    return reverse("web:review-list")


class SnippetCreateView(ReviewerRequiredMixin, View):
    """``POST /review/snippets/`` – recenzent dopisuje własny szablon komentarza.

    Adres jest bez identyfikatora recenzji, bo szablon **nie należy do pracy**: recenzent pisze go
    przy jednej pracy, a używa przy dwudziestu następnych. Numer recenzji jedzie w polu ``review``
    wyłącznie po to, żeby wrócić tam, skąd przyszło żądanie.

    Przypisanie do zadania (pole ``problem``) jest opcjonalne i **wyprowadzane z recenzji**, a nie
    przyjmowane od klienta: recenzent nie ma listy zadań do wyboru, ma pracę przed sobą. Bez pracy
    powstaje szablon ogólny, widoczny przy każdej.
    """

    def post(self, request):
        review_id = (request.POST.get("review") or "").strip()
        review = None
        if review_id:
            review = get_object_or_404(reviews_for_reviewer(self.reviewer, self.competition), pk=review_id)
        problem = review.submission.problem if review is not None else None
        try:
            add_own_snippet(
                self.reviewer,
                request.POST.get("title", ""),
                request.POST.get("text", ""),
                problem=problem if request.POST.get("scope") == "problem" else None,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Szablon komentarza został zapisany.")
        return redirect(_back_to_review(review.pk if review is not None else None))


class SnippetDeleteView(ReviewerRequiredMixin, View):
    """``POST /review/snippets/<id>/delete/`` – skasowanie **własnego** szablonu.

    Queryset jest zawężony do ``owner=self.reviewer``, więc szablon wspólny (koordynatora) i cudzy
    prywatny są tu nieodróżnialne od nieistniejącego: 404. To jest właściwa odpowiedź – istnienie
    cudzego notatnika nie jest informacją, która ma wyciekać przez kod stanu.
    """

    def post(self, request, pk: int):
        snippet = get_object_or_404(CommentSnippet.objects.filter(owner=self.reviewer), pk=pk)
        delete_own_snippet(self.reviewer, snippet, request=request)
        messages.success(request, "Szablon został usunięty.")
        return redirect(_back_to_review((request.POST.get("review") or "").strip()))


class ReviewHeartbeatView(ReviewerScopedMixin, View):
    """``POST /review/<id>/heartbeat/`` – sygnał życia licznika czasu pracy.

    Odpowiedź jest JSON-em, bo czyta ją skrypt (``static/js/review-worklog.js``) i pokazuje
    zaktualizowaną sumę bez przeładowania strony. Nie ma tu wariantu HTML: bez JavaScriptu nikt
    tego adresu nie woła i licznik po prostu nie mierzy – strona oceny działa bez zmian.

    Widok nie przyjmuje od klienta **żadnej długości**: przysłany czas i tak byłby deklaracją
    mierzonego, a nie pomiarem. Serwer liczy odstęp od poprzedniego sygnału i przycina go
    (``apps.grading.worklog.heartbeat``).
    """

    def post(self, request, pk: int):
        log = heartbeat(self.get_review(pk))
        return JsonResponse(
            {
                "seconds": log.seconds,
                "label": format_duration(log.seconds),
                "interval": HEARTBEAT_SECONDS,
            }
        )


class ReviewLineNoteView(ReviewerScopedMixin, View):
    """``POST /review/<id>/line-note/`` – uwaga przypięta do linii kodu.

    Osobny adres zamiast doklejenia pola do zapisu szkicu: uwagę dopisuje się w trakcie czytania
    listingu, wielokrotnie, a formularz oceny stoi w drugiej kolumnie i bywa w połowie wypełniony.
    Wspólny zapis kasowałby to, czego recenzent jeszcze nie zapisał, albo wymuszał wysyłanie całej
    oceny przy każdej uwadze.

    Bramka jest ta sama, co przy szkicu (``_assert_review_open`` w ``save_draft``): do recenzji
    wystawionej, anulowanej ani do pracy poza ocenianiem nie dopisze się tędy nic.
    """

    def post(self, request, pk: int):
        review = self.get_review(pk)
        try:
            add_line_note(
                review,
                request.POST.get("line", ""),
                request.POST.get("text", ""),
                public=bool(request.POST.get("public")),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Uwaga do linii została zapisana.")
        return redirect(_back_to_review(pk))


class ReviewIssueCreateView(ReviewerScopedMixin, View):
    """``POST /review/<id>/issues/`` – „Zgłoś problem z pracą”.

    Zgłoszenie nie wstrzymuje oceniania (patrz ``apps.grading.issues``), więc ten widok niczego nie
    blokuje ani nie przekierowuje poza stronę oceny: recenzent zostaje tam, gdzie był, i widzi nad
    formularzem baner z własnym zgłoszeniem.
    """

    def post(self, request, pk: int):
        review = self.get_review(pk)
        try:
            open_issue(
                review,
                (request.POST.get("kind") or "").strip(),
                request.POST.get("text", ""),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(
                request,
                "Zgłoszenie trafiło do koordynatora. Ocenę możesz wystawić mimo zgłoszenia.",
            )
        return redirect(_back_to_review(pk))
