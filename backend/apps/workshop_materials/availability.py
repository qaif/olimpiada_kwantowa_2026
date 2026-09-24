"""„Czy ten konkurs ma dziś choć jeden materiał z warsztatów do obejrzenia” – dla odnośników w panelu.

Decyzja organizatora z 24.09.2026: odnośnik „Materiały z warsztatów” stoi w **pasku konta**
(``templates/base.html``) i na **pulpicie uczestnika** (``/me/``) – ale wyłącznie wtedy, gdy jest
dokąd prowadzić: przełącznik ``workshop_materials`` włączony **i** co najmniej jeden materiał
opublikowany i gotowy. Pasek konta jest na każdej stronie zalogowanego, a budżety zapytań paneli
pilnuje test (``apps/tenancy/tests/test_invariants.py``, ``QUERY_BUDGET``), więc odpowiedź nie może
kosztować zapytania na odsłonę.

Wzorzec jest ten sam, co ``apps.promo.availability`` (odnośnik do plakatów w stopce):

- **przełącznik najpierw** – ``Competition.has_feature`` czyta pole wiersza, który żądanie już ma,
  więc konkurs bez tej funkcji (dziś każdy) nie płaci ani zapytania, ani odczytu z Redisa,
- potem wpis per konkurs w pamięci podręcznej (godzina), **kasowany przy każdym zapisie
  i skasowaniu materiału** (sygnały niżej: publikacja, zdjęcie, werdykt skanera, usunięcie,
  sprzątanie porzuconych wgrywań – wszystko to idzie przez ``save``/``delete``). Zmiana kolejności
  (``bulk_update``) i licznik wyświetleń (``update``) omijają sygnały, ale nie zmieniają
  odpowiedzi, więc niczego nie trzeba unieważniać,
- wartość w szablonie jest **leniwa** (``SimpleLazyObject``): odpowiedź bez paska konta (JSON,
  przekierowanie, plik) nie płaci nawet za odczyt z pamięci.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.functional import SimpleLazyObject

from .access import feature_enabled
from .models import MaterialStatus, WorkshopMaterial

logger = logging.getLogger(__name__)

CACHE_PREFIX = "workshop_materials:available"
CACHE_TTL_SECONDS = 60 * 60


def cache_key(competition_id) -> str:
    return f"{CACHE_PREFIX}:{competition_id or 'none'}"


def _compute(competition) -> bool:
    try:
        return (
            WorkshopMaterial.objects.for_competition(competition)
            .filter(is_published=True, status=MaterialStatus.READY)
            .exists()
        )
    except DatabaseError:  # pragma: no cover - baza bez migracji tej aplikacji
        return False


def has_visible_materials(competition) -> bool:
    """Przełącznik włączony **i** choć jeden opublikowany, gotowy materiał – z pamięci podręcznej."""
    if not feature_enabled(competition):
        return False
    key = cache_key(competition.pk)
    cached = cache.get(key)
    if cached is not None:
        return bool(cached)
    value = _compute(competition)
    cache.set(key, value, CACHE_TTL_SECONDS)
    return value


@receiver(post_save, sender=WorkshopMaterial, dispatch_uid="workshop_materials.availability.reset_on_save")
@receiver(
    post_delete, sender=WorkshopMaterial, dispatch_uid="workshop_materials.availability.reset_on_delete"
)
def _reset_on_material_change(sender, instance, **kwargs) -> None:
    cache.delete(cache_key(instance.competition_id))


def workshop_materials_link(request) -> dict:
    """Procesor kontekstu: ``workshop_materials_available`` dla paska konta i pulpitu – leniwie.

    Łapacz wszystkiego z tego samego powodu, co ``apps.promo.availability.promo_materials``: to jest
    warstwa doklejana do każdej odpowiedzi HTML, a awaryjną odpowiedzią jest „nie pokazuj odnośnika”
    – jego brak niczego nie psuje, odnośnik do 404 psuje.

    O **rolę** ta wartość nie pyta – szablon łączy ją z ``is_participant``/``is_supervisor``/…
    z procesora ``apps.web.context_processors.roles``, które i tak są już policzone. Nawigacja nie
    jest zabezpieczeniem: dostęp rozstrzyga widok (``apps.workshop_materials.access.can_view``).
    """

    def _value() -> bool:
        try:
            return has_visible_materials(getattr(request, "competition", None))
        except Exception:  # noqa: BLE001 - patrz docstring
            logger.warning(
                "Nie udało się sprawdzić, czy są materiały z warsztatów – bez odnośnika.", exc_info=True
            )
            return False

    return {"workshop_materials_available": SimpleLazyObject(_value)}
