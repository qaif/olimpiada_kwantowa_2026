"""Ekrany testów online: edytor koordynatora i podejście uczestnika.

Jeden moduł na dwie role, a nie dwa – wbrew podziałowi w ``apps.web.views``, gdzie panel
koordynatora i panel uczestnika mieszkają osobno. Powód jest taki: te ekrany opisują **jedną**
maszynę stanów (test → pytania → podejście → wynik) i zmiana po jednej stronie prawie zawsze
pociąga drugą. Rozdzielenie ich znaczyłoby dwa pliki, które trzeba czytać naraz, i dwa komplety
importów z ``apps.quiz.services``.

Reguła jest ta sama, co w reszcie ``apps.web``: widok **orkiestruje**. Nie ma tu ani jednej decyzji
o tym, komu wolno podejść do testu, kiedy mija czas i ile należy się punktów – to wszystko stoi
w ``apps.quiz.services`` i ``apps.quiz.grading``. Widok składa kontekst, oddaje formularz i zamienia
``DomainError`` na komunikat.

Bezpieczeństwo, dwa punkty warte wymienienia wprost:

- **klucz odpowiedzi nie trafia do HTML-a uczestnika**. Kontekst strony podejścia buduje
  ``_attempt_context`` i przepisuje z pytania wyłącznie treść, wariant i jego identyfikator;
  ``is_correct`` i ``settings`` nie przechodzą przez żaden szablon uczestnika,
- **cudzego podejścia nie da się dotknąć**. Wszystkie widoki uczestnika szukają podejścia przez
  ``_own_attempt``, czyli z filtrem po ``entry__participant``. Nie ma ścieżki, w której
  identyfikator z adresu byłby użyty bez tego filtru.
"""

from __future__ import annotations

import json

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.competitions.models import Stage, StageEntry
from apps.core.api import DomainError
from apps.core.exports import Dataset, csv_response
from apps.core.models import audit
from apps.quiz import services as quiz_services
from apps.quiz.forms import (
    QuestionForm,
    QuestionImportForm,
    QuizSettingsForm,
    initial_from_question,
)
from apps.quiz.imports import ImportError_, parse
from apps.quiz.models import AttemptStatus, QuestionKind, QuizAttempt, QuizQuestion
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin, ParticipantRequiredMixin

SETTINGS_TEMPLATE = "web/coordinator/quiz.html"
QUESTIONS_TEMPLATE = "web/coordinator/quiz_questions.html"
QUESTION_FORM_TEMPLATE = "web/coordinator/quiz_question_form.html"
IMPORT_TEMPLATE = "web/coordinator/quiz_import.html"
RESULTS_TEMPLATE = "web/coordinator/quiz_results.html"
PREVIEW_TEMPLATE = "web/quiz/preview.html"
START_TEMPLATE = "web/quiz/start.html"
ATTEMPT_TEMPLATE = "web/quiz/attempt.html"
RESULT_TEMPLATE = "web/quiz/result.html"


# --- wspólne ------------------------------------------------------------------------------------


def _stage(competition, stage_id: int) -> Stage:
    """Etap **tego konkursu** albo 404 – jedno wejście dla wszystkich ekranów testu w panelu.

    Reszta drogi zawęża się sama: test wisi na etapie (``Quiz.stage``), pytanie na teście,
    a podejście na wpisie uczestnika – więc scoping tego jednego obiektu zamyka cały moduł.
    """
    return get_object_or_404(
        Stage.objects.for_competition(competition).select_related("edition"), pk=stage_id
    )


def _quiz_or_404(stage: Stage):
    """Test etapu albo 404. Osobno od ``_stage``, bo ekran ustawień **działa** bez testu."""
    quiz = quiz_services.quiz_for_stage(stage)
    if quiz is None:
        raise Http404("Ten etap nie ma jeszcze testu.")
    return quiz


