"""Unieważnianie cache'a bramki zgód (CONS-01 § 2) – sygnałami modeli, a nie w każdym serwisie.

Dróg zapisu zgody jest kilka (rejestracja WWW, API i przez dostawcę, przyjęcie zaproszenia, zgoda
opiekuna online, ekran uzupełnienia, administracja Django), a dopisanie „wyczyść cache” w każdej
z nich byłoby listą, o której ktoś zapomni przy następnej. Sygnał modelu obejmuje je wszystkie –
z jednym wyjątkiem: ``bulk_create`` (``accounts.services.record_consents``) sygnałów nie wysyła.
Ten serwis zapisuje jednak w tej samej transakcji projekcje na profilu (``participant.save``),
więc sygnał ``Participant`` przychodzi i tak. Zmiana daty urodzenia (zgoda opiekuna staje się
wymagana albo przestaje) idzie tą samą drogą.

Unieważnienie idzie **dwa razy**: od razu i jeszcze raz w ``on_commit``. Samo „od razu” pozwoliłoby
równoległemu żądaniu wczytać do cache'a stan sprzed zatwierdzenia transakcji i trzymać go przez cały
TTL; samo ``on_commit`` nie zadziałałoby w kodzie, który transakcji nie zatwierdza (testy w transakcji,
zagnieżdżony ``atomic`` wycofany wyżej – tam drugie czyszczenie po prostu nie nastąpi, a pierwsze
nie szkodzi).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.accounts.models import ConsentDefinition, ConsentRecord, Participant

from . import state


def _forget_after_commit(competition_id, user_id) -> None:
    if competition_id is None or user_id is None:
        return
    state.forget_state(competition_id, user_id)
    transaction.on_commit(lambda: state.forget_state(competition_id, user_id))


@receiver(post_save, sender=Participant, dispatch_uid="consent_gate.participant_saved")
@receiver(post_delete, sender=Participant, dispatch_uid="consent_gate.participant_deleted")
def participant_changed(sender, instance, **kwargs) -> None:
    _forget_after_commit(instance.competition_id, instance.user_id)


@receiver(post_save, sender=ConsentRecord, dispatch_uid="consent_gate.record_saved")
@receiver(post_delete, sender=ConsentRecord, dispatch_uid="consent_gate.record_deleted")
def record_changed(sender, instance, **kwargs) -> None:
    """Wpis uczestnika – unieważnia jego stan. Wpisy opiekunów (szkolnego, drużyny) nie dotyczą bramki."""
    if instance.participant_id is None:
        return
    origin = kwargs.get("origin")
    if origin is not None and getattr(origin, "model", type(origin)) is not ConsentRecord:
        # Kaskada z usuwanego profilu albo konta: unieważni go sygnał ``Participant`` – bez zapytania
        # o profil na każdy kasowany wpis (usunięcie konta nie może kosztować N zapytań więcej).
        return
    # Profil zwykle już jest w pamięci wpisu (``ConsentRecord(participant=…)``) – bez zapytania.
    participant = instance._state.fields_cache.get("participant")
    if participant is not None:
        _forget_after_commit(participant.competition_id, participant.user_id)
        return
    row = (
        Participant.objects.filter(pk=instance.participant_id)
        .values_list("competition_id", "user_id")
        .first()
    )
    if row is not None:
        _forget_after_commit(*row)


@receiver(post_save, sender=ConsentDefinition, dispatch_uid="consent_gate.definition_saved")
@receiver(post_delete, sender=ConsentDefinition, dispatch_uid="consent_gate.definition_deleted")
def definition_changed(sender, instance, **kwargs) -> None:
    """Zmiana wersji, treści albo wymagalności zgody konkursu – zestaw w cache'u do odświeżenia.

    Stanów kont **nie** czyścimy: trzymają dane (wpisy), a nie wynik, więc nowy zestaw działa
    na nich od następnego żądania.
    """
    competition_id = instance.competition_id
    state.forget_definitions(competition_id)
    transaction.on_commit(lambda: state.forget_definitions(competition_id))
