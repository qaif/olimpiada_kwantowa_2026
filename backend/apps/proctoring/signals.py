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