class _QuizPanelMixin(CoordinatorRequiredMixin):
    """Wspólny kontekst ekranów testu w panelu: etap, test i ostrzeżenia o losowaniu."""

    def panel_context(self, stage: Stage, quiz=None) -> dict:
        return {
            "stage": stage,
            "quiz": quiz,
            "warnings": quiz_services.draw_warnings(quiz) if quiz is not None else [],
            "has_attempts": quiz_services.has_attempts(quiz) if quiz is not None else False,
        }


# --- koordynator: ustawienia --------------------------------------------------------------------


class QuizSettingsView(_QuizPanelMixin, View):
    """``/coordinator/stages/<id>/quiz/`` – ustawienia testu, a przy braku testu jego założenie.

    Ekran istnieje także dla etapu, który **nie** ma jeszcze formy „test online”: przygotowanie
    arkusza i decyzja o formie zawodów to dwie różne czynności i zwykle w tej kolejności. Karta
    u góry strony przypomina wtedy, że uczestnik testu nie zobaczy, dopóki forma etapu się nie
    zmieni – razem z odnośnikiem do ekranu, na którym się ją zmienia.
    """

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = quiz_services.quiz_for_stage(stage)
        form = (
            QuizSettingsForm(instance=quiz)
            if quiz
            else QuizSettingsForm(initial={"title": stage.display_name})
        )
        return self._render(request, stage, quiz, form)

    def post(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = quiz_services.quiz_for_stage(stage)
        form = QuizSettingsForm(request.POST, instance=quiz)
        if not form.is_valid():
            return self._render(request, stage, quiz, form)
        try:
            quiz_services.save_quiz_settings(
                stage=stage, actor=request.user, request=request, **form.cleaned_data
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, stage, quiz, form)
        return redirect(reverse("web:coordinator-stage-quiz", args=[stage.pk]))

    def _render(self, request, stage, quiz, form):
        context = self.panel_context(stage, quiz)
        context["form"] = form
        return TemplateResponse(request, SETTINGS_TEMPLATE, context)


# --- koordynator: pytania -----------------------------------------------------------------------


class QuizQuestionsView(_QuizPanelMixin, View):
    """Lista pytań testu z podsumowaniem puli, punktacji i rozmiaru losowanego zestawu."""

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        context = self.panel_context(stage, quiz)
        context["pools"] = sorted(
            (
                {
                    "name": name,
                    "questions": questions,
                    "points": sum(question.points for question in questions),
                }
                for name, questions in quiz.pools.items()
            ),
            key=lambda item: item["name"],
        )
        return TemplateResponse(request, QUESTIONS_TEMPLATE, context)


class QuizQuestionFormView(_QuizPanelMixin, View):
    """Dodanie albo zmiana jednego pytania. ``question_id`` puste = nowe pytanie."""

    def get(self, request, stage_id: int, question_id: int | None = None):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        question = self._question(quiz, question_id)
        initial = initial_from_question(question) if question else {"order": self._next_order(quiz)}
        return self._render(request, stage, quiz, QuestionForm(initial=initial), question)

    def post(self, request, stage_id: int, question_id: int | None = None):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        question = self._question(quiz, question_id)
        form = QuestionForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, stage, quiz, form, question)
        try:
            # Blokada „test z podejściami” jest w serwisie, a nie w szablonie: to samo żądanie
            # wysłane skryptem musi dostać ten sam komunikat, co kliknięcie w panelu.
            quiz_services.assert_editable(quiz)
            quiz_services.save_question(
                quiz=quiz,
                question=question,
                options=form.option_rows(),
                settings=form.settings(),
                actor=request.user,
                request=request,
                image=form.cleaned_data.get("image"),
                clear_image=form.cleaned_data.get("clear_image", False),
                **form.question_fields(),
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, stage, quiz, form, question)
        except ValueError as exc:
            # ``grading.validate_question_settings`` mówi o kluczu odpowiedzi, więc komunikat
            # ląduje przy polu, które ten klucz opisuje dla wybranego rodzaju pytania.
            form.add_error(self._settings_field(form.cleaned_data.get("kind")), str(exc))
            return self._render(request, stage, quiz, form, question)
        return redirect(reverse("web:coordinator-stage-quiz-questions", args=[stage.pk]))

    @staticmethod
    def _settings_field(kind) -> str | None:
        return {
            QuestionKind.SHORT_TEXT: "accepted",
            QuestionKind.NUMERIC: "answer",
            QuestionKind.MULTIPLE_CHOICE: "partial_credit",
        }.get(kind)

    @staticmethod
    def _question(quiz, question_id) -> QuizQuestion | None:
        if question_id is None:
            return None
        return get_object_or_404(QuizQuestion, pk=question_id, quiz=quiz)

    @staticmethod
    def _next_order(quiz) -> int:
        """Kolejny numer w kolejności – żeby nowe pytanie lądowało na końcu, a nie na pozycji 0."""
        last = quiz.questions.order_by("-order").values_list("order", flat=True).first()
        return (last or 0) + 1

    def _render(self, request, stage, quiz, form, question):
        context = self.panel_context(stage, quiz)
        context.update({"form": form, "question": question})
        return TemplateResponse(request, QUESTION_FORM_TEMPLATE, context)


