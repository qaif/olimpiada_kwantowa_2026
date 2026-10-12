"""Sanityzator tekstu djangocms-text 1.0.1 bez atrybutu ``style`` (audyt 2026-10-10, pozycja niska djcms).

``djangocms_text.html.cms_additional_attributes`` dopuszcza ``style`` na **każdym** znaczniku
(``"*": {"style", "class", "role"}``), a ``nh3`` nie filtruje deklaracji CSS. Redaktor jednego konkursu
mógł więc zapisać w tekście nakładkę ``position:fixed`` na całe okno – w originie, w którym leży też
logowanie aplikacji głównej (podszycie się pod formularz, klikanie w cudze przyciski). Pasek edytora
(``TEXT_EDITOR_SETTINGS`` w ustawieniach) nie ma wyrównania ani kolorów, więc ``style`` nie jest
potrzebny do żadnej funkcji, którą redaktor ma w ręku – zdejmujemy go zamiast filtrować właściwości.

Zmieniamy obie kopie listy: słownik modułu (czytają go nowe ``NH3Parser()``) i gotowy ``cms_parser``
(używa go ``clean_html`` – zapis wtyczki „Tekst”, pól ``HTMLField``, importu i treści z API). Treść
zapisana wcześniej zostaje taka, jaka była, do najbliższego zapisu (sanityzacja działa przy zapisie).
"""

from __future__ import annotations

#: Atrybuty zdejmowane z każdej listy – ``"*"`` (wszystkie znaczniki) i list poszczególnych znaczników,
#: żeby ``TEXT_ADDITIONAL_ATTRIBUTES`` albo kolejna wersja pakietu nie przywróciła ich tylnymi drzwiami.
FORBIDDEN_GLOBAL_ATTRIBUTES = frozenset({"style"})


def tighten_text_sanitizer() -> None:
    """Usuwa ``FORBIDDEN_GLOBAL_ATTRIBUTES`` z list atrybutów djangocms-text (``ready``, idempotentnie)."""
    from djangocms_text import html

    for allowed in (html.cms_additional_attributes, html.cms_parser.ALLOWED_ATTRIBUTES):
        for tag, attributes in list(allowed.items()):
            if attributes & FORBIDDEN_GLOBAL_ATTRIBUTES:
                allowed[tag] = set(attributes) - FORBIDDEN_GLOBAL_ATTRIBUTES
