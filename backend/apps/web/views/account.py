"""Własne konto: edycja danych, zmiana adresu e-mail, usunięcie konta (RODO).

Podział na dwa adresy jest celowy i wynika z tego, co dane **znaczą**:

- ``/me/profile/`` – dane uczestnika zgłoszonego do zawodów (imię, nazwisko, telefon, województwo,
  szkoła, klasa, rocznik). Ekran jest tylko dla uczestnika, bo tylko on ma profil, w którym te pola
  istnieją,
- ``/account/profile/`` – imię i nazwisko dla każdego zalogowanego konta (recenzent, komisja,
  koordynator). Województwa członka komitetu tu **nie ma**: potwierdzony okręg jest podstawą reguły
  konfliktu interesów przy przydziale recenzji, więc jego samoobsługowa zmiana byłaby obejściem tej
  reguły.

Zmiana adresu e-mail i usunięcie konta są wspólne dla wszystkich ról – adres jest loginem
niezależnie od roli, a prawo do usunięcia danych nie zależy od tego, kim ktoś jest w zawodach.

Reguł domenowych w tym module nie ma ani jednej: wszystkie są w ``apps.accounts.profile``, bo te
same operacje wołają ``PATCH /api/auth/me/`` i (w przyszłości) komendy zarządzające.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView

from apps.accounts.profile import (
    competition_footprint,
    confirm_email_change,
    delete_own_account,
    request_email_change,
    update_own_names,
    update_participant_profile,
    verify_self_deletion_credentials,
)
from apps.core.api import DomainError
from apps.web.forms import (
    AccountDeleteForm,
    AccountNamesForm,
    EmailChangeForm,
    ParticipantProfileForm,
    participant_profile_initial,
)
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin


class ServiceFormMixin:
    """Woła serwis domenowy i zamienia ``DomainError`` na błąd formularza.

    Odpowiednik ``apps.web.views.public.ServiceFormView``, ale jako domieszka: te widoki mają już
    własne bazy (mixiny ról, ``ThrottledFormMixin``) i dziedziczenie po tamtej klasie narzucałoby
    kolejność, w której limit stałby przed sprawdzeniem roli.
    """

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


class ParticipantProfileView(ParticipantRequiredMixin, ServiceFormMixin, FormView):
    """``/me/profile/`` – edycja własnych danych uczestnika."""

    template_name = "web/account/profile.html"
    form_class = ParticipantProfileForm
    success_url = reverse_lazy("web:me")
    success_message = "Dane zostały zapisane."

    def get_initial(self) -> dict:
        return participant_profile_initial(self.participant)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["participant"] = self.participant
        # Formularz zmiany adresu stoi na tej samej stronie, ale wysyła się pod własny adres:
        # to osobna operacja z osobnym potwierdzeniem, a nie kolejne pole tych danych.
        context.setdefault("email_form", EmailChangeForm())
        return context

    def call_service(self, form):
        update_participant_profile(
            self.participant, actor=self.request.user, request=self.request, **form.cleaned_data
        )


class AccountProfileView(LoginRequiredMixin, ServiceFormMixin, FormView):
    """``/account/profile/`` – imię i nazwisko dla konta bez profilu uczestnika.

    Uczestnika odsyłamy na jego pełny formularz: dwa ekrany zmieniające to samo imię, jeden
    z telefonem i szkołą, a drugi bez, byłyby zaproszeniem do pytania „na którym to się zapisuje”.
    """

    template_name = "web/account/profile.html"
    form_class = AccountNamesForm
    success_url = reverse_lazy("web:account-profile")
    success_message = "Dane zostały zapisane."

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and hasattr(request.user, "participant"):
            return redirect("web:profile")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self) -> dict:
        return {"first_name": self.request.user.first_name, "last_name": self.request.user.last_name}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["committee"] = getattr(self.request.user, "committee_member", None)
        context.setdefault("email_form", EmailChangeForm())
        return context

    def call_service(self, form):
        update_own_names(self.request.user, **form.cleaned_data, request=self.request)


class EmailChangeView(LoginRequiredMixin, ThrottledFormMixin, ServiceFormMixin, FormView):
    """``/account/email/`` – wniosek o zmianę adresu e-mail konta.

    Limit ze scope'em ``password_reset``: formularz wysyła list na adres podany przez użytkownika,
    więc bez ograniczenia byłby wysyłaczem wiadomości na cudze skrzynki – tak samo jak reset hasła,
    tylko za logowaniem (co samo nie jest ograniczeniem, bo konto zakłada się w minutę).
    """

    template_name = "web/account/email_change.html"
    form_class = EmailChangeForm
    throttle_scope = "password_reset"
    success_message = (
        "Wysłaliśmy link potwierdzający na nowy adres. Do czasu potwierdzenia logujesz się "
        "dotychczasowym adresem."
    )

    def get_success_url(self) -> str:
        return reverse("web:profile" if hasattr(self.request.user, "participant") else "web:account-profile")

    def call_service(self, form):
        request_email_change(
            self.request.user, new_email=form.cleaned_data["new_email"], request=self.request
        )


class EmailChangeConfirmView(TemplateView):
    """``/account/email/confirm/<token>/`` – potwierdzenie nowego adresu z listu.

    Widok jest **poza** logowaniem świadomie: link klika się w skrzynce, często na innym urządzeniu,
    a token sam w sobie dowodzi dostępu do nowej skrzynki i wskazuje konto. Wymuszanie logowania
    zamieniałoby potwierdzenie adresu w zadanie „zaloguj się starym adresem, którego właśnie
    zmieniasz”.
    """

    template_name = "web/account/email_changed.html"
    invalid_template_name = "web/account/email_change_invalid.html"
    error = ""

    def get(self, request, token: str, *args, **kwargs):
        try:
            confirm_email_change(token, request=request)
        except DomainError as exc:
            self.error = str(exc.detail)
        return self.render_to_response(self.get_context_data(**kwargs), status=400 if self.error else 200)

    def get_template_names(self) -> list[str]:
        return [self.invalid_template_name if self.error else self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["error"] = self.error
        return context


class AccountDeleteView(LoginRequiredMixin, ServiceFormMixin, FormView):
    """``/account/delete/`` – usunięcie własnego konta (art. 17 RODO).

    Strona GET **musi** powiedzieć, co się stanie, bo skutek zależy od historii konta: uczestnik,
    który brał udział w zawodach, dostaje anonimizację (dane osobowe znikają, pseudonimowy wiersz
    w wynikach zostaje), a konto bez śladu w dokumentacji znika w całości. Ukrycie tej różnicy za
    jednym przyciskiem „usuń” znaczyłoby, że część ludzi dowiaduje się o niej po fakcie.
    """

    template_name = "web/account/delete.html"
    form_class = AccountDeleteForm
    success_url = reverse_lazy("web:account-deleted")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        footprint = competition_footprint(self.request.user)
        context.update(
            {
                "footprint": footprint,
                # ``True`` = zostanie anonimizacja, ``False`` = skasowanie wiersza. Nazwa mówi
                # o skutku, nie o implementacji, bo to ona stoi w treści strony.
                "keeps_pseudonymous_row": any(footprint.values()),
                "needs_password": self.request.user.has_usable_password(),
                "account_email": self.request.user.email,
            }
        )
        return context

    def call_service(self, form):
        user = self.request.user
        verify_self_deletion_credentials(
            user, password=form.cleaned_data.get("password") or "", email=form.cleaned_data.get("email") or ""
        )
        delete_own_account(user, request=self.request)
        # Wylogowanie **po** usunięciu: sesja wskazuje na konto, którego albo nie ma, albo nie wolno
        # już w nim nic zrobić. ``delete_own_account`` kasuje sesje z bazy, ale tę bieżącą trzyma
        # jeszcze middleware w pamięci żądania.
        logout(self.request)


class AccountDeletedView(TemplateView):
    """Strona po usunięciu konta. Osiągalna bez logowania – konta, którym by się zalogować, już nie ma."""

    template_name = "web/account/deleted.html"
