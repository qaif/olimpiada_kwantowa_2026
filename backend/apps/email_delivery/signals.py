"""Reset stanu adresu przy zmianie albo usunięciu adresu konta (MAIL-02 § 2.4).

Sygnał, a nie wywołanie w każdej drodze zmiany adresu: adres konta zmieniają co najmniej cztery
miejsca (potwierdzenie linkiem, panel koordynatora, anonimizacja konta i retencji, ``/admin/``),
a każde przeoczone zostawiłoby baner „nie możemy dostarczyć poczty” przy adresie, którego konto już
nie ma – albo, gorzej, wstrzymaną wysyłkę na adres, który ktoś inny dostanie później.

Poprzedni adres zapamiętuje ``post_init`` (atrybut instancji, bez zapytania), a nie ``pre_save``
zapytaniem do bazy: konto jest zapisywane przy każdym logowaniu i przy wielu czynnościach, a testy
liczące zapytania nie mają widzieć tej funkcji. Kasowanie (``clear``) idzie do bazy wyłącznie wtedy,
gdy adres naprawdę się zmienił – i tylko przy ``EMAIL_BOUNCE_TRACKING`` (bez śledzenia nie ma
wierszy; po wyłączeniu śledzenia stare wiersze kasuje retencja albo operator – OPERACJE § 52).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_init, post_save, pre_save

ORIGINAL = "_email_delivery_original"


def _remember(sender, instance, **kwargs) -> None:
    # Pole odroczone (``.only(...)`` bez ``email``) nie stoi w ``__dict__`` – nie dociągamy go zapytaniem.
    if "email" in instance.__dict__:
        instance.__dict__[ORIGINAL] = instance.__dict__["email"]


def _email_changed(sender, instance, raw=False, update_fields=None, **kwargs) -> None:
    if raw or instance.pk is None:
        return
    if update_fields is not None and "email" not in update_fields:
        return
    from .services import tracking_enabled

    previous = instance.__dict__.get(ORIGINAL)
    if previous and tracking_enabled() and previous.strip().lower() != (instance.email or "").strip().lower():
        from .services import clear

        clear(previous)


def _saved(sender, instance, **kwargs) -> None:
    _remember(sender, instance)


def _user_deleted(sender, instance, **kwargs) -> None:
    from .services import clear, tracking_enabled

    if tracking_enabled() and "email" in instance.__dict__:
        clear(instance.email)


def connect() -> None:
    user_model = get_user_model()
    post_init.connect(_remember, sender=user_model, dispatch_uid="email_delivery_remember")
    pre_save.connect(_email_changed, sender=user_model, dispatch_uid="email_delivery_email_changed")
    post_save.connect(_saved, sender=user_model, dispatch_uid="email_delivery_saved")
    post_delete.connect(_user_deleted, sender=user_model, dispatch_uid="email_delivery_user_deleted")
