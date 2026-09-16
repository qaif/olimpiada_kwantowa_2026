"""Panel uczestnika ``/me/``.

Zakres (T-08): bieżący etap i odliczanie do deadline'u liczone z czasu **serwera**, rejestracja do
eliminacji, zadania z treścią PDF, upload per zadanie przez HTMX z historią wersji i statusem
antywirusa, własne wyniki po publikacji oraz reklamacja w oknie odwoławczym.

Wszystkie reguły (deadline, okno reklamacji, widoczność wyników) są egzekwowane w serwisach –
tutaj są wyłącznie po to, żeby nie pokazywać formularza, którego serwis i tak by nie przyjął.
"""

from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.accounts.consents import CONSENTS, ConsentKind, ConsentSource, labels
from apps.accounts.services import consents_for_participant, set_publish_name_consent
from apps.appeals.services import appealable_submissions, appeals_for_participant, file_appeal
from apps.competitions.interviews import (
    book_slot,
    booking_for_participant,
    cancel_booking,
    slots_for_participant,
)
from apps.competitions.models import InterviewSlot, Problem, Stage, StageEntry
from apps.competitions.services import (
    SELF_REGISTRATION_KINDS,
    current_edition,
    current_stage,
    register_for_stage,
    training_stage,
)
from apps.core.api import DomainError
from apps.results.services import published_results, results_for_participant
from apps.submissions.services import (
    UNDER_REVIEW_STATUSES,
    create_submission,
    submissions_for_user,
)
from apps.submissions.status_track import status_track
from apps.web.forms import AppealForm, SubmissionUploadForm
from apps.web.mixins import ActionViewMixin, ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin


def _entry_for(participant, stage: Stage | None) -> StageEntry | None:
    if stage is None:
        return None
    return StageEntry.objects.filter(participant=participant, stage=stage).first()


def _under_review(versions: list) -> bool:
    """Czy najnowsza wersja pracy jest już w ocenie – podstawa ostrzeżenia przy uploadzie.

    Odkąd koordynator może wciągnąć prace do oceniania przed zamknięciem etapu
    (``submissions.services.lock_for_review``), uczestnik z otwartym oknem uploadu może mieć pracę
    już czytaną przez komitet. Wysłanie nowej wersji jest wtedy nadal dozwolone, ale kasuje
    dotychczasową ocenę – i to musi być widoczne **przed** kliknięciem, a nie dopiero w historii.

    Lista wersji przychodzi posortowana malejąco (``submissions_for_user``), więc pierwsza jest
    najnowsza. Stany bierzemy z jednej listy w serwisie, żeby panel i reguła unieważniania
    (``grading.services.supersede_earlier_versions``) nie mogły się rozjechać.
    """
    return bool(versions) and versions[0].status in UNDER_REVIEW_STATUSES


def _problem_rows(user, entry: StageEntry | None) -> list[dict]:
    """Zadania etapu wraz z własnymi wersjami rozwiązań (najnowsza pierwsza).

    Rozwiązania biorą się z ``Submission.objects.for_user`` – filtr roli siedzi w queryseckie,
    a nie w tym widoku (PROJEKT.md 2.3).
    """
    if entry is None:
        return []
    problems = list(Problem.objects.filter(stage=entry.stage).order_by("number", "id"))
    versions: dict[int, list] = defaultdict(list)
    for submission in submissions_for_user(user).filter(entry=entry):
        versions[submission.problem_id].append(submission)
    # Publikacja etapu jest jedna na całą listę zadań, więc czytamy ją **raz**: w środku pętli
    # byłaby jednym zapytaniem na zadanie, czyli N+1 na każdym wejściu do panelu.
    publication = published_results(entry.stage_id)
    return [
        {
            "problem": problem,
            "versions": versions.get(problem.pk, []),
            "under_review": _under_review(versions.get(problem.pk, [])),
            "track": _track_for(versions.get(problem.pk, []), entry.stage, publication),
        }
        for problem in problems
    ]


def _track_for(versions: list, stage: Stage, publication) -> object:
    """Ścieżka „oddane → w ocenie → oceniona → wyniki” dla **najnowszej** wersji pracy.

    Lista wersji przychodzi posortowana malejąco (``submissions_for_user``), więc pierwsza jest
    najnowsza – i to ona wyznacza status, bo tylko ona idzie do oceniania. Regułę mapowania
    trzyma ``apps.submissions.status_track``; tutaj zostaje samo wybranie wersji.
    """
    return status_track(submission=versions[0] if versions else None, stage=stage, publication=publication)


