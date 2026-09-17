"""Zgodność ``Competition.primary_domain`` z domeną witryny.

``primary_domain`` dubluje ``Site.hostname`` celowo (patrz ``models.Competition``): domena jest
potrzebna tam, gdzie żądania nie ma. Cena duplikatu to możliwość rozjazdu – redaktor zmienia
domenę w ``/cms/`` (Ustawienia → Witryny) i od tej chwili listy z zadań Celery prowadziłyby pod
adres, którego już nie ma. Sygnał jest tańszy niż przypominanie o tym w instrukcji obsługi.

Aktualizacja idzie przez ``queryset.update()``, a nie przez ``save()`` na obiekcie: to jeden UPDATE
bez sygnałów, więc nie ma jak zapętlić zapisu ani nadpisać pól, których nikt nie zmieniał.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver
from wagtail.models import Site

from apps.tenancy.models import Competition


@receiver(post_save, sender=Site, dispatch_uid="tenancy.sync_primary_domain")
def sync_primary_domain(sender, instance, **kwargs) -> None:
    """Po zmianie domeny witryny dociąga za nią ``primary_domain`` jej konkursu."""
    Competition.objects.filter(site=instance).exclude(primary_domain=instance.hostname).update(
        primary_domain=instance.hostname
    )
