# ACC-DUP-01: Zdublowane konta uczestników w panelu koordynatora

## 0. Cel i granice

Prośba organizatora z 9.10.2026: „dodaj funkcję wyszukiwania zdublowanych kont do panelu
koordynatora”. Ręczne zapytanie na produkcji znalazło 17 osób z 36 kontami uczestnika w tym samym
konkursie (to samo imię, nazwisko i szkoła, różny adres e-mail). Typowy przebieg: rejestracja
z literówką w adresie (`gmail.con`, `5lo.bielsko.pl` zamiast `lo5.bielsko.pl`) albo list aktywacyjny
zatrzymany przez skrzynkę (blokada Outlooka), a potem drugie konto. W 14 grupach dokładnie jedno
konto się logowało, pozostałe nigdy.

Ekran **rozszerza istniejące zarządzanie kontami** (`/coordinator/accounts/`), więc kod mieszka
w `apps/accounts` (serwis) i `apps/web` (widoki, szablony) – bez nowej aplikacji.

Czego zadanie **nie** robi:
- nie scala kont (przeniesienie prac, zgód i wpisów między profilami to osobna, ryzykowna operacja –
  propozycja na przyszłość w § 7),
- nie kasuje niczego samo: każde usunięcie jest decyzją koordynatora z ekranem potwierdzenia,
- nie dodaje drugiej ścieżki kasowania – każde usunięcie idzie przez
  `apps.accounts.profile.delete_account_by_coordinator` (ten sam audyt `account.deleted_by_coordinator`,
  ta sama reguła „ślad w zawodach → anonimizacja, brak śladu → usunięcie w całości”).

## 1. Grupowanie (`apps/accounts/duplicates.py`, `find_duplicate_groups(competition)`)

1. **Zakres:** profile uczestnika (`Participant`) **tego** konkursu, bez kont zanonimizowanych
   (`exclude_anonymised()`). Profile innych konkursów nie wchodzą nigdy – ta sama osoba startująca
   w dwóch olimpiadach to nie duplikat.
2. **Klucz osoby:** znormalizowane imię i nazwisko konta: NFKC, `casefold()`, usunięte kropki
   i cudzysłowy/apostrofy (`. " ' „ ” “ ‚ ’ ‘ \` « »`), zbite białe znaki. Profil z pustym imieniem
   albo nazwiskiem (po normalizacji) jest pomijany – pusty napis nie jest tożsamością.
3. **Klucz szkoły:** `school_ref_id` (szkoła z wykazu), inaczej `custom_institution_ref_id` (placówka
   z wykazu konkursu), inaczej znormalizowany tekst `school` (ta sama normalizacja). Pusty tekst bez
   odnośnika – profil pomijany. Klucze trzech rodzajów się nie mieszają (świadomie: to samo „LO 5”
   raz z wykazu, raz wpisane ręcznie nie trafi do jednej grupy – § 7).
4. **Grupa** = co najmniej dwa profile o tym samym kluczu (osoba, szkoła). Profil jest jeden na konto
   i konkurs (więz bazy), więc dwa profile w grupie to zawsze dwa konta.

**Koszt:** stała liczba zapytań niezależna od liczby osób – jedno lekkie zapytanie `values_list` po
wszystkich profilach konkursu (grupowanie w Pythonie, bo NFKC i `casefold` nie mają odpowiednika
w SQL), potem po jednym zapytaniu zbiorczym na: profile z kontami i znacznikami ról, wpisy do etapów,
prace, zaświadczenia o statusie ucznia. Razem 5 zapytań, także przy zerze duplikatów (wtedy 1).

## 2. Co pokazujemy przy każdym koncie

Kod publiczny, imię i nazwisko, e-mail, szkoła, data założenia, ostatnie logowanie (`last_login`),
stan (aktywne / nieaktywowane / zablokowane – ta sama trójka, co na liście kont), wpisy do etapów
**z rozróżnieniem trening / etap zawodów** (nazwy etapów), liczba prac (zadania z czymkolwiek
oddanym, wszystkie edycje tego konkursu), zgody (RODO, zgoda opiekuna), bieżące zaświadczenie
o statusie ucznia (stan), znacznik „inne role” (patrz § 3.3).

