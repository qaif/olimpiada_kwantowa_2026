"""Uprawnienia DRF zakresowane konkursem.

Dzisiejsze ``apps.accounts.permissions.IsCoordinator`` pyta „czy w grupie ``coordinator``”, czyli
o rolę **w instalacji**. W bazie wielokonkursowej to pytanie jest za szerokie: koordynator konkursu
A dostawałby endpointy konkursu B. Klasa poniżej dokłada brakujący wymiar – konkurs żądania.

Reguła roli jest od zadania T2 zapisana w ``apps.accounts.services.has_role`` i to ona – a nie ta
klasa – rozstrzyga, czy odpowiada grupa Django, czy wiersz ``accounts.Membership`` (przełącznik
``memberships_enforced``, § 3.8). Tutaj zostaje wyłącznie warunek „konkurs żądania istnieje”.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.accounts.models import CompetitionRole


class IsCompetitionCoordinator(BasePermission):
    """Koordynator **tego** konkursu.

    Bez cichej eskalacji dla superużytkownika – tak samo, jak w ``IsCoordinator``. Operator
    platformy ma własne narzędzie (``/admin/``) i nie wchodzi bokiem do panelu organizatora;
    eskalacja zrobiłaby z każdego konta serwisowego konto z dostępem do danych uczestników.
    """

    message = "Wymagana rola koordynatora tego konkursu."

    def has_permission(self, request, view) -> bool:
        competition = getattr(request, "competition", None)
        if competition is None:
            return False
        # Import lokalny: ``apps.accounts.services`` ciągnie za sobą aktywację, zgody i pocztę,
        # a ten moduł jest ładowany przy składaniu widoków DRF.
        from apps.accounts.services import has_role

        return has_role(request.user, competition, CompetitionRole.COORDINATOR)
