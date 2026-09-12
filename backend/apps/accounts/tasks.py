"""Kosiarka kont nieaktywowanych – decyzja organizatora, nie optymalizacja bazy.

Konto, którego adresu nikt nie potwierdził w ciągu ``ACTIVATION_MAX_AGE`` (cztery godziny),
jest kasowane razem z profilem. Powód jest praktyczny i dotyczy uczestnika, nie serwera:
adres e-mail jest u nas unikalny, więc konto-widmo **blokuje ten adres**. Bez kosiarki uczeń,
który pomylił się w haśle albo nie doczekał listu i zamknął kartę, dostawałby przy drugiej próbie
„Konto z tym adresem e-mail już istnieje.” i nie miałby żadnej drogi dalej: aktywacji nie dostanie
(link przepadł), a resetu hasła nie użyje (konto nieaktywne nie dostaje listu resetu). Skasowanie
wiersza zwalnia adres i rejestracja po prostu działa.

Czego kosiarka **nie** rusza:

- kont z potwierdzonym adresem (``email_verified_at`` wypełnione) – także tych zablokowanych przez
  organizatora (``is_active=False``) i tych zanonimizowanych na żądanie RODO. ``is_active=False``
  niesie kilka różnych znaczeń, więc sam ten warunek nie może rozstrzygać o skasowaniu danych,
- kont młodszych niż okno aktywacji – łącznie z tymi, które właśnie powstały i których list jeszcze
  leży w kolejce,
- kont, do których cokolwiek się odwołuje w dokumentacji zawodów (zgłoszenie, praca, recenzja).
  Świeże konto takich obiektów mieć nie może, więc wystąpienie takiego wiersza znaczy, że stało się
  coś, czego nie przewidzieliśmy – i wtedy kasowanie danych jest najgorszą z możliwych reakcji.
  Taki wiersz zostaje i trafia do logu, dla człowieka.

Wpis audytowy jest **jeden na przebieg** i niesie wyłącznie liczby. Wpis per konto byłby listą
adresów, które ktoś kiedyś próbował zarejestrować – czyli dokładnie tą daną osobową, którą ten
przebieg właśnie usunął.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.core.models import audit

from .activation import ACTIVATION_MAX_AGE
from .models import User

logger = logging.getLogger(__name__)


def unactivated_accounts(now=None):
    """Konta przeterminowane: nieaktywne, bez potwierdzenia adresu, starsze niż okno aktywacji."""
    now = now or timezone.now()
    return User.objects.filter(
        is_active=False,
        email_verified_at__isnull=True,
        date_joined__lt=now - timedelta(seconds=ACTIVATION_MAX_AGE),
    )


@transaction.atomic
def purge_unactivated_accounts(now=None) -> dict:
    """Kasuje przeterminowane konta nieaktywowane. Zwraca ``{"deleted": n, "skipped": m}``.

    Funkcja jest zwykłym wywołaniem, a zadanie Celery i komenda zarządzająca są nad nią cienkimi
    powłokami: dzięki temu test nie musi udawać kolejki, a koordynator może uruchomić to samo
    ręcznie, kiedy chce zwolnić adres od razu.
    """
    from .profile import competition_footprint

    now = now or timezone.now()
    deleted = 0
    skipped = 0
    # ``select_for_update``: między wybraniem wiersza a jego skasowaniem ktoś może kliknąć link
    # aktywacyjny. Blokada plus powtórne sprawdzenie stanu pod blokadą sprawiają, że wyścig kończy
    # się na korzyść uczestnika – aktywowane konto zostaje.
    for user in User.objects.select_for_update().filter(pk__in=unactivated_accounts(now)).iterator():
        if user.email_verified_at is not None or user.is_active:
            skipped += 1
            continue
        footprint = competition_footprint(user)
        if any(footprint.values()):
            logger.warning(
                "Konto #%s nie jest aktywowane, ale odwołuje się do niego dokumentacja zawodów "
                "(%s) – pomijam skasowanie.",
                user.pk,
                footprint,
            )
            skipped += 1
            continue
        user.delete()
        deleted += 1
    if deleted or skipped:
        # Cel wpisu to ``User`` jako **typ**, a nie konkretny wiersz (tych już nie ma). Stąd
        # ``target_id`` pustej wartości i liczby w ``diff`` – bez ani jednego adresu e-mail.
        audit(None, "account.purged_unactivated", User(pk=0), {"deleted": deleted, "skipped": skipped})
    logger.info("Kosiarka kont nieaktywowanych: skasowano %s, pominięto %s.", deleted, skipped)
    return {"deleted": deleted, "skipped": skipped}


@shared_task(name="apps.accounts.tasks.purge_unactivated_accounts")
def purge_unactivated_accounts_task() -> dict:
    """Wywołanie kosiarki z ``beat`` (co 15 minut, ``CELERY_BEAT_SCHEDULE``)."""
    return purge_unactivated_accounts()
