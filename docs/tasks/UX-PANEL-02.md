# UX-PANEL-02 — panel nauczyciela (opiekuna szkolnego): menu, Start, postęp klasy, eksport

Prośba organizatora z 21.10.2026: „ładny, spójny z nowym panelem uczestnika panel nauczyciela, z widokiem
postępu uczniów i eksportem klasy”. Wzorzec: UX-PANEL-01 (menu w Pythonie, rama z kolumną menu /
`<details>` na telefonie, arkusz na tokenach ról motywu).

## 1. Inwentaryzacja (stan na `origin/main` z 21.10.2026)

| Funkcja | Adres | Flaga / konfiguracja | Kto / warunek | Gdzie był odnośnik |
|---|---|---|---|---|
| Pulpit „Moi uczniowie”: uczniowie ze ścieżką statusu prac w etapach, suma punktów po ogłoszeniu, udział szkoły, własne zaświadczenia | `/supervisor/` | – (rola `supervisor` domyślnie wł., `supervisor_role`) | `SupervisorRequiredMixin` → `supervisors.supervisor_profile` (rola **i** profil w konkursie z żądania) | pasek konta „Moi uczniowie”, przekierowanie po zalogowaniu |
| Konta uczniów (zaproszony / aktywny / zapisał się sam), ponowne zaproszenie | `/supervisor/students/`, POST `…/<pk>/resend/` | – | jw.; limit `supervisor_resend` per konto + karencja 1 h | przycisk na pulpicie |
| Import listy klasy (podgląd ze statusami wierszy REG-UX-01, zatwierdzenie) | `/supervisor/import/` | – | jw.; limit `upload`; szkoła z rejestru tylko własna | przycisk na pulpicie i liście |
| Potwierdzenie udziału szkoły w edycji | POST `/supervisor/participation/` | – | jw. | karta na pulpicie |
| Zaświadczenia opiekuna (PDF) | `/supervisor/certificates/<pk>/` | `certificates` | jw.; tylko własne, konkurs z żądania | lista na pulpicie |
| Statystyki uczniów i szkoły, raport PDF | `/supervisor/statistics/…` | `school_statistics` (wył.) | jw.; agregat szkoły po weryfikacji | `{% school_stats_link %}` na pulpicie |
| Plakaty do szkoły | `/plakaty/` | opublikowany plakat (`promo_available`) | wszyscy | przycisk na pulpicie, stopka |
| Materiały z warsztatów | `/warsztaty/materialy/` | `workshop_materials` + opublikowany materiał | `workshop_materials.access.can_view` **obejmuje** opiekuna | pasek konta (`_workshop_materials_link.html`) |
| Harmonogram warsztatów, kalendarz ICS | strona CMS „Warsztaty”, `/kalendarz.ics` | – | publiczne | strona główna, stopka |
| Wiadomości (czat) | – | – | **nie dla opiekuna** (`chat.services`: opiekun nie pisze i nie odbiera) | – |
| Powiadomienia w panelu | – | – | **tylko uczestnicy** (UX-PANEL-01 D9; widoki `ParticipantRequiredMixin`) | – |
| Webinary | – | `webinars` | **nie dla opiekuna** (`webinars.services.Viewer`: uczestnik, komitet, koordynator) | – |
| Profil, hasło, 2FA, usunięcie konta | `/account/profile/`, `/account/password/`, `/account/2fa/` | 2FA: `TWO_FACTOR_ENABLED` | każde konto | pasek konta |
| Zgłoś problem | `/support/new/` | – | każdy | stopka |

**Zakres wglądu dziś** (źródło: `apps.accounts.supervisors` – docstring i `student_rows`; obietnica dla
ucznia na stronie zgody `web/supervisor/consent.html`, w liście `supervisor_consent_body.*` i w opisie
pola profilu `apps.web.forms`): imię i nazwisko, kod uczestnika, szkoła, klasa, czy konto jest aktywne,
na jakim etapie procedury są prace w każdym etapie („oddane / w ocenie / ocenione / wyniki”), suma
punktów **wyłącznie** po ogłoszeniu wyników etapu (ta sama, co w tabeli publicznej). Uczniowi napisano
wprost: „nauczyciel **nie** zobaczy Twoich prac, komentarzy recenzentów, punktów przed ogłoszeniem
wyników **ani Twoich danych kontaktowych i zgód**”. Adresów e-mail uczniów panel nie pokazuje.

