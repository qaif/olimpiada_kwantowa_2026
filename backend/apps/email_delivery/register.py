"""Wiersz rejestru czynności przetwarzania (art. 30 RODO) – doręczalność poczty (MAIL-02 § 2.8).

Czynność **warunkowa** instalacji, jak monitorowanie błędów (``apps.monitoring.register``): wchodzi do
rejestru wyłącznie przy ``EMAIL_BOUNCE_TRACKING`` – bez przełącznika aplikacja nie zapisuje żadnego
odbicia. Podpowiedź literówek nie jest tu opisana, bo niczego nie zapisuje (sprawdza adres, który
osoba właśnie wpisuje, a pytanie DNS dotyczy samej domeny i trafia do cache'u jako skrót).
"""

from __future__ import annotations

from .services import RETENTION_DAYS, tracking_enabled


def activity():
    """``ProcessingActivity`` doręczalności poczty albo ``None``, gdy śledzenie jest wyłączone."""
    if not tracking_enabled():
        return None
    from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

    return _activity(
        key="doreczalnosc-poczty",
        name="Doręczalność poczty (odbicia listów)",
        purpose=(
            "Wykrycie adresu e-mail, na który listy serwisu nie dochodzą (skrzynka albo domena nie "
            "istnieje), poinformowanie właściciela konta i organizatora oraz wstrzymanie listów "
            "nieobowiązkowych, które i tak by nie dotarły i psułyby reputację serwera poczty."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO (kontakt w sprawach udziału w zawodach wymaga działającego "
            "adresu) oraz art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – "
            "dostarczalność poczty serwisu)"
        ),
        subjects=(
            "osoby, na których adres serwis wysłał list: uczestnicy (w tym niepełnoletni), opiekunowie, "
            "członkowie komitetu, rodzice i osoby zaproszone"
        ),
        categories=[
            "adres e-mail, data pierwszego twardego odbicia, kod i treść odpowiedzi serwera odbiorcy "
            "(np. „550 5.1.1 user unknown”), liczba odbić twardych i miękkich, data ostatniego zdarzenia",
            "zawiadomienie o niedoręczeniu (z kopią wysłanego listu) jest czytane i kasowane w ciągu "
            "kilku minut – jego treść nie jest zapisywana",
        ],
        recipients=[
            HOSTING_RECIPIENT,
            "koordynator konkursu – lista kont tego konkursu z niedoręczalnym adresem (ekran i CSV)",
        ],
        retention=(
            f"do zmiany adresu konta, potwierdzenia adresu przez właściciela albo koordynatora, usunięcia "
            f"konta – najdłużej {RETENTION_DAYS} dni od ostatniego zdarzenia (kasowanie automatyczne)"
        ),
        measures=[
            "funkcja działa wyłącznie przy włączonym przełączniku instalacji ``EMAIL_BOUNCE_TRACKING``",
            "zawiadomienia trafiają z relaya poczty do skrzynki na wolumenie serwera, bez udziału "
            "zewnętrznego dostawcy; relay nie przyjmuje poczty z internetu",
            "w dzienniku workera tylko domena i kod odbicia, bez adresu; eksport CSV i każda decyzja "
            "koordynatora są wpisami w dzienniku zdarzeń",
        ],
    )
