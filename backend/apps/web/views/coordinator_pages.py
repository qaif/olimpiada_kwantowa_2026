"""Ekrany, które wyprowadziły się z pulpitu koordynatora.

Pulpit odpowiadał dotąd na wszystkie pytania naraz: był listą etapów, kolejką moderacji, kolejką
aktywacji, ekranem komitetu, generatorem zaproszeń i podglądem wyników w jednym przewijaniu.
Skutek był taki, że **żadne** z tych zadań nie miało własnego adresu – nie dało się wysłać
odnośnika „zatwierdź tych trzech członków komisji” ani wrócić do miejsca, w którym się było.

Tutaj każda z tych spraw dostaje własną stronę, a pulpit zostaje przy jednym pytaniu: „co wymaga
uwagi”. Żadna reguła domenowa się przy tym nie przeniosła: **akcje zostały tam, gdzie były**
(``coordinator.py``), razem ze swoimi adresami, formularzami i wpisami audytowymi. Te widoki
wyłącznie pokazują – i dlatego wszystkie są ``TemplateView`` z jednym ``GET``.

Powrót po akcji rozstrzyga ``CoordinatorActionView.get_success_url``: akcja wywołana stąd wraca
tutaj (nagłówek ``Referer`` sprawdzany pod kątem tego samego serwera i przedrostka panelu), a nie
na pulpit, z którego już jej nie ma.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.accounts.activation import ACTIVATION_HOURS
from apps.accounts.models import CommitteeMember, CommitteeStatus
from apps.competitions.models import Stage
from apps.grading.comparison import notes_by_submission
from apps.grading.services import moderation_queue, reviewer_pool
from apps.results.models import ResultsPublication
from apps.web.coordinator_nav import attention_counters, current_stages, focus_stage, resolve
from apps.web.coordinator_search import MIN_QUERY_LENGTH, search
from apps.web.forms import (
    VOIVODESHIP_CHOICES,
    AssignThirdReviewerForm,
    BulkInvitationForm,
    InvitationForm,
    PublishResultsForm,
    ResolveModerationForm,
    VerifyDistrictForm,
)
from apps.web.mixins import CoordinatorRequiredMixin


class CoordinatorSearchView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/search/?q=`` – jedno pole na uczestników, komisję, zadania i etapy.

    Formularz stoi na górze menu, więc ten ekran jest dostępny z każdego miejsca panelu. Wynik
    jest pogrupowany po rodzaju obiektu, bo ta sama fraza („Kowalski”) trafia zwykle w kilka
    rodzajów naraz, a koordynator wie, którego szuka.
    """

    template_name = "web/coordinator/search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get("q") or "").strip()
        groups = search(query)
        context.update(
            {
                "query": query,
                "groups": groups,
                "total": sum(len(group.hits) for group in groups),
                "too_short": 0 < len(query) < MIN_QUERY_LENGTH,
                "min_length": MIN_QUERY_LENGTH,
            }
        )
        return context


