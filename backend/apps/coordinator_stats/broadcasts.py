"""Raport wysyłki komunikatu: nagłówek listu, liczniki porcji, przypisanie odbić (STATS-02 § 2).

Droga liczby od kliknięcia „Wyślij” do raportu:

1. ``accounts.messaging.send_broadcast_chunk`` dokłada do każdego listu :func:`headers_for`
   (``X-Olimpiada-Broadcast: <id>:<podpis>``) i po porcji woła :func:`record_chunk` z adresami
   zakolejkowanymi i pominiętymi – tu zamieniają się w **liczby** po grupach domen,
2. relay odrzuca list w trakcie wysyłki albo odsyła zawiadomienie (DSN); ``apps.email_delivery``
   wysyła sygnał ``bounces_reported`` z nagłówkami listu pierwotnego, a :func:`record_bounces`
   czyta z nich komunikat i dopisuje odbicie (adres – do listy koordynatora, liczba – do licznika),
3. ekran czyta :func:`broadcast_report` (szczegóły) i :func:`attach_summaries` (historia).

Podpis nagłówka: przez ten sam relay idą też listy przekazywane dalej (``postmaster@``), a nagłówki
takiego listu pisze nadawca z zewnątrz. Bez podpisu dałoby się dopisać odbicie do dowolnego
komunikatu dowolnego konkursu.
"""

from __future__ import annotations

import logging
from collections import Counter

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from . import domains
from .models import KIND_WEIGHT, BounceKind, BroadcastBounce, BroadcastDomainTally

logger = logging.getLogger(__name__)

HEADER = "X-Olimpiada-Broadcast"
_SALT = "coordinator_stats.broadcast"
#: Najdłuższy zapisany powód odbicia – lista koordynatora ma pokazać istotę, a nie cały raport serwera.
REASON_LENGTH = 300
#: Najwięcej adresów na liście odbitych w szczegółach raportu (reszta jest w licznikach).
BOUNCE_LIST_LIMIT = 1000

_COUNTER = {
    BounceKind.HARD: "bounced_hard",
    BounceKind.POLICY: "bounced_policy",
    BounceKind.SOFT: "bounced_soft",
}


# --- nagłówek ----------------------------------------------------------------------------------------


def headers_for(broadcast_id: int) -> dict[str, str]:
    """Nagłówki listu komunikatu – identyfikator z podpisem, nic o odbiorcy."""
    return {HEADER: signing.Signer(salt=_SALT).sign(str(int(broadcast_id)))}


def broadcast_id_from(value) -> int | None:
    """Identyfikator komunikatu z wartości nagłówka albo ``None`` (brak, śmieci, zły podpis)."""
    # Podpis nie zawiera spacji, więc nagłówek nigdy nie jest zawijany – wystarczy obciąć brzegi.
    text = str(value or "").strip()
    if not text:
        return None
    try:
        raw = signing.Signer(salt=_SALT).unsign(text)
    except signing.BadSignature:
        logger.warning("Nagłówek %s z nieprawidłowym podpisem – odbicie bez komunikatu.", HEADER)
        return None
    return int(raw) if raw.isdigit() else None


# --- liczniki porcji wysyłki --------------------------------------------------------------------------


def _bump(broadcast_id: int, group: str, **fields: int) -> None:
    """``field += n`` w wierszu (komunikat, grupa) – zakładanym przy pierwszym użyciu.

    Porcje jednego komunikatu idą równolegle, więc przyrost jest wyrażeniem ``F`` w bazie, a nie
    sumą policzoną w Pythonie; ``get_or_create`` znosi wyścig dwóch porcji zakładających ten sam
    wiersz (więz unikalności i ponowny odczyt).
    """
    fields = {name: value for name, value in fields.items() if value}
    if not fields:
        return
    row, _created = BroadcastDomainTally.objects.get_or_create(broadcast_id=broadcast_id, domain_group=group)
    BroadcastDomainTally.objects.filter(pk=row.pk).update(
        **{name: F(name) + value for name, value in fields.items()}
    )


def record_chunk(broadcast_id: int, *, queued, suppressed) -> None:
    """Liczby porcji po grupach domen: zakolejkowane i pominięte (twarde odbicie, MAIL-02 § 2.7).

    Najwyżej dwa zapytania na grupę domen obecną w porcji (pięć grup), niezależnie od liczby adresów.
    Całość w punkcie zapisu (``atomic``): błąd licznika nie może zostawić przerwanej transakcji
    wołającemu, który go połyka i kończy porcję.
    """
    queued_by = Counter(domains.group_of(email) for email in queued)
    suppressed_by = Counter(domains.group_of(email) for email in suppressed)
    with transaction.atomic():
        for group in domains.GROUPS:
            _bump(broadcast_id, group, queued=queued_by[group], suppressed=suppressed_by[group])


