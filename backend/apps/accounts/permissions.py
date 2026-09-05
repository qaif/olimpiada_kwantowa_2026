"""Klasy uprawnień DRF dla ról RBAC.

Każdy widok w projekcie deklaruje uprawnienia jawnie – nie ma widoków bez ``permission_classes``.
Rola = przynależność do grupy Django; dla recenzenta dodatkowo wymagany jest status ACTIVE profilu.
"""

from rest_framework.permissions import BasePermission

from .models import (
    GROUP_APPEALS,
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    CommitteeStatus,
)


def _in_group(user, name: str) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return user.groups.filter(name=name).exists()


def _active_committee_member(user):
    """Profil komitetu użytkownika, o ile istnieje i jest aktywny."""
    member = getattr(user, "committee_member", None)
    if member is None or member.status != CommitteeStatus.ACTIVE:
        return None
    return member


class IsParticipant(BasePermission):
    """Zalogowany uczestnik (grupa ``participant`` + profil ``Participant``)."""

    message = "Wymagana rola uczestnika."

    def has_permission(self, request, view) -> bool:
        return _in_group(request.user, GROUP_PARTICIPANT) and hasattr(request.user, "participant")


class IsActiveReviewer(BasePermission):
    """Recenzent w grupie ``reviewer`` z profilem ``CommitteeMember`` w statusie ACTIVE.

    Recenzent PENDING lub SUSPENDED dostaje 403 – sama grupa nie wystarcza. Regułę rozstrzyga
    ``apps.accounts.services.active_reviewer_profile``: ta sama funkcja decyduje o widoczności
    rozwiązań (``Submission.objects.for_user``) i o przydziałach, więc uprawnienie i widoczność
    nie mogą się rozjechać.
    """

    message = "Wymagany aktywny recenzent."

    def has_permission(self, request, view) -> bool:
        # Import lokalny: ``services`` importuje ``permissions`` pośrednio przez warstwę API.
        from .services import active_reviewer_profile

        return active_reviewer_profile(request.user) is not None


class IsAppealsCommittee(BasePermission):
    """Członek komisji odwoławczej: grupa ``appeals`` + aktywny profil z ``is_appeals_committee``."""

    message = "Wymagana rola komisji odwoławczej."

    def has_permission(self, request, view) -> bool:
        if not _in_group(request.user, GROUP_APPEALS):
            return False
        member = _active_committee_member(request.user)
        return member is not None and member.is_appeals_committee


class IsCoordinator(BasePermission):
    """Koordynator olimpiady (grupa ``coordinator``). Bez cichej eskalacji dla superusera."""

    message = "Wymagana rola koordynatora."

    def has_permission(self, request, view) -> bool:
        return _in_group(request.user, GROUP_COORDINATOR)
