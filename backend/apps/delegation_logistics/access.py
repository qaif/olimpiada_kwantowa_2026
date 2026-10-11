"""Kto widzi dane logistyki finału – reguły dostępu w jednym miejscu (LOG-01 § 2).

Trzy piętra, każde sprawdzane w serwisie i w widoku tą samą funkcją:

- **koordynator** konkursu – ustawienia finału, przydziały, liczby zbiorcze; bez danych osób,
- **oficer logistyki** – koordynator z przydziałem ``OFFICER``: pełny wgląd (paszporty, zdrowie),
- **obsługa rejestracji** – dowolne aktywne konto z przydziałem ``CHECKIN`` (wolontariusz przy
  wejściu): wyłącznie ekran skanowania identyfikatorów.

Dlaczego oficer musi **nadal** być koordynatorem: przydział jest zawężeniem roli, a nie osobną
drogą do danych. Koordynator odwołany z konkursu traci wgląd od razu, bez sprzątania przydziałów
(ta sama zasada, co „rola bez wiersza delegacji nie otwiera listy uczniów” w DEL-01).
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from rest_framework import status

from apps.accounts.models import CompetitionRole, User
from apps.accounts.services import has_role
from apps.core.api import DomainError
from apps.core.models import audit

from .models import AccessRole, LogisticsAccess


def is_coordinator(user, competition) -> bool:
    return has_role(user, competition, CompetitionRole.COORDINATOR)


def _granted(user, competition, roles) -> bool:
    if competition is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return LogisticsAccess.objects.filter(competition=competition, user=user, role__in=roles).exists()


def is_officer(user, competition) -> bool:
    """Oficer logistyki: koordynator **tego** konkursu z przydziałem ``OFFICER``."""
    return is_coordinator(user, competition) and _granted(user, competition, [AccessRole.OFFICER])


def can_check_in(user, competition) -> bool:
    """Ekran skanowania: oficer albo konto z przydziałem ``CHECKIN`` (bez wymogu roli w konkursie)."""
    if _granted(user, competition, [AccessRole.CHECKIN]):
        return True
    return is_officer(user, competition)


def require_officer(user, competition) -> None:
    """403 z wyjaśnieniem – koordynator bez przydziału ma się dowiedzieć, czego mu brakuje."""
    if not is_officer(user, competition):
        raise PermissionDenied(
            "Dane osób w logistyce finału widzi wyłącznie oficer logistyki (przydział na ekranie "
            "„Logistyka finału → Dostęp”)."
        )


def grants_of(competition):
    return (
        LogisticsAccess.objects.for_competition(competition)
        .select_related("user", "granted_by")
        .order_by("role", "user__last_name", "user__email")
    )


def _may_grant_officer(actor, competition) -> bool:
    """Oficera nadaje superkoordynator albo istniejący oficer; pierwszego – dowolny koordynator.

    Bez tej reguły każdy z kilkunastu koordynatorów mógłby jednym kliknięciem nadać sobie wgląd
    w paszporty i dane o zdrowiu, a przydział nie zawężałby niczego. Wyjątek „pierwszego oficera”
    jest konieczny, bo inaczej konkurs bez superkoordynatora nie mógłby zacząć – i jest widoczny:
    każde nadanie stoi w audycie i na ekranie dostępu.
    """
    from apps.accounts.super_coordinator import is_super_coordinator

    if is_super_coordinator(actor) or is_officer(actor, competition):
        return True
    return not LogisticsAccess.objects.filter(competition=competition, role=AccessRole.OFFICER).exists()


def _may_manage_checkin(actor, competition) -> bool:
    """Obsługę rejestracji nadaje i odbiera oficer albo superkoordynator (L1).

    Obsługa widzi zdjęcia i nazwiska całej delegacji, więc przydział nie może być czymś, co
    dowolny koordynator daje sobie albo znajomemu bez wiedzy osoby odpowiedzialnej za logistykę.
    """
    from apps.accounts.super_coordinator import is_super_coordinator

    return is_super_coordinator(actor) or is_officer(actor, competition)


def grant(competition, *, email: str, role: str, actor, request=None) -> LogisticsAccess:
    """Nadaje przydział kontu o tym adresie. Oficer musi być koordynatorem tego konkursu."""
    if role not in AccessRole.values:
        raise DomainError("Nieznany przydział.", "ACCESS_ROLE_INVALID", status.HTTP_400_BAD_REQUEST)
    if not is_coordinator(actor, competition):
        raise PermissionDenied("Przydziały nadaje koordynator konkursu.")
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if user is None:
        raise DomainError(
            "Nie ma aktywnego konta o tym adresie – osoba musi najpierw mieć konto w serwisie.",
            "ACCESS_USER_UNKNOWN",
            status.HTTP_400_BAD_REQUEST,
        )
    if role == AccessRole.CHECKIN:
        if not _may_manage_checkin(actor, competition):
            raise DomainError(
                "Obsługę rejestracji nadaje oficer logistyki albo superkoordynator.",
                "ACCESS_CHECKIN_FORBIDDEN",
                status.HTTP_403_FORBIDDEN,
            )
        if user.pk == actor.pk:
            raise DomainError(
                "Przydziału obsługi rejestracji nie nadaje się samemu sobie.",
                "ACCESS_SELF_GRANT",
                status.HTTP_400_BAD_REQUEST,
            )
    if role == AccessRole.OFFICER:
        if not _may_grant_officer(actor, competition):
            raise DomainError(
                "Oficera logistyki nadaje superkoordynator albo inny oficer logistyki tego konkursu.",
                "ACCESS_OFFICER_FORBIDDEN",
                status.HTTP_403_FORBIDDEN,
            )
        if not is_coordinator(user, competition):
            raise DomainError(
                "Oficerem logistyki może być wyłącznie koordynator tego konkursu.",
                "ACCESS_OFFICER_NOT_COORDINATOR",
                status.HTTP_400_BAD_REQUEST,
            )
    access, created = LogisticsAccess.objects.get_or_create(
        competition=competition, user=user, role=role, defaults={"granted_by": actor}
    )
    if created:
        audit(actor, "logistics.access_granted", access, {"role": role, "user_id": user.pk}, request=request)
    return access


def revoke(access: LogisticsAccess, *, actor, request=None) -> None:
    if access.role == AccessRole.OFFICER and not _may_grant_officer(actor, access.competition):
        raise DomainError(
            "Przydział oficera odbiera superkoordynator albo inny oficer logistyki.",
            "ACCESS_OFFICER_FORBIDDEN",
            status.HTTP_403_FORBIDDEN,
        )
    if access.role == AccessRole.CHECKIN and not _may_manage_checkin(actor, access.competition):
        raise DomainError(
            "Przydział obsługi rejestracji odbiera oficer logistyki albo superkoordynator.",
            "ACCESS_CHECKIN_FORBIDDEN",
            status.HTTP_403_FORBIDDEN,
        )
    audit(
        actor,
        "logistics.access_revoked",
        access,
        {"role": access.role, "user_id": access.user_id},
        request=request,
    )
    access.delete()
