"""Mixiny ról dla widoków HTML.

Reguła dostępu jest ta sama, co w klasach uprawnień DRF (``apps.accounts.permissions``) – mixiny
wołają dokładnie te helpery, żeby panel i API nie mogły się rozjechać. Zachowanie kontraktowe
(T-08, kryteria 1–2):

- niezalogowany → 302 na ``/login/?next=…`` (``AccessMixin.handle_no_permission``),
- zalogowany w złej roli → 403 (``PermissionDenied``).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from apps.accounts.models import GROUP_APPEALS, GROUP_COORDINATOR, GROUP_PARTICIPANT
from apps.accounts.services import active_reviewer_profile
from apps.appeals.services import appeals_committee_profile
from apps.core.api import DomainError


def _in_group(user, name: str) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.groups.filter(name=name).exists())


class RoleRequiredMixin(LoginRequiredMixin):
    """Baza mixinów ról: logowanie z ``LoginRequiredMixin``, rola z ``has_role``."""

    role_denied_message = "Twoje konto nie ma uprawnień do tej części serwisu."

    def has_role(self, user) -> bool:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self.has_role(request.user):
            raise PermissionDenied(self.role_denied_message)
        return super().dispatch(request, *args, **kwargs)


class ParticipantRequiredMixin(RoleRequiredMixin):
    """Uczestnik: grupa ``participant`` i profil ``Participant`` (odpowiednik ``IsParticipant``)."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla uczestników."

    def has_role(self, user) -> bool:
        return _in_group(user, GROUP_PARTICIPANT) and hasattr(user, "participant")

    @property
    def participant(self):
        return self.request.user.participant


class ReviewerRequiredMixin(RoleRequiredMixin):
    """Recenzent aktywny – ta sama definicja, co ``IsActiveReviewer`` i widoczność plików."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla aktywnych recenzentów."

    def has_role(self, user) -> bool:
        return active_reviewer_profile(user) is not None

    @property
    def reviewer(self):
        return active_reviewer_profile(self.request.user)


class CoordinatorRequiredMixin(RoleRequiredMixin):
    """Koordynator (grupa ``coordinator``) – odpowiednik ``IsCoordinator``, bez eskalacji superusera."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla koordynatora."

    def has_role(self, user) -> bool:
        return _in_group(user, GROUP_COORDINATOR)


class AppealsCommitteeRequiredMixin(RoleRequiredMixin):
    """Komisja odwoławcza – odpowiednik ``IsAppealsCommittee`` (grupa ``appeals`` + aktywny profil)."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla komisji odwoławczej."

    def has_role(self, user) -> bool:
        return _in_group(user, GROUP_APPEALS) and appeals_committee_profile(user) is not None

    @property
    def member(self):
        return appeals_committee_profile(self.request.user)


class ActionViewMixin:
    """Widok-akcja POST: woła serwis, zamienia ``DomainError`` na komunikat i wraca na stronę.

    Dzięki temu w widokach nie ma ani jednego ``try/except`` wokół logiki domenowej, a użytkownik
    dostaje czytelny komunikat zamiast 500. Kod maszynowy błędu zostaje w logu serwisu.
    """

    success_url = "/"

    def perform(self, request, *args, **kwargs) -> str:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def get_success_url(self, *args, **kwargs) -> str:
        return self.success_url

    def post(self, request, *args, **kwargs):
        try:
            message = self.perform(request, *args, **kwargs)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            if message:
                messages.success(request, message)
        return redirect(self.get_success_url(*args, **kwargs))
