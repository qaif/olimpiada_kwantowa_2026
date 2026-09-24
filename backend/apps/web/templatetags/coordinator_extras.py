"""Znaczniki szablonów panelu koordynatora.

Osobna biblioteka od ``web_extras``: tamta jest wspólna dla całego serwisu (formaty dat, tony
odznak) i ładuje ją też strona publiczna, a te znaczniki mają sens wyłącznie w panelu, do którego
wchodzi jedna rola. Ta sama zasada, co przy widokach – ``coordinator_*.py`` zamiast jednego
worka – i ten sam skutek: dołożenie tu czegokolwiek nie rozszerza tego, co ładuje strona główna.

Świadomie ubogie: żaden znacznik nie generuje HTML-a i żaden nie oznacza wyniku jako bezpiecznego.
"""

from django import template
from django.urls import reverse

from apps.accounts.anonymised import DELETED_ACCOUNT_LABEL, is_anonymised, person_label

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


# --- konta usunięte na żądanie (apps.accounts.anonymised) ---------------------------------------
#
# Konto po anonimizacji zostaje w bazie z adresem ``deleted-<pk>@invalid.…`` i pustym imieniem.
# Listy, które takie konto **muszą** pokazać (przydziały, recenzje, odwołania, audyt, karta),
# wypisywały dotąd ten adres – wzorcem ``get_full_name|default:email`` albo wprost. Trzy filtry
# niżej zastępują oba wzorce jednym miejscem, które zamiast adresu technicznego daje neutralny
# podpis „Konto usunięte” (zgłoszenie organizatora z 24.09.2026).


@register.filter
def is_deleted_account(user) -> bool:
    """Czy konto jest po anonimizacji – do rozgałęzienia w szablonie."""
    return is_anonymised(user)


@register.filter
def person(user, public_code: str = "") -> str:
    """Imię i nazwisko, w ich braku adres – a konto usunięte: „Konto usunięte (kod)”.

    Zastępuje ``get_full_name|default:email``; kod publiczny (argument) dopisujemy wyłącznie
    kontu usuniętemu, bo tylko tam jest jedynym, co wiąże wiersz z aktami zawodów.
    """
    return person_label(user, public_code or "")


@register.filter
def account_email(user) -> str:
    """Adres konta do pokazania – a konto usunięte: „Konto usunięte” zamiast adresu technicznego."""
    if user is None:
        return ""
    return DELETED_ACCOUNT_LABEL if is_anonymised(user) else user.email
