# ACC-DUP-02: Usuwanie w wierszu i nieaktywne konta uczestników

## 0. Cel i granice

Prośba organizatora z 10.10.2026: „w sekcji zdublowane konta pozwól na usuwanie pojedynczych kont
od razu w widoku głównym; dodaj jeszcze sekcję nieaktywne konta – te konta, na które nikt się nie
logował, i tu pozwól wybrać liczbę dni lub nigdy”.

Zadanie rozszerza ACC-DUP-01 (`docs/tasks/ACC-DUP-01.md`) w tych samych miejscach: serwis
w `apps/accounts`, widoki i szablony w `apps/web`, bez nowej aplikacji i bez migracji.

Czego zadanie **nie** robi:
- nie dodaje drugiej ścieżki kasowania – każde usunięcie (pojedyncze i zbiorcze, z obu ekranów)
  idzie przez `apps.accounts.profile.delete_account_by_coordinator` (audyt
  `account.deleted_by_coordinator`, „ślad w zawodach → anonimizacja, brak śladu → usunięcie”),
- nie usuwa niczego samo ani według harmonogramu – ekran „Nieaktywne konta” **nie jest polityką
  retencji** (§ 5),
- nie rusza kosiarki kont nieaktywowanych (`apps.accounts.tasks`, doba na aktywację) – ta działa
  dalej jak dotąd.

## 1. Wspólny mechanizm usuwania (`apps/accounts/account_cleanup.py`)

ACC-DUP-01 miał `delete_candidates` i `BulkResult` wewnątrz `duplicates.py`. Teraz usuwają dwa
ekrany, więc mechanizm wychodzi do osobnego modułu i jest **uogólniony, a nie skopiowany**:

1. `AccountFacts` + `collect_facts(competition, participant_ids)` – dane konta do tabeli (etapy
   z rozróżnieniem trening / zawody, prace, zaświadczenie, znaczniki „chronione” i „inne role” z listą
   ról). Stała liczba zapytań (4), niezależna od liczby kont. `DuplicateAccount` dziedziczy po
   `AccountFacts` i dokłada sugestię.
2. `delete_accounts(user_ids, *, eligible, actor, request)` – pętla po kontach: każde osobno
   w transakcji, wiersz konta pod `select_for_update`, **funkcja `eligible(user)` wołana na
   zablokowanym wierszu** (warunki ekranu w chwili usuwania), dopiero potem
   `delete_account_by_coordinator`. Odmowa serwisu (`DomainError` – koordynator, własne konto) = konto
   pominięte z powodem. Wynik `BulkResult(deleted, anonymised, skipped, reasons)`.
3. `login_stamp(user)` – znacznik ostatniego logowania (`""` = nigdy, inaczej ISO 8601 w UTC).
   Formularz w wierszu niesie znacznik, który koordynator **widział**; serwer porównuje go ze stanem
   zablokowanego wiersza. Inny znacznik = ktoś zalogował się na to konto po wyświetleniu ekranu →
   konto pominięte, komunikat „odśwież i zdecyduj jeszcze raz”.
4. `MAX_BULK_IDS = 500` – wspólny limit listy identyfikatorów w POST (więcej → 400).

`delete_candidates` (zbiorcze w duplikatach) jest teraz cienką nakładką na `delete_accounts`
z warunkiem „nadal kandydat i nigdy się nie logowało” – zachowanie bez zmian.

## 2. Usuwanie w wierszu na ekranie „Zdublowane konta”

1. **Przy każdym koncie, którego serwis nie odrzuci** (czyli poza kontem chronionym: koordynator,
   superużytkownik), stoi „Usuń” – także przy „do zachowania” i „do decyzji”. Ekran nie zgaduje za
   koordynatora, ale **mówi wprost**, co zabiera: przy koncie używanym („logowało się”, etap zawodów,
   prace) i przy koncie z innymi rolami potwierdzenie ma ostrzeżenie, a przy jedynym używanym koncie
   w grupie – ostrzeżenie najmocniejsze („to jedyne używane konto tej osoby”). Konto z wpisem do
   etapu/treningu zostanie zanonimizowane – i to też stoi przy przycisku.
2. **Potwierdzenie bez skryptu (CSP):** `<details><summary>Usuń</summary>` rozwija w komórce mały
   formularz POST + CSRF z ostrzeżeniami i przyciskiem **„Tak, usuń konto <kod>”**. Wartość
   potwierdzenia niesie **sam ten przycisk** (`name="confirm" value="<id konta>"`), więc żądanie bez
   kliknięcia go (np. formularz złożony ręcznie, inny przycisk) nie ma potwierdzenia. Serwer
   odrzuca POST bez `confirm` równego identyfikatorowi konta (komunikat, nic nie usunięte).
   Wybrałem `<details>` zamiast pola wyboru „potwierdzam”, bo: dwa kliknięcia w tym samym miejscu,
   brak stanu formularza do pamiętania i ostrzeżenia widoczne dokładnie wtedy, gdy są potrzebne.
