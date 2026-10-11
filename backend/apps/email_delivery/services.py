"""Zapis odbić, odczyt stanu adresu i wstrzymanie wysyłki (MAIL-02 § 2.3–2.8).

Jedno wejście dla każdego, kto pyta „czy ten adres odbija”: baner (przez cache), ``queue_mail``
(listy nieobowiązkowe), komunikaty grupowe, lista koordynatora, eksport danych konta.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from .bounces import REASON_LENGTH, Bounce
from .models import DeliveryStatus, Source

logger = logging.getLogger(__name__)

#: Ile dni wiersz bez nowego zdarzenia zostaje w bazie (RODO, art. 5 ust. 1 lit. e). Rok, bo
#: konto bywa nieruszane między edycjami, a adres, który twardo odbił w zeszłym roku, w tym roku
#: prawdopodobnie dalej nie istnieje – ale dłużej nie ma podstawy tego pamiętać.
RETENTION_DAYS = 365

#: Czas życia odpowiedzi banera w cache'u (sekundy). Zapis odbicia i reset unieważniają ją od razu,
#: więc to tylko górna granica dla zmian zrobionych poza tym modułem (np. w /admin/).
BANNER_CACHE_SECONDS = 300


def normalize(email: str) -> str:
    return (email or "").strip().strip("<>").strip().lower()


def tracking_enabled() -> bool:
    return bool(getattr(settings, "EMAIL_BOUNCE_TRACKING", False))


def _cache_key(email: str) -> str:
    return "email_delivery:status:" + hashlib.sha256(email.encode()).hexdigest()[:32]


def _forget(email: str) -> None:
    cache.delete(_cache_key(email))


def _domain(email: str) -> str:
    return email.rpartition("@")[2]


def record_bounce(bounce: Bounce, *, source: str = Source.DSN, now=None) -> DeliveryStatus | None:
    """Dopisuje odbicie do stanu adresu. Twarde ustawia ``undeliverable_at`` (pierwsze zostaje)."""
    email = normalize(bounce.email)
    if "@" not in email or len(email) > 254:
        return None
    now = now or timezone.now()
    row, _created = DeliveryStatus.objects.get_or_create(email=email)
    reason = (bounce.reason or bounce.status or "")[:REASON_LENGTH]
    if bounce.hard:
        # Aktualizacja w bazie (F, Coalesce), nie na obiekcie: dwa przebiegi zadania i odmowa
        # w workerze mogą dopisać odbicie tego samego adresu naraz – licznik nie może zgubić żadnego.
        DeliveryStatus.objects.filter(pk=row.pk).update(
            hard_bounces=F("hard_bounces") + 1,
            undeliverable_at=Coalesce(F("undeliverable_at"), now),
            reason=reason,
            status_code=(bounce.status or "")[:16],
            source=source,
            updated_at=now,
        )
    else:
        DeliveryStatus.objects.filter(pk=row.pk).update(
            soft_bounces=F("soft_bounces") + 1,
            last_soft_bounce_at=now,
            last_soft_reason=reason,
            updated_at=now,
        )
    _forget(email)
    # W logu domena i kod, bez adresu: log workera nie ma być listą adresów uczestników.
    logger.info(
        "Odbicie %s (%s, %s) adresu w domenie %s",
        "twarde" if bounce.hard else "miękkie",
        source,
        bounce.status or "bez kodu",
        _domain(email),
    )
    row.refresh_from_db()
    return row


def undeliverable(email: str) -> DeliveryStatus | None:
    """Wiersz adresu z twardym odbiciem albo ``None`` (bez cache'u – dla serwisów i widoków)."""
    email = normalize(email)
    if not email:
        return None
    return DeliveryStatus.objects.filter(email=email, undeliverable_at__isnull=False).first()


def is_suppressed(email: str) -> bool:
    """Czy wstrzymać list **nieobowiązkowy** na ten adres (twarde odbicie, § 2.7).

    Bez ``EMAIL_BOUNCE_TRACKING`` – zawsze ``False`` i bez zapytania: instalacja bez śledzenia nie ma
    czego wstrzymywać, a jej wysyłka ma kosztować tyle samo zapytań, co przed MAIL-02.
    """
    email = normalize(email)
    return (
        bool(email)
        and tracking_enabled()
        and DeliveryStatus.objects.filter(email=email, undeliverable_at__isnull=False).exists()
    )


def suppressed_among(emails) -> set[str]:
    """Adresy (małymi literami) z listy, które twardo odbiły – jedno zapytanie na porcję wysyłki."""
    normalized = {normalize(email) for email in emails or ()} - {""}
    if not normalized or not tracking_enabled():
        return set()
    return set(
        DeliveryStatus.objects.filter(email__in=normalized, undeliverable_at__isnull=False).values_list(
            "email", flat=True
        )
    )


def banner_status(email: str) -> dict | None:
    """Dane banera (data i powód) z cache'u – bez zapytania do bazy na każdej stronie serwisu."""
    email = normalize(email)
    if not email or not tracking_enabled():
        return None
    key = _cache_key(email)
    cached = cache.get(key)
    if cached is None:
        row = undeliverable(email)
        cached = {"since": row.undeliverable_at, "reason": row.reason} if row else False
        cache.set(key, cached, BANNER_CACHE_SECONDS)
    return cached or None


def clear(email: str) -> bool:
    """Kasuje stan adresu (potwierdzenie właściciela, decyzja koordynatora, zmiana adresu konta)."""
    email = normalize(email)
    if not email:
        return False
    deleted, _ = DeliveryStatus.objects.filter(email=email).delete()
    _forget(email)
    return bool(deleted)


def purge_expired(now=None) -> int:
    """Kasuje wiersze bez zdarzenia od :data:`RETENTION_DAYS` dni. Zwraca liczbę skasowanych."""
    limit = (now or timezone.now()) - timedelta(days=RETENTION_DAYS)
    deleted, _ = DeliveryStatus.objects.filter(updated_at__lt=limit).delete()
    return deleted


def undeliverable_accounts(competition, *, limit: int = 2000) -> list[dict]:
    """Konta **tego konkursu** z twardo odbitym adresem – lista i CSV koordynatora (§ 2.6).

    Zakres kont to ``users_for_competition`` (to samo, co lista kont koordynatora): konto cudzego
    konkursu nie wypływa, nawet jeśli jego adres odbił. Adres konta jest zapisywany małymi literami
    (``User.save``), więc złączenie po równości jest dokładne.
    """
    from apps.web.views.coordinator_accounts import users_for_competition

    statuses = DeliveryStatus.objects.filter(undeliverable_at__isnull=False)
    users = list(
        users_for_competition(competition)
        .filter(email__in=statuses.values("email"))
        .order_by("email")
        .only("pk", "email", "first_name", "last_name", "is_active")[:limit]
    )
    by_email = {row.email: row for row in statuses.filter(email__in=[user.email.lower() for user in users])}
    rows = []
    for user in users:
        status = by_email.get(user.email.lower())
        if status is not None:
            rows.append({"user": user, "status": status})
    rows.sort(key=lambda item: item["status"].undeliverable_at, reverse=True)
    return rows


def export_section(user) -> dict:
    """Sekcja ``doreczalnosc_poczty`` w eksporcie danych konta (art. 15/20 RODO)."""
    row = DeliveryStatus.objects.filter(email=normalize(getattr(user, "email", ""))).first()
    if row is None:
        return {"adres_niedoreczalny": False}
    return {
        "adres_niedoreczalny": row.undeliverable,
        "twarde_odbicie_od": row.undeliverable_at.isoformat() if row.undeliverable_at else None,
        "powod": row.reason,
        "kod_stanu": row.status_code,
        "zrodlo": row.source,
        "twarde_odbicia": row.hard_bounces,
        "miekkie_odbicia": row.soft_bounces,
        "ostatnie_miekkie_odbicie": row.last_soft_bounce_at.isoformat() if row.last_soft_bounce_at else None,
        "powod_miekkiego_odbicia": row.last_soft_reason,
        "ostatnie_zdarzenie": row.updated_at.isoformat(),
    }
