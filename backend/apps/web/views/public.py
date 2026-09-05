"""Strony publiczne: logowanie, rejestracja, ogłoszone wyniki.

Strona główna (``/``) należy od T-09 do Wagtaila (``apps.cms.models.HomePage``) – tutaj nie ma już
widoku ``home``. Adres ``/`` jest w kodzie zapisany dosłownie, bo wyznacza go korzeń witryny
Wagtaila, a nie wpis w ``urls.py``.

Wszystkie treści od użytkowników (nazwy szkół, etykiety w tabeli wyników) renderują się
z domyślnym autoescapowaniem Django. W żadnym szablonie nie ma ``|safe`` ani ``mark_safe``.

Logowanie i obie rejestracje są objęte limitem żądań identycznym z tym na endpointach API –
patrz ``apps.web.throttle``. Formularz HTML robi to samo, co ``POST /api/auth/…``, więc limit
tylko po stronie DRF byłby obejściem długości jednego adresu URL.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import LogoutView as DjangoLogoutView
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from apps.accounts.services import register_committee, register_participant
from apps.core.api import DomainError
from apps.results.services import published_results
from apps.web.context_processors import roles
from apps.web.forms import (
    CommitteeRegisterForm,
    EmailAuthenticationForm,
    ParticipantRegisterForm,
)
from apps.web.throttle import ThrottledFormMixin


class LoginView(ThrottledFormMixin, DjangoLoginView):
    """Logowanie sesyjne (Django auth). Loginem jest adres e-mail.

    Limit (scope ``login``) liczy **wyłącznie nieudane** próby: udane logowanie kasuje licznik.
    Inaczej ucierpiałby ten, kto po prostu często się loguje, a nie ten, kto zgaduje hasło.
    """

    template_name = "web/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True
    throttle_scope = "login"
    throttle_on_request = False

    def form_valid(self, form):
        self.reset_throttle()
        return super().form_valid(form)

    def form_invalid(self, form):
        self.consume_throttle()
        return super().form_invalid(form)

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
        return "/"


class LogoutView(DjangoLogoutView):
    """Wylogowanie. Wyłącznie POST – wylogowanie GET-em byłoby podatne na CSRF przez ``<img>``."""

    next_page = "/"


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


class RegisterParticipantView(ThrottledFormMixin, ServiceFormView):
    """Rejestracja otwarta uczestnika – cała logika w ``accounts.services.register_participant``."""

    template_name = "web/register.html"
    form_class = ParticipantRegisterForm
    success_url = reverse_lazy("web:login")
    success_message = "Konto uczestnika zostało założone. Zaloguj się."
    # Tu liczy się każdy POST, także udany: limit ma powstrzymać seryjne zakładanie kont.
    throttle_scope = "register"

    def call_service(self, form):
        register_participant(**form.cleaned_data)


class RegisterCommitteeView(ThrottledFormMixin, ServiceFormView):
    """Rejestracja członka komitetu na kod zaproszenia.

    Ten sam scope co rejestracja otwarta – limit chroni tu dodatkowo przed zgadywaniem kodu
    zaproszenia, bo każda próba użycia kodu przechodzi przez ten formularz.
    """

    template_name = "web/register_committee.html"
    form_class = CommitteeRegisterForm
    success_url = reverse_lazy("web:login")
    throttle_scope = "register"
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