def _consent_rows(participant) -> list[dict]:
    """Zgody uczestnika do pokazania w panelu: po jednym wierszu na rodzaj, stan najświeższy.

    Historia w bazie bywa dłuższa niż jeden wpis na rodzaj (zgoda wycofana i wyrażona ponownie),
    ale panel odpowiada na pytanie „co obowiązuje teraz i od kiedy”. Wpisy przychodzą posortowane
    malejąco po dacie (``ConsentRecord.Meta.ordering``), więc pierwszy napotkany jest najnowszy.

    Rodzaje bez ani jednego wpisu też są na liście: profil sprzed wprowadzenia zestawu zgód ma
    tylko projekcje na ``Participant`` i uczestnik ma prawo zobaczyć, że dowodu nie ma, zamiast
    domyślać się z pustej listy.
    """
    latest: dict[str, object] = {}
    for record in consents_for_participant(participant):
        latest.setdefault(record.kind, record)
    texts = labels()
    return [
        {
            "kind": consent.kind,
            "name": ConsentKind(consent.kind).label,
            "label": texts[consent.kind],
            "version": consent.version,
            "optional": consent.is_optional,
            "record": latest.get(consent.kind),
        }
        for consent in CONSENTS
    ]


def _problem_row(user, entry: StageEntry, problem: Problem) -> dict:
    """Jedna karta zadania – odpowiedź HTMX po uploadzie. Kształt musi być ten sam, co w pulpicie.

    Ścieżka oceniania jest tu liczona tak samo jak w ``_problem_rows``: karta wracająca po
    wysyłce ma pokazać krok „oddane” od razu, a nie dopiero po przeładowaniu całej strony.
    """
    versions = list(submissions_for_user(user).filter(entry=entry, problem=problem))
    return {
        "problem": problem,
        "versions": versions,
        "under_review": _under_review(versions),
        "track": _track_for(versions, entry.stage, published_results(entry.stage_id)),
    }


class MeView(ParticipantRequiredMixin, TemplateView):
    """Pulpit uczestnika."""

    template_name = "web/participant/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()
        edition = current_edition()
        stage = current_stage(edition, now) if edition else None
        entry = _entry_for(self.participant, stage)
        context.update(
            {
                "now": now,
                "edition": edition,
                "stage": stage,
                "entry": entry,
                # Ta sama lista rodzajów, na której stoi ``register_for_stage`` – widok tylko
                # ukrywa przycisk, którego serwis i tak by nie przyjął. Gdyby powtarzał tu regułę
                # własnym warunkiem (``kind == ELIM``), dołożenie treningu do zapisów otwartych
                # zmieniłoby serwis, a przycisk zostałby ukryty.
                "can_register": (
                    stage is not None
                    and entry is None
                    and stage.kind in SELF_REGISTRATION_KINDS
                    and stage.is_open_for_submissions(now)
                ),
                "stage_opened": stage is not None and stage.has_opened(now),
                # Etap w formie rozmowy nie ma uploadu w ogóle – nie „zamkniętego”, tylko żadnego
                # (``submissions.create_submission`` odmawia z ``STAGE_NOT_ACCEPTING_FILES``).
                "upload_open": (
                    entry is not None
                    and not stage.is_interview
                    and stage.is_open_for_submissions(now)
                    and stage.closed_at is None
                ),
                "upload_form": SubmissionUploadForm(),
                "problem_rows": _problem_rows(user, entry),
                # Terminy rozmów liczymy tylko dla etapu w formie rozmowy: w pozostałych obie
                # wartości byłyby pustą listą i ``None``, a zapytania i tak by poszły.
                "interview_rows": (
                    slots_for_participant(stage, self.participant, now)
                    if stage is not None and stage.is_interview
                    else []
                ),
                "interview_booking": (
                    booking_for_participant(stage, self.participant)
                    if stage is not None and stage.is_interview
                    else None
                ),
                "results": results_for_participant(user),
                # Reguła „co podlega reklamacji” mieszka w serwisie reklamacji, nie w widoku –
                # ten sam predykat obowiązuje w API i przy walidacji w ``file_appeal``.
                "appealable": appealable_submissions(user, now),
                "appeal_form": AppealForm(),
                "my_appeals": list(appeals_for_participant(user)),
                "consent_rows": _consent_rows(self.participant),
                "publish_name_kind": ConsentKind.PUBLISH_NAME,
            }
        )
        context.update(self._training_context(user, edition, now))
        return context

    def _training_context(self, user, edition, now) -> dict:
        """Etap treningowy jako **druga**, niezależna karta pulpitu.

        Trening nie może przyjść z ``current_stage`` (ta funkcja go pomija – inaczej piaskownica
        bez terminu zostawałaby „etapem bieżącym” w każdej przerwie między zawodami), a jest
        jedynym miejscem, w którym uczestnik przejdzie całą ścieżkę zgłoszenie → upload → wyniki
        poza zawodami. Dlatego pulpit liczy dla niego ten sam komplet wartości, co dla etapu
        zawodów, i renderuje tym samym ``_problem_card.html``: karta uploadu, która zachowuje się
        „prawie jak prawdziwa”, nie nauczyłaby niczego o tej prawdziwej.

        Rozmów tu nie ma: trening jest z definicji etapem oddawania plików
        (``StageFormat.SUBMISSIONS``), więc żadnej gałęzi ``is_interview`` ta karta nie potrzebuje.
        """
        stage = training_stage(edition)
        if stage is None:
            return {"training_stage": None}
        entry = _entry_for(self.participant, stage)
        return {
            "training_stage": stage,
            "training_entry": entry,
            "training_can_register": entry is None and stage.is_open_for_submissions(now),
            "training_upload_open": (
                entry is not None and stage.is_open_for_submissions(now) and stage.closed_at is None
            ),
            "training_problem_rows": _problem_rows(user, entry),
        }


