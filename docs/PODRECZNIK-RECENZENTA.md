# Podręcznik recenzenta

Krótka instrukcja dla członka komitetu, który ocenia prace. Organizator może ją dołączyć do zaproszenia.

Ocenianie jest **ślepe**: nie wiesz, czyją pracę czytasz (widzisz kod uczestnika), a do chwili
wystawienia kompletu ocen nie widzisz punktów drugiego recenzenta. Panel działa **bez JavaScriptu** —
skrypty dokładają wygodę, nigdy treść.

---

## 1. Konto

Konto komitetu powstaje **wyłącznie na kod zaproszenia** od koordynatora — nie ma otwartej rejestracji
dla recenzentów.

1. Otwórz `/register/committee/`, wpisz kod z listu (albo przekazany inną drogą) i załóż konto.
2. Aktywuj adres, klikając link z listu. **Link jest ważny 4 godziny** i tyle samo żyje nieaktywowane
   konto; ponowną wysyłkę zamawia się pod `/activate/resend/`.
3. Poczekaj na **zatwierdzenie przez koordynatora** — do tego czasu panel recenzenta jest zamknięty.

Kod zaproszenia z listu jest **jednorazowy** i ma termin ważności. Jeśli wygasł, poproś koordynatora
o ponowną wysyłkę — dostaniesz **nowy** kod, bo starego nie da się odtworzyć.

| Co | Gdzie |
|---|---|
| Logowanie | `/login/` |
| Nie pamiętasz hasła | `/password-reset/` (link jednorazowy, ważny 24 h) |
| Imię i nazwisko | `/account/profile/` |
| Zmiana adresu e-mail | `/account/email/` (potwierdzenie na **dotychczasowy** adres) |
| Język interfejsu i wysoki kontrast | dwie ikony w pasku konta |

**Województwa nie zmieniasz sam** — ustala je koordynator, bo to na nim stoi reguła konfliktu interesów:
na etapie wojewódzkim nie dostaniesz pracy uczestnika ze swojego województwa. Członek bez wpisanego
województwa może oceniać prace ze wszystkich.

---

## 2. Kolejka — `/review/`

Ekran **„Moje przydziały”** odpowiada na pytanie „co mam dziś zrobić”. Nad listą pasek podsumowania
z trzema liczbami — **ile zostało**, **najbliższy termin**, **łączny czas pracy** — oraz przycisk
**„Pobierz moje prace (ZIP)”**.

Cztery zakładki po stanie recenzji:

| Zakładka | Co w niej jest |
|---|---|
| **Do zrobienia** | przydziały, których jeszcze nie otwierałeś |
| **W toku** | zapisane szkice |
| **Wystawione** | oceny oddane (da się je jeszcze poprawić — § 5) |
| **Anulowane** | prace, które wyszły z Twojej kolejki; najważniejszą kolumną jest **powód** |

Wewnątrz zakładki prace są pogrupowane **po etapie i zadaniu** z licznikiem „6 z 12 do zrobienia” — bo
tak wygląda robota: jedno zadanie w wielu pracach. Wiersze idą **po terminie**, a nie po chwili
przydziału: zaczyna się od tego, co przepadnie najwcześniej. Wiersz niesie kod pracy, odznakę terminu
(„po terminie”, „dziś”, „za 3 dni”), postęp („nie zaczęta”, „szkic z punktami”, „otwarta …”), marker
zgłoszonego problemu i przycisk **„Otwórz”**.

Zwinięte **„Jak oceniać — w pięciu zdaniach”** jest pierwszą pomocą, jeśli oceniasz po raz pierwszy.

**Paczka ZIP** zawiera wszystkie prace z Twoimi nieanulowanymi recenzjami. Nazwy plików są **anonimowe**
(`<kod>_zad<numer>_v<wersja>`), a w archiwum jest `README.txt` z wierszami `recenzja <id> → <plik>`,
po których odnajdziesz pracę w panelu.

### Powody w zakładce „Anulowane”

| Powód | Co znaczy |
|---|---|
| „Uczestnik wysłał nową wersję rozwiązania…” | praca została zastąpiona; do oceny wejdzie nowa wersja, być może znowu u Ciebie |
| „Koordynator odebrał Ci tę pracę” | decyzja organizatora (np. konflikt interesów, zaległość, zgłoszony problem) |

To są **dwie różne rzeczy** i dlatego stoją osobno. W obu przypadkach Twoje punkty i komentarze zostają
w historii, ale przestają się liczyć.

---

## 3. Ekran oceny — `/review/<id>/`

Dwie kolumny: po lewej **rozwiązanie**, po prawej **panel oceny**. Powyżej 1000 px panel jest
**przyklejony** — przy ośmiostronicowym PDF-ie formularz nie ucieka tam, gdzie już nie patrzysz. Na
węższym ekranie kolumny układają się jedna pod drugą, a do panelu prowadzi przyklejony u dołu
odnośnik **„Oceń”**.

