"""„Czy ten konkurs ma dziś choć jeden plakat do pobrania” – z pamięcią podręczną, dla stopki.

Odnośnik „Plakaty do pobrania” stoi w stopce **każdej** strony serwisu (``templates/base.html``)
i w panelu opiekuna szkolnego, ale wyłącznie wtedy, gdy jest dokąd prowadzić – ``/plakaty/`` bez
opublikowanych plakatów oddaje 404. Bez pamięci podręcznej każda odsłona każdej strony
kosztowałaby zapytanie ``EXISTS`` – a budżety zapytań stron publicznych i paneli są pilnowane
testem (``apps/tenancy/tests/test_invariants.py``, ``QUERY_BUDGET``).

Wzorzec jest ten sam, co przy komunikatach i sliderze sponsorów (``apps.cms.announcements``,
``apps.cms.sponsor_slider``): wpis per konkurs w pamięci podręcznej, **unieważniany przy każdym
zapisie i skasowaniu plakatu** (sygnały niżej) i dodatkowo przeliczany od razu, w żądaniu
koordynatora, który zmianę zrobił – żeby koszt zapytania zapłacił on, a nie pierwszy gość po nim.
TTL jest długi (godzina), bo unieważnianie jest jawne; zostaje wyłącznie jako zabezpieczenie dla
zapisów z pominięciem sygnałów (``QuerySet.update`` w migracji danych).

Wartość w szablonie jest **leniwa** (``SimpleLazyObject``, jak ``supervisor_registration_open``
w ``apps.web.context_processors``): odpowiedź, która stopki nie renderuje (JSON, plik, przekierowanie),
nie płaci nawet za odczyt z pamięci podręcznej.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.functional import SimpleLazyObject

from .models import PromoMaterial

logger = logging.getLogger(__name__)

CACHE_PREFIX = "promo:available"
CACHE_TTL_SECONDS = 60 * 60


def cache_key(competition_id) -> str:
    return f"{CACHE_PREFIX}:{competition_id or 'none'}"


def public_materials(competition):
    """Plakaty widoczne na ``/plakaty/``: opublikowane, nie w archiwum, w kolejności koordynatora.

    Jedyna definicja „co widzi świat” – czyta ją lista publiczna, widok pobrania i ta pamięć
    podręczna. Trzy miejsca z własnym filtrem rozjechałyby się przy pierwszej zmianie reguły
    (np. dołożeniu daty publikacji) i odnośnik w stopce prowadziłby w 404.
    """
    return PromoMaterial.objects.for_competition(competition).filter(
        is_published=True, archived_at__isnull=True
    )


def _compute(competition) -> bool:
    try:
        return public_materials(competition).exists()
    except DatabaseError:  # pragma: no cover - baza bez migracji tej aplikacji
        return False


def has_public_materials(competition) -> bool:
    """Czy konkurs ma choć jeden plakat do pobrania – z pamięci podręcznej, unieważnianej przy zapisie.

    Konkurs ``None`` (host bez konkursu) nie ma plakatów: lista publiczna i tak oddałaby tam 404.
    """
    if competition is None:
        return False
    key = cache_key(competition.pk)
    cached = cache.get(key)
    if cached is not None:
        return bool(cached)
    value = _compute(competition)
    cache.set(key, value, CACHE_TTL_SECONDS)
    return value


def refresh(competition) -> None:
    """Przelicza i zapisuje wartość **od razu** – woła to ekran koordynatora po każdej zmianie."""
    if competition is None:
        return
    cache.set(cache_key(competition.pk), _compute(competition), CACHE_TTL_SECONDS)


@receiver(post_save, sender=PromoMaterial, dispatch_uid="promo.availability.reset_on_save")
@receiver(post_delete, sender=PromoMaterial, dispatch_uid="promo.availability.reset_on_delete")
def _reset_on_material_change(sender, instance, **kwargs) -> None:
    cache.delete(cache_key(instance.competition_id))


def promo_materials(request) -> dict:
    """Procesor kontekstu: ``promo_available`` dla stopki i panelu opiekuna – leniwie.

    Owinięte w łapacz wszystkiego z tego samego powodu, co procesor slidera sponsorów: to jest
    warstwa doklejana do **każdej** odpowiedzi HTML, więc awaria pamięci podręcznej ani bazy nie
    może być nowym miejscem, w którym strona pada. Awaryjną odpowiedzią jest „nie pokazuj
    odnośnika” – brak linku do plakatów niczego nie psuje, link do 404 psuje.
    """

    def _value() -> bool:
        try:
            return has_public_materials(getattr(request, "competition", None))
        except Exception:  # noqa: BLE001 - patrz docstring
            logger.warning("Nie udało się sprawdzić, czy konkurs ma plakaty – bez odnośnika.", exc_info=True)
            return False

    return {"promo_available": SimpleLazyObject(_value)}