class QuizQuestionDeleteView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """Usunięcie pytania (POST). Odmowa dla testu z podejściami stoi w serwisie."""

    def perform(self, request, stage_id: int, question_id: int) -> str:
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        question = get_object_or_404(QuizQuestion, pk=question_id, quiz=quiz)
        quiz_services.delete_question(question=question, actor=request.user, request=request)
        return "Pytanie usunięte."

    def get_success_url(self, stage_id: int, question_id: int = 0) -> str:
        return reverse("web:coordinator-stage-quiz-questions", args=[stage_id])


class QuizImportView(_QuizPanelMixin, View):
    """Import pytań z pliku albo z wklejonego tekstu, z podglądem przed zapisem.

    Zapis jest w **jednej** transakcji przez ``save_question`` pytanie po pytaniu: parser
    sprawdził już kształt pliku, ale klucz odpowiedzi waliduje dopiero serwis i jego odmowa musi
    cofnąć całość. Plik z błędem w trzydziestym pytaniu nie może zostawić po sobie dwudziestu
    dziewięciu – koordynator nie miałby wtedy jak stwierdzić, na czym stanął import.
    """

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        return self._render(request, stage, quiz, QuestionImportForm())

    def post(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        form = QuestionImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, stage, quiz, form)
        try:
            quiz_services.assert_editable(quiz)
            parsed = parse(form.cleaned_data["text"], fmt=form.cleaned_data["fmt"])
            created = self._store(request, quiz, parsed, replace=form.cleaned_data.get("replace", False))
        except ImportError_ as exc:
            form.add_error("text", str(exc))
            return self._render(request, stage, quiz, form)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, stage, quiz, form)
        except ValueError as exc:
            form.add_error("text", str(exc))
            return self._render(request, stage, quiz, form)
        context = self.panel_context(stage, quiz)
        context.update({"form": QuestionImportForm(), "created": created})
        return TemplateResponse(request, IMPORT_TEMPLATE, context)

    @staticmethod
    def _store(request, quiz, parsed, *, replace: bool) -> int:
        from django.db import transaction

        with transaction.atomic():
            if replace:
                quiz.questions.all().delete()
            order = 0
            for item in parsed:
                order += 1
                quiz_services.save_question(
                    quiz=quiz,
                    options=item.options,
                    settings=item.settings,
                    actor=request.user,
                    request=request,
                    pool=item.pool,
                    order=order,
                    kind=item.kind,
                    text=item.text,
                    points=item.points,
                    negative_points=item.negative_points,
                )
        audit(request.user, "quiz.questions_imported", quiz, {"count": ["", str(len(parsed))]}, request)
        return len(parsed)

    def _render(self, request, stage, quiz, form):
        context = self.panel_context(stage, quiz)
        context["form"] = form
        return TemplateResponse(request, IMPORT_TEMPLATE, context)