class ConsentPublishNameView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Wyrażenie albo wycofanie zgody na publikację imienia i nazwiska w tabelach wyników.

    Jedyna zgoda odwracalna z poziomu portalu – uzasadnienie w
    ``accounts.services.set_publish_name_consent``. Wartość przychodzi **jawnie** w polu ``given``
    (``1``/``0``), a nie jako „odwróć bieżący stan”: dwa kliknięcia w tę samą stronę (podwójne
    wysłanie formularza, powrót „wstecz”) muszą dawać ten sam wynik, a nie przełączać zgodę tam
    i z powrotem.
    """

    success_url = reverse_lazy("web:me")

    def perform(self, request) -> str:
        given = request.POST.get("given") == "1"
        set_publish_name_consent(self.participant, given=given, source=ConsentSource.PANEL, request=request)
        return (
            "Zgoda na publikację imienia i nazwiska została zapisana."
            if given
            else "Zgoda na publikację imienia i nazwiska została wycofana."
        )


class StageRegisterView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Rejestracja do etapu eliminacyjnego (``competitions.services.register_for_stage``)."""

    success_url = reverse_lazy("web:me")

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        register_for_stage(self.participant, stage)
        return "Zgłoszenie do etapu zostało przyjęte."


class InterviewBookView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Zapis na termin rozmowy albo zmiana już wybranego (``competitions.interviews.book_slot``).

    Jedna akcja na oba przypadki, bo z punktu widzenia uczestnika to jedno kliknięcie: „chcę ten
    termin”. Rozstrzygnięcie, czy to nowy zapis, czy przeniesienie, należy do serwisu – razem
    z blokadą wiersza terminu, bez której dwa równoległe kliknięcia zajęłyby jedno miejsce.
    """

    success_url = reverse_lazy("web:me")

    def perform(self, request, slot_id: int) -> str:
        slot = get_object_or_404(InterviewSlot.objects.select_related("stage", "stage__edition"), pk=slot_id)
        book_slot(self.participant, slot, request=request)
        return "Termin rozmowy został zapisany. Potwierdzenie wysyłamy e-mailem."


class InterviewCancelView(ActionViewMixin, ParticipantRequiredMixin, View):
    """Rezygnacja z zapisanego terminu rozmowy w etapie."""

    success_url = reverse_lazy("web:me")

    def perform(self, request, stage_id: int) -> str:
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        cancel_booking(self.participant, stage=stage, request=request)
        return "Termin rozmowy został odwołany. Możesz wybrać inny."


class ProblemUploadView(ParticipantRequiredMixin, ThrottledFormMixin, View):
    """Upload rozwiązania jednego zadania. Odpowiedź HTMX to odświeżona karta zadania.

    Deadline i walidację pliku (rozmiar, magic bytes, formaty) egzekwuje
    ``submissions.services.create_submission`` – widok nie powtarza ani jednej z tych reguł.

    Kolejność mixinów: najpierw rola (anonim dostaje 302, obcy 403), dopiero potem limit –
    licznik uploadów nie ma się zapełniać żądaniami, które i tak nie wchodzą do widoku.
    Scope ``upload`` jest ten sam, co w ``POST /api/submissions/``.
    """

    template_name = "web/participant/_problem_card.html"
    throttle_scope = "upload"

    def post(self, request, stage_id: int, number: int):
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        entry = get_object_or_404(StageEntry, participant=self.participant, stage=stage)
        problem = get_object_or_404(Problem, stage=stage, number=number)
        form = SubmissionUploadForm(request.POST, request.FILES)
        error = None
        if form.is_valid():
            try:
                create_submission(
                    user=request.user,
                    stage=stage,
                    problem_number=number,
                    upload=form.cleaned_data["file"],
                    request=request,
                )
            except DomainError as exc:
                error = str(exc.detail)
        else:
            error = " ".join(message for messages_ in form.errors.values() for message in messages_)
        now = timezone.now()
        context = {
            "row": _problem_row(request.user, entry, problem),
            "stage": stage,
            "entry": entry,
            "now": now,
            "upload_open": stage.is_open_for_submissions(now) and stage.closed_at is None,
            "upload_form": SubmissionUploadForm(),
            "error": error,
        }
        return TemplateResponse(request, self.template_name, context)


class AppealCreateView(ParticipantRequiredMixin, View):
    """Złożenie reklamacji na własne rozwiązanie (``appeals.services.file_appeal``)."""

    def post(self, request, submission_id: int):
        submission = get_object_or_404(submissions_for_user(request.user), pk=submission_id)
        form = AppealForm(request.POST)
        if not form.is_valid():
            messages.error(request, " ".join(form.errors.get("argument", ["Nieprawidłowe uzasadnienie."])))
            return redirect(reverse("web:me"))
        try:
            file_appeal(request.user, submission, form.cleaned_data["argument"], request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Reklamacja została złożona.")
        return redirect(reverse("web:me"))