# --- odbicia -----------------------------------------------------------------------------------------


def is_s3150(reason: str) -> bool:
    """Odrzucenie Microsoftu z powodu blokady adresu IP nadawcy („… on our block list (S3150)”)."""
    return "s3150" in (reason or "").lower()


def kind_of(bounce) -> str:
    """Rodzaj odbicia w raporcie: polityka (``x.7.x``, ``5.0.350``, S3150), twarde albo inne.

    Polityka **przed** twardym, choć twarde według MAIL-02 się z nią nie pokrywa: gdyby kiedyś
    tablica twardych kodów się rozrosła, odrzucenie z powodu naszej reputacji nadal ma stać w
    kolumnie „polityka”, bo to ono mówi organizatorowi, że problem jest po stronie nadawcy.
    """
    status = bounce.status or ""
    parts = status.split(".")
    if is_s3150(bounce.reason) or status == "5.0.350" or (len(parts) == 3 and parts[1] == "7"):
        return BounceKind.POLICY
    if bounce.hard:
        return BounceKind.HARD
    return BounceKind.SOFT


def record_bounces(bounces, headers, *, source: str = "", now=None) -> int:
    """Dopisuje odbicia do komunikatu z nagłówka ``headers``. Zwraca liczbę **nowych** adresów.

    ``headers`` – nagłówki listu pierwotnego (``email.message.Message`` albo słownik). Bez
    poprawnego nagłówka albo przy komunikacie, którego już nie ma – nic (odbicie listu spoza
    komunikatów, np. aktywacyjnego, zostaje sprawą wyłącznie MAIL-02).

    Ten sam adres w tym samym komunikacie to jeden wiersz: zawiadomienie o opóźnieniu i późniejsza
    ostateczna odmowa, ponowienie porcji po awarii relaya. Licznik przechodzi wtedy z rodzaju na
    rodzaj (poważniejszy wygrywa), zamiast liczyć adres dwa razy.
    """
    from apps.accounts.models import MessageBroadcast

    broadcast_id = broadcast_id_from(headers.get(HEADER) if headers is not None else None)
    if broadcast_id is None or not MessageBroadcast.objects.filter(pk=broadcast_id).exists():
        return 0
    now = now or timezone.now()
    created = 0
    for bounce in bounces:
        email = (bounce.email or "").strip().strip("<>").strip().lower()
        if "@" not in email or len(email) > 254:
            continue
        kind = kind_of(bounce)
        s3150 = is_s3150(bounce.reason)
        group = domains.group_of(email)
        values = {
            "domain_group": group,
            "kind": kind,
            "status_code": (bounce.status or "")[:16],
            "reason": (bounce.reason or bounce.status or "")[:REASON_LENGTH],
            "microsoft_s3150": s3150,
            "source": (source or "")[:8],
        }
        try:
            with transaction.atomic():
                row = (
                    BroadcastBounce.objects.select_for_update()
                    .filter(broadcast_id=broadcast_id, email=email)
                    .first()
                )
                if row is None:
                    BroadcastBounce.objects.create(
                        broadcast_id=broadcast_id, email=email, created_at=now, **values
                    )
                    _bump(broadcast_id, group, **{_COUNTER[kind]: 1, "bounced_s3150": int(s3150)})
                    created += 1
                    continue
                if KIND_WEIGHT[kind] < KIND_WEIGHT[BounceKind(row.kind)]:
                    continue  # łagodniejsze odbicie po poważniejszym niczego nie zmienia
                if kind != row.kind or (s3150 and not row.microsoft_s3150):
                    # Zmiana rodzaju: licznik starego rodzaju w dół, nowego w górę – jedna osoba,
                    # jedno odbicie w raporcie.
                    BroadcastDomainTally.objects.filter(
                        broadcast_id=broadcast_id, domain_group=row.domain_group
                    ).update(
                        **{_COUNTER[BounceKind(row.kind)]: F(_COUNTER[BounceKind(row.kind)]) - 1},
                        **({"bounced_s3150": F("bounced_s3150") - 1} if row.microsoft_s3150 else {}),
                    )
                    _bump(broadcast_id, group, **{_COUNTER[kind]: 1, "bounced_s3150": int(s3150)})
                for name, value in values.items():
                    setattr(row, name, value)
                row.save()
        except IntegrityError:
            # Dwa przebiegi naraz założyły ten sam wiersz – drugi jest powtórzeniem pierwszego.
            continue
    if created:
        logger.info("Komunikat %s: %s nowych odbić (%s).", broadcast_id, created, source or "?")
    return created


# --- odczyt ------------------------------------------------------------------------------------------


