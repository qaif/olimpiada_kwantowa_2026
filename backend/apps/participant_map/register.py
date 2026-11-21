"""Wiersz rejestru czynności przetwarzania dla mapy uczestników (MAP-01).

Wiersz jest **warunkowy** – wchodzi do rejestru wyłącznie konkursom z flagą ``participant_map``
(``apps.accounts.processing_register.activities_for``), tak jak statystyki szkół: rejestr opisuje
przetwarzanie, które naprawdę zachodzi u danego administratora.

Nowych danych funkcja nie zbiera. Wiersz jest potrzebny, bo dane już zebrane (szkoła z profilu
uczestnika) są używane w **nowym celu** – publikacji statystyki zbiorczej – a dotychczasowy rejestr
mówił o publikacji wyłącznie przy wynikach (kod publiczny, punktacja). Zestawienie zbiorcze
z progiem ochrony nie jest daną osobową; samo jego wyliczenie z profili – jest przetwarzaniem.
"""

from __future__ import annotations

from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

PARTICIPANT_MAP_ACTIVITY = _activity(
    key="mapa-uczestnikow",
    name="Publikacja zbiorczej statystyki uczestników według miejscowości (mapa uczestników)",
    purpose=(
        "Informacja publiczna o zasięgu olimpiady: z ilu i z jakich miejscowości pochodzą uczestnicy "
        "bieżącej edycji (strona „Mapa uczestników” w sekcji „Dokumenty”)."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. e RODO (zadanie realizowane w interesie publicznym – popularyzacja wiedzy "
        "i sprawozdawczość organizatora olimpiady) dla wyliczenia zestawienia z profili; publikowane "
        "zestawienie z progiem ochrony nie jest daną osobową"
    ),
    subjects="uczestnicy bieżącej edycji (bez kont zanonimizowanych)",
    categories=[
        "odczyt istniejących danych: szkoła z wykazu SIO wskazana w profilu uczestnika (jej gmina "
        "i województwo) – bez nazwy szkoły w publikacji",
        "publikowane wyłącznie liczby: uczestnicy łącznie, liczba szkół i miejscowości, liczba uczestników "
        "w miejscowości – miejscowość z mniej niż 3 uczestnikami (próg konkursu, nie niższy niż 3) "
        "tylko zbiorczo jako „pozostałe miejscowości” województwa, bez nazwy i bez punktu na mapie",
    ],
    recipients=[HOSTING_RECIPIENT, "publiczność serwisu (strona jest jawna)"],
    retention=(
        "zestawienie jest liczone z bieżących danych i leży w pamięci podręcznej serwera do godziny "
        "(albo do przeliczenia przez koordynatora); nie powstaje z niego żaden trwały zapis – po "
        "anonimizacji konta uczestnik znika z kolejnego przeliczenia"
    ),
    measures=[
        "funkcja domyślnie **wyłączona** (flaga konkursu `participant_map`), włącza ją organizator",
        "próg ochrony co najmniej 3 uczestników na miejscowość, którego panel nie pozwala obniżyć",
        "w publikacji brak nazw szkół, kodów uczestników i jakichkolwiek danych pojedynczych osób",
        "odświeżenie na żądanie wyłącznie przez koordynatora, z limitem i wpisem w dzienniku zdarzeń",
    ],
)
