"""Sygnały sieci absolwentów: zmiana daty urodzenia osoby w otwartej relacji mentorskiej (M2).

Bez ani jednego dodatkowego zapytania przy zwykłym zapisie profilu uczestnika: wartość sprzed zmiany
zapamiętujemy przy tworzeniu obiektu (``post_init`` – samo przypisanie atrybutu), a po zapisie
porównujemy. Dopiero **rzeczywista** zmiana daty pyta bazę o relacje mentorskie tej osoby – a to
zdarza się kilka razy w roku, nie przy każdym zapisie profilu.

Zmiany robione ``update()`` (minimalizacja przy retencji, anonimizacja) sygnału nie wysyłają i to
jest zamierzone: to nie jest osoba „poprawiająca” swój wiek, tylko serwis czyszczący dane.
"""

from __future__ import annotations

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from apps.accounts.models import Participant

_STASH = "_alumni_birth"


@receiver(post_init, sender=Participant, dispatch_uid="alumni-birth-stash")
def _remember_birth(sender, instance, **kwargs):
    values = instance.__dict__
    # Pola odroczone (``only()``/``defer()``) nie są w ``__dict__`` – wtedy nie porównujemy wcale,
    # bo odczyt po zapisie dociągnąłby je zapytaniem.
    if "birth_date" in values and "birth_year" in values:
        values[_STASH] = (values["birth_date"], values["birth_year"])


@receiver(post_save, sender=Participant, dispatch_uid="alumni-birth-changed")
def _birth_changed(sender, instance, created, **kwargs):
    before = instance.__dict__.get(_STASH)
    if created or before is None:
        return
    now = (instance.birth_date, instance.birth_year)
    instance.__dict__[_STASH] = now
    if before == now:
        return
    from apps.accounts.profile import ANONYMISED_BIRTH_YEAR

    if instance.birth_year == ANONYMISED_BIRTH_YEAR:
        return  # anonimizacja konta, a nie „poprawka wieku” – relacje i tak się kończą
    from .mentoring import _ever_enabled, birth_date_changed

    if _ever_enabled(instance.competition):
        birth_date_changed(instance)
