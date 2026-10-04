"""Wiersz rejestru czynności przetwarzania dla statystyk szkół (STAT-01 § 7).

Wiersz jest **warunkowy** – wchodzi do rejestru wyłącznie konkursom z flagą ``school_statistics``
(``apps.accounts.processing_register.activities_for``), z tego samego powodu co forum i status
ucznia: rejestr opisuje przetwarzanie, które naprawdę zachodzi.

Nowych danych osobowych funkcja nie zbiera. Wiersz jest potrzebny mimo to, bo zmienia się **zakres
wglądu** opiekuna szkolnego: dotąd widział stan prac i sumę punktów ucznia w bieżącej edycji, teraz
widzi przebieg ucznia przez kolejne edycje i jego wynik na tle szkoły i województwa. To jest
przetwarzanie w nowym celu (informacja zwrotna dla szkoły), więc musi mieć wskazaną podstawę.
"""

from __future__ import annotations

from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

SCHOOL_STATISTICS_ACTIVITY = _activity(
    key="statystyki-szkol",
    name="Statystyki szkół i opiekunów szkolnych",
    purpose=(
        "Informacja zwrotna dla szkół i opiekunów szkolnych o udziale i wynikach uczniów w kolejnych "
        "edycjach oraz planowanie akcji promocyjnej organizatora (szkoły, które przestały zgłaszać "
        "uczniów)."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora i szkoły – wsparcie "
        "pracy z uczniem uzdolnionym) dla wglądu opiekuna w przebieg i ogłoszone wyniki ucznia, który "
        "sam wskazał opiekuna w swoim profilu (wskazanie jest odwracalne jednym kliknięciem); dane "
        "zbiorcze z progiem k-anonimowości nie są danymi osobowymi"
    ),
    subjects="uczestnicy, którzy wskazali opiekuna szkolnego; opiekunowie szkolni",
    categories=[
        "brak nowych kategorii – odczyt istniejących: wpisy do etapów, fakt i termin oddania pracy, "
        "suma punktów i kwalifikacja z oficjalnie ogłoszonych wyników, szkoła i województwo z profilu",
        "agregaty szkół i województw (liczba uczestników, oddanych prac, zakwalifikowanych, średnia) "
        "ukrywane, gdy grupa liczy mniej niż 5 osób; u opiekuna także, gdy poza jego uczniami jest "
        "w grupie od 1 do 4 osób",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "opiekun szkolny – wyłącznie uczniowie, którzy go wskazali, i agregaty; agregaty całej szkoły "
        "tylko po weryfikacji szkoły opiekuna przez organizatora",
        "dyrektor szkoły – raport PDF zawierający wyłącznie dane zbiorcze, bez nazwisk i kodów uczestników",
    ],
    retention=(
        "nic nie jest przechowywane trwale: zestawienia liczone są przy wyświetleniu, a w pamięci "
        "podręcznej serwera leżą wyłącznie agregaty (do doby); PDF i CSV powstają przy pobraniu"
    ),
    measures=[
        "funkcja domyślnie **wyłączona** (flaga konkursu `school_statistics`)",
        "punkty i kwalifikacja wyłącznie z zamrożonej publikacji wyników – nic przed ogłoszeniem, "
        "a przy ogłoszeniu samej listy awansujących bez średnich i bez punktów osób spoza listy",
        "próg k-anonimowości 5 z regułą dopełnienia przeciw odejmowaniu agregatów",
        "pobrania CSV i PDF zapisywane w dzienniku zdarzeń bez danych osobowych",
    ],
)
