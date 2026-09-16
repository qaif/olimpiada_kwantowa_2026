"""Znaczniki szablonów panelu koordynatora.

Osobna biblioteka od ``web_extras``: tamta jest wspólna dla całego serwisu (formaty dat, tony
odznak) i ładuje ją też strona publiczna, a te znaczniki mają sens wyłącznie w panelu, do którego
wchodzi jedna rola. Ta sama zasada, co przy widokach – ``coordinator_*.py`` zamiast jednego
worka – i ten sam skutek: dołożenie tu czegokolwiek nie rozszerza tego, co ładuje strona główna.

Świadomie ubogie: żaden znacznik nie generuje HTML-a i żaden nie oznacza wyniku jako bezpiecznego.
"""

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def participant_card_url(participant) -> str:
    """Adres karty uczestnika albo pusty napis, gdy karty dla tego wiersza nie ma.

    Istnieje po to, żeby ekrany **cudzych** agentów (tabela przydziałów, lista wyników, wyszukiwarka)
    mogły dowiązać kartę bez sięgania po ``{% url 'web:coordinator-participant' … %}`` i bez
    powtarzania w każdym szablonie warunku „a co, jeśli tu nie ma uczestnika”. Nazwa adresu zostaje
    wtedy w jednym miejscu, a zmiana kształtu URL-a nie wymaga obchodzenia szablonów po kolei.

    Pusty napis zamiast wyjątku, bo wołający wstawia wynik w ``href``: wiersz bez uczestnika
    (praca po anonimizacji konta, podsumowanie w stopce tabeli) ma stracić odnośnik, a nie
    wywrócić całą stronę. Szablon rozstrzyga to zwykłym ``{% if %}``.
    """
    pk = getattr(participant, "pk", None)
    if pk is None:
        return ""
    return reverse("web:coordinator-participant", args=[pk])
