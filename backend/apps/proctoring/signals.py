"""Pliki w buckecie znikają razem z wierszami – także przy kaskadzie z usuniętego konta albo etapu.

Wiersz nagrania i sesji (zdjęcie dokumentu) kasuje się nie tylko w ``services``: konto usunięte
w całości (``apps.accounts.profile``) zabiera profil uczestnika, a z nim – kaskadą – sesje nadzoru.
Kaskada Django wysyła ``post_delete`` dla każdego wiersza, więc sprzątanie stoi **tutaj**, a nie
w każdym miejscu, które może coś skasować. Kasowanie pliku dopiero po commicie: wycofana transakcja
nie może zostawić wiersza wskazującego na nieistniejący plik.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ProctoringRecording, ProctoringSession
from .storage import delete_quietly


@receiver(post_delete, sender=ProctoringRecording, dispatch_uid="proctoring_recording_file")
def _recording_deleted(sender, instance, **kwargs):
    if instance.storage_key and instance.purged_at is None:
        key = instance.storage_key
        transaction.on_commit(lambda: delete_quietly(key))


@receiver(post_delete, sender=ProctoringSession, dispatch_uid="proctoring_session_photo")
def _session_deleted(sender, instance, **kwargs):
    if instance.id_photo_key:
        key = instance.id_photo_key
        transaction.on_commit(lambda: delete_quietly(key))


def _leader_gone(instance) -> None:
    """Opiekun odwołany z delegacji (DEL-01) – wyproszenie z pokoju tej delegacji we wszystkich etapach.

    Zakres i tak liczy się przy każdym tokenie (``services.proctor_scope``), ale połączenie raz
    nawiązane trwa do końca etapu; bez tego odwołany opiekun dalej oglądałby uczniów kraju.
    """
    from .models import ProctorAssignment, ProctorKind
    from .services import kick_proctor

    assignments = list(
        ProctorAssignment.objects.filter(
            user_id=instance.user_id, kind=ProctorKind.LEADER, delegation_id=instance.delegation_id
        ).select_related("stage", "user")
    )

    def run():
        for assignment in assignments:
            kick_proctor(assignment.stage, assignment.user, [f"d{assignment.delegation_id}"])

    if assignments:
        transaction.on_commit(run)


def leader_saved(sender, instance, **kwargs):
    if getattr(instance, "removed_at", None) is not None:
        _leader_gone(instance)


def leader_deleted(sender, instance, **kwargs):
    _leader_gone(instance)


def connect_delegation_signals() -> None:
    """Podpina sygnały modelu opiekuna DEL-01, gdy ten jest w instalacji (wołane z ``apps.ready``)."""
    from django.apps import apps
    from django.db.models.signals import post_save

    try:
        model = apps.get_model("accounts", "DelegationLeader")
    except LookupError:
        return
    post_save.connect(leader_saved, sender=model, dispatch_uid="proctoring_leader_saved")
    post_delete.connect(leader_deleted, sender=model, dispatch_uid="proctoring_leader_deleted")
