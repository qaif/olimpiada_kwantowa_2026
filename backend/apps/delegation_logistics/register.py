"""Wiersz rejestru czynności przetwarzania (art. 30 RODO) – logistyka finału dla delegacji.

Czynność **warunkowa**: wchodzi do rejestru konkursu wyłącznie wtedy, gdy logistyka finału działa
(``models.enabled``) – ta sama zasada, co forum i status ucznia w ``apps.accounts.processing_register``:
rejestr opisuje przetwarzanie, które naprawdę zachodzi. Wiersz stoi tutaj, a nie w rejestrze, żeby
treść czynności zmieniała się razem z kodem, który ją wykonuje; rejestr dokłada go jedną linią.

Podstawy prawne są **propozycją do zatwierdzenia przez administratora** (jak przy ocenie AI):
obowiązek meldunkowy hotelu i wymogi konsulatu zależą od kraju finału.
"""

from __future__ import annotations


def activity(competition=None):
    """``ProcessingActivity`` logistyki finału albo ``None``, gdy w tym konkursie jej nie ma."""
    from apps.accounts.processing_register import HOSTING_RECIPIENT, MAIL_RECIPIENT, _activity

    from .models import DEFAULT_RETENTION_DAYS, enabled

    if not enabled(competition):
        return None
    return _activity(
        key="logistyka-finalu",
        name="Logistyka finału stacjonarnego – delegacje krajowe",
        purpose=(
            "Organizacja pobytu delegacji na finale: zaproszenia do wizy, odbiór z lotniska i dworca, "
            "zakwaterowanie (z zasadami dla niepełnoletnich), wyżywienie, identyfikatory, kontrola "
            "obecności i kontakt w nagłych wypadkach."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO (udział w finale na zasadach Regulaminu); lit. c (obowiązek "
            "meldunkowy obcokrajowców w miejscu zakwaterowania – jeśli wynika z prawa kraju finału) "
            "i lit. f (bezpieczeństwo uczestników, kontakt alarmowy); dane o zdrowiu, diecie religijnej "
            "i alergiach – art. 9 ust. 2 lit. a RODO (wyraźna zgoda osoby albo jej rodzica, zaznaczona "
            "przez opiekuna drużyny i zapisana z datą); dane osób poniżej 16 lat – zgoda rodzica "
            "(art. 8 RODO) zbierana przez opiekuna drużyny – do potwierdzenia przez administratora"
        ),
        subjects="uczniowie, opiekunowie drużyn narodowych, obserwatorzy i goście delegacji; osoby "
        "wskazane jako kontakt alarmowy",
        categories=[
            "dokument podróży: imię i nazwisko jak w paszporcie, obywatelstwo, data urodzenia, numer "
            "i data ważności paszportu (szyfrowane w bazie)",
            "podróż: daty, godziny, środek, numer lotu lub pociągu, lotnisko lub dworzec przyjazdu i wyjazdu",
            "zakwaterowanie: potrzeba noclegu, płeć (wyłącznie do przydziału pokoi), preferencja "
            "współlokatora, uwagi, przydział pokoju",
            "wyżywienie i zdrowie (tylko gdy organizator włączył zbieranie potrzeb szczególnych): dieta, "
            "uwagi do diety, alergie, uwagi medyczne – szyfrowane; zgoda z datą i osobą",
            "identyfikator: rozmiar koszulki, zdjęcie, losowy token w kodzie QR; obecność w punktach "
            "kontroli",
            "kontakt alarmowy: imię i nazwisko oraz telefon (szyfrowane)",
            "rejestr listów zapraszających: numer, kraj, data, liczba osób; migawka danych paszportowych "
            "osób z listu (szyfrowana)",
        ],
        recipients=[
            HOSTING_RECIPIENT,
            MAIL_RECIPIENT + " – wyłącznie przypomnienia o brakach (bez danych paszportowych i zdrowia)",
            "oficerowie logistyki – koordynatorzy z osobnym przydziałem; pozostali koordynatorzy widzą "
            "wyłącznie liczby zbiorcze",
            "obsługa rejestracji (osobny przydział) – imię i nazwisko, kraj, rola i zdjęcie osoby skanowanej",
            "opiekunowie drużyny tego samego kraju – dane członków swojej delegacji",
            "hotel, firma cateringowa, przewoźnik i konsulat – wyłącznie wyciągi przekazane przez "
            "organizatora (lista pokoi, lista diet, tablica przylotów, list zapraszający)",
        ],
        retention=(
            f"do {DEFAULT_RETENTION_DAYS} dni (ustawienie finału) po ostatnim dniu finału – potem "
            "automatyczne usunięcie wszystkich danych członków, zdjęć i migawek listów (zadanie dobowe); "
            "rejestr listów zostaje bez danych osób; wcześniej – przy usunięciu konta, wypisaniu z "
            "delegacji albo usunięciu gościa"
        ),
        measures=[
            "funkcja działa wyłącznie w konkursie z trybem delegacji i włączoną logistyką etapu "
            "stacjonarnego; dane o zdrowiu – dopiero po osobnej decyzji organizatora",
            "szyfrowanie pól wrażliwych w bazie (Fernet, klucz wyprowadzony z sekretu aplikacji, "
            "z obsługą rotacji klucza)",
            "dostęp per osoba wyłącznie dla oficera logistyki (przydział nadawany przez "
            "superkoordynatora albo innego oficera, każdy w dzienniku zdarzeń)",
            "terminy per grupa danych i blokada zmian po terminie; dziennik zdarzeń notuje nazwy "
            "zmienionych pól, nigdy ich wartości",
            "kod QR identyfikatora zawiera wyłącznie losowy token – bez danych osobowych; token "
            "unieważnialny",
            "zdjęcie: format rozpoznawany po treści, limit 5 MB, skan antywirusowy przed pokazaniem, "
            "prywatny magazyn bez publicznego adresu",
            "eksporty CSV i pobrania listów zapisywane w dzienniku zdarzeń",
        ],
    )