# --- koordynator: wyniki -------------------------------------------------------------------------


class QuizResultsView(_QuizPanelMixin, View):
    """Wyniki testu: punkty per uczestnik i statystyka pytań (trudność, moc różnicująca)."""

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        # Domykamy porzucone podejścia przed policzeniem czegokolwiek – inaczej ekran pokazywałby
        # zera przy osobach, którym po prostu padło łącze, a ich odpowiedzi leżą w bazie.
        quiz_services.finalise_overdue(quiz=quiz)
        context = self.panel_context(stage, quiz)
        context.update(
            {
                "rows": quiz_services.attempt_rows(quiz),
                "stats": quiz_services.question_stats(quiz),
                "in_progress": quiz.attempts.filter(status=AttemptStatus.IN_PROGRESS).count(),
            }
        )
        return TemplateResponse(request, RESULTS_TEMPLATE, context)


class QuizResultsExportView(CoordinatorRequiredMixin, View):
    """Eksport wyników testu do CSV – ta sama droga, co pozostałe eksporty panelu (z audytem)."""

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        quiz_services.finalise_overdue(quiz=quiz)
        rows = quiz_services.results_csv_rows(quiz)
        audit(request.user, "export.generated", quiz, {"rows": ["", str(len(rows))]}, request)
        return csv_response(
            Dataset(
                header=quiz_services.RESULTS_CSV_HEADER,
                rows=iter(rows),
                count=len(rows),
                title=f"Wyniki testu: {quiz.title}",
                filename=f"test-etap-{stage.pk}",
            )
        )


class QuizRegradeView(ActionViewMixin, CoordinatorRequiredMixin, View):
    """„Przelicz punkty” – ponowna ocena wszystkich podejść po poprawce klucza odpowiedzi."""

    def perform(self, request, stage_id: int) -> str:
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        result = quiz_services.regrade_quiz(quiz=quiz, actor=request.user, request=request)
        return (
            f"Przeliczono {result['attempts']} podejść – wynik zmienił się w {result['changed']}. "
            "Zmiana jest zapisana w audycie."
        )

    def get_success_url(self, stage_id: int) -> str:
        return reverse("web:coordinator-stage-quiz-results", args=[stage_id])


class QuizPreviewView(CoordinatorRequiredMixin, View):
    """„Podgląd jako uczestnik”: arkusz w takiej postaci, w jakiej zobaczy go uczestnik.

    Podgląd **nie zakłada podejścia** i niczego nie zapisuje. To nie jest oszczędność, tylko
    warunek jego użyteczności: gdyby zakładał, koordynator sprawdzający arkusz zjadałby limit
    podejść wpisowi, który do niego nie należy, a jego odpowiedzi trafiałyby do statystyki pytań.
    Zestaw jest losowany w locie (a więc za każdym odświeżeniem inny) i to jest zaleta – dwa
    wejścia pokazują dwa możliwe zestawy, czyli dokładnie to, co chce się zobaczyć przed startem.
    """

    def get(self, request, stage_id: int):
        stage = _stage(request.competition, stage_id)
        quiz = _quiz_or_404(stage)
        questions = list(quiz.questions.prefetch_related("options").order_by("pool", "order", "id"))
        order = quiz_services.draw_question_order(quiz, questions)
        by_id = {question.pk: question for question in questions}
        drawn = []
        for question_id in order[QuizAttempt.ORDER_QUESTIONS]:
            question = by_id[question_id]
            options = {option.pk: option for option in question.options.all()}
            ordered = order[QuizAttempt.ORDER_OPTIONS].get(str(question_id), [])
            question.drawn_options = [options[pk] for pk in ordered] if ordered else list(options.values())
            drawn.append(question)
        return TemplateResponse(
            request,
            PREVIEW_TEMPLATE,
            {"stage": stage, "quiz": quiz, "questions": _public_questions(drawn), "preview": True},
        )


# --- uczestnik ------------------------------------------------------------------------------------


