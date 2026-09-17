"""Mixiny ról dla widoków HTML.

Reguła dostępu jest ta sama, co w klasach uprawnień DRF (``apps.accounts.permissions``) – mixiny
wołają dokładnie te helpery, żeby panel i API nie mogły się rozjechać. Zachowanie kontraktowe
(T-08, kryteria 1–2):

- niezalogowany → 302 na ``/login/?next=…`` (``AccessMixin.handle_no_permission``),
- zalogowany w złej roli → 403 (``PermissionDenied``).

Trzeci wiersz tej tabeli dokłada wielokonkursowość i jest tu tylko po to, żeby go **nie** pomylić
z drugim: obiekt należący do innego konkursu daje **404**, a nie 403, i wychodzi to z zawężonego
querysetu (``for_competition``), a nie stąd. 403 mówi „jesteś, ale nie tobie”, 404 – „nie ma tego
tutaj”; istnienie cudzego etapu nie jest niczyją informacją (``docs/UNIWERSALNY-ETAP-1.md`` § 3.6).

Rolę rozstrzyga ``apps.accounts.services.has_role`` – ta sama funkcja, co w DRF. Konkurs bierzemy
z żądania (``request.competition``, ustawia je ``apps.tenancy.middleware``); żądanie bez konkursu
schodzi w ``has_role`` do grup Django, czyli do zachowania sprzed wielokonkursowości.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from apps.accounts.models import CompetitionRole
from apps.accounts.services import active_reviewer_profile, has_role, participant_for
from apps.appeals.services import appeals_committee_profile
from apps.core.api import DomainError


class RoleRequiredMixin(LoginRequiredMixin):
    """Baza mixinów ról: logowanie z ``LoginRequiredMixin``, rola z ``has_role``."""

    role_denied_message = "Twoje konto nie ma uprawnień do tej części serwisu."

    def has_role(self, user) -> bool:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    @property
    def competition(self):
        """Konkurs żądania albo ``None``. Skrót dla mixinów – regułą jest ``request.competition``."""
        return getattr(self.request, "competition", None)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self.has_role(request.user):
            raise PermissionDenied(self.role_denied_message)
        return super().dispatch(request, *args, **kwargs)


class ParticipantRequiredMixin(RoleRequiredMixin):
    """Uczestnik: rola ``participant`` i profil w tym konkursie (odpowiednik ``IsParticipant``)."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla uczestników."

    def has_role(self, user) -> bool:
        return (
            has_role(user, self.competition, CompetitionRole.PARTICIPANT)
            and participant_for(user, self.competition) is not None
        )

    @property
    def participant(self):
        """Profil uczestnika **w tym konkursie**.

        ``dispatch`` nie wpuszcza tu nikogo bez profilu, więc ``None`` w tym miejscu jest błędem
        programu, a nie stanem do obsłużenia – i tak ma zostać: cicha ``None`` przeszłaby do
        szablonu jako pusta strona zamiast 403.
        """
        return participant_for(self.request.user, self.competition)


class ReviewerRequiredMixin(RoleRequiredMixin):
    """Recenzent aktywny – ta sama definicja, co ``IsActiveReviewer`` i widoczność plików."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla aktywnych recenzentów."

    def has_role(self, user) -> bool:
        return active_reviewer_profile(user, self.competition) is not None

    @property
    def reviewer(self):
        return active_reviewer_profile(self.request.user, self.competition)


class CoordinatorRequiredMixin(RoleRequiredMixin):
    """Koordynator tego konkursu – odpowiednik ``IsCoordinator``, bez eskalacji superusera."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla koordynatora."

    def has_role(self, user) -> bool:
        return has_role(user, self.competition, CompetitionRole.COORDINATOR)


class AppealsCommitteeRequiredMixin(RoleRequiredMixin):
    """Komisja odwoławcza – odpowiednik ``IsAppealsCommittee`` (rola ``appeals`` + aktywny profil)."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla komisji odwoławczej."

    def has_role(self, user) -> bool:
        return (
            has_role(user, self.competition, CompetitionRole.APPEALS)
            and appeals_committee_profile(user) is not None
        )

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
