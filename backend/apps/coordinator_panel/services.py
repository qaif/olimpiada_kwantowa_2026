"""Serwisy panelu koordynatora (UX-PANEL-04): przypinanie pozycji menu i dane Startu.

Każdy serwis sprawdza rolę sam (``has_role`` – ta sama reguła, co ``CoordinatorRequiredMixin``), a nie
polega na tym, że wołający jest widokiem za bramką: Start i przypinanie bywają wołane z testów i z komend,
a zakres konkursu jest zawsze konkursem żądania (``request.competition``).
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max

from apps.core.api import DomainError

from . import shell


def require_coordinator(request) -> None:
    """Koordynator konkursu z żądania – inaczej ``PermissionDenied`` (403, jak bramka widoku)."""
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    competition = getattr(request, "competition", None)
    if competition is None or not has_role(
        getattr(request, "user", None), competition, CompetitionRole.COORDINATOR
    ):
        raise PermissionDenied


class PinLimitReached(DomainError):
    default_code = "PIN_LIMIT"
    default_detail = "Osiągnięto limit przypiętych pozycji."


class NotAMenuItem(DomainError):
    default_code = "PIN_NOT_MENU_ITEM"
    default_detail = "Przypiąć można wyłącznie pozycję menu panelu."


def toggle_pin(request, raw_path: str) -> bool:
    """Przypina albo odpina pozycję menu. Zwraca ``True``, gdy pozycja jest po wszystkim przypięta.

    Ścieżka przychodzi z formularza, więc jest daną wejściową: przyjmujemy **wyłącznie** adres, który
    w tym żądaniu jest pozycją menu tego konkursu (płaska lista z ``shell.navigation``). To jednocześnie
    walidacja (żadnych dowolnych napisów w bazie, żadnych kart osób – D9) i izolacja konkursów: menu
    składa adresy z konkursu żądania, więc ścieżki cudzego konkursu na liście nie ma.

    Odpięcie nie wymaga, żeby pozycja nadal była w menu – inaczej pozycji za wyłączoną flagą nie dałoby się
    usunąć z bazy, choć na ekranie i tak jej nie widać.
    """
    from .models import PATH_MAX_LENGTH, PinnedItem

    require_coordinator(request)
    path = shell.relative_path((raw_path or "").strip())[:PATH_MAX_LENGTH]
    user, competition = request.user, request.competition
    with transaction.atomic():
        rows = PinnedItem.objects.for_competition(competition).filter(user=user)
        existing = rows.filter(path=path).first()
        if existing is not None:
            existing.delete()
            shell.forget_pins(request)
            return False
        menu_paths = {shell.relative_path(row["url"]) for row in shell.navigation(request)["nav_flat"]}
        if not path or path not in menu_paths:
            raise NotAMenuItem()
        if rows.count() >= shell.MAX_PINS:
            raise PinLimitReached()
        top = rows.aggregate(top=Max("position"))["top"]
        PinnedItem.objects.create(
            user=user, competition=competition, path=path, position=0 if top is None else top + 1
        )
    shell.forget_pins(request)
    return True


# --- RODO (UX-PANEL-04 D13) -------------------------------------------------------------------------------


def erase_for_user(user) -> int:
    """Kasuje przypięte pozycje konta (anonimizacja). Zwraca liczbę usuniętych wierszy."""
    from .models import PinnedItem

    deleted, _ = PinnedItem._base_manager.filter(user=user).delete()
    return deleted


def export_rows(user, competition=None) -> list[dict]:
    """Przypięte pozycje w eksporcie danych konta – ścieżka i data, bez niczego więcej (nic więcej nie ma)."""
    from .models import PinnedItem

    rows = PinnedItem._base_manager.filter(user=user)
    if competition is not None:
        rows = rows.filter(competition=competition)
    return [
        {"sciezka": row.path, "kolejnosc": row.position, "przypieto": row.created_at.isoformat()}
        for row in rows.order_by("competition_id", "position", "pk")
    ]
