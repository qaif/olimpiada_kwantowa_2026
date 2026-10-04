"""Wiersz rejestru czynności przetwarzania (art. 30 RODO) – monitorowanie błędów aplikacji (OPS-02 § 6).

Czynność **warunkowa**: wchodzi do rejestru wyłącznie wtedy, gdy klient błędów działa (niepusty
``SENTRY_DSN``) – ta sama zasada, co forum i logistyka finału w ``apps.accounts.processing_register``:
rejestr opisuje przetwarzanie, które naprawdę zachodzi. Warunek jest instalacyjny, a nie per konkurs:
klient obejmuje cały proces ``web``/``worker``, więc wiersz dostaje rejestr każdego konkursu.

Odbiorca jest **wewnętrzny**: GlitchTip stoi na serwerze organizatora (profil ``monitoring``
docker-compose), więc nie ma tu nowego podmiotu ani przekazania do państwa trzeciego – jest ten sam
dostawca hostingu, co dla reszty serwisu.
"""

from __future__ import annotations

from django.conf import settings

#: Retencja zdarzeń w GlitchTipie (``GLITCHTIP_RETENTION_DAYS`` w docker-compose.yml) – ta sama
#: liczba, która stoi w compose; test pilnuje zgodności.
RETENTION_DAYS = 30


def activity():
    """``ProcessingActivity`` monitorowania błędów albo ``None``, gdy klient jest wyłączony."""
    if not (getattr(settings, "SENTRY_DSN", "") or "").strip():
        return None
    from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

    browser = bool(getattr(settings, "SENTRY_BROWSER", False))
    categories = [
        "dane techniczne błędu: typ i komunikat wyjątku, ślad stosu (pliki i wiersze kodu, bez wartości "
        "zmiennych), wydanie aplikacji, nazwa kontenera, czas zdarzenia",
        "metoda i ścieżka adresu żądania – bez parametrów zapytania i bez fragmentu",
        "skrót (slug) konkursu, w którym wystąpił błąd",
        "ostatnie zdarzenia techniczne przed błędem (zapytania do bazy bez wartości, wpisy dziennika) "
        "po automatycznym usunięciu adresów e-mail, numerów PESEL i telefonu, tokenów i kluczy",
    ]
    if browser:
        categories.append(
            "błędy JavaScriptu w przeglądarce: komunikat i ślad stosu (po tym samym filtrze), adres "
            "strony bez zapytania – bez ciasteczek, identyfikatora konta i nagłówka przeglądarki"
        )
    return _activity(
        key="monitoring-bledow",
        name="Monitorowanie błędów aplikacji",
        purpose=(
            "Wykrywanie i usuwanie błędów serwisu (np. nieudane oddanie pracy, błąd strony) – "
            "zapewnienie ciągłości działania i bezpieczeństwa systemu."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – sprawne "
            "i bezpieczne działanie serwisu)"
        ),
        subjects=(
            "osoby korzystające z serwisu, w których żądaniu wystąpił błąd (w tym uczestnicy "
            "niepełnoletni) – w praktyce bez danych pozwalających je zidentyfikować"
        ),
        categories=categories,
        recipients=[
            "brak odbiorcy zewnętrznego – własna instancja GlitchTip na serwerze organizatora "
            "(podmiot przetwarzający wewnętrzny), bez przekazania do państwa trzeciego",
            HOSTING_RECIPIENT,
            "koordynator techniczny (administrator GlitchTip) – konta zakładane ręcznie, bez samorejestracji",
        ],
        retention=f"{RETENTION_DAYS} dni od zdarzenia, potem automatyczne usunięcie przez GlitchTip",
        measures=[
            "nie są wysyłane: adres IP, ciasteczka, treść żądań i formularzy, parametry zapytania, "
            "identyfikator konta, zmienne lokalne programu (filtr po stronie aplikacji przed wysyłką)",
            "pola o nazwach wskazujących na dane osobowe lub szczególne (paszport, zdrowie, dieta, PESEL, "
            "data urodzenia, telefon, e-mail, pliki) zastępowane znacznikiem [Filtered]",
            "panel GlitchTip wyłącznie przez HTTPS, z kontem administratora; rejestracja wyłączona",
        ],
    )