3. **Adres:** `POST /coordinator/accounts/duplicates/<id>/delete/`
   (`web:coordinator-duplicates-delete-one`). Konto, które nie jest profilem uczestnika (bez
   anonimizacji) **tego** konkursu → 404.
4. **Warunki w chwili usuwania:** konto nadal należy do grupy duplikatów (grupy przeliczone teraz –
   bez tego drugie „Usuń” mogłoby zabrać osobie ostatnie konto, gdy kopię usunął w międzyczasie ktoś
   inny) **i** znacznik logowania się nie zmienił. Inaczej – pominięte z komunikatem.
5. **Powrót:** na ten sam ekran, do kotwicy grupy (`#grupa-<n>`; `n` z formularza, walidowane jako
   liczba), z komunikatem o wyniku. Kotwica jest liczbą, nie adresem – brak otwartego przekierowania.

Odnośnik do osobnego ekranu potwierdzenia (`?back=duplicates`) zostaje działający (zakładki,
dokumentacja), ale lista z niego nie korzysta.

## 3. Ekran „Nieaktywne konta” (`/coordinator/accounts/inactive/`)

Menu „Uczestnicy i konta” → **„Nieaktywne konta”**, tuż po „Zdublowane konta”. Osobny ekran, a nie
zakładka duplikatów: inne pytanie („kto się nie loguje”), własne filtry, stronicowanie i zbiorcze
zaznaczanie – zakładka zmieszałaby dwa formularze zbiorcze na jednej stronie.

### 3.1 Zakres

Profile uczestnika (`Participant`) **tego** konkursu, bez zanonimizowanych, bez kont chronionych
(koordynator, superużytkownik – serwis i tak by ich nie usunął). Konto uczestnika, które ma też
**inne role** (komitet, opiekun szkolny, opiekun drużyny, profil albo członkostwo w innym konkursie),
jest pokazane z odznaką ról: da się je usunąć wyłącznie **pojedynczo** (z ostrzeżeniem, co zabierze
kaskada platformowego konta), a usunięcie zbiorcze je **pomija** – sprzątanie hurtem nie może zabrać
roli w komitecie ani profilu sąsiedniej olimpiady.

### 3.2 Filtry (GET, cały stan w adresie)

| Parametr | Znaczenie | Domyślnie |
|---|---|---|
| `login=never` | `last_login IS NULL` – nikt nigdy się nie zalogował | **tak** |
| `login=days&days=N` | ostatnie logowanie **co najmniej N dni temu** (`last_login <= teraz − N dni`) **albo nigdy**; N 1–3650, podpowiedzi 7/14/30/60/90/180/365 (`<datalist>`) | – |
| `joined=N` | konto założone **co najmniej N dni temu** (`date_joined <= teraz − N dni`), 0 = bez warunku, 0–3650 | **7** |
| `activation=` / `verified` / `unverified` | wszystkie / z potwierdzonym adresem (`email_verified_at` ustawione) / bez potwierdzenia | wszystkie |

Granice są domknięte: konto zalogowane dokładnie N dni temu **jest** na liście „od N dni”, konto
założone dokładnie N dni temu – też. Domyślne `joined=7` chroni konta z wczoraj, którym list
aktywacyjny albo zaproszenie z importu (link żyje 14 dni) dopiero idzie. Nieprawidłowa wartość
→ komunikat przy formularzu i **pusta lista** (nie lista z domyślnymi filtrami, którą łatwo wziąć
za wynik własnego filtra).

### 3.3 Tabela

Liczniki u góry (jedno zapytanie agregujące): kont spełnia filtr, w tym nigdy się nie logowało,
bez potwierdzonego adresu, ze śladem w zawodach (etap poza treningiem albo prace).

Kolumny: pole wyboru (zbiorcze), kod, imię i nazwisko, e-mail (+ odznaka „z importu”), szkoła,
założone, ostatnie logowanie („nigdy”), stan (aktywne / nieaktywowane / zablokowane – ta sama
trójka, co na liście kont), etapy (trening osobno), prace, zaświadczenie, opiekun szkolny
(`supervisor_email` wskazany – tak/—, bez pokazywania adresu), inne role; operacje: „Usuń” w wierszu
(jak w § 2), „Edytuj”, „Karta”. Wiersz z etapem zawodów albo pracami ma ostrzeżenie „ślad
w zawodach – konto zostanie zanonimizowane”.

Stronicowanie po 100 wierszy (zwykłe odnośniki, filtry przeżywają przejście między stronami).
Zapytania stałe względem liczby wierszy: agregat + licznik stron + identyfikatory strony +
`collect_facts` (4).

### 3.4 Usuwanie

- **Pojedynczo w wierszu:** `POST /coordinator/accounts/inactive/<id>/delete/`
  (`web:coordinator-inactive-delete-one`) – ten sam `<details>` i ten sam przycisk-potwierdzenie,
  co w § 2. Formularz niesie filtry (ukryte pola); serwer w chwili usuwania sprawdza na
  zablokowanym wierszu, że konto **nadal spełnia filtr** (to samo zapytanie, co lista, zawężone
  do konta) i że znacznik logowania się nie zmienił. Powrót na listę z tymi samymi filtrami i stroną.