class CoordinatorCommitteeView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/committee/`` – wszystko o składzie komisji w jednym miejscu.

    Cztery sprawy, które dotąd stały w czterech sekcjach pulpitu, a robi się je jedna po drugiej:
    zatwierdzenie zgłoszeń, województwa (konflikt interesów na etapie wojewódzkim), kod
    zaproszenia i zaproszenia e-mailem razem z listą wysłanych.

    Kotwice ``#zaproszenia`` i ``#wojewodztwa`` są celem pozycji menu – żeby „Zaproszenia”
    prowadziło do zaproszeń, a nie na górę długiej strony.
    """

    template_name = "web/coordinator/committee.html"

    def get_context_data(self, **kwargs):
        from apps.web.views.coordinator import sent_invitation_rows

        context = super().get_context_data(**kwargs)
        context.update(
            {
                "pending_members": list(
                    CommitteeMember.objects.select_related("user")
                    .filter(status=CommitteeStatus.PENDING)
                    .order_by("created_at", "id")
                ),
                "active_members": list(
                    CommitteeMember.objects.select_related("user")
                    .filter(status=CommitteeStatus.ACTIVE)
                    .order_by("user__email")
                ),
                "voivodeship_choices": VOIVODESHIP_CHOICES,
                "verify_form": VerifyDistrictForm(),
                "invitation_form": InvitationForm(),
                "bulk_invitation_form": BulkInvitationForm(),
                "sent_invitations": sent_invitation_rows(),
            }
        )
        return context


class CoordinatorActivationsView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/activations/`` – konta, które nie potwierdziły adresu e-mail.

    Ekran jest **obejściem operacyjnym z terminem ważności** (README § 4.2): dopóki domena
    nadawcy nie ma poprawnych rekordów SPF/DKIM, część listów aktywacyjnych nie dociera, a bez
    potwierdzonego adresu nikt się nie zaloguje. Kolejka ma własny adres, bo obsługuje się ją
    seriami – zwykle po telefonie ze szkoły.
    """

    template_name = "web/coordinator/activations.html"

    def get_context_data(self, **kwargs):
        from apps.web.views.coordinator import pending_activation_rows

        context = super().get_context_data(**kwargs)
        context.update({"rows": pending_activation_rows(), "activation_hours": ACTIVATION_HOURS})
        return context


