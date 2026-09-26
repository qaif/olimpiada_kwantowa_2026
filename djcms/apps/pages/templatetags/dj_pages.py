"""Znaczniki szablonów ramy stron (``{% load dj_pages %}``)."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def dj_primary_nodes(nodes) -> list:
    """Węzły menu oznaczone „w przyklejonym pasku” (``MenuExtension.primary``, ``cms_menus.py``).

    Osobna lista zamiast warunku w pętli: pasek ``nav--primary`` ma się nie pojawić wcale, gdy
    nie ma w nim ani jednej pozycji – tak jak ``{% if cms_menu_primary %}`` w ``base.html`` backendu.
    """
    return [node for node in nodes or [] if node.attr.get("dj_primary")]
