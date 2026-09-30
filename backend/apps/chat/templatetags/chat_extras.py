"""Renderowanie treści wiadomości. Jedyne miejsce w Wiadomościach, w którym z tekstu powstaje HTML.

Kopia pięciu linijek filtra ``post_body`` z ``apps.forum.templatetags.forum_extras`` – świadomie
kopia, a nie import: biblioteka znaczników forum jest częścią forum, a Wiadomości nie mają zależeć
od tego, jak forum kiedyś zechce renderować swoje wpisy. Kolejność operacji jest treścią i jest ta
sama, co tam (pełne uzasadnienie w docstringu tamtego modułu):

1. ``urlize`` z ``autoescape=True`` – **najpierw** escapujemy tekst, potem robimy odnośniki,
2. ``rel`` rozszerzony do ``nofollow noopener noreferrer`` – odnośnik z prywatnej rozmowy nie
   wynosi adresu wątku do cudzego logu i nie daje otwartej stronie dostępu do ``window.opener``,
3. ``linebreaksbr`` bez drugiego escapowania – tekst jest już bezpieczny krok wyżej.

Czego filtr nie robi: Markdowna, obrazów, podglądów odnośników. Każda z tych rzeczy byłaby nową
drogą, którą cudza treść trafia do strony osoby niepełnoletniej.
"""

from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import urlize
from django.utils.safestring import mark_safe

register = template.Library()

#: Atrybut dokładany przez ``urlize(nofollow=True)`` – podmieniany na pełny zestaw niżej.
DJANGO_NOFOLLOW = 'rel="nofollow"'
SAFE_REL = 'rel="nofollow noopener noreferrer" target="_blank"'


@register.filter(needs_autoescape=True, is_safe=True)
def message_body(value, autoescape=True):
    """Treść wiadomości jako bezpieczny HTML: zescapowana, z odnośnikami i złamaniami wierszy."""
    linked = urlize(value or "", nofollow=True, autoescape=autoescape)
    linked = linked.replace(DJANGO_NOFOLLOW, SAFE_REL)
    return mark_safe(linebreaksbr(linked, autoescape=False))  # noqa: S308 - patrz docstring modułu


@register.filter
def snippet(value, length: int = 80) -> str:
    """Skrót ostatniej wiadomości na liście rozmów: jeden wiersz, bez złamań, z wielokropkiem."""
    text = " ".join((value or "").split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"
