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
from django.db.models import Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.api import DomainError
from apps.grading.code_view import code_listing, line_notes
from apps.grading.comparison import comparison_context
from apps.grading.deadlines import due_in_days
from apps.grading.issues import open_issues_for_reviewer
from apps.grading.models import (
    ROUND_TIEBREAK,
    ReviewStatus,
    ReviewWorkLog,
    WorkIssueKind,
    WorkIssueStatus,
)
from apps.grading.navigation import group_by_problem, queue_position
from apps.grading.rubric import rubric_from_post, rubric_rows
from apps.grading.services import (
    GRADE_CHANGE_BLOCK_MESSAGES,
    REVIEWER_ZIP_FILENAME,
    build_reviewer_zip,
    cancel_message,
    dispute_context,
    reviews_for_reviewer,
    revise_review,
    revision_block_reason,
    save_draft,
    scale_items,
    submit_review,
)
from apps.grading.snippets import snippets_for
from apps.grading.worklog import format_duration, review_seconds
from apps.web.forms import ReviewDraftForm, ReviewSubmitForm
from apps.web.mixins import ReviewerRequiredMixin


class ReviewerScopedMixin(ReviewerRequiredMixin):
    """Wspólny queryset panelu: wyłącznie własne przydziały zalogowanego recenzenta."""

    def get_queryset(self):
        # Kolejność z § 3.5: najpierw konkurs (własność), potem recenzent (rola). Serwis robi
        # jedno i drugie w tej kolejności, więc ta sama osoba recenzująca w dwóch olimpiadach
        # widzi tu wyłącznie przydziały spod domeny, na której właśnie jest.
        return reviews_for_reviewer(self.reviewer, self.competition)

    def get_review(self, pk: int):
        return get_object_or_404(self.get_queryset(), pk=pk)


#: Zakładki kolejki w kolejności, w jakiej recenzent ich używa: najpierw to, co czeka na robotę,
#: potem to, co zaczął, dalej to, co skończył, a na końcu to, przy czym nie ma już nic do zrobienia.
#: Zakładka „Anulowane” jest w tej krotce, a nie doklejona osobno, właśnie po to, żeby recenzje
#: odebrane nie zniknęły z panelu bez śladu – recenzent, któremu praca zniknęła, ma prawo sądzić,
#: że to awaria (i pisze wtedy do koordynatora zamiast oceniać).
REVIEW_TABS = (
    ("todo", ReviewStatus.ASSIGNED, _("Do zrobienia")),
    ("draft", ReviewStatus.DRAFT, _("W toku")),
    ("submitted", ReviewStatus.SUBMITTED, _("Wystawione")),
    ("cancelled", ReviewStatus.CANCELLED, _("Anulowane")),
)

#: Zakładki, w których wiersz niesie termin i postęp. W „Wystawionych” i „Anulowanych” nie ma już
#: czego pilnować, więc odznaka terminu byłaby tam wyrzutem sumienia bez żadnej możliwej reakcji.
TABS_WITH_DEADLINE = ("todo", "draft")


def _queue_order(reviews, now):
    """Kolejka posortowana tak, jak recenzent ją przerabia: najpilniejsze najpierw.

    Klucz jest trójdzielny, bo samo ``due_at`` nie wystarcza: recenzja bez terminu (etap sprzed
    wprowadzenia terminów, praca przydzielona ręcznie) nie może wypaść na początek kolejki tylko
    dlatego, że ``None`` porównuje się dziwnie – stąd pierwszy element pary. ``pk`` na końcu robi
    z tego porządek **stały**: dwie prace z tym samym terminem mają stać w tej samej kolejności
    przy każdym wejściu, bo inaczej „następna” znaczyłaby co chwilę co innego.
    """
    return sorted(reviews, key=lambda review: (review.due_at is None, review.due_at or now, review.pk))


