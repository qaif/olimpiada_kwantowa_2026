"""Kontrole konfiguracji wielokonkursowości (``manage.py check --database default``).

- ``tenancy.E001`` – więcej niż jeden aktywny konkurs, a któryś z nich liczy role z globalnych grup
  Django (``memberships_enforced`` wyłączone). Poprawka po audycie izolacji (01.10.2026).
"""

from __future__ import annotations

from django.core import checks
from django.db import DatabaseError

#: Przełącznik, którego dotyczy kontrola – ta sama nazwa, co w ``FEATURE_DEFAULTS``.
MEMBERSHIPS_FLAG = "memberships_enforced"


def unscoped_competitions() -> list:
    """Aktywne konkursy z wyłączonym ``memberships_enforced`` – o ile aktywnych jest więcej niż jeden.

    Pusta lista znaczy „stan bezpieczny”, i to są dwa różne stany: jeden aktywny konkurs (dzisiejsza
    produkcja – role z grup Django są wtedy rolami w jedynym konkursie, czyli poprawnymi) albo
    wszystkie aktywne konkursy z flagą włączoną. Osobna funkcja, a nie ciało kontroli, bo to samo
    pytanie zadaje test i warto, żeby odpowiedź miała jedną definicję.
    """
    from .models import Competition

    active = list(Competition.objects.filter(is_active=True).order_by("slug"))
    if len(active) < 2:
        return []
    return [competition for competition in active if not competition.has_feature(MEMBERSHIPS_FLAG)]


@checks.register(checks.Tags.database)
def check_memberships_enforced(app_configs=None, databases=None, **kwargs) -> list[checks.CheckMessage]:
    """``tenancy.E001``: dwa aktywne konkursy i role liczone z globalnych grup w którymś z nich.

    Przy wyłączonym ``memberships_enforced`` o roli rozstrzyga globalna grupa Django
    (``apps.accounts.services.has_role``), więc koordynator, recenzent i komisja odwoławcza jednego
    konkursu przechodzą bramki **każdego** konkursu instalacji. Przy jednym konkursie to jest stan
    poprawny (tak działa dziś produkcja), przy dwóch – wyciek między organizatorami. Zakładanie
    konkursu (``apps.tenancy.provisioning``) takiego stanu już nie wytworzy; ta kontrola łapie
    drogi obok niego: konkurs dopisany w ``/admin/``, ponowne włączenie konkursu nieaktywnego,
    ręczne wyłączenie flagi.

    Błąd, a nie ostrzeżenie: to jest dostęp do danych osobowych uczestników przez cudzego
    organizatora. Kontrola jest **bazodanowa** (``Tags.database``) – Django uruchamia ją tylko przy
    ``migrate`` i ``check --database default``, a nie przy każdej komendzie; inaczej blokowałaby
    także ``check_memberships``, czyli narzędzie, którym ten stan się naprawia. Konsekwencja jest
    świadoma: ``migrate`` w takim stanie się zatrzymuje, a obejściem na czas naprawy jest
    ``migrate --skip-checks``.

    Baza bez tabeli konkursów (pierwsze ``migrate``, baza przed ``tenancy.0001``) nie jest błędem
    konfiguracji, tylko stanem przed nią – kontrola milczy.
    """
    if not databases:
        return []
    try:
        unscoped = unscoped_competitions()
    except DatabaseError:
        return []
    if not unscoped:
        return []
    names = ", ".join(competition.slug for competition in unscoped)
    return [
        checks.Error(
            f"Instalacja prowadzi więcej niż jeden aktywny konkurs, a konkursy: {names} mają wyłączony "
            "przełącznik memberships_enforced – role liczą się w nich z globalnych grup kont, więc "
            "koordynatorzy, recenzenci i komisja odwoławcza mają uprawnienia także w pozostałych "
            "konkursach.",
            hint=(
                "Uruchom „manage.py check_memberships --fix”, a po zielonym wyniku włącz "
                "memberships_enforced tym konkursom (docs/OPERACJE.md § 6.1). Na czas naprawy: "
                "„manage.py migrate --skip-checks”."
            ),
            id="tenancy.E001",
        )
    ]
