"""Znaczniki list panelu koordynatora: nagłówek sortowalnej kolumny i przełącznik kont usuniętych.

Stan listy liczy ``apps.web.list_controls.ListControls`` w widoku; tutaj jest wyłącznie
prezentacja, z szablonów w ``templates/web/includes/``. Znaczniki są „inclusion tags”, a nie
kawałki HTML-a składane w Pythonie – autoescape zostaje w szablonie, a żaden wynik nie jest
oznaczany jako bezpieczny ręcznie.
"""

from django import template

register = template.Library()


@register.inclusion_tag("web/includes/sort_header.html")
def sort_header(controls, key: str, css_class: str = ""):
    """``<th>`` kolumny ``key`` z odnośnikiem sortowania, strzałką i ``aria-sort``."""
    return {"column": controls.header(key), "css_class": css_class}


@register.inclusion_tag("web/includes/deleted_toggle.html")
def deleted_toggle(controls):
    """Przycisk „Pokaż/Ukryj usunięte konta” z liczbą ukrytych (gdy była policzona)."""
    return {"controls": controls}