def _public_questions(questions) -> list[dict]:
    """Pytania w postaci **bez klucza odpowiedzi** – jedyna droga pytań do szablonu uczestnika.

    Świadomie słowniki, a nie obiekty modelu: szablon, który dostaje ``QuizQuestion``, ma
    w zasięgu ``question.options.all.0.is_correct`` i ``question.settings.answer``. Jedna
    nieostrożna pętla w szablonie wystarczyłaby wtedy, żeby klucz pojawił się w HTML-u – a takiego
    błędu nie widać w przeglądzie kodu i nie wykrywa go żaden test poza tym, który go szuka.
    Ten filtr jest więc zabezpieczeniem konstrukcyjnym: klucza **nie ma czym** wypisać.
    """
    rows = []
    for question in questions:
        rows.append(
            {
                "id": question.pk,
                "kind": question.kind,
                "text": question.text,
                "image": question.image,
                "points": question.points,
                "negative_points": question.negative_points,
                "unit": (
                    (question.settings or {}).get("unit", "") if question.kind == QuestionKind.NUMERIC else ""
                ),
                "is_choice": question.has_options,
                "is_multiple": question.kind == QuestionKind.MULTIPLE_CHOICE,
                "is_text": question.kind == QuestionKind.SHORT_TEXT,
                "is_numeric": question.kind == QuestionKind.NUMERIC,
                "options": [{"id": option.pk, "text": option.text} for option in question.drawn_options],
            }
        )
    return rows


class _ParticipantQuizMixin(ParticipantRequiredMixin):
    """Wspólne wyszukiwanie testu i podejścia dla ekranów uczestnika – zawsze z filtrem właściciela."""

    def entry(self, stage_id: int) -> StageEntry:
        """Wpis uczestnika do etapu. Brak wpisu to 404, a nie 403: udziału w tym etapie po prostu nie ma."""
        return get_object_or_404(
            StageEntry.objects.select_related("stage", "stage__edition"),
            stage_id=stage_id,
            participant=self.participant,
        )

    def quiz_of(self, entry: StageEntry):
        quiz = quiz_services.quiz_for_stage(entry.stage)
        if quiz is None or not entry.stage.is_quiz:
            raise Http404("Ten etap nie odbywa się w formie testu online.")
        return quiz

    def own_attempt(self, attempt_id: int):
        """Podejście **tej** osoby. Cudze jest nie do odróżnienia od nieistniejącego (404)."""
        from apps.quiz.models import QuizAttempt

        return get_object_or_404(
            QuizAttempt.objects.select_related("quiz", "quiz__stage", "entry"),
            pk=attempt_id,
            entry__participant=self.participant,
        )


class QuizStartView(_ParticipantQuizMixin, View):
    """Strona startowa: zasady, czas, pozostałe podejścia – i przycisk rozpoczęcia (POST).

    Rozpoczęcie jest POST-em, nie odnośnikiem, i to nie jest formalność: GET ma być bezpieczny,
    a tutaj jedno wejście zużywa podejście i uruchamia licznik. Bez tego prefetch przeglądarki
    albo skaner linków w komunikatorze potrafiłby rozpocząć komuś zawody.
    """

    def get(self, request, stage_id: int):
        entry = self.entry(stage_id)
        quiz = self.quiz_of(entry)
        now = timezone.now()
        # Domknięcie **własnego** porzuconego podejścia przy wejściu: ktoś, kto zamknął kartę,
        # ma tu zobaczyć wynik albo kolejne podejście, a nie licznik sprzed godziny.
        quiz_services.finalise_overdue(quiz=quiz, now=now)
        active = quiz_services.active_attempt(quiz, entry)
        opens, closes = quiz.window
        return TemplateResponse(
            request,
            START_TEMPLATE,
            {
                "stage": entry.stage,
                "quiz": quiz,
                "entry": entry,
                "active": active,
                "attempts_left": quiz_services.attempts_left(quiz, entry),
                "is_open": quiz.is_open(now),
                "opens_at": opens,
                "closes_at": closes,
                "question_count": quiz.draw_size,
                # Bez wyniku: strona startowa wymienia podejścia i prowadzi do podsumowania, ale
                # o tym, czy wolno pokazać punkty, rozstrzyga ``may_show_result`` – raz, na stronie
                # podsumowania. Powtórzenie tej reguły tutaj byłoby drugą drogą, którą wynik
                # zawodów mógłby wyjść przed ogłoszeniem.
                "previous": list(quiz.attempts.filter(entry=entry).exclude(status=AttemptStatus.IN_PROGRESS)),
            },
        )

    def post(self, request, stage_id: int):
        entry = self.entry(stage_id)
        quiz = self.quiz_of(entry)
        try:
            attempt = quiz_services.start_attempt(quiz=quiz, entry=entry, request=request)
        except DomainError as exc:
            from django.contrib import messages

            messages.error(request, str(exc.detail))
            return redirect(reverse("web:quiz-start", args=[stage_id]))
        return redirect(reverse("web:quiz-attempt", args=[attempt.pk]))


