"""Odnośniki do ekranów, których adres może w danej instalacji nie istnieć.

Panel koordynatora rośnie ekranami, a ekrany odwołują się do siebie nawzajem: karta zadania
prowadzi do karty uczestnika, karta członka komisji – do obu. Zwykłe ``{% url %}`` w szablonie
wywraca całą stronę, gdy docelowego adresu jeszcze nie ma w ``urlpatterns`` (``NoReverseMatch``
jest wyjątkiem, a nie pustą wartością). To zła zamiana: brakujący odnośnik jest drobiazgiem,
a rozsypana karta recenzenta – awarią w trakcie oceniania.

Dlatego adres liczy się **w Pythonie**, przed szablonem: ``None`` znaczy „tego ekranu tu nie ma”
i szablon pokazuje wtedy sam kod uczestnika zamiast odnośnika. Rozstrzygnięcie zostaje w jednym
miejscu, więc nie trzeba go powtarzać w każdym szablonie, który o taki odnośnik pyta.
"""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse

#: Nazwa adresu karty uczestnika w panelu koordynatora. Stała, a nie napis w trzech modułach:
#: ekran należy do innej części panelu i jego adres bywa dokładany osobno.
PARTICIPANT_CARD_URL_NAME = "web:coordinator-participant"


def optional_url(name: str, *args) -> str | None:
    """Adres widoku albo ``None``, gdy tej nazwy nie ma w mapie adresów."""
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def participant_card(participant) -> str | None:
    """Karta uczestnika albo ``None`` – dla wiersza bez uczestnika i dla instalacji bez tego ekranu.

    Odpowiednik znacznika szablonu ``participant_card_url`` po stronie danych: karta zadania
    i karta członka komisji budują swoje wiersze w Pythonie i mają nieść gotowy odnośnik, a nie
    obiekt uczestnika do rozwinięcia w szablonie (w tabeli ocenianej ślepo uczestnik jest jedynie
    kodem publicznym i tyle wolno stamtąd wziąć).
    """
    if participant is None:
        return None
    return optional_url(PARTICIPANT_CARD_URL_NAME, participant.pk)