class CoordinatorModerationView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/moderation/`` – rozjazdy ocen czekające na rozstrzygnięcie.

    Kolejka zeszła z pulpitu, bo jest najdłuższą jego częścią i jedyną, przy której koordynator
    siedzi dłużej niż minutę: każda praca to tabela ocen, wątek notatek recenzentów i dwa
    formularze. Na pulpicie zostaje sama liczba i odnośnik tutaj.
    """

    template_name = "web/coordinator/moderation.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queue = list(moderation_queue())
        context.update(
            {
                "moderation": queue,
                # Notatki recenzentów jednym zapytaniem na cały ekran – przy pracy w moderacji
                # są materiałem do decyzji, a nie ciekawostką.
                "moderation_notes": notes_by_submission(queue),
                "reviewer_pool": reviewer_pool(),
                "resolve_form": ResolveModerationForm(),
                "assign_third_form": AssignThirdReviewerForm(),
            }
        )
        return context


class CoordinatorStageResultsView(CoordinatorRequiredMixin, TemplateView):
    """``GET /coordinator/stages/<id>/results/`` – wyniki etapu: przeliczenie i publikacja.

    Wejście na stronę **niczego nie liczy**. Przeliczenie zapisuje sumy punktów wpisów
    (``results.services.compute_stage_results``) i odmawia, gdy któraś praca jest jeszcze
    w ocenianiu – a to jest zmiana stanu i decyzja, więc należy do przycisku (POST), a nie do
    otwarcia adresu. Podgląd pojawia się dopiero po kliknięciu i jest renderowany przez
    ``ComputeResultsView`` tym samym szablonem.
    """

    template_name = "web/coordinator/results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stage = get_object_or_404(
            Stage.objects.select_related("edition", "qualification_rule"), pk=self.kwargs["stage_id"]
        )
        context.update(stage_results_context(stage))
        return context


def stage_results_context(stage: Stage, rows: list[dict] | None = None) -> dict:
    """Kontekst ekranu wyników – wspólny dla wejścia (GET) i podglądu po przeliczeniu (POST)."""
    return {
        "stage": stage,
        # ``OneToOneField``, więc publikacja jest co najwyżej jedna – ``filter().first()`` zamiast
        # dostępu przez relację, bo etap bez publikacji ma rzucić ``None``, a nie wyjątek.
        "publication": ResultsPublication.objects.filter(stage=stage).first(),
        "publish_form": PublishResultsForm(),
        "preview": {"stage": stage, "rows": rows} if rows is not None else None,
    }


def attention_rows() -> list[dict]:
    """Kafelki „co wymaga uwagi” na pulpicie: liczba, podpis i adres kolejki.

    Źródłem liczb jest ten sam moduł, co dla badge'ów w menu (``coordinator_nav``) – pulpit
    i menu nie mogą pokazać dwóch różnych odpowiedzi na to samo pytanie, a liczniki są liczone
    raz na minutę dla całego panelu.

    Reklamacje i zaległe recenzje dochodzą tutaj, bo są tego samego rodzaju sprawą („ktoś czeka”),
    choć nie stoją w menu: reklamacje mają własny panel komisji odwoławczej, a zaległe recenzje
    czyta się na postępie konkretnego etapu.

    „Zaległe” znaczy **po terminie** (``Review.due_at`` w przeszłości), a nie „nieskończone”:
    recenzja przydzielona wczoraj z terminem za tydzień nie jest sprawą, którą ktokolwiek ma się
    dziś zająć, a kafelek liczący ją razem z resztą nigdy nie pokazywałby zera.
    """
    from django.utils import timezone

    from apps.appeals.models import Appeal, AppealStatus
    from apps.grading.models import Review, ReviewStatus

    counters = attention_counters()
    stages = current_stages()
    stage_ids = [stage.pk for stage in stages]
    progress = focus_stage(stages)
    overdue_reviews = Review.objects.filter(
        submission__entry__stage_id__in=stage_ids,
        status__in=(ReviewStatus.ASSIGNED, ReviewStatus.DRAFT),
        due_at__lt=timezone.now(),
    ).count()
    open_appeals = Appeal.objects.filter(
        submission__entry__stage_id__in=stage_ids, status=AppealStatus.OPEN
    ).count()
    rows = [
        {
            "key": "moderation",
            "value": counters["moderation"],
            "label": "Prace w moderacji",
            "hint": "Rozjazd ocen czeka na rozstrzygnięcie.",
            "url_names": ("web:coordinator-moderation",),
        },
        {
            "key": "activations",
            "value": counters["activations"],
            "label": "Konta do aktywacji",
            "hint": "Adres e-mail niepotwierdzony – konto się nie zaloguje.",
            "url_names": ("web:coordinator-activations",),
        },
        {
            "key": "committee",
            "value": counters["committee"],
            "label": "Komitet do zatwierdzenia",
            "hint": "Zgłoszenia do komisji czekają na decyzję.",
            "url_names": ("web:coordinator-committee",),
        },
        {
            "key": "issues",
            "value": counters["issues"],
            "label": "Zgłoszone problemy",
            "hint": "Recenzent sygnalizuje kłopot z pracą.",
            "url_names": ("web:coordinator-issues",),
        },
        {
            "key": "tickets",
            "value": counters["tickets"],
            "label": "Zgłoszenia do organizatora",
            "hint": "Pytania i kłopoty zgłoszone przez ludzi – także bez konta.",
            "url_names": ("web:coordinator-support",),
        },
        {
            "key": "appeals",
            "value": open_appeals,
            "label": "Otwarte reklamacje",
            "hint": "Uczestnik zakwestionował ocenę.",
            "url_names": ("web:appeals",),
        },
        {
            "key": "reviews",
            "value": overdue_reviews,
            "label": "Zaległe recenzje",
            "hint": "Termin recenzji minął, oceny nadal nie ma.",
            "url_names": ("web:coordinator-stage-progress",),
            "args": (progress.pk,) if progress is not None else None,
        },
    ]
    # Adres rozwiązujemy tutaj, a nie w szablonie: kafelek, którego ekranu nie ma (etapu jeszcze
    # nie założono, panel reklamacji wyłączono), przestaje być odnośnikiem i zostaje samą liczbą.
    for row in rows:
        args = row.pop("args", None)
        row["url"] = resolve(row.pop("url_names"), args or ()) or ""
    return rows
