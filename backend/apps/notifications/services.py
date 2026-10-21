"""Zapis, odczyt i sprzątanie powiadomień panelu uczestnika (UX-PANEL-01).

Reguły:

- **izolacja**: każde zapytanie idzie po parze (konto, konkurs). Powiadomienie z konkursu A nie
  istnieje pod domeną konkursu B – ani na liście, ani w liczniku, ani pod adresem ``/<pk>/``,
- **scalanie** (D6): zdarzenie z kluczem (``chat:12``) podbija nieprzeczytane powiadomienie z tym
  samym kluczem. Więz w bazie (``notif_one_unread_per_key``) pilnuje, że dwa równoległe zapisy nie
  zrobią z jednej rozmowy dwóch pozycji,
- **tylko uczestnicy** (D9): haki zapisują wyłącznie dla kont z profilem uczestnika w konkursie
  (:func:`participant_user_ids`) – lista jest na ``/me/notifications/``, a tam wchodzi tylko on,
- **retencja** (D10): wiersze starsze niż ``NOTIFICATION_RETENTION_DAYS`` (90) znikają nocą.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from . import kinds
from .models import PARAM_MAX_LENGTH, Notification

#: Domyślny czas życia powiadomienia. Dłużej nie ma po co: wiadomość, decyzja i wyniki leżą na
#: swoich ekranach, a powiadomienie jest wyłącznie drogowskazem do nich.
DEFAULT_RETENTION_DAYS = 90
#: Pozycji na stronę listy.
PAGE_SIZE = 30
#: Porcja ``bulk_create`` przy rozsyłaniu do wszystkich uczestników (ogłoszenie, wyniki).
BULK_BATCH = 1000
#: Atrybut żądania z policzonym licznikiem – menu i kafel pulpitu pytają o niego w jednym żądaniu.
_REQUEST_MEMO = "_notifications_unread"


def retention_days() -> int:
    return int(getattr(settings, "NOTIFICATION_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def _clip(params: dict | None) -> dict:
    """Parametry zdania – napisy przycięte do długości kolumny tytułu, nic poza napisami i liczbami."""
    clean = {}
    for key, value in (params or {}).items():
        if isinstance(value, (int, float)):
            clean[key] = value
        else:
            clean[key] = str(value)[:PARAM_MAX_LENGTH]
    return clean


def _fields(kind: str, target: str, args, suffix: str, params: dict | None) -> dict:
    return {
        "kind": kind,
        "target": target,
        "target_args": [str(arg) if not isinstance(arg, int) else arg for arg in args],
        "target_suffix": suffix,
        "params": _clip(params),
    }


# --- zapis --------------------------------------------------------------------------------------


def participant_user_ids(competition, user_ids=None) -> set[int]:
    """Konta z profilem uczestnika **w tym konkursie** (aktywne). ``user_ids=None`` = wszyscy."""
    from apps.accounts.models import Participant

    rows = Participant.objects.filter(competition=competition, user__is_active=True)
    if user_ids is not None:
        rows = rows.filter(user_id__in=list(user_ids))
    return set(rows.values_list("user_id", flat=True))


def notify(
    user_id: int,
    competition,
    kind: str,
    target: str,
    *,
    args=(),
    suffix: str = "",
    params: dict | None = None,
    key: str = "",
    now=None,
) -> None:
    """Jedno powiadomienie (albo podbicie nieprzeczytanego z tym samym kluczem)."""
    notify_many(
        [user_id], competition, kind, target, args=args, suffix=suffix, params=params, key=key, now=now
    )


def notify_many(
    user_ids,
    competition,
    kind: str,
    target: str,
    *,
    args=(),
    suffix: str = "",
    params: dict | None = None,
    key: str = "",
    now=None,
) -> int:
    """Powiadomienie dla wielu kont naraz – stała liczba zapytań, niezależna od liczby odbiorców.

    Zwraca liczbę kont, którym coś przybyło (nowe albo podbite).
    """
    ids = {int(pk) for pk in user_ids if pk is not None}
    if not ids or competition is None:
        return 0
    now = now or timezone.now()
    competition_id = getattr(competition, "pk", competition)
    fields = _fields(kind, target, args, suffix, params)
    bumped: set[int] = set()
    if key:
        pending = Notification.objects.filter(
            competition_id=competition_id, user_id__in=ids, dedupe_key=key, read_at__isnull=True
        )
        bumped = set(pending.values_list("user_id", flat=True))
        if bumped:
            pending.update(created_at=now, **fields)
    fresh = [
        Notification(competition_id=competition_id, user_id=pk, dedupe_key=key, created_at=now, **fields)
        for pk in sorted(ids - bumped)
    ]
    try:
        # Punkt zapisu: wyścig z drugim zapisem tego samego klucza kończy się więzem, a nie
        # wycofaniem transakcji wołającego (wiadomość, decyzja koordynatora).
        with transaction.atomic():
            Notification.objects.bulk_create(fresh, batch_size=BULK_BATCH, ignore_conflicts=bool(key))
    except IntegrityError:  # pragma: no cover - ``ignore_conflicts`` łapie to w PostgreSQL
        pass
    return len(ids)


# --- odczyt -------------------------------------------------------------------------------------


def _scope(user, competition):
    return Notification.objects.filter(user_id=user.pk, competition_id=getattr(competition, "pk", None))


def unread_count_for(user, competition) -> int:
    if user is None or not getattr(user, "is_authenticated", False) or competition is None:
        return 0
    return _scope(user, competition).filter(read_at__isnull=True).count()


def unread_count(request) -> int:
    """Licznik dla menu panelu i kafla pulpitu – **jedno** zapytanie na żądanie."""
    cached = getattr(request, _REQUEST_MEMO, None)
    if cached is None:
        cached = unread_count_for(getattr(request, "user", None), getattr(request, "competition", None))
        setattr(request, _REQUEST_MEMO, cached)
    return cached


def forget_unread_count(request) -> None:
    if hasattr(request, _REQUEST_MEMO):
        delattr(request, _REQUEST_MEMO)


def target_url(notification: Notification) -> str | None:
    """Adres celu w bieżącym żądaniu (prefiks konkursu z ``reverse``); ``None`` = ekranu już nie ma."""
    try:
        path = reverse(notification.target, args=list(notification.target_args or []))
    except NoReverseMatch:
        return None
    return f"{path}{notification.target_suffix}"


def page_for(user, competition, number) -> object:
    paginator = Paginator(_scope(user, competition).order_by("-created_at", "-pk"), PAGE_SIZE)
    return paginator.get_page(number)


def present(notification: Notification) -> dict:
    """Pozycja listy dla szablonu: zdanie w języku oglądającego, ikona, odnośnik przez :func:`open_url`."""
    spec = kinds.spec_for(notification.kind)
    return {
        "pk": notification.pk,
        "text": kinds.text_for(notification.kind, notification.params),
        "icon": spec.icon,
        "kind": notification.kind,
        "created_at": notification.created_at,
        "is_read": notification.is_read,
        "has_target": target_url(notification) is not None,
    }


def open_notification(user, competition, pk: int, now=None) -> str | None:
    """Oznacza jako przeczytane i oddaje adres celu. ``Notification.DoesNotExist`` = cudze / nie ma."""
    notification = _scope(user, competition).get(pk=pk)
    if notification.read_at is None:
        _scope(user, competition).filter(pk=notification.pk).update(read_at=now or timezone.now())
    return target_url(notification)


def mark_all_read(user, competition, now=None) -> int:
    return _scope(user, competition).filter(read_at__isnull=True).update(read_at=now or timezone.now())


def mark_read_by_key(user_id: int, competition, key: str, now=None) -> int:
    """Odczyt na ekranie celu (otwarty wątek czatu) gasi powiadomienie z tym kluczem."""
    if not key or competition is None:
        return 0
    return Notification.objects.filter(
        user_id=user_id,
        competition_id=getattr(competition, "pk", competition),
        dedupe_key=key,
        read_at__isnull=True,
    ).update(read_at=now or timezone.now())


# --- RODO ---------------------------------------------------------------------------------------


def purge_expired(now=None) -> int:
    """Retencja (D10): usuwa powiadomienia starsze niż ``NOTIFICATION_RETENTION_DAYS``."""
    cutoff = (now or timezone.now()) - timedelta(days=retention_days())
    deleted, _per_model = Notification.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def erase_for_user(user) -> int:
    """Anonimizacja konta: powiadomienia tej osoby znikają ze wszystkich konkursów."""
    deleted, _per_model = Notification.objects.filter(user_id=user.pk).delete()
    return deleted


def export_rows(user, competition=None) -> list[dict]:
    """Sekcja ``powiadomienia_w_panelu`` eksportu danych (art. 15) – zawężona do konkursu, jeśli podany."""
    rows = Notification.objects.filter(user_id=user.pk).order_by("-created_at", "-pk")
    if competition is not None:
        rows = rows.filter(competition_id=competition.pk)
    return [
        {
            "konkurs": row.competition_id,
            "rodzaj": row.kind,
            "tresc": kinds.text_for(row.kind, row.params),
            "utworzono": row.created_at.isoformat(),
            "przeczytano": row.read_at.isoformat() if row.read_at else None,
        }
        for row in rows
    ]
