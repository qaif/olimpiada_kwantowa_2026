"""Uprawnienia DRF zakresowane konkursem.

Dzisiejsze ``apps.accounts.permissions.IsCoordinator`` pyta „czy w grupie ``coordinator``”, czyli
o rolę **w instalacji**. W bazie wielokonkursowej to pytanie jest za szerokie: koordynator konkursu
A dostawałby endpointy konkursu B. Klasa poniżej dokłada brakujący wymiar – konkurs żądania.

Stan przejściowy, nazwany wprost: modelu ``accounts.Membership`` i funkcji ``has_role`` jeszcze nie
ma (powstają w zadaniu T2). Do tego czasu rola czyta się z grupy Django, czyli **dokładnie tak, jak
czyta ją serwis dziś** – a warunek „konkurs żądania istnieje” już obowiązuje. Po T2 zmienia się tu
jedna linia: wywołanie ``has_role(user, competition, CompetitionRole.COORDINATOR)``.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.accounts.models import GROUP_COORDINATOR


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
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        # T2: ``has_role(user, competition, CompetitionRole.COORDINATOR)``. Do czasu backfillu
        # członkostw grupa Django jest jedyną zapisaną w bazie odpowiedzią na pytanie o rolę.
        return user.groups.filter(name=GROUP_COORDINATOR).exists()