def _decorate(groups, *, now, with_reason):
    """Dokłada do wierszy grupy to, czego nie wie ``group_by_problem``: ile dni do terminu i postęp.

    Liczenie dni w szablonie (przez filtr ``timeuntil``) dawałoby napis „2 dni, 3 godziny”, czyli
    dokładność, której nikt na liście nie potrzebuje – potrzebna jest odpowiedź „dziś czy jeszcze
    nie”. ``last_seen_at`` z licznika czasu jest jedynym śladem, że recenzent już przy tej pracy
    siedział: ``Review`` nie ma znacznika zapisu szkicu (patrz ``apps.grading.models``).
    """
    for group in groups:
        for row in group["rows"]:
            review = row["review"]
            row["due_days"] = due_in_days(review, now)
            log = getattr(review, "work_log", None)
            row["last_seen_at"] = log.last_seen_at if log is not None else None
            if with_reason:
                row["reason"] = cancel_message(review)
    return groups


class ReviewListView(ReviewerScopedMixin, TemplateView):
    """Kolejka pracy recenzenta: cztery zakładki po stanie recenzji, w każdej grupy po zadaniu.

    Dlaczego zakładki, a nie jedna długa lista. Recenzent wchodzi tu po jedno: „co mam dziś zrobić”.
    Odpowiedź ginęła w tabeli, w której recenzje wystawione, anulowane i czekające stały obok
    siebie i różniły się jedną odznaką w trzeciej kolumnie. Zakładki („Do zrobienia”, „W toku”,
    „Wystawione”, „Anulowane”) rozdzielają te cztery stany, a pasek podsumowania nad nimi odpowiada
    na pytanie, po które dotąd trzeba było przewijać całą stronę: ile zostało, na kiedy i ile już
    to zajęło.

    Wszystkie cztery zakładki są **wypełnione przez serwer**, a ``static/js/reviewer-layout.js``
    tylko chowa te nieaktywne. Bez JavaScriptu strona jest kompletną listą z czterema nagłówkami
    i odnośnikami do nich – nic nie znika, a „zakładka”, która bez skryptu nie pokazuje treści,
    byłaby ukryciem danych, a nie uporządkowaniem ich.

    Kolejność wewnątrz zakładki idzie po terminie (``_queue_order``), a nie po chwili przydziału:
    recenzent ma zacząć od tego, co przepadnie najwcześniej. Grupowanie po etapie i zadaniu zostaje
    (``grading.navigation``), bo tak wygląda sama robota: jedno zadanie w wielu pracach.
    """

    template_name = "web/reviewer/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        # ``work_log`` dociągamy tu, a nie w ``reviews_for_reviewer``: postęp („ostatnio otwarta”)
        # jest potrzebny wyłącznie tej liście, a serwis obsługuje też API i paczkę ZIP.
        reviews = _queue_order(self.get_queryset().select_related("work_log"), now)
        by_status: dict[str, list] = {}
        for review in reviews:
            by_status.setdefault(review.status, []).append(review)

        tabs = []
        for key, status, label in REVIEW_TABS:
            rows = by_status.get(status, [])
            tabs.append(
                {
                    "key": key,
                    "label": label,
                    "count": len(rows),
                    "groups": _decorate(group_by_problem(rows, now), now=now, with_reason=key == "cancelled"),
                    "with_deadline": key in TABS_WITH_DEADLINE,
                }
            )
        context["tabs"] = tabs
        # Zakładka otwierana przy wejściu: pierwsza niepusta, czyli zwykle „Do zrobienia”.
        # Otwieranie pustej zakładki tylko dlatego, że stoi pierwsza, pokazywałoby recenzentowi
        # komunikat „nic tu nie ma” nad listą, w której coś jest.
        context["active_tab"] = next((tab["key"] for tab in tabs if tab["count"]), "todo")

        open_reviews = by_status.get(ReviewStatus.ASSIGNED, []) + by_status.get(ReviewStatus.DRAFT, [])
        context["reviews"] = [item for item in reviews if item.status != ReviewStatus.CANCELLED]
        context["summary"] = {
            "todo": len(open_reviews),
            "overdue": sum(1 for item in open_reviews if item.due_at and item.due_at < now),
            "next_due": min((item.due_at for item in open_reviews if item.due_at), default=None),
            # Jedna suma na cały panel zamiast ``review_seconds`` w pętli: przydziałów bywa kilkaset,
            # a pasek podsumowania potrzebuje wyłącznie łącznej liczby.
            "worklog_label": format_duration(
                ReviewWorkLog.objects.for_competition(self.competition)
                .filter(review__reviewer=self.reviewer)
                .aggregate(total=Sum("seconds"))["total"]
                or 0
            ),
        }
        context["open_statuses"] = (ReviewStatus.ASSIGNED, ReviewStatus.DRAFT)
        # Zgłoszone problemy z pracami – jedno zapytanie na całą listę, bo marker stoi przy
        # wierszach, a przydziałów bywa kilkaset. Szablon pyta o pojedynczy wiersz, stąd słownik.
        context["open_issues"] = open_issues_for_reviewer(self.reviewer)
        return context


