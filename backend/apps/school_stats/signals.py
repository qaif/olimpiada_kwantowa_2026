"""Zamrożenie przynależności wpisów przy publikacji wyników – jedyny punkt styku z ``apps.results``.

Odbiornik ``post_save`` publikacji, a nie wywołanie w ``publish_results``: aplikacja wyników nie wie
o statystykach szkół (i nie ma się dowiadywać – STAT-01 M3). ``publish_results`` zapisuje publikację
przez ``update_or_create`` **po** ``apply_qualification``, więc w chwili sygnału statusy wpisów są
już ostateczne, a odbiornik biegnie w tej samej transakcji: wycofana publikacja wycofuje też
zamrożenie. Ponowna publikacja zamraża etap od nowa – tak samo, jak nadpisuje ``entry_totals``.

Tylko w konkursie z włączoną flagą ``school_statistics``: konkurs bez tej funkcji nie dostaje ani
jednego wiersza. Etapy ogłoszone przed zapaleniem flagi zamraża leniwie ``services.build_summary``.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.results.models import ResultsPublication


@receiver(post_save, sender=ResultsPublication, dispatch_uid="school_stats.freeze_on_publish")
def freeze_on_publish(sender, instance: ResultsPublication, **kwargs) -> None:
    from apps.competitions.models import Stage

    from .services import enabled, freeze_stage

    stage = Stage.objects.select_related("edition__competition").filter(pk=instance.stage_id).first()
    if stage is None or stage.is_training or not enabled(stage.edition.competition):
        return
    freeze_stage(stage.pk, replace=True)


@receiver(post_delete, sender=ResultsPublication, dispatch_uid="school_stats.unfreeze_on_unpublish")
def unfreeze_on_unpublish(sender, instance: ResultsPublication, **kwargs) -> None:
    """Zdjęta publikacja = etap bez wyników, więc i bez zamrożonej przynależności."""
    from .models import FrozenMembership

    FrozenMembership.objects.filter(stage_id=instance.stage_id).delete()