Kolejność w panelu odpowiada kolejności czynności:

1. **nagłówek** — kod pracy, etap, runda, wersja, termin i licznik „5 z 18 w tym zadaniu”
   z odnośnikami **„← Poprzednia praca”** / **„Następna praca →”**,
2. zwinięte **„Rozwiązanie wzorcowe i uwagi dla recenzentów”** (gdy koordynator je wgrał),
3. **„Rubryka oceniania”** albo lista punktów ze skali etapu,
4. **komentarz dla uczestnika** — z szablonami tuż pod polem,
5. **komentarz wewnętrzny** — dla komitetu; uczestnik **nigdy** go nie zobaczy,
6. **„Zapisz szkic”** i **„Wystaw ocenę”**.

Wszystko, co nie jest wystawianiem oceny — porównanie ocen, zgłoszenie problemu, własne szablony, czas
pracy, skróty — stoi **pod** formularzem, zwinięte.

**Adnotacje.** Nad podglądem jest pasek: **„Dodaj zaznaczenie”** (da się wyłączyć, gdy chcesz tylko
czytać), **„Ukryj adnotacje”** i filtr **publiczne / wewnętrzne**. Lista adnotacji pod podglądem ma przy
każdej „przejdź” (przewija podgląd na jej stronę) i „usuń”. **Publiczne** adnotacje trafiają do
informacji zwrotnej uczestnika po ogłoszeniu wyników, **wewnętrzne** zostają w komitecie.

**Skróty klawiaturowe** (opisane w zwijaczu „Skróty klawiaturowe”): <kbd>n</kbd> / <kbd>p</kbd> —
następna i poprzednia praca w serii, <kbd>s</kbd> — zapis szkicu. Skróty milczą, gdy piszesz w polu
tekstowym.

**Czas pracy.** Przy recenzji stoi „Czas pracy: 1 h 12 min”. Licznik mierzy **wyłącznie** czas, i to po
to, żeby organizator umiał zaplanować obciążenie komitetu („ile godzin zajmuje ocena zadania 3”).
Nie zapisujemy tego, co piszesz, ani gdzie klikasz; po pięciu minutach bez ruchu licznik przestaje liczyć.

---

## 4. Skala i rubryka

**Bez rubryki** wybierasz jedną wartość ze **skali etapu** (albo z własnej skali zadania) — każda ma
opis, np. `2 — istotny postęp, rozwiązanie niepełne`.

**Z rubryką** dostajesz po jednym polu punktów i komentarzu **na kryterium**, a sumę liczy serwer i to
ona jest oceną. Panel pokazuje podgląd sumy i mówi, czy mieści się w skali.

> **Suma musi należeć do skali.** Cztery punkty przy skali 0/2/5/6 kończą się odmową z listą
> dopuszczalnych wartości. **System nie zaokrągla** — to byłaby zmiana Twojej decyzji, a nie pomoc.

Szkic przyjmuje rubrykę niekompletną; **wystawienie oceny** — nie.

---

## 5. Wystawienie i poprawienie oceny

**„Zapisz szkic”** zapisuje punkty i komentarze bez oddawania oceny. **„Wystaw ocenę”** oddaje ją do
rozstrzygnięcia rundy: dwie zgodne oceny dają ocenę uzgodnioną, rozjazd kieruje pracę do **moderacji**.

**Wystawiona ocena nie jest nieodwracalna.** Na ekranie recenzji widzisz wtedy formularz wypełniony
swoimi punktami i przycisk **„Popraw ocenę”**. Chwila pierwszego wystawienia zostaje zapisana (to fakt
procesowy), a poprawka dopisuje datę zmiany. Po poprawce runda jest rozstrzygana **od nowa**; jeśli
poprawka **kończy** rozjazd, wiszący przydział trzeciego recenzenta jest anulowany, żeby nie wisiał
w cudzej kolejce.

Poprawić **nie wolno** — ekran pokazuje wtedy powód zamiast formularza:

| Sytuacja |
|---|
| koordynator odebrał Ci tę pracę |
| ocena nie została jeszcze wystawiona (idzie zwykła ścieżka) |
| wyniki etapu są już ogłoszone |
| praca jest w reklamacji albo finalna |
| ocenę rozstrzygnął człowiek: moderacja, trzeci recenzent, korekta koordynatora, decyzja reklamacyjna |

---

## 6. Szablony komentarzy

Ocena trzydziestu prac z jednego zadania to w praktyce trzydzieści razy te same trzy zdania. Szablon jest
gotowym akapitem **do wstawienia i poprawienia** — system nigdy nie wstawia go sam i nigdy nie nadpisuje
tego, co już napisałeś.

- **wspólne szablony komitetu** przypisuje do zadania koordynator; stoją **przed** Twoimi,
- **własne szablony** zakładasz na ekranie oceny, w sekcji **„Moje szablony komentarzy”** pod
  formularzem — z zaznaczeniem „tylko dla tego zadania” albo bez (szablon ogólny). Limit: 50 pozycji,
