"""Płatności w rejestrze czynności przetwarzania (RODO) – czynność warunkowa za flagą ``fees``.

Definicja stoi tutaj, a nie w ``apps.accounts.processing_register``, bo opisuje dane tej aplikacji;
rejestr dokleja ją jednym warunkiem w ``activities_for`` (tak jak delegacje i materiały warsztatów).

**Okres przechowywania jest inny niż przy koncie.** Faktury i zapis wpłat są dokumentacją księgową
– ustawa o rachunkowości i ordynacja podatkowa każą je trzymać 5 lat od końca roku, w którym minął
termin płatności podatku. Usunięcie konta odpina więc autora zamówienia (``SET_NULL``), ale nie
kasuje zamówienia ani dokumentu: podstawą jest wtedy art. 6 ust. 1 lit. c RODO (obowiązek prawny).
"""

from __future__ import annotations

from apps.accounts.processing_register import HOSTING_RECIPIENT, MAIL_RECIPIENT, _activity

PAYMENTS_ACTIVITY = _activity(
    key="platnosci",
    name="Opłaty za udział – zamówienia, płatności online, faktury i zwroty",
    purpose=(
        "Pobranie opłaty za udział w zawodach (delegacja krajowa albo uczestnik), wystawienie faktury "
        "pro forma i faktury, potwierdzenie wpłaty, zwroty oraz rozliczenie z księgowością organizatora."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. b RODO (umowa – udział w zawodach na zasadach Regulaminu i opłata za udział); "
        "art. 6 ust. 1 lit. c RODO (obowiązek przechowywania dokumentacji księgowej i podatkowej)"
    ),
    subjects="płacący: opiekunowie drużyn narodowych, uczestnicy; osoby wskazane jako nabywca na fakturze",
    categories=[
        "nabywca: nazwa instytucji albo imię i nazwisko, adres, kraj, NIP/VAT ID (opcjonalnie), "
        "e-mail do rozliczeń",
        "zamówienie: pozycje, kwoty, waluta, kod referencyjny, daty, numer faktury pro forma i faktury",
        "płatność: operator (Stripe, Przelewy24, przelew), identyfikatory transakcji u operatora, stan, "
        "zwroty; dla przelewu – data wpływu, notatka koordynatora i opcjonalnie skan dowodu wpłaty",
        "danych kart płatniczych serwis nie przetwarza – wpisuje się je wyłącznie na stronie operatora",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        MAIL_RECIPIENT,
        "Stripe Payments Europe Ltd. (Irlandia) – operator płatności kartą, odrębny administrator danych "
        "płatniczych; przekazujemy kwotę, opis, kod zamówienia i e-mail nabywcy",
        "PayPro S.A. (Przelewy24, Polska) – operator płatności, odrębny administrator danych płatniczych",
        "księgowość organizatora (eksport CSV)",
    ],
    retention=(
        "5 lat od końca roku kalendarzowego, w którym upłynął termin płatności podatku za rok wystawienia "
        "dokumentu (dokumentacja księgowa). Skan dowodu wpłaty – do zamknięcia rozliczenia edycji; plik "
        "zainfekowany jest usuwany od razu. Usunięcie konta odpina autora zamówienia, dokumenty zostają."
    ),
    measures=[
        "funkcja działa wyłącznie w konkursie z włączonymi opłatami",
        "kwotę zapłaty liczy serwer; webhook operatora uwierzytelniony podpisem (HMAC-SHA256 / SHA-384) "
        "z tolerancją czasu, idempotentny; sekrety wyłącznie w zmiennych środowiskowych",
        "dowód wpłaty skanowany antywirusowo przed udostępnieniem, pobierany wyłącznie jako załącznik",
        "każda czynność finansowa (zniżka, zwolnienie, zwrot, wpływ przelewu) w dzienniku zdarzeń",
    ],
)
