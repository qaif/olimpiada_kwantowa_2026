"""Renderowanie treści wpisu forum. Jeden filtr i jest to jedyne miejsce z ``mark_safe`` na forum.

Filtry interfejsu (``apps.web.templatetags.web_extras``) mają w docstringu zdanie, że **żaden
z nich nie generuje HTML-a i żaden nie oznacza wyniku jako bezpiecznego**. Ten filtr łamie obie
części tej zasady, więc stoi osobno – tak, żeby pytanie „gdzie na forum powstaje HTML z tekstu
użytkownika” miało jedną odpowiedź i żeby dopisanie drugiego takiego filtru wymagało świadomej
decyzji, a nie było dopisaniem linijki do pliku, w którym już coś podobnego jest.

Kolejność operacji jest treścią, a nie szczegółem:

1. ``urlize`` z ``autoescape=True`` – **najpierw** escapujemy, potem robimy odnośniki. Odwrotna
   kolejność znaczyłaby escapowanie własnych znaczników ``<a>`` albo, gorzej, przepuszczenie
   cudzych,
2. ``rel`` rozszerzamy do ``nofollow noopener noreferrer``. ``nofollow`` daje sam Django (odnośnik
   z forum nie jest naszą rekomendacją dla wyszukiwarki); ``noopener`` odcina otwartej stronie
   dostęp do ``window.opener``, a ``noreferrer`` nie wynosi adresu strony forum do cudzego logu,
3. ``linebreaksbr`` z ``autoescape=False`` – tekst jest już zescapowany krok wyżej, więc drugie
   escapowanie zamieniłoby odnośniki w widoczne ``&lt;a href=…``.

Czego ten filtr **nie** robi: nie interpretuje Markdowna, nie osadza obrazów i nie rozwija
odnośników w podgląd. Treść wpisu jest tekstem (``apps.forum.models``), a każda z tych trzech
rzeczy byłaby nową drogą, którą do strony trafia cudza treść.
"""

from django import template
from django.template.defaultfilters import linebreaksbr
from django.utils.html import urlize
from django.utils.safestring import mark_safe

register = template.Library()

#: Atrybut, który dokłada ``urlize(nofollow=True)``. Podmieniamy go na pełny zestaw – zapis jest
#: stały w Django (``' rel="nofollow"'`` w ``django.utils.html``), a test renderowania wpisu
#: z odnośnikiem pilnuje, żeby podmiana nadal trafiała.
DJANGO_NOFOLLOW = 'rel="nofollow"'

#: Pełny zestaw. ``noopener`` i ``noreferrer`` wprost, a nie w nadziei, że przeglądarka domyśli
#: się ich z ``target``: odnośnik z forum nie ma ``target``, więc żadna domyślna reguła tu nie
#: zadziała.
SAFE_REL = 'rel="nofollow noopener noreferrer" target="_blank"'


@register.filter(needs_autoescape=True, is_safe=True)
def post_body(value, autoescape=True):
    """Treść wpisu jako bezpieczny HTML: zescapowana, z odnośnikami i złamaniami wierszy."""
    linked = urlize(value or "", nofollow=True, autoescape=autoescape)
    linked = linked.replace(DJANGO_NOFOLLOW, SAFE_REL)
    return mark_safe(linebreaksbr(linked, autoescape=False))  # noqa: S308 - patrz docstring modułu
