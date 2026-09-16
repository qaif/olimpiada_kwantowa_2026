"""Panel recenzenta ``/review/``.

Ocenianie jest ślepe: w kontekście szablonów nie ma ani imienia, ani nazwiska, ani e-maila, ani
szkoły uczestnika – jedynym identyfikatorem pracy jest ``public_code`` (PROJEKT.md 2.2). Widoczność
przydziałów bierze się z ``grading.services.reviews_for_reviewer``, więc cudza recenzja to 404.

Podgląd PDF i warstwa adnotacji żyją w ``static/js/review-annotations.js`` (jedyna „wyspa” JS).
Adnotacje jadą do bazy dwiema drogami, obiema przez ten sam serwis ``save_draft``:
``PATCH /api/grading/reviews/{id}/`` z warstwy pdf.js oraz ukryte pole formularza przy zapisie
szkicu i wystawieniu oceny (żeby nic nie ginęło, gdy recenzent zapisze bez ruszania PDF-u).
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.core.api import DomainError
from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
from apps.grading.services import (
    GRADE_CHANGE_BLOCK_MESSAGES,
    cancel_message,
    dispute_context,
    reviews_for_reviewer,
    revise_review,
    revision_block_reason,
    save_draft,
    submit_review,
)
from apps.web.forms import ReviewDraftForm, ReviewSubmitForm
from apps.web.mixins import ReviewerRequiredMixin


class ReviewerScopedMixin(ReviewerRequiredMixin):
    """Wspólny queryset panelu: wyłącznie własne przydziały zalogowanego recenzenta."""

    def get_queryset(self):
        return reviews_for_reviewer(self.reviewer)

    def get_review(self, pk: int):
        return get_object_or_404(self.get_queryset(), pk=pk)


class ReviewListView(ReviewerScopedMixin, TemplateView):
    """Lista przydziałów recenzenta ze statusem każdej recenzji.

    Recenzje anulowane stoją osobno, a nie w jednej tabeli z resztą: nie ma już przy nich nic do
    zrobienia, a wymieszane z bieżącymi wyglądałyby jak zaległość. Ukrycie ich odpadło – recenzent,
    któremu praca zniknęła z listy bez śladu, ma prawo sądzić, że to awaria. Każdy wiersz niesie
    powód: odebranie pracy przez koordynatora to co innego niż nowa wersja od uczestnika.
    """

    template_name = "web/reviewer/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reviews = list(self.get_queryset())
        context["reviews"] = [item for item in reviews if item.status != ReviewStatus.CANCELLED]
        context["cancelled_rows"] = [
            {"review": item, "reason": cancel_message(item)}
            for item in reviews
            if item.status == ReviewStatus.CANCELLED
        ]
        context["open_statuses"] = (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT)
        return context


def _scale_options(review) -> list[dict]:
    """Pozycje skali punktowej etapu – radio z etykietami, nie wolne pole liczbowe."""
    scale = getattr(review.submission.entry.stage, "scoring_scale", None)
    if scale is None:
        return []
    options = []
    for item in scale.values or []:
        if isinstance(item, dict) and isinstance(item.get("value"), int):
            options.append({"value": item["value"], "label": item.get("label", "")})
    return options


class ReviewDetailView(ReviewerScopedMixin, TemplateView):
    """Formularz oceny jednej pracy wraz z podglądem PDF i warstwą adnotacji.

    Ten sam ekran obsługuje trzy sytuacje: ocenę jeszcze niewystawioną (``editable``), poprawkę
    oceny już wystawionej (``revision_allowed``) i recenzję, przy której nic już nie zrobisz.
    O tej ostatniej mówi ``revision_hint`` – powód bierze się z ``revision_block_reason``, czyli
    z tej samej reguły, którą zastosuje zapis. Ekran nie może obiecywać więcej niż serwis przyjmie.
    """

    template_name = "web/reviewer/detail.html"

    def get_context_data(self, pk: int, **kwargs):
        context = super().get_context_data(**kwargs)
        review = self.get_review(pk)
        submission_file = review.submission.latest_file
        editable = review.status in (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT)
        block_reason = None if editable else revision_block_reason(review)
        context.update(
            {
                "review": review,
                # JSON w atrybucie ``data-*``, nie w bloku <script>: atrybut przechodzi przez
                # autoescapowanie Django, a warstwa pdf.js czyta go przez ``dataset`` i JSON.parse.
                "annotations_json": json.dumps(review.annotations or [], ensure_ascii=False),
                "submission": review.submission,
                "problem": review.submission.problem,
                "public_code": review.submission.entry.participant.public_code,
                "scale_options": _scale_options(review),
                "file_available": submission_file is not None and submission_file.is_clean,
                "download_url": reverse(
                    "submissions:submission-download", kwargs={"pk": review.submission_id}
                ),
                "annotations_url": reverse("grading:review-detail", kwargs={"pk": review.pk}),
                "editable": editable,
                "revision_allowed": not editable and block_reason is None,
                # Recenzję anulowaną pokazujemy osobnym komunikatem, a nie podpowiedzią przy
                # formularzu: formularza tam w ogóle nie ma. Powód bierze się z ``cancel_reason``,
                # bo „anulowana” znaczy co innego dla pracy odebranej przez koordynatora, a co
                # innego dla pracy, której uczestnik wysłał nową wersję.
                "withdrawn": review.status == ReviewStatus.CANCELLED,
                "cancel_message": (
                    cancel_message(review) if review.status == ReviewStatus.CANCELLED else None
                ),
                "revision_hint": GRADE_CHANGE_BLOCK_MESSAGES.get(block_reason) if block_reason else None,
                # Panel sporu istnieje tylko w rundzie rozjemczej. Ten sam serwis obsługuje
                # ``GET /api/grading/reviews/{id}/dispute/`` – jedna reguła, dwie prezentacje.
                "dispute_rows": dispute_context(review) if review.round == ROUND_TIEBREAK else None,
            }
        )
        return context


class ReviewDraftView(ReviewerScopedMixin, View):
    """Zapis szkicu (HTMX). Odpowiedź to mały fragment ze statusem, nie cała strona."""

    template_name = "web/reviewer/_draft_status.html"

    def post(self, request, pk: int):
        review = self.get_review(pk)
        form = ReviewDraftForm(request.POST)
        error = None
        if form.is_valid():
            try:
                review = save_draft(
                    review,
                    score=form.cleaned_data["score"],
                    comment_internal=form.cleaned_data["comment_internal"],
                    comment_for_participant=form.cleaned_data["comment_for_participant"],
                    annotations=form.cleaned_data["annotations"],
                )
            except DomainError as exc:
                error = str(exc.detail)
        else:
            error = " ".join(message for values in form.errors.values() for message in values)
        return TemplateResponse(
            request, self.template_name, {"review": review, "error": error, "saved": error is None}
        )


class ReviewSubmitView(ReviewerScopedMixin, View):
    """Wystawienie oceny. Konsensus, moderację i ocenę uzgodnioną rozstrzyga ``submit_review``."""

    def post(self, request, pk: int):
        review = self.get_review(pk)
        form = ReviewSubmitForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Wybierz ocenę ze skali przed wysłaniem.")
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        # Puste pole adnotacji oznacza „nie przysłano”, nie „skasuj”: ``submit_review`` nadpisuje
        # listę bezwarunkowo, więc brak wartości zastępujemy stanem z bazy. Inaczej wysłanie oceny
        # z przeglądarki bez działającego pdf.js kasowałoby wcześniej zapisane adnotacje.
        annotations = form.cleaned_data["annotations"]
        if annotations is None:
            annotations = review.annotations
        try:
            submit_review(
                review,
                form.cleaned_data["score"],
                form.cleaned_data["comment_internal"],
                form.cleaned_data["comment_for_participant"],
                annotations,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        messages.success(request, "Ocena została wystawiona.")
        return redirect(reverse("web:review-list"))


class ReviewReviseView(ReviewerScopedMixin, View):
    """Poprawienie własnej, już wystawionej oceny – bliźniak ``ReviewSubmitView``.

    Osobny widok, a nie gałąź w tamtym: obie czynności mają własną bramkę w serwisie i własny
    komunikat, a wspólny kod sprowadzałby się do jednego ``if``. Adnotacje traktujemy tak samo –
    brak pola znaczy „nie przysłano”, nie „skasuj”.
    """

    def post(self, request, pk: int):
        review = self.get_review(pk)
        form = ReviewSubmitForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Wybierz ocenę ze skali przed wysłaniem.")
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        annotations = form.cleaned_data["annotations"]
        if annotations is None:
            annotations = review.annotations
        try:
            revise_review(
                review,
                form.cleaned_data["score"],
                form.cleaned_data["comment_internal"],
                form.cleaned_data["comment_for_participant"],
                annotations,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        messages.success(request, "Poprawiona ocena została zapisana.")
        return redirect(reverse("web:review-list"))
