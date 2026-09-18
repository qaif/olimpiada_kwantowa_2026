"""Kwalifikacja ręczna: decyzja komitetu, która wygrywa z progiem punktowym.

Po co w ogóle taka furtka, skoro cała idea progu polega na tym, że jest jeden dla wszystkich.
Bo regulamin zna sytuacje, których punkty nie opisują, a komitet i tak musi je rozstrzygnąć:
zerwane łącze w trakcie rozmowy kwalifikacyjnej, praca oddana poza systemem na wyraźne polecenie
organizatora, wynik unieważniony za naruszenie zasad mimo wysokiej sumy. Dopóki tej drogi nie
było, jedynym wyjściem było majstrowanie przy punktach – czyli wpisanie do protokołu nieprawdy
o tym, jak praca została oceniona.

Trzy zasady, które ten moduł egzekwuje:

- **decyzja zawsze z uzasadnieniem.** Wyjątek od reguły jest dokładnie tym, co ktoś kiedyś będzie
  musiał wytłumaczyć uczestnikowi albo organowi odwoławczemu. Krótsze niż ``MIN_..._REASON``
  znaków nie jest uzasadnieniem, tylko odhaczeniem pola,
- **decyzja jest jawna w wynikach.** Wiersz, o którym rozstrzygnął komitet, jest w ogłoszonej
  tabeli podpisany odznaką. Kwalifikacja wbrew progowi, której nie widać, wygląda z zewnątrz
  jak błąd rachunkowy albo jak protekcja,
- **decyzja niczego nie ogłasza sama.** Zmiana dotyczy przeliczenia, a przeliczenie i publikacja
  są osobnymi kliknięciami. Jeśli wyniki etapu są już ogłoszone, serwis oddaje
  ``results_stale=True`` i to panel mówi, że trzeba je przeliczyć i opublikować ponownie –
  dokładnie tak, jak przy korekcie oceny końcowej (``grading.services.override_final_grade``).

Czego moduł **nie** robi: nie zmienia ``StageEntry.status``. Status jest wynikiem przeliczenia
i nadaje go ``apply_qualification`` – gdyby ustawiał go też ten serwis, dwa źródła prawdy
rozjechałyby się przy pierwszym przeliczeniu po decyzji.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http

from apps.competitions.models import (
    MIN_MANUAL_QUALIFICATION_REASON,
    ManualQualification,
    StageEntry,
)
from apps.core.api import DomainError
from apps.core.models import audit

from .models import ResultsPublication

logger = logging.getLogger(__name__)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _clean_decision(decision: str | None) -> str:
    """Sprowadza decyzję do wartości z ``ManualQualification`` albo odmawia.

    ``None`` i pusty napis znaczą to samo – „zdejmij decyzję, niech rozstrzyga próg” – bo tak
    wygląda pusty ``<select>`` w formularzu i tak wygląda pole w bazie.
    """
    value = (decision or "").strip()
    if value not in ManualQualification.values:
        raise _bad_request(f"Nieznana decyzja kwalifikacyjna: {decision}.", "INVALID_DECISION")
    return value


def _clean_reason(reason: str | None, *, required: bool) -> str:
    """Uzasadnienie decyzji. Wymagane wszędzie poza zdjęciem decyzji.

    Zdjęcie decyzji też zostawia ślad w audycie, ale nie wymaga zdania: wraca wtedy zwykła reguła
    punktowa, czyli stan, który i tak jest opisany progiem etapu.
    """
    text = (reason or "").strip()
    if not required:
        return text
    if len(text) < MIN_MANUAL_QUALIFICATION_REASON:
        raise _bad_request(
            f"Uzasadnienie decyzji musi mieć co najmniej {MIN_MANUAL_QUALIFICATION_REASON} znaków.",
            "REASON_REQUIRED",
        )
    return text


def results_published(entry: StageEntry) -> bool:
    """Czy wyniki etapu tego wpisu są już ogłoszone – jedyne źródło flagi ``results_stale``."""
    return ResultsPublication.objects.filter(stage_id=entry.stage_id).exists()


@transaction.atomic
def set_manual_qualification(
    entry: StageEntry,
    decision: str | None,
    reason: str | None,
    *,
    actor,
    request=None,
) -> dict:
    """Zapisuje (albo zdejmuje) decyzję komitetu o kwalifikacji jednego wpisu.

    Zwraca ``{"entry": …, "results_stale": bool}``. Wiersz etapu blokujemy na czas transakcji
    (``select_for_update``), bo tę samą decyzję potrafi kliknąć dwoje ludzi na posiedzeniu –
    a wtedy w audycie ma zostać ślad obu kliknięć w ustalonej kolejności, a nie dwie decyzje
    zapisane na tych samych, przeczytanych wcześniej danych.

    Wpis audytowy (``entry.manual_qualification``) niesie decyzję i **długość** uzasadnienia,
    nigdy jego treść ani danych uczestnika: uzasadnienie bywa opisem zdarzenia losowego z życia
    konkretnego ucznia, a audyt czytają osoby bez prawa do tych informacji. Samo uzasadnienie
    jest przy wpisie, dla tych, którzy mają do niego dostęp.
    """
    value = _clean_decision(decision)
    text = _clean_reason(reason, required=value != ManualQualification.NONE)
    # ``of=("self",)``: od kiedy zgłoszenie może należeć do drużyny, ``participant`` jest nullowalny,
    # a PostgreSQL nie pozwala zablokować nullowalnej strony złączenia zewnętrznego. Blokujemy sam
    # wiersz zgłoszenia – uczestnika i tak tylko czytamy.
    locked = StageEntry.objects.select_for_update(of=("self",)).select_related("participant").get(pk=entry.pk)
    before = locked.manual_qualification
    locked.manual_qualification = value
    locked.manual_qualification_reason = text
    # Autor i data znikają razem z decyzją: „zdjęte przez X dnia Y” brzmiałoby jak druga decyzja,
    # a jest po prostu powrotem do reguły. Zdarzenie zostaje w audycie.
    locked.manual_qualified_by = actor if getattr(actor, "is_authenticated", False) else None
    locked.manual_qualified_at = timezone.now() if value != ManualQualification.NONE else None
    locked.save(
        update_fields=[
            "manual_qualification",
            "manual_qualification_reason",
            "manual_qualified_by",
            "manual_qualified_at",
        ]
    )
    stale = results_published(locked)
    audit(
        actor,
        "entry.manual_qualification",
        locked,
        {
            "stage_id": locked.stage_id,
            "before": before,
            "after": value,
            "reason_length": len(text),
            "results_stale": stale,
        },
        request=request,
    )
    logger.info(
        "Wpis %s (etap %s): kwalifikacja ręczna %s → %s (wyniki ogłoszone: %s).",
        locked.pk,
        locked.stage_id,
        before or "—",
        value or "—",
        stale,
    )
    return {"entry": locked, "results_stale": stale}