class ReviewQueueDownloadView(ReviewerScopedMixin, View):
    """``GET /review/download/`` – wszystkie własne prace w jednym archiwum ZIP.

    Odpowiednik ``GET /api/grading/reviews/download/``: cała reguła (zakres, nazwy plików, audyt)
    jest w ``build_reviewer_zip``, więc panel i API nie mogą się rozjechać. Pusta kolejka kończy
    się stroną 404 z powodem, a nie przekierowaniem – żądanie wysłane skryptem ma dostać kod,
    po którym widać, że pliku nie ma.
    """

    def get(self, request):
        try:
            package = build_reviewer_zip(self.reviewer, actor=request.user, request=request)
        except DomainError as exc:
            return TemplateResponse(request, "404.html", {"reason": str(exc.detail)}, status=404)
        return FileResponse(
            package.stream,
            as_attachment=True,
            filename=REVIEWER_ZIP_FILENAME,
            content_type="application/zip",
        )


#: Typy MIME pokazywane w panelu jako zdjęcie, a nie jako dokument dla pdf.js.
IMAGE_PREVIEW_MIMES = ("image/jpeg",)


def _preview_kind(submission_file) -> str:
    """Czym jest podgląd pracy: dokumentem (pdf.js) czy zdjęciem (``<img>``).

    Rozstrzyga typ MIME wyliczony **przez serwer** przy uploadzie (``validate_upload``), a nie
    rozszerzenie z nazwy od uczestnika: nazwę kontroluje przesyłający, a typ potwierdziła treść.
    """
    mime = (getattr(submission_file, "mime", "") or "").lower()
    return "image" if mime in IMAGE_PREVIEW_MIMES else "pdf"