## 2. Decyzje

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | Nowa aplikacja `apps/teacher_panel`: `nav.py` (sekcje i pozycje), znacznik `{% teacher_nav %}`, rama `teacher_panel/base.html`. Klasy `Item`/`Group` i `resolve` **importowane** z `apps.participant_panel.nav` (bez zmiany tamtego pliku), arkusz `participant_panel/panel.css` i ikony `participant_panel/_icons.html` **włączane** bez zmian; własne dodatki w `teacher_panel/teacher.css` i `_icons.html` (symbole `tp-i-…`). | jeden wzorzec, zero zmian we wspólnych plikach – równolegle powstaje panel komisji |
| D2 | Pozycje warunkowe wyłącznie z istniejących warunków: `workshop_materials_available` (+ flaga), `promo_available`, `school_stats.services.enabled`, `twofactor.is_enabled`. Brak adresu w urlconfie = pozycja znika. | jak UX-PANEL-01 D2 |
| D3 | `/supervisor/` zostaje adresem pulpitu (przekierowanie po zalogowaniu, pasek konta) i staje się „Start” z kaflami; lista ze ścieżkami statusu przechodzi do „Postępu klasy”, a udział szkoły i zaświadczenia – na ekran „Szkoła” (`/supervisor/school/`). POST potwierdzenia wraca na „Szkołę”. | jedno pytanie na ekran |
| D4 | „Postęp klasy” (`/supervisor/progress/`) i eksport liczone przez **jeden** serwis (`teacher_panel.services.class_progress`) na `students_of` – ta sama reguła dostępu i izolacja konkursu, co dziś. Eksport = te same kolumny i ten sam filtr, co ekran. | ekran i plik nie mogą się rozjechać |
| D5 | Dane „formalności” (zaświadczenie o statusie ucznia, zgoda rodzica u niepełnoletnich, obecność na warsztatach) **nie** wchodzą do panelu domyślnie – uczniowi obiecano, że nauczyciel nie zobaczy jego zgód, a zaświadczenia i obecności nie było w zakresie wglądu. Wchodzą za nową flagą konkursu **`supervisor_class_details`** (domyślnie wył.), która jednocześnie dopisuje je do informacji dla ucznia (strona zgody, list z prośbą o zgodę, opis pola w profilu) i dokłada wiersz rejestru czynności. | zmiana zakresu wglądu = decyzja organizatora o nowym celu (wzorzec `school_statistics`) |
| D6 | Powiadomień dla nauczyciela **nie ma** w tym zadaniu. Nowe rodzaje w `apps.notifications` to zmiana wyborów modelu (migracja `notifications.0002`) we wspólnej aplikacji, w której równolegle pracuje panel komisji (ryzyko dwóch migracji 0002), a haki w imporcie i uploadzie prac to nowy zapis w ścieżce ucznia. Start pokazuje zamiast tego kafle z liczbami „do zrobienia”. | „tylko jeśli tanio” – nie jest |
| D7 | Bez czatu i webinarów w menu – nauczyciel nie ma do nich dostępu (§ 1). Profil, hasło i 2FA są w menu, ale ich ekrany zostają wspólne dla ról (bez ramy) – jak UX-PANEL-01 § 5. | nie dajemy dostępu, którego nie ma |
| D8 | Eksport: CSV i XLSX przez `apps.core.exports` (`Dataset`), audyt `export.generated` (`kind=supervisor_class_progress`, format, liczba wierszy) **przed** oddaniem pliku, bez adresów e-mail (jak pozostałe eksporty paneli – bez limitu żądań:
GET niczego nie wysyła ani nie zapisuje poza audytem). Wiersz rejestru „konta opiekunów szkolnych” dostaje kategorię „lista klasy pobrana przez opiekuna”. Rejestr 1.26. | dane wychodzą z serwisu na komputer nauczyciela |
| D9 | Stała liczba zapytań względem liczby uczniów: wpisy, zgłoszenia, liczby zadań, publikacje, podejścia do testów, (za flagą) zaświadczenia i obecności – każde jednym zapytaniem dla całej klasy. Test porównuje klasę 2- i 6-osobową. | |
| D10 | Napisy przez gettext (polskie msgid) z katalogiem aplikacji w 10 językach; dotychczasowe ekrany opiekuna (pulpit był bez tłumaczeń) przechodzą do ramy z tłumaczeniami. | IQO anglojęzyczny |

## 3. Menu

- **Start**: Pulpit.
- **Uczniowie**: Moi uczniowie (konta i zaproszenia), Zaproś uczniów z listy (import), Postęp klasy.
- **Materiały i warsztaty**: Materiały z warsztatów*, Plakaty do powieszenia w szkole*, Kalendarz konkursu (`.ics`).
- **Szkoła**: Udział szkoły i zaświadczenia, Statystyki szkoły*.
- **Pomoc**: Zgłoś problem.
- **Konto**: Profil i dane, Hasło, Weryfikacja dwuetapowa*.