- **Zbiorczo:** pola wyboru w wierszach należą do formularza pod tabelą (atrybut `form=` – bez
  zagnieżdżania formularzy, bez JS). „Zaznacz wszystkie na stronie” to zwykły odnośnik
  (`&zaznacz=1`), który wyświetla tę samą stronę z zaznaczonymi polami; „Odznacz” – bez parametru.
  `POST /coordinator/accounts/inactive/delete/` (`web:coordinator-inactive-delete`):
  - pierwszy POST (bez `confirm`) → **ekran potwierdzenia z dokładną listą** kont (stan przeliczony
    teraz, pole wyboru przy każdym, domyślnie zaznaczone) i osobną listą „zostaną pominięte” z powodem
    (nie spełnia już filtra / inne role),
  - drugi POST (`confirm=1`) → `delete_inactive`: dla każdego konta osobno blokada wiersza, ponowne
    sprawdzenie filtra (**z chwili kliknięcia**, nie z chwili wyświetlenia listy), brak innych ról,
    potem `delete_account_by_coordinator`. Komunikat: ile usunięto (w tym zanonimizowanych), ile
    pominięto i dlaczego,
  - identyfikator spoza profili uczestnika tego konkursu → 404 (nic nie usunięte), więcej niż 500 → 400,
    nieprawidłowe filtry → 400.

### 3.5 Eksport CSV

`GET /coordinator/accounts/inactive/export.csv?<filtry>` – wszystkie wiersze spełniające filtr
(nie tylko strona), `apps.core.exports.csv_response`. Audyt `account.inactive_exported` z liczbą
wierszy i wartościami filtrów (bez danych osobowych).

## 4. Uprawnienia i bezpieczeństwo

- Wszystkie ekrany i akcje: wyłącznie koordynator tego konkursu (`CoordinatorRequiredMixin`);
  konto spoza konkursu → 404 (kod odpowiedzi nie potwierdza istnienia cudzego konta).
- Wszystkie usunięcia: POST + CSRF, potwierdzenie (§ 2.2 / ekran zbiorczy), warunki przeliczane
  pod `select_for_update`, audyt przez `delete_account_by_coordinator`.
- Bez inline JS (CSP): `<details>`, atrybut `form=`, odnośnik „zaznacz wszystkie”.
- Powroty: zamknięte formy (kotwica z liczby, filtry przepuszczone przez walidację i złożone na nowo
  `urlencode`) – nigdy adres z formularza.

## 5. RODO

Ekran pokazuje dane, które koordynator już widzi na liście kont i karcie uczestnika; eksport ma wpis
w audycie. Cel – usunięcie kont, z których nikt nie korzysta (minimalizacja danych, art. 5 ust. 1
lit. c i e RODO) – mieści się w istniejącej czynności „obsługa kont uczestników”; rejestr czynności
nie wymaga nowej pozycji.

**Usunięcie konta nieaktywnego jest decyzją koordynatora, podejmowaną ręcznie, i nie zastępuje
polityki retencji.** Okresy przechowywania danych uczestników (po zakończeniu edycji, po
publikacji wyników) określa polityka retencji organizatora i dokumentacja RODO; ten ekran jest
narzędziem do jej wykonania, gdy organizator tak zdecyduje, a nie automatem. Konto ze śladem
w zawodach jest anonimizowane (pseudonimowy wiersz w wynikach zostaje), więc ekran nie służy też do
„czyszczenia” wyników.

## 6. Testy

`apps/accounts/tests/test_inactive.py` (serwis): filtr „nigdy” vs „od N dni” z granicami (dokładnie
N dni = na liście, N dni minus sekunda = nie), filtr wieku konta (granica), aktywacja, wykluczenie
zanonimizowanych, koordynatorów i innego konkursu, inne role widoczne; parsowanie filtrów (domyślne,
błędne wartości, zakres 1–3650); `delete_inactive` – pominięcie konta, które w międzyczasie się
zalogowało, pominięcie konta z innymi rolami w zbiorczym, pojedyncze z innymi rolami dozwolone,
znacznik logowania; stała liczba zapytań.
`apps/web/tests/test_coordinator_inactive.py` (ekran): uprawnienia (uczestnik 403, cudzy konkurs
404), liczniki, filtry w adresie, „zaznacz wszystkie”, usunięcie w wierszu z potwierdzeniem i bez
(odrzucone), powrót z filtrami, zbiorcze dwa kroki z pominięciem konta, które się zalogowało, eksport
z audytem, budżet zapytań niezależny od liczby wierszy, pozycja w menu.
`apps/web/tests/test_coordinator_duplicates.py` (dopisane): „Usuń” w wierszu przy każdym koncie
poza chronionym, usunięcie z potwierdzeniem (audyt, powrót do kotwicy), bez potwierdzenia – odrzucone,
konto z innego konkursu – 404, konto, które zalogowało się po wyświetleniu – pominięte, ostatnie konto
osoby (grupa się rozpadła) – pominięte.
