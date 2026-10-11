"""Znacznik dla panelu opiekuna drużyny: czy pokazać wejście do logistyki finału.

Znacznik zamiast klucza w kontekście widoku DEL-01: panel delegacji nie musi wiedzieć o tej
aplikacji – szablon pyta tę samą bramkę, co widoki (``models.enabled``), bez zapytania do bazy.
"""

from django import template

from apps.delegation_logistics.models import enabled

register = template.Library()


@register.simple_tag
def final_logistics_enabled(competition) -> bool:
    return enabled(competition)