def _scale_options(review) -> list[dict]:
    """Pozycje obowiązującej skali – radio z etykietami, nie wolne pole liczbowe.

    Skala bierze się z ``grading.services.scale_items``, więc zadanie z własną skalą pokazuje
    recenzentowi swoje wartości, a nie wartości etapu. Ekran nie może oferować oceny, której
    ``submit_review`` by nie przyjął.
    """
    return scale_items(review.submission.entry.stage, review.submission.problem)


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
                # Zdjęcie rozwiązania (JPEG) ma ten sam ekran, co PDF: inny jest wyłącznie sposób
                # narysowania strony. Warstwa adnotacji zostaje – obrazek jest jedną stroną.
                "preview_kind": _preview_kind(submission_file),
                "download_url": reverse(
                    "submissions:submission-download", kwargs={"pk": review.submission_id}
                ),
                "annotations_url": reverse("grading:review-detail", kwargs={"pk": review.pk}),
                "editable": editable,
                # Ile dni do terminu – dla odznaki w nagłówku panelu oceny. Filtr ``timeuntil``
                # dałby tu „2 dni, 3 godziny”, czyli dokładność, której recenzent nie potrzebuje:
                # pytanie brzmi „czy zdążę”, a nie „ile dokładnie mam czasu”.
                "due_days": due_in_days(review),
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
                # Rubryka zadania. Pusta lista znaczy „zadanie bez kryteriów” i wtedy ekran pokazuje
                # zwykły wybór oceny ze skali – dokładnie jak dotąd.
                "rubric_rows": rubric_rows(review),
                # Porównanie ocen jest ``None``, dopóki oceny nie są odsłonięte (patrz
                # ``grading.comparison``) – szablon nie ma wtedy czego pokazać i sekcja nie istnieje.
                "comparison": comparison_context(review),
                # Seria prac tego samego zadania: „5 z 18” oraz sąsiedzi do przeskoczenia.
                "queue": queue_position(review),
                # Wzorcówka i uwagi dla recenzentów – materiał komitetu, nigdy dla uczestnika.
                "model_solution_url": (
                    reverse("web:problem-model-solution", kwargs={"pk": review.submission.problem_id})
                    if review.submission.problem.model_solution_pdf
                    else None
                ),
                # Szablony komentarzy: wspólne komitetu przed prywatnymi recenzenta. Lista jest
                # czytelna także bez JavaScriptu – treść stoi na ekranie do skopiowania, a przycisk
                # „Wstaw” jest wygodą, którą dokłada ``static/js/review-snippets.js``.
                "snippets": snippets_for(review.submission.problem, self.reviewer),
                # Zmierzony czas pracy nad tą recenzją. Zero znaczy „nie mierzono” (recenzja sprzed
                # wprowadzenia licznika albo ocena zrobiona z wydruku) – szablon mówi to wprost.
                "worklog_seconds": review_seconds(review),
                "worklog_label": format_duration(review_seconds(review)),
                # Podgląd rozwiązania oddanego jako kod. ``None`` dla PDF-u i zdjęcia – tam
                # obowiązuje warstwa prostokątów i sekcja listingu w ogóle nie powstaje.
                "code_listing": (
                    code_listing(submission_file, review.annotations)
                    if submission_file is not None and submission_file.is_clean
                    else None
                ),
                "line_notes": line_notes(review),
                # Otwarte zgłoszenie problemu z tą pracą. Baner, a nie bramka: ocenianie zostaje
                # dozwolone (patrz ``apps.grading.issues``).
                "open_issue": review.issues.filter(status=WorkIssueStatus.OPEN).first(),
                "issue_kinds": WorkIssueKind.choices,
            }
        )
        return context


def _score_form_and_rubric(request, problem):
    """Formularz oceny dobrany do zadania plus rubryka odczytana z żądania.

    Zadanie z rubryką nie ma na ekranie pola „punkty”: sumę liczy serwer z punktów cząstkowych
    (``submit_review``). Formularz wymagający oceny odrzucałby wtedy poprawne zgłoszenie
    komunikatem „Wybierz ocenę ze skali”, choć recenzent wypełnił wszystko, o co go poproszono –
    stąd przy rubryce wchodzi wariant z opcjonalnym ``score``.

    Błąd odczytu rubryki (punkty nie są liczbą) wychodzi stąd jako ``DomainError``, czyli tą samą
    drogą, co odmowy serwisu – widok ma jeden sposób pokazywania takich rzeczy.
    """
    rubric = rubric_from_post(problem, request.POST)
    form_class = ReviewSubmitForm if rubric is None else ReviewDraftForm
    return form_class(request.POST), rubric


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
                    # Szkic przyjmuje rubrykę niekompletną – recenzent zapisuje robotę w połowie.
                    rubric=rubric_from_post(review.submission.problem, request.POST),
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
        try:
            form, rubric = _score_form_and_rubric(request, review.submission.problem)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
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
                rubric=rubric,
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
        try:
            form, rubric = _score_form_and_rubric(request, review.submission.problem)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
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
                rubric=rubric,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:review-detail", kwargs={"pk": pk}))
        messages.success(request, "Poprawiona ocena została zapisana.")
        return redirect(reverse("web:review-list"))
