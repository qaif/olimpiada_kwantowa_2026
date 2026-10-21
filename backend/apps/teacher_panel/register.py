"""Wiersz rejestru czynności dla formalności uczniów w panelu nauczyciela (UX-PANEL-02 § 6).

Wiersz jest **warunkowy** – wchodzi do rejestru wyłącznie konkursom z flagą ``supervisor_class_details``
(``apps.accounts.processing_register.activities_for``), z tego samego powodu co statystyki szkół:
rejestr opisuje przetwarzanie, które naprawdę zachodzi.

Nowych danych funkcja nie zbiera. Wiersz jest potrzebny, bo zmienia się **zakres wglądu** nauczyciela:
dotąd widział stan prac i ogłoszone wyniki ucznia, teraz widzi też, czy uczeń dopełnił formalności
zawodów (zaświadczenie o statusie ucznia, zgoda rodzica) i ile razy był na warsztatach. Uczniowi
obiecano wcześniej, że nauczyciel jego zgód nie zobaczy – stąd flaga, nowa podstawa i zmieniona
informacja dla ucznia.
"""

from __future__ import annotations

from apps.accounts.processing_register import HOSTING_RECIPIENT, _activity

CLASS_DETAILS_ACTIVITY = _activity(
    key="panel-nauczyciela-formalnosci",
    name="Panel nauczyciela – formalności i obecność uczniów",
    purpose=(
        "Wsparcie ucznia przez nauczyciela-opiekuna w dopełnieniu formalności zawodów (zaświadczenie "
        "o statusie ucznia, zgoda rodzica albo opiekuna prawnego) i w udziale w warsztatach."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes organizatora i szkoły – sprawny udział "
        "ucznia w zawodach) dla wglądu opiekuna w stan formalności ucznia, który sam wskazał opiekuna "
        "w swoim profilu albo przyjął jego zaproszenie (wskazanie jest odwracalne jednym kliknięciem)"
    ),
    subjects="uczestnicy, którzy wskazali opiekuna szkolnego; opiekunowie szkolni",
    categories=[
        "odczyt istniejących danych: stan zaświadczenia o statusie ucznia (brak / czeka / zaakceptowane / "
        "odrzucone – bez pliku i bez powodu odrzucenia), to, czy niepełnoletni ma zgodę rodzica "
        "(bez daty urodzenia, adresu i danych rodzica), liczba warsztatów z odnotowaną obecnością",
        "te same dane w liście klasy pobieranej przez opiekuna (CSV, XLSX)",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "opiekun szkolny – wyłącznie uczniowie, którzy go wskazali; plik listy klasy na jego urządzeniu",
    ],
    retention=(
        "dane liczone przy wyświetleniu, bez kopii w bazie; plik listy klasy powstaje przy pobraniu, "
        "a jego dalszy los jest sprawą opiekuna (dziennik zdarzeń notuje pobranie bez danych osobowych)"
    ),
    measures=[
        "funkcja domyślnie **wyłączona** (flaga konkursu `supervisor_class_details`); informacja dla "
        "ucznia (strona zgody na opiekuna, list z prośbą o zgodę, opis pola w profilu) zmienia się "
        "razem z flagą",
        "zakres wyłącznie z listy `students_of` – uczniowie, którzy sami wskazali adres opiekuna, "
        "z konkursu jego profilu, bez kont po anonimizacji",
        "pobranie listy klasy zapisywane w dzienniku zdarzeń (`export.generated`) bez danych osobowych",
    ],
)