## 3. Sugestia

1. **„Używane”** konto = logowało się (`last_login`) **albo** ma wpis do etapu zawodów (nie treningu)
   **albo** ma prace.
2. **„Do zachowania”** – jeśli w grupie jest **dokładnie jedno** konto używane, to ono.
3. **„Kandydat do usunięcia”** – wyłącznie w grupie z **co najmniej jednym** kontem używanym
   i wyłącznie konto, które: nigdy się nie logowało, nie ma wpisu do etapu poza treningiem, nie ma
   prac, nie ma zaświadczenia, nie jest kontem chronionym (koordynator, superużytkownik) i **nie ma
   innych ról** (profil uczestnika w innym konkursie, członkostwo w innym konkursie, profil komitetu,
   opiekuna szkolnego, opiekun drużyny). Inne role wykluczają, bo usunięcie konta jest platformowe –
   kaskada zabrałaby profil sąsiedniej olimpiady albo rolę w komitecie. Przy dwóch kontach używanych
   pusta kopia obok nich nadal jest kandydatem: jej usunięcie niczego nie zabiera, niezależnie od
   tego, które z używanych kont zostanie.
4. **„Do decyzji”** – wszystko inne: grupa bez konta używanego (np. dwa nieaktywowane konta – i tak
   zniknie je kosiarka kont nieaktywowanych), każde z kilku kont używanych (które jest prawdziwe, wie
   koordynator), konto z innymi rolami. Przy koncie „do decyzji” ekran podaje powód.

Konto-kandydat z wpisem do **treningu** zostanie przy usunięciu zanonimizowane, a nie skasowane
(wpis to ślad w zawodach w rozumieniu `competition_footprint`) – mówi o tym ekran potwierdzenia.

## 4. Ekrany

1. **`/coordinator/accounts/duplicates/`** (`web:coordinator-duplicates`, menu „Uczestnicy i konta” →
   „Zdublowane konta”, tuż po „Wszystkie konta”). U góry trzy liczniki: osoby z duplikatami, konta
   w grupach, kandydaci do usunięcia. Każda grupa to tabela kont z odznaką sugestii i powodem.
   Przy kandydacie przycisk **„Usuń”** – prowadzi do istniejącego ekranu potwierdzenia
   `/coordinator/accounts/<id>/delete/?back=duplicates` (`CoordinatorAccountDeleteView`); po
   usunięciu (POST + CSRF tam) wraca na listę duplikatów. Przy każdym koncie „Edytuj” i „Karta”.
2. **Zbiorcze usunięcie kandydatów** – `POST /coordinator/accounts/duplicates/delete/` z listą
   identyfikatorów kont (`account`, ukryte pola formularza, bez JS):
   - pierwszy POST (bez `confirm`) pokazuje **ekran potwierdzenia z dokładną listą** kont, każde
     z polem wyboru (domyślnie zaznaczonym) i ze stanem przeliczonym w tej chwili; konto, które
     przestało być kandydatem, jest pokazane jako „zostanie pominięte”,
   - drugi POST (`confirm=1`) – serwis `delete_candidates` przelicza grupy **w chwili usuwania**
     i dla każdego konta osobno: blokuje wiersz konta (`select_for_update`), sprawdza ponownie
     warunki kandydata i dopiero wtedy woła `delete_account_by_coordinator`. Konto, które w międzyczasie
     się zalogowało (albo w którego grupie nie zostało żadne konto używane), jest pomijane. Komunikat:
     „Usunięto N kont, pominięto M (przestały spełniać warunki kandydata)”.
   - identyfikator konta spoza profili uczestnika tego konkursu → **404** (ani jedno konto nie jest
     usuwane); więcej niż 500 identyfikatorów → 400.