def _share(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def broadcast_report(broadcast) -> dict:
    """Szczegóły raportu: liczby ogółem, tabela grup domen i lista odbitych adresów.

    Dwa zapytania (liczniki, lista), bez względu na liczbę odbiorców. ``has_data`` – czy komunikat
    ma liczniki (komunikaty sprzed STATS-02 ich nie mają i raport mówi „brak danych”, zamiast
    udawać zera).
    """
    from apps.results.statistics import width_class

    tallies = {row.domain_group: row for row in BroadcastDomainTally.objects.filter(broadcast=broadcast)}
    peak = max((row.queued for row in tallies.values()), default=0)
    rows = []
    for group in domains.GROUPS:
        row = tallies.get(group)
        if row is None:
            continue
        rows.append(
            {
                "group": group,
                "label": domains.label(group),
                "queued": row.queued,
                "suppressed": row.suppressed,
                "bounced": row.bounced,
                "hard": row.bounced_hard,
                "policy": row.bounced_policy,
                "soft": row.bounced_soft,
                "s3150": row.bounced_s3150,
                "bounce_share": _share(row.bounced, row.queued),
                "queued_width": width_class(row.queued * 100 / peak if peak else 0),
                # Ta sama skala, co słupek wysłanych: słupek odbitych nie może być dłuższy od wysłanych.
                "bounced_width": width_class(row.bounced * 100 / peak if peak else 0),
                "microsoft": group == domains.MICROSOFT,
            }
        )
    totals = {
        key: sum(row[key] for row in rows)
        for key in ("queued", "suppressed", "bounced", "hard", "policy", "soft", "s3150")
    }
    totals["bounce_share"] = _share(totals["bounced"], totals["queued"])
    bounces = list(
        BroadcastBounce.objects.filter(broadcast=broadcast).order_by("kind", "domain_group", "email")[
            :BOUNCE_LIST_LIMIT
        ]
    )
    return {
        "has_data": bool(tallies),
        "rows": rows,
        "totals": totals,
        "bounces": bounces,
        "bounce_list_limited": len(bounces) >= BOUNCE_LIST_LIMIT,
        "bounce_list_limit": BOUNCE_LIST_LIMIT,
    }


def attach_summaries(broadcasts: list) -> list:
    """Dokleja do każdego komunikatu ``delivery`` (zakolejkowane, pominięte, odbite) – jedno zapytanie.

    ``delivery`` jest ``None`` przy komunikacie bez liczników (sprzed STATS-02, zaplanowany,
    anulowany) – szablon pokazuje wtedy kreskę, a w kolumnie „W kolejce” dotychczasowy ``sent_count``.
    """
    ids = [broadcast.pk for broadcast in broadcasts]
    sums = {}
    if ids:
        sums = {
            row["broadcast_id"]: row
            for row in BroadcastDomainTally.objects.filter(broadcast_id__in=ids)
            .values("broadcast_id")
            .annotate(
                queued_sum=Sum("queued"),
                suppressed_sum=Sum("suppressed"),
                hard_sum=Sum("bounced_hard"),
                policy_sum=Sum("bounced_policy"),
                soft_sum=Sum("bounced_soft"),
                s3150_sum=Sum("bounced_s3150"),
            )
            .order_by()
        }
    for broadcast in broadcasts:
        row = sums.get(broadcast.pk)
        broadcast.delivery = (
            None
            if row is None
            else {
                "queued": row["queued_sum"] or 0,
                "suppressed": row["suppressed_sum"] or 0,
                "bounced": (row["hard_sum"] or 0) + (row["policy_sum"] or 0) + (row["soft_sum"] or 0),
                "s3150": row["s3150_sum"] or 0,
            }
        )
    return broadcasts


def purge_expired(now=None) -> int:
    """Kasuje odbicia (adresy) starsze niż retencja MAIL-02. Liczniki zostają."""
    from datetime import timedelta

    from apps.email_delivery.services import RETENTION_DAYS

    limit = (now or timezone.now()) - timedelta(days=RETENTION_DAYS)
    deleted, _ = BroadcastBounce.objects.filter(updated_at__lt=limit).delete()
    return deleted


def forget_address(email: str) -> int:
    """Kasuje odbicia adresu we wszystkich komunikatach (zmiana albo usunięcie adresu konta)."""
    email = (email or "").strip().lower()
    if not email:
        return 0
    deleted, _ = BroadcastBounce.objects.filter(email=email).delete()
    return deleted


def export_rows(email: str) -> list[dict]:
    """Odbicia adresu w komunikatach – do eksportu danych konta (art. 15/20 RODO)."""
    email = (email or "").strip().lower()
    if not email:
        return []
    return [
        {
            "komunikat": row.broadcast.subject,
            "wyslany": row.broadcast.created_at.isoformat(),
            "rodzaj": BounceKind(row.kind).label,
            "kod_stanu": row.status_code,
            "powod": row.reason,
            "pierwsze_odbicie": row.created_at.isoformat(),
        }
        for row in BroadcastBounce.objects.filter(email=email)
        .select_related("broadcast")
        .order_by("created_at")
    ]
