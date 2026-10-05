"""„Czy ten konkurs ma opublikowaną deklarację dostępności” – z pamięcią podręczną, dla stopki.

Odnośnik „Deklaracja dostępności” stoi w stopce **każdej** strony obu motywów, ale wyłącznie wtedy,
gdy strona ``/dokumenty/deklaracja-dostepnosci/`` jest opublikowana w drzewie **tego** konkursu –
projekt czekający na zatwierdzenie organizatora oddaje 404, a odnośnik do 404 na produkcji jest
gorszy niż jego brak.

Wzorzec ``apps.promo.availability``: wartość per konkurs w pamięci podręcznej (trafienie = zero
zapytań do bazy), **unieważniana** sygnałami Wagtaila przy publikacji, wycofaniu, przeniesieniu
i skasowaniu strony dokumentu; TTL długi (godzina) – wyłącznie zabezpieczenie dla zmian z pominięciem
sygnałów. W szablonie wartość jest leniwa: odpowiedź bez stopki nie czyta nawet pamięci podręcznej.
Awaria pamięci albo bazy = „nie pokazuj odnośnika” (brak linku niczego nie psuje, link do 404 – tak).
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.functional import SimpleLazyObject
from wagtail.signals import page_published, page_unpublished, post_page_move

logger = logging.getLogger(__name__)

PAGE_SLUG = "deklaracja-dostepnosci"
CACHE_PREFIX = "a11y:statement"
CACHE_TTL_SECONDS = 60 * 60


def cache_key(competition_id) -> str:
    return f"{CACHE_PREFIX}:{competition_id or 'none'}"


def _compute(competition) -> bool:
    """Jedno zapytanie ``EXISTS``: opublikowana strona o tym slugu w drzewie witryny konkursu.

    Korzeń drzewa bierzemy podzapytaniem (``Site.root_page.path``), a nie osobnym odczytem witryny –
    pierwsze żądanie po zimnym starcie płaci jedno zapytanie, nie dwa (budżety zapytań:
    ``apps/core/tests/query_budgets.py``).
    """
    from django.db.models import Subquery
    from wagtail.models import Site

    from apps.cms.models import DocumentPage

    root_path = Site.objects.filter(pk=competition.site_id).values("root_page__path")[:1]
    try:
        return (
            DocumentPage.objects.live().filter(slug=PAGE_SLUG, path__startswith=Subquery(root_path)).exists()
        )
    except DatabaseError:  # pragma: no cover - baza bez migracji drzewa stron
        return False


def statement_published(competition) -> bool:
    """Czy konkurs ma opublikowaną deklarację – z pamięci podręcznej, unieważnianej sygnałami."""
    if competition is None or not getattr(competition, "site_id", None):
        return False
    key = cache_key(competition.pk)
    cached = cache.get(key)
    if cached is not None:
        return bool(cached)
    value = _compute(competition)
    cache.set(key, value, CACHE_TTL_SECONDS)
    return value


def forget_all() -> None:
    """Unieważnia wartość wszystkich konkursów – strona dokumentu nie zna swojego konkursu wprost.

    Konkursów jest kilka, a publikacja dokumentu – rzadka; jedno zapytanie o identyfikatory przy
    publikacji jest tańsze niż odtwarzanie „który konkurs ma tę stronę w drzewie”.
    """
    from apps.tenancy.models import Competition

    try:
        cache.delete_many([cache_key(pk) for pk in Competition.objects.values_list("pk", flat=True)])
    except Exception:  # noqa: BLE001 - unieważnienie nie może wywrócić zapisu strony w /cms/
        logger.warning("Nie udało się unieważnić pamięci deklaracji dostępności.", exc_info=True)


def _is_statement(instance) -> bool:
    return getattr(instance, "slug", None) == PAGE_SLUG or instance.__class__.__name__ == "DocumentPage"


@receiver(page_published, dispatch_uid="accessibility.statement.published")
@receiver(page_unpublished, dispatch_uid="accessibility.statement.unpublished")
@receiver(post_page_move, dispatch_uid="accessibility.statement.moved")
def _reset_on_page_change(sender, instance, **kwargs) -> None:
    if _is_statement(instance):
        forget_all()


@receiver(post_delete, dispatch_uid="accessibility.statement.deleted")
def _reset_on_page_delete(sender, instance, **kwargs) -> None:
    from wagtail.models import Page

    if isinstance(instance, Page) and getattr(instance, "slug", None) == PAGE_SLUG:
        forget_all()


def accessibility_statement(request) -> dict:
    """Procesor kontekstu: ``accessibility_statement_available`` dla stopek obu motywów – leniwie."""

    def _value() -> bool:
        try:
            return statement_published(getattr(request, "competition", None))
        except Exception:  # noqa: BLE001 - patrz docstring modułu
            logger.warning("Nie udało się sprawdzić deklaracji dostępności – bez odnośnika.", exc_info=True)
            return False

    return {"accessibility_statement_available": SimpleLazyObject(_value)}