def _attempt_context(attempt, *, now=None) -> dict:
    """Kontekst strony podejścia: pytania bez klucza, zapisane odpowiedzi i termin.

    Odpowiedzi wracają jako słownik ``{id pytania: payload}`` i to jest **cała** pamięć strony:
    po odświeżeniu (albo po powrocie na innym urządzeniu) formularz odtwarza się z tego, co
    zapisał autozapis. Bez tego autozapis byłby wyłącznie zabezpieczeniem na wypadek awarii,
    a nie tym, czym ma być – zapisem, do którego wolno wrócić.
    """
    questions = quiz_services.attempt_questions(attempt)
    answers = {answer.question_id: answer.payload for answer in attempt.answers.all()}
    rows = _public_questions(questions)
    for row in rows:
        payload = answers.get(row["id"]) or {}
        row["selected"] = set(payload.get("options") or []) if row["is_choice"] else set()
        row["value"] = payload.get("text") if row["is_text"] else payload.get("value")
    return {
        "attempt": attempt,
        "quiz": attempt.quiz,
        "stage": attempt.quiz.stage,
        "questions": rows,
        # Termin w ISO 8601 z przesunięciem strefy – licznik w przeglądarce czyta **tę** wartość,
        # a nie zegar urządzenia jako punkt odniesienia. Wartość jest jedynie informacją: o tym,
        # czy odpowiedź przyszła na czas, rozstrzyga serwer.
        "deadline_iso": attempt.deadline_at.isoformat(),
        "seconds_left": max(0, int((attempt.deadline_at - (now or timezone.now())).total_seconds())),
    }


class QuizAttemptView(_ParticipantQuizMixin, View):
    """Arkusz: wszystkie pytania na jednej stronie, przyklejony licznik, autozapis i „Zakończ”.

    Dlaczego jedna strona, a nie pytanie po pytaniu: strona z jednym pytaniem wymaga żądania
    między pytaniami, więc na słabym łączu każdy powrót do pytania drugiego kosztuje czas
    z licznika, a zerwane połączenie zostawia uczestnika w środku testu bez możliwości przejścia
    dalej. Jedna strona jest też jedyną postacią, która działa **bez JavaScriptu** – wtedy jest
    zwykłym formularzem z jednym przyciskiem na końcu, a autozapis po prostu nie działa.

    POST bez JavaScriptu obsługuje ta sama metoda: pola formularza mają nazwy ``q<id>``, więc
    komplet odpowiedzi przychodzi jednym żądaniem razem z „Zakończ”.
    """

    def get(self, request, attempt_id: int):
        attempt = self.own_attempt(attempt_id)
        now = timezone.now()
        if not attempt.accepts_answers_at(now) and attempt.is_open:
            quiz_services.expire_attempt(attempt, now=now)
        if not attempt.is_open:
            return redirect(reverse("web:quiz-result", args=[attempt.pk]))
        return TemplateResponse(request, ATTEMPT_TEMPLATE, _attempt_context(attempt, now=now))

    def post(self, request, attempt_id: int):
        attempt = self.own_attempt(attempt_id)
        from django.contrib import messages

        try:
            quiz_services.save_answers(attempt=attempt, answers=_answers_from_post(request.POST))
            quiz_services.submit_attempt(attempt=attempt)
        except DomainError as exc:
            # Jedyny powód, dla którego tu się trafia, to „czas minął”. Podejście jest już wtedy
            # domknięte razem z zapisanymi odpowiedziami, więc kierujemy na wynik – strona
            # z komunikatem i przyciskiem „spróbuj ponownie” nie miałaby czego ponawiać.
            messages.warning(request, str(exc.detail))
        return redirect(reverse("web:quiz-result", args=[attempt.pk]))