3. **Eksport CSV** – `GET /coordinator/accounts/duplicates/export.csv` (`apps.core.exports.csv_response`,
   średnik, BOM, ochrona przed formułami): wiersz na konto z numerem grupy i sugestią. Audyt
   `account.duplicates_exported` z liczbą grup i wierszy (bez danych osobowych).
4. **Słabszy sygnał – podobne adresy e-mail** (osobna sekcja pod grupami, bez przycisków usuwania):
   konta tego konkursu, których adresy po poprawieniu typowych literówek domeny (`gmail.con` →
   `gmail.com`, `gmial.com`, `gmai.com`, `wp.p`, `o2.p`, `onet.p`, `interia.p`, `.con`/`.cmo`/`.ocm`
   → `.com`, …) są identyczne, a w oryginale różne. Pokazujemy wyłącznie te pary, których grupy
   imienno-szkolne nie pokryły (inaczej to samo konto stałoby na ekranie dwa razy). Koordynator
   decyduje z ekranu edycji konta.

Wszystkie ekrany: wyłącznie koordynator tego konkursu (`CoordinatorRequiredMixin`), bez inline JS
(CSP), napisy po polsku jak na sąsiednich ekranach panelu (panel koordynatora nie jest tłumaczony).
Konta koordynatora i własnego konta nie da się usunąć – pilnuje serwis (`COORDINATOR_PROTECTED`,
`SELF_DELETE`), a ekran i tak nie oznacza ich jako kandydatów.

## 5. RODO

Ekran nie przetwarza **nowych** danych osobowych ani nie zbiera niczego od uczestnika: pokazuje
dane, które koordynator już widzi na liście kont, liście uczestników i karcie uczestnika (imię,
nazwisko, e-mail, szkoła, kod, daty, stan konta, etapy, prace, zgody, stan zaświadczenia), tylko
zestawione obok siebie. Cel mieści się w istniejącej czynności „obsługa kont uczestników”
(poprawność danych, art. 5 ust. 1 lit. d RODO – usunięcie zbędnych kopii danych tej samej osoby),
więc rejestr czynności przetwarzania nie wymaga nowej pozycji. Eksport CSV jest wynoszeniem danych
poza system i ma wpis w audycie (kto, kiedy, ile wierszy). Usunięcie konta – jak dotąd: audyt
`account.deleted_by_coordinator`, skutek zależny od śladu w zawodach.

## 6. Testy

`apps/accounts/tests/test_duplicates.py` (serwis): normalizacja (wielkość liter, spacje, kropki,
cudzysłowy, NFKC), szkoła przez `school_ref` / placówkę / tekst, rozłączność rodzajów klucza,
konkursy się nie mieszają, zanonimizowani pominięci, puste nazwisko pominięte; sugestie – jeden
zalogowany, żaden, dwóch, konto z innymi rolami, konto z etapem zawodów, trening nie przeszkadza;
podobne adresy e-mail; stała liczba zapytań.
`apps/web/tests/test_coordinator_duplicates.py` (ekran): uprawnienia (uczestnik 403, cudzy konkurs
404 przy zbiorczym usuwaniu), liczniki i przyciski, powrót z ekranu usuwania, zbiorcze usunięcie
z pominięciem konta, które zdążyło się zalogować, audyt, eksport CSV z audytem, budżet zapytań
ekranu niezależny od liczby grup, pozycja w menu.

## 7. Propozycje na później (poza zakresem)

- scalanie kont (przeniesienie wpisów/prac/zgód na konto zachowywane) – dziś koordynator usuwa
  kopię bez śladu i, jeśli trzeba, poprawia adres e-mail konta zachowywanego,
- dopasowanie szkoły „z wykazu” do tej samej szkoły wpisanej ręcznie (po nazwie z wykazu),
- ostrzeżenie już przy rejestracji („konto o tym imieniu, nazwisku i szkole istnieje – czy to Ty?”)
  – wymaga ostrożności, bo zdradzałoby istnienie cudzego konta.