`*` – pozycja warunkowa (D2). Rama ma podpisy „Menu nauczyciela” (telefon) i „Nawigacja panelu
nauczyciela” (`aria-label`) – żadnego „Menu panelu…”, które należy do koordynatora.

## 4. Start (`/supervisor/`)

Kafle (lista `<ul>`, każdy z nagłówkiem `<h3>` i jedną czynnością): **Uczniowie** (z kontem / zaproszeni
bez konta), **Najbliższy termin** (najbliższy termin oddania otwartego etapu albo start następnego
etapu bieżącej edycji, bez treningu), **Bez pracy w otwartym etapie** (zapisani do etapu przyjmującego
prace, bez oddanej pracy / zakończonego testu), **Najbliższy warsztat** (z harmonogramu strony
„Warsztaty”, bez linku do spotkania), **Udział szkoły** (potwierdzony / nie). Za flagą
`supervisor_class_details`: **Bez zaświadczenia o statusie ucznia** (gdy konkurs zbiera zaświadczenia)
i **Bez zgody rodzica** (niepełnoletni, gdy zestaw zgód konkursu ją wymaga). Kafel z liczbą prowadzi do
„Postępu klasy” z ustawionym filtrem.

## 5. Postęp klasy (`/supervisor/progress/`)

**Zakres danych i podstawa.** Wiersz = uczeń z `students_of(supervisor)` (wskazał adres nauczyciela;
konkurs profilu opiekuna; bez kont po anonimizacji). Kolumny zawsze: imię i nazwisko, kod, klasa, stan
konta, a dla każdego etapu bieżącej edycji (bez treningu): zapis, liczba zadań z oddaną pracą / liczba
zadań etapu (praca odrzucona przez antywirusa się nie liczy; w teście online – zakończone podejście),
dzień ostatniego oddania, stan procedury (najmniej zaawansowana praca: oddane / w ocenie / ocenione /
wyniki), a **po ogłoszeniu wyników** suma punktów i decyzja (zakwalifikowany / niezakwalifikowany) –
te same wartości, co w tabeli publicznej. Treści prac, plików, komentarzy recenzentów, ocen cząstkowych,
punktów przed ogłoszeniem i adresów e-mail nie ma ani na ekranie, ani w pliku. Podstawa: ta sama, co
dziś (wiersz rejestru „Prowadzenie kont opiekunów szkolnych”, art. 6 ust. 1 lit. f, wskazanie
odwracalne przez ucznia). Za flagą `supervisor_class_details` dochodzą: zgoda rodzica (u niepełnoletnich:
jest / brak; u pełnoletnich „pełnoletni”), stan zaświadczenia o statusie ucznia (brak / czeka na
weryfikację / zaakceptowane / odrzucone – bez pliku i bez powodu odrzucenia) i liczba warsztatów
z odnotowaną obecnością – podstawa w nowym, warunkowym wierszu rejestru (§ 6 niżej).

**Filtry** (formularz GET, bez JS): klasa, stan konta, „bez pracy w otwartym etapie”, za flagą – stan
zaświadczenia i „bez zgody rodzica”. **Sortowanie** nagłówkami (`?sort=name|grade|account|work`, `-` =
malejąco, `aria-sort`). Przyciski „Pobierz CSV” / „Pobierz XLSX” niosą bieżący filtr i sortowanie.

## 6. RODO

- Rejestr czynności **1.26**: wiersz „Prowadzenie kont opiekunów szkolnych” – nowa kategoria (lista klasy
  pobierana przez opiekuna, CSV/XLSX, z wpisem w dzienniku zdarzeń) i odbiorca (opiekun – plik na jego
  urządzeniu); nowy wiersz warunkowy „Panel nauczyciela – formalności i obecność uczniów” (flaga
  `supervisor_class_details`): odczyt istniejących danych (stan zaświadczenia, zgoda rodzica, liczba
  obecności), podstawa art. 6 ust. 1 lit. f (wsparcie ucznia przez szkołę w formalnościach zawodów),
  środki: flaga domyślnie wyłączona, informacja dla ucznia zmienia się razem z flagą, wskazanie
  nauczyciela odwracalne.
- Nowych modeli i nowych danych nie ma; migracji brak.

## 7. Poza zakresem

Powiadomienia dla nauczyciela (D6), rama dla ekranów wspólnych z innymi rolami (profil, hasło, 2FA,
materiały, plakaty), eksport z danymi kontaktowymi uczniów, historia uczniów przez edycje (to robią
statystyki szkół, `school_statistics`).