def _answers_from_post(data) -> dict:
    """Odpowiedzi z pól ``q<id>`` zwykłego formularza (ścieżka bez JavaScriptu).

    Kształt wyniku jest **taki sam**, jak ten, który przysyła autozapis, bo dalej obsługuje je
    jedna funkcja (``services.save_answers``). Rozpoznanie „wybór czy tekst” jest tutaj zbędne:
    wysyłamy oba klucze, a serwis i tak zostawia z nich tylko ten, który pasuje do rodzaju pytania.
    """
    answers: dict[str, dict] = {}
    for key in data:
        if not key.startswith("q") or not key[1:].isdigit():
            continue
        values = data.getlist(key)
        question_id = key[1:]
        answers[question_id] = {
            "options": [value for value in values if value.isdigit()],
            "text": values[0] if values else "",
            "value": values[0] if values else "",
        }
    return answers


class QuizAutosaveView(_ParticipantQuizMixin, View):
    """Autozapis odpowiedzi (POST, JSON). Odpowiada JSON-em, bo woła go ``static/js/quiz.js``.

    Kontrakt odpowiedzi jest celowo ubogi: ``{"saved": n, "seconds_left": s}`` przy powodzeniu
    i ``{"code": …, "detail": …}`` przy odmowie – ten sam kształt błędu, co w API (``apps.core.api``).
    Skrypt potrzebuje dokładnie dwóch rzeczy: potwierdzenia zapisu i wiedzy, kiedy przestać
    ponawiać. Wszystko ponadto byłoby danymi wysyłanymi co dwadzieścia sekund bez odbiorcy.
    """

    def post(self, request, attempt_id: int):
        attempt = self.own_attempt(attempt_id)
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"code": "INVALID_JSON", "detail": "Nieczytelne dane."}, status=400)
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return JsonResponse({"code": "INVALID_PAYLOAD", "detail": "Brak odpowiedzi."}, status=400)
        try:
            saved = quiz_services.save_answers(attempt=attempt, answers=answers)
        except DomainError as exc:
            return JsonResponse({"code": exc.machine_code, "detail": str(exc.detail)}, status=exc.status_code)
        return JsonResponse(
            {
                "saved": saved,
                "seconds_left": max(0, int((attempt.deadline_at - timezone.now()).total_seconds())),
            }
        )


class QuizResultView(_ParticipantQuizMixin, View):
    """Podsumowanie podejścia. Wynik pokazuje się wyłącznie zgodnie z ``show_results_after``."""

    def get(self, request, attempt_id: int):
        attempt = self.own_attempt(attempt_id)
        now = timezone.now()
        if attempt.is_open and not attempt.accepts_answers_at(now):
            quiz_services.expire_attempt(attempt, now=now)
        if attempt.is_open:
            return redirect(reverse("web:quiz-attempt", args=[attempt.pk]))
        quiz = attempt.quiz
        return TemplateResponse(
            request,
            RESULT_TEMPLATE,
            {
                "attempt": attempt,
                "quiz": quiz,
                "stage": quiz.stage,
                "show_score": quiz_services.may_show_result(quiz, attempt, now),
                "attempts_left": quiz_services.attempts_left(quiz, attempt.entry),
                "closes_at": quiz.window[1],
            },
        )
