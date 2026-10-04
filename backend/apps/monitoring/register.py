"""Wiersz rejestru czynności przetwarzania (art. 30 RODO) – monitorowanie błędów aplikacji (OPS-02 § 6).

Czynność **warunkowa**: wchodzi do rejestru wyłącznie wtedy, gdy zdarzenia są wysyłane (niepusty
``SENTRY_DSN`` albo włączone błędy przeglądarek z poprawnym DSN) – ta sama zasada, co forum
i logistyka finału w ``apps.accounts.processing_register``: rejestr opisuje przetwarzanie, które
naprawdę zachodzi. Warunek jest instalacyjny, a nie per konkurs: klient obejmuje cały proces
``web``/``worker``, więc wiersz dostaje rejestr każdego konkursu.

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
    """``ProcessingActivity`` monitorowania błędów albo ``None``, gdy nic nie wysyła zdarzeń."""
    from .browser import browser_config

    # Przeglądarki mogą wysyłać błędy także przy pustym DSN serwera (``SENTRY_BROWSER_DSN``) – wiersz
    # musi wtedy stać tak samo, bo przetwarzanie zachodzi.
    browser = browser_config() is not None
    if not (getattr(settings, "SENTRY_DSN", "") or "").strip() and not browser:
        return None
    from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

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
            "niepełnoletni); znane identyfikatory są usuwane przed wysyłką, ale komunikat błędu może "
            "wyjątkowo zawierać fragment danych osoby, którego filtr nie rozpoznał (pseudonimizacja, "
            "a nie anonimizacja)"
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
            "filtr po stronie aplikacji, przed wysyłką: bez treści żądań i formularzy, ciasteczek, "
            "parametrów zapytania, identyfikatora konta i zmiennych lokalnych programu; ścieżka adresu "
            "zastąpiona wzorcem trasy (tokeny i kody z adresów nie są wysyłane), adresy IP, e-mail, "
            "PESEL, telefon i wartości z błędów bazy danych zastępowane znacznikiem [Filtered]",
            "pola o nazwach wskazujących na dane osobowe lub szczególne (paszport, zdrowie, dieta, PESEL, "
            "data urodzenia, telefon, e-mail, pliki) zastępowane znacznikiem [Filtered]",
            "panel GlitchTip wyłącznie przez HTTPS, konta zakładane przez administratora (samorejestracja "
            "wyłączona po założeniu pierwszego konta), zalecane logowanie dwuskładnikowe; limit zdarzeń "
            "na klucz projektu",
        ],
    )