- **cudzego prywatnego szablonu nie widzi nikt** — ani inny recenzent, ani koordynator,
- zwinięte **„Szablony komentarzy (N)”** tuż pod polem „Komentarz dla uczestnika” jest podpowiedzią
  **do tego pola**: tytuł, treść i przycisk **„Wstaw”**, który dopisuje treść **na końcu** pola.

Obie sekcje działają bez JavaScriptu — treść każdego szablonu stoi na ekranie do skopiowania.

---

## 7. Porównanie ocen i notatki

Dopóki komplet ocen rundy 1 nie jest wystawiony, **nie widzisz cudzych punktów**. Gdy wszystkie są
wystawione (albo praca jest w moderacji lub oceniona), na ekranie pojawia się **„Porównanie ocen”**:
punkty drugiej strony, jej komentarz dla uczestnika i różnica ze znakiem. **Tożsamość zostaje ukryta** —
„Recenzent A/B”. Osobna strona z tym samym materiałem: `/review/<id>/compare/`.

Pod spodem jest **„Notatki recenzentów”** — wątek krótkich wypowiedzi (do 1000 znaków). Piszą w nim
recenzenci tej pracy, czytają oni **i koordynator** (ekran moderacji). To jest miejsce na zdanie „zgadzam
się, przeoczyłem drugi przypadek” przed posiedzeniem komisji. Wątek zamyka się razem ze sprawą: praca
finalna albo etap z ogłoszonymi wynikami nie przyjmuje już notatek.

---

## 8. Terminy i przypomnienia

Przy przydziale recenzja dostaje **własny termin** (chwila przydziału plus liczba dni ustawiona przy
etapie, przycięta do terminu recenzji etapu). Termin jest **zapisany**, więc późniejsza zmiana ustawień
etapu go nie przesuwa; po przydziale widzisz go w komunikacie i w wierszu kolejki.

- kolejka sortuje prace **po terminie** i odznacza „po terminie”, „dziś”, „za N dni”,
- raz na dobę idzie **jeden list** z pracami po terminie i z terminem w ciągu 2 dni — kody prac, bez
  danych osobowych,
- koordynator może przypomnieć osobno. **Taki list nie zawiera ani kodów prac, ani tytułów zadań** —
  komplet widzisz po zalogowaniu; lista przydziałów w skrzynce byłaby wyciekiem tego, co ocenianie ślepe
  ma chronić,
- jeśli nie zdążysz, napisz do koordynatora **zanim** minie termin: praca da się przekazać komuś innemu
  bez utraty Twoich notatek.

---

## 9. Zgłoszenie problemu z pracą

Pod formularzem oceny stoi **„Zgłoś problem”**. Używaj go, gdy praca nie nadaje się do oceny **taką, jaką
jest**:

- plik się nie otwiera albo jest nieczytelny (rozmyte zdjęcie, obrócone strony),
- to nie jest rozwiązanie tego zadania,
- podejrzenie niesamodzielności albo dane osobowe wpisane w treść pracy,
- konflikt interesów po Twojej stronie (rozpoznajesz autora).

Zgłoszenie trafia do kolejki koordynatora (**„Zgłoszone problemy z pracami”**), a przy wierszu w Twojej
kolejce pojawia się marker. Koordynator rozstrzyga sprawę albo odbiera Ci pracę.

**Nie jest to kanał na pytania o skalę i interpretację zadania** — te idą do koordynatora zwykłą drogą
(`/support/new/` albo adres z zaproszenia).

---

## 10. Zasady, o których warto pamiętać

- **uczestnik czyta Twój komentarz.** Po ogłoszeniu wyników trafia do niego „Komentarz dla uczestnika”
  i adnotacje oznaczone jako publiczne, podpisane „Recenzent A/B”. Komentarz wewnętrzny nie opuszcza
  komitetu w żadnej postaci,
- **nigdy nie pisz w komentarzu danych osobowych** — ani swoich, ani uczestnika,
- **nie pobieraj prac poza panel** więcej, niż potrzebujesz. Paczka ZIP jest anonimowa i taka ma zostać,
- **nie ma potrzeby zgadywać, kto to napisał.** Jeśli rozpoznajesz autora — zgłoś to (§ 9), zamiast
  oceniać z tą wiedzą,
- **skala to nie procenty.** Każda wartość ma opis; oceniaj opisem, nie przeliczeniem,
- **coś nie działa?** Sprawdź `/status/`, potem `/faq/`, potem `/support/new/`. Jeśli dostaniesz
  **403 „nie udało się zweryfikować formularza”**, najczęściej w innej karcie zalogowano się lub
  wylogowano — kliknij „odśwież” na stronie błędu i wyślij ocenę ponownie (szkic zapisuj często).
