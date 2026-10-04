"""Od kogo drugi składnik jest wymagany i do kiedy trwa okres przejściowy (SEC-01 § 1–3).

Jedno pytanie, zadawane z trzech miejsc (warstwa wymuszająca, logowanie przez API,
uwierzytelnienie tokenem): **czy to konto, w tym konkursie, musi mieć drugi składnik – i czy już
po terminie**. Odpowiedź składa się z dwóch list ról:

- **platformy** – ``settings.TWO_FACTOR_REQUIRED_ROLES`` (domyślnie ``superkoordynator,admin``:
  te dwie role widzą wszystkie konkursy naraz, więc ich wymóg nie może zależeć od konkursu),
- **konkursu** – :class:`~apps.staff_mfa.models.TwoFactorPolicy`; brak wiersza = tryb ``auto``,
  czyli personel konkursów z funkcjami wnoszącymi dane wrażliwe.

Uczestnik nie jest rolą tej polityki i nie da się go nią objąć (``participant`` jest odrzucany
z każdej listy). Uczestnik, który drugi składnik włączył sam, podaje kod tak jak dotąd – to jest
jego wybór, a nie wymóg.

Kod nie sprawdza wyłącznika ``TWO_FACTOR_ENABLED`` – robią to wołający
(``apps.accounts.twofactor``), zanim tu zajrzą. Przy wyłączonej funkcji ten moduł nie jest wołany.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Klucze ról polityki → opis (ekran koordynatora, ``/admin/``, dokumentacja). Kolejność = kolejność
#: na ekranie. Klucze ról konkursu są **identyczne** z nazwami grup (``CompetitionRole``), więc
#: dotychczasowa wartość ``TWO_FACTOR_REQUIRED_ROLES=coordinator,reviewer,appeals`` znaczy to samo.
ROLE_KEYS: dict[str, str] = {
    "superkoordynator": "superkoordynator (wszystkie konkursy)",
    "admin": "dostęp do /admin/ (is_staff, superuser)",
    "coordinator": "koordynator (także oficer logistyki)",
    "team_leader": "opiekun drużyny narodowej",
    "logistics": "przydział w logistyce finału (oficer, obsługa rejestracji)",
    "reviewer": "komitet / recenzent",
    "appeals": "komisja odwoławcza",
    "supervisor": "opiekun szkolny",
}

#: Role **platformy**: nie należą do żadnego konkursu, więc nie wybiera ich polityka konkursu.
PLATFORM_ONLY_KEYS = frozenset({"superkoordynator", "admin"})

#: Role konkursu, które wolno zaznaczyć w polityce konkursu.
COMPETITION_ROLE_KEYS = tuple(key for key in ROLE_KEYS if key not in PLATFORM_ONLY_KEYS)

#: Domyślny wymóg konkursu z funkcją wrażliwą (tryb ``auto``). Uzasadnienie: koordynator widzi
#: wszystko; oficer logistyki i obsługa rejestracji – paszporty, zdrowie i zdjęcia; opiekun drużyny
#: wpisuje paszporty i dane zdrowotne swoich uczniów. Komitet i komisja widzą prace bez danych
#: szczególnych – organizator dokłada je trybem ``custom`` (``docs/tasks/SEC-01.md`` § 2).
SENSITIVE_DEFAULT_ROLES = frozenset({"coordinator", "team_leader", "logistics"})

#: Flagi konkursu, które wnoszą dane wrażliwe. Nazwa spoza katalogu ``FEATURE_DEFAULTS`` jest
#: pomijana (``proctoring`` wchodzi z gałęzią PROC-01) – dzięki temu lista nie musi czekać na
#: kolejność scalania gałęzi, a ``has_feature`` nie podnosi ``KeyError``.
SENSITIVE_FLAGS = ("fees", "onsite_logistics", "proctoring")

#: Domyślna długość okresu przejściowego, gdy brakuje ustawienia (dni).
DEFAULT_GRACE_DAYS = 14


@dataclass(frozen=True)
class Requirement:
    """Wymóg 2FA dla jednego konta w jednym konkursie."""

    roles: frozenset[str]
    deadline: datetime

    def overdue(self, now: datetime | None = None) -> bool:
        return (now or timezone.now()) >= self.deadline


# --- polityka --------------------------------------------------------------------------------------


def _clean_keys(raw, *, allowed) -> frozenset[str]:
    keys = {str(name).strip() for name in (raw or []) if str(name).strip()}
    unknown = keys - set(allowed)
    if unknown:
        # ``participant`` trafia tu celowo: uczestnika nie wolno objąć wymogiem (SEC-01 § 0).
        logger.warning("2FA: pomijam nieznane albo niedozwolone role polityki: %s", sorted(unknown))
    return frozenset(keys & set(allowed))


def platform_roles() -> frozenset[str]:
    """Role z ``TWO_FACTOR_REQUIRED_ROLES`` – obowiązują w każdym konkursie."""
    return _clean_keys(getattr(settings, "TWO_FACTOR_REQUIRED_ROLES", None), allowed=ROLE_KEYS)


def policy_for(competition):
    """Wiersz polityki konkursu albo ``None`` (= tryb ``auto`` z wartościami platformy).

    Bez pamięci na obiekcie konkursu: pytanie pada raz na sesję (warstwa wymuszająca zapisuje
    odpowiedź w sesji), a zapamiętany wiersz przeżyłby zmianę polityki w tym samym procesie.
    """
    if competition is None or not getattr(competition, "pk", None):
        return None
    from .models import TwoFactorPolicy

    return TwoFactorPolicy.objects.filter(competition=competition).first()


def sensitive_features(competition) -> list[str]:
    """Funkcje konkursu, które wnoszą dane wrażliwe – do decyzji ``auto`` i do ekranu polityki."""
    if competition is None:
        return []
    from apps.tenancy.models import FEATURE_DEFAULTS

    found = ["delegations"] if getattr(competition, "uses_delegations", False) else []
    found += [name for name in SENSITIVE_FLAGS if name in FEATURE_DEFAULTS and competition.has_feature(name)]
    return found


def competition_roles(competition) -> frozenset[str]:
    """Role wymagane przez **konkurs** (bez platformy)."""
    from .models import PolicyMode

    if competition is None:
        return frozenset()
    row = policy_for(competition)
    if row is not None and row.mode == PolicyMode.CUSTOM:
        return _clean_keys(row.roles, allowed=COMPETITION_ROLE_KEYS)
    return SENSITIVE_DEFAULT_ROLES if sensitive_features(competition) else frozenset()


def required_roles(competition) -> frozenset[str]:
    return platform_roles() | competition_roles(competition)


def grace_days(competition) -> int:
    row = policy_for(competition)
    if row is not None and row.grace_days is not None:
        return int(row.grace_days)
    return int(getattr(settings, "TWO_FACTOR_GRACE_DAYS", DEFAULT_GRACE_DAYS))


def remember_days(competition) -> int:
    """Ile dni trwa „zapamiętaj to urządzenie”; zero = opcji nie ma."""
    days = int(getattr(settings, "TWO_FACTOR_REMEMBER_DAYS", 0) or 0)
    row = policy_for(competition)
    if row is not None and not row.allow_remember:
        return 0
    return max(days, 0)


#: Klucz cache'a z „wersją” polityki. Znaczniki sesji warstwy wymuszającej niosą tę wersję, więc
#: zaostrzenie polityki działa od następnego żądania, a nie dopiero od następnego logowania.
VERSION_KEY = "2fa:policy:version"


def version() -> str:
    """Wersja polityki: znacznik z cache'a (zmieniany przy zapisie polityki) + skrót ustawień platformy.

    Jedno odczytanie cache'a na żądanie konta bez urządzenia – i wyłącznie przy włączonej funkcji.
    Wyczyszczony cache daje nową wersję, czyli najwyżej jedno ponowne liczenie polityki na sesję.
    """
    import hashlib

    from django.core.cache import cache

    stamp = cache.get(VERSION_KEY)
    if stamp is None:
        stamp = secrets.token_hex(6)
        cache.add(VERSION_KEY, stamp, None)
        stamp = cache.get(VERSION_KEY) or stamp
    platform = repr(
        (
            sorted(getattr(settings, "TWO_FACTOR_REQUIRED_ROLES", None) or []),
            getattr(settings, "TWO_FACTOR_GRACE_DAYS", DEFAULT_GRACE_DAYS),
        )
    )
    return f"{stamp}:{hashlib.sha256(platform.encode()).hexdigest()[:8]}"


def bump_version() -> None:
    from django.core.cache import cache

    cache.set(VERSION_KEY, secrets.token_hex(6), None)


# --- role konta ------------------------------------------------------------------------------------


def role_keys_of(user, competition, wanted: frozenset[str] | None = None) -> frozenset[str]:
    """Klucze ról, które to konto ma w tym konkursie – **zawężone do** ``wanted`` (gdy podane).

    Zawężenie nie jest ozdobą: ``logistics`` kosztuje osobne zapytanie, a ``superkoordynator``
    kolejne – pytamy o nie wyłącznie wtedy, gdy polityka ich w ogóle wymaga.
    """
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    wanted = frozenset(ROLE_KEYS) if wanted is None else wanted
    found: set[str] = set()
    if "admin" in wanted and (user.is_staff or user.is_superuser):
        found.add("admin")
    if "superkoordynator" in wanted:
        from apps.accounts.super_coordinator import is_super_coordinator

        if is_super_coordinator(user):
            found.add("superkoordynator")
    competition_wanted = wanted - PLATFORM_ONLY_KEYS - {"logistics"}
    if competition_wanted:
        from apps.accounts.services import roles_for

        found |= set(roles_for(user, competition)) & competition_wanted
    if "logistics" in wanted and competition is not None:
        from apps.delegation_logistics.models import LogisticsAccess

        if LogisticsAccess.objects.filter(competition=competition, user=user).exists():
            found.add("logistics")
    return frozenset(found)


def matching_roles(user, competition) -> frozenset[str]:
    """Role tego konta, z powodu których drugi składnik jest wymagany. Pusty zbiór = nie jest."""
    wanted = required_roles(competition)
    if not wanted:
        return frozenset()
    return role_keys_of(user, competition, wanted)


def is_staff_account(user, competition) -> bool:
    """Czy konto jest „personelem” w rozumieniu resetu 2FA (SEC-01 § 4): rola z § 1 poza opiekunem."""
    return bool(role_keys_of(user, competition, frozenset(ROLE_KEYS) - {"supervisor"}))


# --- okres przejściowy -----------------------------------------------------------------------------


def grace_row(user, *, start: bool, request=None):
    """Wiersz okresu przejściowego konta; ``start=True`` zakłada go przy pierwszym wymogu."""
    from .models import TwoFactorGrace

    row = TwoFactorGrace.objects.filter(user=user).first()
    if row is None and start:
        row, created = TwoFactorGrace.objects.get_or_create(user=user)
        if created:
            from apps.core.models import audit

            audit(user, "2fa.grace_started", user, {}, request)
    return row


def close_grace(user, competition) -> None:
    """Wyłączenie 2FA przez konto objęte wymogiem zużywa okres przejściowy (SEC-01 § 3).

    Konto, które włączyło 2FA, zanim warstwa wymuszająca zobaczyła u niego wymóg, nie ma jeszcze
    wiersza okresu przejściowego – bez tego kroku „wyłącz” dawałoby mu świeże czternaście dni bez
    drugiego składnika. Wiersz już istniejący zostaje, jaki był (termin się nie przesuwa).
    """
    from .models import TwoFactorGrace

    if matching_roles(user, competition):
        TwoFactorGrace.objects.get_or_create(
            user=user, defaults={"required_since": timezone.now() - timedelta(days=3650)}
        )


def requirement_for(user, competition, *, request=None, start_grace: bool = True) -> Requirement | None:
    """Wymóg 2FA konta w konkursie albo ``None``. Nie patrzy, czy konto ma już urządzenie."""
    roles = matching_roles(user, competition)
    if not roles:
        return None
    row = grace_row(user, start=start_grace, request=request)
    since = row.required_since if row is not None else timezone.now()
    return Requirement(roles=roles, deadline=since + timedelta(days=grace_days(competition)))


def may_reset(actor, target, competition) -> bool:
    """Czy ``actor`` może zdjąć drugi składnik z konta ``target`` (SEC-01 § 4).

    - konto personelu: wyłącznie superkoordynator. Wyjątek: na platformie nie ma żadnego aktywnego
      superkoordynatora – wtedy koordynator (inaczej instalacja bez tej roli nie miałaby drogi
      powrotu dla koordynatora bez telefonu poza powłoką serwera),
    - pozostałe konta (uczestnik, opiekun szkolny): koordynator konkursu, jak dotąd.

    Sprawdzenie roli koordynatora robi wołający (bramka panelu); tu – wyłącznie dodatkowy próg.
    """
    from apps.accounts.super_coordinator import is_super_coordinator

    if is_super_coordinator(actor):
        return True
    if not is_staff_account(target, competition):
        return True
    from apps.accounts.models import GROUP_SUPER_COORDINATOR, User

    return not User.objects.filter(groups__name=GROUP_SUPER_COORDINATOR, is_active=True).exists()
