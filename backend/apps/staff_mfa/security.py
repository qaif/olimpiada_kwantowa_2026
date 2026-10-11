"""Czynności bezpieczeństwa konta wokół 2FA (przegląd SEC-01: H1, M3b, L4).

- poprzedni adres e-mail – drugi odbiorca listu o resecie 2FA, gdy adres zmieniono niedawno,
- zamknięcie **innych** sesji konta przy włączeniu, wyłączeniu i resecie 2FA,
- „zapomnij wszystkie urządzenia” (unieważnienie ciasteczek ``2fa_trust``).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def remember_previous_email(user, old_email: str) -> None:
    """Zapisuje adres, z którego konto właśnie przeniesiono, i sprząta wpisy starsze niż okno."""
    from .models import PREVIOUS_EMAIL_DAYS, PreviousEmail

    if not old_email or old_email == getattr(user, "email", None):
        return
    PreviousEmail.objects.filter(changed_at__lt=timezone.now() - timedelta(days=PREVIOUS_EMAIL_DAYS)).delete()
    PreviousEmail.objects.create(user=user, email=old_email)


def recent_previous_emails(user) -> list[str]:
    """Poprzednie adresy konta z ostatnich ``PREVIOUS_EMAIL_DAYS`` dni (bez bieżącego, bez powtórzeń)."""
    from .models import PREVIOUS_EMAIL_DAYS, PreviousEmail

    since = timezone.now() - timedelta(days=PREVIOUS_EMAIL_DAYS)
    found: list[str] = []
    rows = PreviousEmail.objects.filter(user=user, changed_at__gte=since).order_by("-changed_at")
    for email in rows.values_list("email", flat=True):
        if email and email.lower() != (user.email or "").lower() and email not in found:
            found.append(email)
    return found


def drop_other_sessions(user, *, keep_session_key: str | None = None) -> int:
    """Kasuje sesje konta poza bieżącą (M3b).

    Po włączeniu 2FA sesja „po samym haśle” z innej przeglądarki niosła znacznik zwolnienia albo
    okresu przejściowego; po wyłączeniu i po resecie – znacznik „zweryfikowano”. Żaden z nich nie
    jest już prawdą, a znaczników w cudzych sesjach nie da się poprawić – można je tylko zamknąć.
    Ta sama metoda, co przy usuwaniu konta (sesje w bazie, zaszyfrowane – trzeba je odkodować);
    zdarzenie jest rzadkie (włączenie, wyłączenie, reset), więc przegląd tabeli jest do przyjęcia.
    """
    from django.contrib.sessions.models import Session

    identifier = str(user.pk)
    stale = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        if session.session_key == keep_session_key:
            continue
        try:
            if session.get_decoded().get("_auth_user_id") == identifier:
                stale.append(session.session_key)
        except Exception:  # noqa: BLE001 - uszkodzona sesja nie może zatrzymać czynności 2FA
            logger.warning("2FA: nie udało się odkodować sesji przy zamykaniu sesji konta.")
    if not stale:
        return 0
    return Session.objects.filter(session_key__in=stale).delete()[0]


def revoke_trusted_devices(user) -> None:
    """„Zapomnij wszystkie urządzenia” – każde wydane wcześniej ciasteczko ``2fa_trust`` traci ważność."""
    from .models import TrustRevocation

    TrustRevocation.objects.update_or_create(user=user, defaults={"revoked_at": timezone.now()})


def trust_revoked_at(user):
    from .models import TrustRevocation

    row = TrustRevocation.objects.filter(user=user).first()
    return row.revoked_at if row is not None else None
