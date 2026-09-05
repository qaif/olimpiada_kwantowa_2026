"""Strony publiczne: strona główna, logowanie, rejestracja, ogłoszone wyniki.

Wszystkie treści od użytkowników (nazwy szkół, etykiety w tabeli wyników) renderują się
z domyślnym autoescapowaniem Django. W żadnym szablonie nie ma ``|safe`` ani ``mark_safe``.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import LogoutView as DjangoLogoutView
from django.http import Http404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from apps.accounts.services import register_committee, register_participant
from apps.competitions.services import current_edition, current_stage
from apps.core.api import DomainError
from apps.results.models import ResultsPublication
from apps.results.services import published_results
from apps.web.context_processors import roles
from apps.web.forms import (
    CommitteeRegisterForm,
    EmailAuthenticationForm,
    ParticipantRegisterForm,
)


class HomeView(TemplateView):
    """Strona główna: bieżąca edycja, oś czasu etapów i linki do ogłoszonych wyników."""

    template_name = "web/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        edition = current_edition()
        stages = list(edition.stages.order_by("opens_at", "id")) if edition else []
        published = set(
            ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True)
        )
        context.update(
            {
                "edition": edition,
                "now": now,
                "current_stage": current_stage(edition, now) if edition else None,
                "stage_rows": [
                    {
                        "stage": stage,
                        "is_open": stage.is_open_for_submissions(now),
                        "has_results": stage.pk in published,
                    }
                    for stage in stages
                ],
            }
        )
        return context


class LoginView(DjangoLoginView):
    """Logowanie sesyjne (Django auth). Loginem jest adres e-mail."""

    template_name = "web/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self) -> str:
        """Po zalogowaniu bez ``next`` – panel właściwy dla roli, a nie zawsze ``/me/``.

        Bez tego recenzent i koordynator lądowali na panelu uczestnika, czyli od razu na 403.
        Parametr ``next`` (obsługiwany przez ``get_redirect_url``) ma pierwszeństwo i jest
        walidowany przez Django, więc otwarte przekierowanie nie wchodzi w grę.
        """
        context = roles(self.request)
        for flag, name in (
            ("is_participant", "web:me"),
            ("is_reviewer", "web:review-list"),
            ("is_coordinator", "web:coordinator"),
            ("is_appeals_committee", "web:appeals"),
        ):
            if context.get(flag):
                return str(reverse_lazy(name))
        return str(reverse_lazy("web:home"))


class LogoutView(DjangoLogoutView):
    """Wylogowanie. Wyłącznie POST – wylogowanie GET-em byłoby podatne na CSRF przez ``<img>``."""

    next_page = reverse_lazy("web:home")


class ServiceFormView(FormView):
    """``FormView``, który woła serwis domenowy i zamienia ``DomainError`` na błąd formularza."""

    success_message = ""

    def call_service(self, form):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def form_valid(self, form):
        try:
            self.call_service(form)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.form_invalid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return super().form_valid(form)


class RegisterParticipantView(ServiceFormView):
    """Rejestracja otwarta uczestnika – cała logika w ``accounts.services.register_participant``."""

    template_name = "web/register.html"
    form_class = ParticipantRegisterForm
    success_url = reverse_lazy("web:login")
    success_message = "Konto uczestnika zostało założone. Zaloguj się."

    def call_service(self, form):
        register_participant(**form.cleaned_data)


class RegisterCommitteeView(ServiceFormView):
    """Rejestracja członka komitetu na kod zaproszenia."""

    template_name = "web/register_committee.html"
    form_class = CommitteeRegisterForm
    success_url = reverse_lazy("web:login")
    success_message = (
        "Konto zostało założone. Jeśli kod wymagał zatwierdzenia, poczekaj na decyzję koordynatora."
    )

    def call_service(self, form):
        register_committee(**form.cleaned_data)


class PublicResultsView(TemplateView):
    """Ogłoszona tabela wyników etapu – wyłącznie z zamrożonego snapshotu, bez logowania.

    Etap bez publikacji to 404: dla publiczności taka tabela nie istnieje, nawet jeśli etap
    istnieje w bazie (ta sama reguła, co w ``GET /api/public/results/{stage_id}/``).
    """

    template_name = "web/results.html"

    def get_context_data(self, stage_id: int, **kwargs):
        context = super().get_context_data(**kwargs)
        publication = published_results(stage_id)
        if publication is None:
            raise Http404("Wyniki tego etapu nie zostały ogłoszone.")
        rows = publication.rows
        problem_numbers = sorted(
            {key for row in rows for key in (row.get("points") or {})},
            key=lambda value: (len(value), value),
        )
        context.update(
            {
                "publication": publication,
                "stage": publication.stage,
                "rows": rows,
                "problem_numbers": problem_numbers,
            }
        )
        return context
