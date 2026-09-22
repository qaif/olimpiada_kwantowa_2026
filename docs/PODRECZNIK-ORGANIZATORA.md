# Podręcznik organizatora (koordynatora)

Dla osoby, która **prowadzi zawody**: przygotowuje edycję, zaprasza komitet, pilnuje terminów,
rozstrzyga spory, ogłasza wyniki i wystawia dokumenty. Podręcznik jest **przedmiotowo neutralny** —
opisuje mechanizm, a nie konkretną dziedzinę; wszystko, co dotyczy treści zawodów (regulamin, zadania,
skala, progi), jest ustawieniem w panelu, a nie założeniem w kodzie.

Instalacja i utrzymanie serwera: [`PODRECZNIK-ADMINISTRATORA.md`](PODRECZNIK-ADMINISTRATORA.md).
Krótkie instrukcje do rozesłania: [`PODRECZNIK-UCZESTNIKA.md`](PODRECZNIK-UCZESTNIKA.md),
[`PODRECZNIK-RECENZENTA.md`](PODRECZNIK-RECENZENTA.md). Szczegóły techniczne każdego ekranu —
[`../README.md`](../README.md) § 5–6.

---

## 1. Panel koordynatora

Wejście: **`/coordinator/`** (nagłówek „Prowadzenie edycji <rocznik>”, sekcja **„Co wymaga uwagi”**).
Każdy ekran panelu stoi w tej samej ramie: menu boczne po lewej, nagłówek z jednym zdaniem „po co jest
ta strona” i akcją główną, treść pod spodem. Poniżej 900 px menu zwija się do rozwijanego
**„Menu panelu”** na górze strony. Cały panel działa **bez JavaScriptu**.

Na górze menu stoi **wyszukiwarka** (`/coordinator/search/?q=`) — jedno pole na pięć rodzajów obiektów:
uczestnicy (kod publiczny, nazwisko, imię, e-mail, szkoła), członkowie komisji, zadania, etapy bieżącej
edycji i zgłoszenia po numerze. Fraza krótsza niż dwa znaki nie szuka niczego.

| Sekcja menu | Pozycje |
|---|---|
| **Pulpit** | Co wymaga uwagi |
| **Etapy** | jeden wpis na etap, a pod nim: *Zadania* (albo *Rozmowy*), *Przydziały i oceny*, *Postęp*, *Wyniki* |
| **Ocenianie** | Moderacja, Zgłoszone problemy, Kalibracja recenzentów, Podobieństwo rozwiązań |
| **Uczestnicy i konta** | Uczestnicy, Wszystkie konta, Opiekunowie szkolni, Aktywacje |
| **Komitet** | Członkowie, Zatwierdzenia, Zaproszenia, Województwa |
| **Komunikacja** | Komunikaty, Zgłoszenia, Ogłoszenia |
| **Raporty** | Eksport danych, Audyt, Symulacja kwalifikacji, Dyplomy, Retencja danych, Rejestr czynności |
| **Ustawienia** | Rejestracja uczestników, Wydarzenia linii czasu, Skala punktacji |

Przy czterech pozycjach (Moderacja, Aktywacje, Zatwierdzenia, Zgłoszenia) stoją **liczniki spraw
czekających**. Zero nie rysuje kropki — kropka przy pozycji, pod którą nic nie czeka, uczyłaby ignorować
kropki. Liczniki są wspólne dla menu i kafelków pulpitu i odświeżają się co minutę.

> **Dwa ekrany o podobnej nazwie.** W menu „**Komunikaty**” prowadzi do `/coordinator/messages/`
> (listy **e-mail** do grupy odbiorców), a „**Ogłoszenia**” do `/coordinator/announcements/`
> (**pasek** na każdej stronie serwisu). Oba mają w nagłówku słowo „Komunikaty” — rozróżnia je adres.

Ekrany, które są **wyłącznie odczytem** (karta uczestnika, karta członka komisji, karta zadania), nie
mają własnych adresów zapisu: ich przyciski celują w te same widoki-akcje, co ekran przydziałów etapu.
Dlatego po zapisie wracają tam, gdzie akcja wraca zawsze, a nie tam, skąd się kliknęło.

---

## 2. Przygotowanie edycji

### 2.1 Okno rejestracji i retencja — `/coordinator/registration/`

Ekran **„Rejestracja uczestników”** (menu: Ustawienia → Rejestracja uczestników). Stan („otwarta od… /
rusza… / zamknięta… / wyłączona”) stoi na pulpicie nad listą etapów.

| Pole | Znaczenie |
|---|---|
| Rejestracja włączona | wyłącznik awaryjny: odznaczenie zamyka rejestrację natychmiast i **nie kasuje terminów**, więc po ponownym włączeniu okno wraca |
| Otwarcie rejestracji | puste = otwarta od zaraz. Data w przyszłości jest **zapowiedzią**: strona główna pokazuje „Rejestracja rusza …” i prowadzi na `/register/` z pełnym komunikatem |
| Zamknięcie rejestracji | puste = do odwołania; musi być po otwarciu |
| Okres retencji danych (miesiące) | domyślnie 24; liczony od **ostatniego deadline'u etapu tej edycji**. Zero wyłącza automat dla rocznika (§ 9.1) |

Godziny podaje się i czyta **w czasie polskim**, z dokładnością do minuty. Bramka siedzi w serwisie, więc
obowiązuje wszystkie trzy drogi naraz: formularz `/register/`, API i rejestrację przez Google/Facebooka.
Ukrycie przycisku na stronie jest wyłącznie uprzejmością — żądanie wysłane skryptem dostaje ten sam błąd.

Rejestracji komitetu to **nie dotyczy** (tam regulatorem jest kod zaproszenia) ani zapisów do etapów,
które mają własne terminy.

### 2.2 Etapy i terminy — `/coordinator/stages/<id>/edit/`

Wejście: karta etapu na pulpicie → **„Edytuj terminy”** (nagłówek: „Terminy etapu: <nazwa>”).
Nowy etap: **„Dodaj etap”** → `/coordinator/stages/new/`.

| Pole | Co ustawia |
|---|---|
| **Nazwa etapu** | puste = nazwa domyślna rodzaju; wpisana zastępuje ją **wszędzie** (harmonogram, strona główna, panele, tabele wyników). Wolno zmienić także po zamknięciu etapu |
| **Forma etapu** | „rozwiązania pisemne” albo „rozmowa kwalifikacyjna online”. **Nie da się jej zmienić**, gdy wpłynęły już prace albo są zapisy na rozmowy |
| Otwarcie etapu (`opens_at`) | od tej chwili treści zadań są jawne i wolno oddawać rozwiązania |
| Termin oddania + karencja | upload zamyka się po `deadline_at` **plus** karencja; ten moment zamyka etap |
| Termin recenzji | do kiedy komitet ma wystawić oceny |
| **Dni na jedną recenzję** | domyślnie 14; z tego liczy się indywidualny termin każdej recenzji (§ 4.5). Edytowalne także po zamknięciu etapu |
| Okno reklamacji (od / do) | publikacja wyników przed jego zamknięciem jest odrzucana |
| Miejsce | puste = etap zdalny; trafia na stronę główną i do terminarza |
| **Termin wydarzenia (od / do)** | dni pobytu etapu stacjonarnego, ogłaszane publicznie jako jeden zakres. To **inny fakt** niż okno uploadu: wpisanie dni pobytu nie zmienia godzin przyjmowania plików |

Godziny podaje się i czyta **w czasie polskim**; do bazy idzie UTC. Kolejność terminów jest sprawdzana —
błąd staje pod polem i **nic się nie zapisuje**. Każdy zapis zostawia w audycie różnicę pól.

Trzy blokady, których formularz nie obejdzie:

1. **termin oddania nie cofa się w przeszłość**, jeżeli do etapu wpłynęło choć jedno rozwiązanie.
   Chcesz zamknąć etap wcześniej — użyj „Zamknij etap” (§ 4.1),
2. **etap zamknięty** przyjmuje już tylko termin recenzji, okno reklamacji i miejsce,
3. **daty publikacji wyników nie da się wpisać ręcznie** — nakłada ją i zdejmuje publikacja (§ 5.4).

Próg kwalifikacji etapu ustawia się regułą na ekranie symulacji (§ 5.2) albo — dla wartości startowej —
w panelu administracyjnym (odnośnik „Próg kwalifikacji (admin)” na karcie etapu).

### 2.3 Skala punktacji — `/coordinator/stages/<id>/scale/`

Ekran **„Skala punktacji: <etap>”** (menu: Ustawienia → Skala punktacji). Wartości wpisuje się w jednym
polu tekstowym, po jednej w wierszu, w postaci `wartość;opis`:

```text
0;brak istotnego postępu
2;istotny postęp, rozwiązanie niepełne
5;rozwiązanie pełne z drobnymi usterkami
6;rozwiązanie pełne i poprawne
```

Obok stoi **maksimum punktów** — osobne pole, które musi być równe największej wartości skali. Skala
musi zawierać **0**, wartości muszą być unikalne i rosnące. Pod formularzem jest sekcja
**„Podgląd skali”**: tak zobaczy ją recenzent. Etap bez skali dostaje domyślną 0/2/5/6.

**Zadanie może mieć własną skalę** (pola „Skala punktacji tego zadania” i „Maksimum punktów tego
zadania” w formularzu zadania). **Puste = punktuje skala etapu.** Pierwszeństwo ma zadanie — i tę samą
kolejność stosują wszystkie zapisy ocen oraz listy wyboru punktów w panelu recenzenta.

**Czego nie wolno:** usunąć wartości, którą ktoś **już wystawił** w recenzji (także anulowanej) albo
w ocenie końcowej. Dokładanie wartości i poprawianie opisów jest wolne zawsze. Blokada działa w obie
strony: skala etapu patrzy na oceny zadań, które ją **dziedziczą**.

**Uwaga proceduralna:** progi kwalifikacji podaje się w punktach **bezwzględnych**, więc zmiana skali
**nie przelicza ich automatycznie** — po zmianie skali sprawdź próg.

### 2.4 Zadania — `/coordinator/stages/<id>/problems/`

Ekran **„Zadania: <etap>”** z sekcjami „Lista zadań” i „Dodaj zadanie”. Tytuł zadania na liście prowadzi
do **karty zadania** `/coordinator/problems/<id>/` (nagłówek „Zadanie N: <tytuł>”), a stamtąd „Edytuj”
otwiera formularz `/coordinator/problems/<id>/edit/`.

Co ustawia się przy zadaniu:

| Pole | Uwagi |
|---|---|
| Numer, tytuł (i wersja angielska) | tytuł angielski pokazuje się w interfejsie angielskim, gdy jest wpisany |
| **Treść zadania (PDF)** | leży na prywatnym magazynie i staje się jawna dopiero po otwarciu etapu. Koordynator może ją obejrzeć wcześniej |
| Dozwolone formaty | np. `pdf`, `jpg`/`jpeg` (zdjęcie ręcznie pisanego rozwiązania), `py`, `ipynb` |
| Limit rozmiaru pliku (MB) | musi być mniejszy niż limit na proxy (`MAX_UPLOAD_MB`) |
| **Rozwiązanie wzorcowe (PDF)** | prywatne; **nie staje się jawne po otwarciu etapu**. Pobiera je wyłącznie komitet |
| **Uwagi dla recenzentów** | tekst widoczny tylko w panelu recenzenta |
| **Rubryka oceniania** | po jednym kryterium w wierszu: `punkty;tytuł;opis` (opis nieobowiązkowy) |
| **Szablony komentarzy dla recenzentów** | po jednym w wierszu: `tytuł;treść` (średnik rozdziela **raz**, więc treść może mieć średniki) |
| Skala i maksimum tego zadania | puste = dziedziczy po etapie (§ 2.3) |

**Rubryka zmienia ekran recenzenta**: zamiast listy ocen ze skali dostaje po jednym polu punktów
i komentarzu na kryterium, a sumę liczy serwer. **Suma musi należeć do skali** — system nie zaokrągla,
bo to byłaby zmiana decyzji recenzenta. Zadanie bez kryteriów ocenia się dokładnie jak dotąd.

Poprawienie tytułu kryterium **nie zrywa** powiązania z zapisanymi już punktami. Zapis szablonów
wspólnych **nie rusza** prywatnych szablonów recenzentów.

Karta zadania zbiera w jednym miejscu: treść i ustawienia, skalę z odpowiedzią „skąd się wzięła”,
kryteria i szablony, reguły przydziału, wzorcówkę, wszystkie prace z ocenami, przycisk
**„Pobierz ZIP zadania”** i **statystyki** (rozkład ocen końcowych tego zadania).

**Etap w formie rozmowy nie ma zadań** i nie przyjmuje plików — w menu zamiast „Zadania” stoi „Rozmowy”.

### 2.5 Reguły przydziału recenzentów

Reguła „to zadanie recenzuje ta osoba” obowiązuje **wszystkie** rozwiązania tego zadania. Dodaje się ją
na karcie zadania (sekcja **„Reguły przydziału”**), na karcie członka komisji (sekcja **„Reguły”**) albo
na ekranie przydziałów etapu (zwinięty panel **„Zadania → recenzenci z góry”**).

- prace już zablokowane do oceny dostają recenzenta **od razu**, prace późniejsze — przy najbliższym
  „Przydziel recenzentów”,
- recenzenci z reguł zajmują miejsca z liczby „recenzentów na pracę”, a automat dobiera **resztę**;
  gdy reguł jest więcej niż miejsc, przydzielani są wszyscy (decyzja organizatora wygrywa z liczbą),
- **usunięcie reguły nie kasuje recenzji**, które już z niej powstały — pojedynczy przydział cofa się
  przyciskiem „Cofnij”,
- **konflikt interesów obowiązuje także regułę**: recenzent z województwa uczestnika nie dostanie jego
  pracy na etapie wojewódzkim; taka praca trafia na listę pominiętych z powodem.

### 2.6 Wydarzenia linii czasu — `/coordinator/events/`

Ekran **„Wydarzenia linii czasu: <rocznik>”** (menu: Ustawienia). Wpisy, które trafiają na pasek linii
czasu w nagłówku serwisu, na `/harmonogram/` i do kalendarza uczestnika — obok etapów i okna rejestracji.
Każde wydarzenie (np. warsztat) jest **osobnym** wpisem. Dodanie: „Dodaj wydarzenie”
(`/coordinator/events/new/`), zmiana i usunięcie — z wiersza listy.

---

## 3. Komitet

### 3.1 Zaproszenia — `/coordinator/committee/`

Ekran **„Komitet”**. Konto komitetu powstaje **wyłącznie na kod zaproszenia**; formularz rejestracji dla
zapraszanych to `/register/committee/`. Kod rozdaje się dwiema drogami:

| Droga | Sekcja ekranu | Co dostaje zapraszany |
|---|---|---|
| Pojedynczy kod „do ręki” | **„Kod zaproszenia”** → „Wygeneruj kod” | kod pokazany koordynatorowi **raz**, do przekazania własnym kanałem; może mieć wiele użyć |
| Wysyłka listem | **„Zaproszenia e-mailem”** → „Wyślij zaproszenia” | **własny, jednorazowy** kod w liście z linkiem, terminem ważności i opcjonalną dopiską |

Adresy wkleja się listą (nowe wiersze, przecinki, średniki albo spacje), najwyżej **200 na raz**. Adresy
są sprowadzane do małych liter i odsiewane z powtórzeń; **błędny adres zatrzymuje całą wysyłkę** i jest
wypisany z nazwy — przy częściowej wysyłce nie dałoby się już stwierdzić, do kogo kod poszedł. Adresy,
które mają już konto komitetu, są pomijane z powodem.

W bazie zostaje **wyłącznie skrót kodu**. Kod jawny istnieje tylko w wysłanej wiadomości — nikt, także
organizator, nie odtworzy go z bazy ani z audytu. Stąd semantyka przycisków w tabeli
**„Wysłane zaproszenia”**:

- **„Wyślij ponownie”** nie powtarza starego kodu (nie ma skąd) — **unieważnia go** i wystawia nowy,
  z tymi samymi parametrami i ważnością liczoną od nowa,
- **„Unieważnij”** zamyka kod; wiersz zostaje, bo to jedyna odpowiedź na pytanie „dlaczego ten adres
  dostał od nas list”.

Obu odmawia się, gdy kod został **już użyty** — konta założonego na kod nie cofa się unieważnieniem,
tylko zawieszeniem członka komitetu. Stan w tabeli jest wyliczany w kolejności:
**użyte → unieważnione → wygasłe → wysłane**.

### 3.2 Zatwierdzenia i województwa

Konto założone na kod czeka w sekcji **„Oczekujący na zatwierdzenie”** (przycisk „Zatwierdź”; licznik
w menu przy pozycji *Zatwierdzenia*). Zatwierdzenie nadaje status aktywny i grupy uprawnień.

Sekcja **„Województwa członków komitetu”** ustala województwo osoby. Jest ono **opcjonalne**: recenzent
bez województwa może oceniać prace ze wszystkich województw. Gdy jest ustawione, działa **reguła
konfliktu interesów** — na etapie wojewódzkim ta osoba nie dostanie pracy uczestnika ze swojego
województwa. Województwo członka komitetu zmienia **wyłącznie koordynator** (nie sam zainteresowany),
a wartość od koordynatora jest oznaczana jako potwierdzona.

### 3.3 Role

| Rola | Co wolno |
|---|---|
| **Członek komitetu (recenzent)** | ocenia przydzielone prace, pisze komentarze, zgłasza problemy z pracą |
| **Komisja odwoławcza** | osobna flaga na profilu: rozpatruje reklamacje na `/appeals/` (bez autorów recenzji rundy 1) |
| **Koordynator** | wszystko powyższe plus prowadzenie edycji; konta koordynatorów są w panelu chronione |
| **Opiekun szkolny** | otwarta rejestracja `/register/supervisor/`; widzi **tylko** uczniów, którzy sami wskazali jego adres |

### 3.4 Spis i karta członka

**`/coordinator/members/`** („Członkowie komisji”) — wiersz dostaje **każdy** profil, także bez ani
jednej recenzji: zero przydziałów jest informacją o rozkładzie pracy. Kolejność jest kolejnością
pilności (najpierw zalegający, potem najbardziej obciążeni); filtry `?status=` i `?stage=` jadą w adresie,
a etap zawęża **liczniki**, nie listę osób.

**`/coordinator/members/<id>/`** — karta jednej osoby: *Dane* (z zatwierdzeniem, województwem, flagą
komisji odwoławczej), *Obciążenie* (przydzielone / szkice / wystawione / anulowane / po terminie
w rozbiciu na etapy, ze zmierzonym czasem pracy), *Recenzje* (z „Zmień punkty” i „Odbierz”),
*Przydziel pracę*, *Reguły*, *Kalibracja*, *Zgłoszone problemy*, *Przypomnij e-mailem* i *Historia*.

---

## 4. Prowadzenie etapu

### 4.1 Zamknięcie i wciąganie prac do oceny

Etap zamyka się **sam** po terminie oddania plus karencja. Ręcznie — karta etapu na pulpicie →
**„Zamknij etap”** (powtórzenie daje komunikat „już zamknięty”, a nie ciche „nic się nie stało”).

**Komitet nie musi czekać na deadline.** Obok stoi **„Zablokuj oddane prace do oceny”**: najnowsza
oddana wersja każdej pary (uczestnik, zadanie) wchodzi do przydziału, a **etap zostaje otwarty** —
okno uploadu działa dalej. Pojedynczą pracę wciąga przycisk **„Zablokuj do oceny”** w tabeli
**„Rozwiązania i oceny”**. Na karcie etapu stoją dwa liczniki, po których widać, czy jest co blokować:
**oddane (niezablokowane)** i **w ocenie**.

> **Nowa wersja unieważnia rozpoczętą ocenę.** Skoro okno uploadu zostaje otwarte, uczestnik może
> wysłać poprawkę pracy, którą komitet już czyta — i wtedy **wygrywa uczestnik**: wszystkie
> nieanulowane recenzje (także wystawione) przechodzą w „anulowane”, ocena końcowa jest wycofywana,
> a praca wraca do stanu „oddane” i wymaga ponownego zablokowania. Uczestnik widzi przy uploadzie
> ostrzeżenie, a recenzent w panelu — powód „Uczestnik wysłał nową wersję rozwiązania…”.
> Praca **finalna, w reklamacji albo z ogłoszonymi wynikami** jest poza zasięgiem tej reguły.

### 4.2 Przydział recenzentów

Automatycznie: karta etapu → **„Przydziel recenzentów”** (domyślnie 2 na pracę). Prace, dla których nie
da się skompletować recenzentów bez konfliktu województwa, są wypisane jako pominięte — **z kodem
uczestnika, nie z nazwiskiem**. Dwa równoległe kliknięcia nie dadzą czterech recenzentów.

Ręcznie: **`/coordinator/stages/<id>/assignments/`** („Przydziały i oceny: <etap>”). Ekran ma na górze
zwinięty panel reguł (§ 2.5), a pod nim tabelę **„Rozwiązania i oceny”** — w jednym wierszu uczestnik
(kod i nazwisko, odnośnik do karty), zadanie, wersja z plikiem i liczbą stron, status, recenzenci, ocena
końcowa i czynności: **„Zablokuj do oceny”**, **„Przydziel”** (lista wyboru), **„Zmień punkty”**,
**„Ocena końcowa”**, **„Cofnij”** / **„Odbierz”** oraz rozwijana **„Historia”** (20 ostatnich wpisów
audytu tej pracy i jej recenzji).

**Filtry są przełącznikami.** Nad tabelą stoją liczniki kroków obiegu (oddane / do przydziału / w ocenie
/ moderacja / ocenione) policzone dla całego etapu; kliknięcie licznika włącza filtr, ponowne — zdejmuje.
Filtry składają się ze sobą i są w adresie (`?q=`, `?problem=`, `?status=`, `?reviewer=`, `?page=`),
więc przefiltrowaną tabelę da się wysłać odnośnikiem. 100 wierszy na stronę; filtr i strona wracają po
każdej akcji.

**Czynności zbiorcze.** Zaznaczenie wierszy obsługuje paczkę ZIP („Pobierz zaznaczone”) i pasek czynności:
przydzielenie i odebranie wskazanego recenzenta oraz zablokowanie do oceny. Odmowa dotycząca jednej pracy
**nie przerywa reszty** — ląduje w komunikacie jako „Pominięto N: …”.

### 4.3 „Cofnij” i „Odbierz”

- **„Cofnij”** — dla przydziału, którego recenzent jeszcze nie tknął,
- **„Odbierz”** — dla szkicu i dla **oceny już wystawionej**. Rekord zostaje (punkty i komentarze są
  historią), ale przestaje się liczyć, a recenzent widzi „Koordynator odebrał Ci tę pracę”.

Odebranie **wystawionej** oceny zdejmuje ocenę uzgodnioną konsensusem i zawraca pracę do oceniania, żeby
dało się przydzielić kogoś na miejsce odebranego. Runda 1 jest rozstrzygana od nowa **tylko wtedy, gdy
zostają co najmniej dwie recenzje** — z jedną powstałaby „ocena uzgodniona” z jednego głosu.

Czego „Odbierz” nie ruszy: etapu z **ogłoszonymi wynikami**, pracy w reklamacji albo finalnej oraz oceny
rozstrzygniętej **przez człowieka** (moderacja, trzeci recenzent, korekta koordynatora, decyzja
reklamacyjna). Takie oceny zmienia się formularzem **„Ocena końcowa”**, z **obowiązkowym uzasadnieniem**.

### 4.4 Moderacja rozjazdów — `/coordinator/moderation/`

Ekran **„Moderacja (rozjazdy ocen)”**. Trafiają tu prace, w których dwie niezależne oceny rundy 1 się
rozeszły. Widać obie oceny, komentarze i sekcję **„Notatki recenzentów”** (wątek, który recenzenci tej
pracy prowadzą między sobą). Dwie drogi wyjścia:

- **„Rozstrzygnij (posiedzenie komisji)”** — koordynator wpisuje ocenę końcową z uzasadnieniem,
- **„Wyznacz trzeciego recenzenta”** — runda 2 (rozjemcza); trzeci recenzent dostaje materiał, w którym
  poprzednicy są podpisani literami, nie nazwiskami.

Ocena rozstrzygnięta przez człowieka jest odtąd chroniona (§ 4.3).

### 4.5 Terminy recenzji i przypomnienia

Przy przydziale recenzja dostaje **własny termin**: chwila przydziału plus „Dni na jedną recenzję”,
**przycięte** do terminu recenzji etapu, jeśli ten wypada wcześniej — ale nigdy w przeszłość (praca
przydzielona po terminie etapu dostaje pełne okno, bo termin „wczoraj” nie jest terminem). Termin jest
**zapisywany**: późniejsza zmiana ustawień etapu nie przesuwa terminów już przyznanych.

**`/coordinator/stages/<id>/progress/`** („Postęp oceniania: <etap>”) odpowiada na dwa pytania: „jak
daleko jesteśmy” i „na kogo czekamy”. U góry pasek segmentowy (oddane, zablokowane, przydzielone,
w moderacji, reklamacja, ocenione, odrzucone przez antywirusa) — segmenty są **rozłączne i sumują się do
liczby wszystkich prac**. Niżej tabela recenzentów: przydzielone / szkice / wystawione / po terminie /
czas pracy, zalegającymi do góry. Ekran pisze wprost, **której definicji „po terminie”** użył.

Dwa przyciski wysyłają listy: **„Przypomnij e-mailem”** (jedna osoba) i **„Przypomnij wszystkim
zaległym”**. List **nie zawiera ani kodów prac, ani tytułów zadań** — komplet recenzent widzi po
zalogowaniu, a lista przydziałów w skrzynce byłaby wyciekiem tego, co ocenianie ślepe ma chronić. Osoba
bez niedokończonych recenzji listu nie dostaje. Niezależnie od tego raz na dobę idzie automatyczne
przypomnienie do recenzentów z pracami po terminie i z terminem w ciągu 2 dni.

### 4.6 Zgłoszone problemy z pracami — `/coordinator/issues/`

Kolejka zgłoszeń od recenzentów („plik się nie otwiera”, „to nie jest rozwiązanie tego zadania”,
„podejrzenie niesamodzielności”). Licznik w menu. Z wiersza: **rozstrzygnięcie** zgłoszenia albo
**odebranie** pracy recenzentowi.

### 4.7 Rozmowy kwalifikacyjne — `/coordinator/stages/<id>/interviews/`

Dla etapu w formie rozmowy (§ 2.2). Ekran **„Rozmowy kwalifikacyjne: <etap>”**, sekcje
**„Dodaj terminy”** i **„Wyznaczone terminy”**.

1. Terminy powstają **serią**: początek pierwszego, długość jednej rozmowy (1–480 min), ile terminów po
   kolei (1–50) i ile miejsc w każdym (1–20). Sloty idą jeden po drugim, bez przerw.
2. **Okno rozmów to okno etapu** — wszystkie terminy muszą się zmieścić między otwarciem a terminem
   etapu. Chcesz rozmawiać w innych dniach: najpierw przesuń terminy etapu.
3. **Kolizji świadomie nie sprawdzamy** — kilka komisji rozmawia równolegle. Rozróżnia je pole
   **„Oznaczenie”** (np. „komisja A”), a liczbę osób — „Miejsc w jednym terminie”.
4. **Link do rozmowy** widzi wyłącznie osoba zapisana na dany termin. Pokój powstaje sam w chwili zapisu,
   z losowym sufiksem w nazwie — na publicznej instancji wideo nazwa pokoju **jest** poświadczeniem.
5. Tabela terminów pokazuje **dane osobowe** zapisanych (kod, imię i nazwisko, e-mail) — to obok podglądu
   wyników jedyny taki ekran w serwisie, stąd odznaka „dane osobowe”.
6. **Termin da się usunąć tylko dopóki nikt się na niego nie zapisał.**

Uczestnik zapisuje się z `/me/`, ma w etapie **jeden** termin, a „Zmień na ten termin” przenosi zapis
w jednej transakcji. Dzień wcześniej idzie automatyczne przypomnienie z linkiem i linkiem testowym.

> **Etap w formie rozmowy nie ma ścieżki oceniania w systemie** — punkty wpisuje koordynator poza nim.

### 4.8 Przekazywanie rozwiązań na skrzynkę — `/coordinator/submission-forwarding/`

Ekran **„Przekazywanie rozwiązań”** (menu: Ustawienia → Przekazywanie rozwiązań). Serwis może
przesyłać **każdą** przyjętą pracę na wskazane skrzynki komitetu — pocztą, razem z plikiem
w załączniku.

| Pole | Znaczenie |
|---|---|
| Adresy, na które trafiają rozwiązania | jeden adres na wiersz albo po przecinku, **najwyżej pięć**. Puste pole = przekazywanie wyłączone (tak zaczyna każdy konkurs) |

Jak to działa:

1. **List wychodzi dopiero po czystym skanie antywirusowym**, a nie w chwili wysłania pracy —
   zwykle kilka–kilkanaście sekund po uploadzie. Plik odrzucony przez skan nie jest przekazywany
   nigdy, tak samo jak plik, którego skan się nie powiódł.
2. **Każda wersja to osobny list.** Uczestnik, który poprawi rozwiązanie, tworzy nową wersję —
   dostaniesz ją drugim listem. Oceniana jest ostatnia wersja.
3. **Temat**: `[Olimpiada Kwantowa] Nowe rozwiązanie: <etap> – zadanie <nr> – <kod uczestnika>`.
   Nawias kwadratowy na początku jest po to, żeby dało się ustawić na te listy regułę w skrzynce.
4. **Treść** niesie metryczkę pracy: konkurs, etap, zadanie, numer wersji, czas przyjęcia, kod
   publiczny, imię i nazwisko, szkołę, nazwę pliku, jego rozmiar i sumę kontrolną SHA-256 oraz
   odnośnik do karty uczestnika w panelu. **Punktów ani recenzji w liście nie ma.**
5. **Załącznik** ma granicę 20 MB (ustawienie instalacji `SUBMISSION_FORWARD_MAX_ATTACHMENT_MB`).
   Większy plik przychodzi bez załącznika, z wyjaśnieniem i odnośnikiem do panelu — pobierzesz go
   stamtąd.
6. **Etap treningowy też jest przekazywany.** To najtańszy sposób sprawdzenia, czy ustawienie
   działa, zanim ruszą eliminacje.
7. Awaria poczty **nie rusza przyjętej pracy**: praca jest przyjęta, skan zapisany, a list
   ponawiany. Zmiana adresów zostaje w audycie (`competition.forwarding_updated`, bez adresów).

> **To jest wyniesienie danych osobowych uczestników poza serwis.** Administratorem tych danych
> jesteś Ty (organizator), a skrzynka, na którą trafiają prace, nie jest już pod kontrolą
> platformy — wpisuj wyłącznie adresy komitetu i pamiętaj o wierszu w rejestrze czynności (§ 9.2,
> czynność „Przyjmowanie i ocenianie prac konkursowych”).

### 4.9 Slider sponsorów — `/coordinator/sponsor-slider/`

Ekran **„Slider sponsorów”** (menu: Ustawienia → Slider sponsorów). W pasku menu serwisu, po
prawej stronie pozycji „FAQ”, jedzie taśma logotypów partnerów i organizatora — kilka naraz,
przesuwa się o jeden logotyp co ustawioną liczbę sekund, w nieskończonej pętli (jak na stronie
Olimpiady Biologicznej).

| Pole | Znaczenie |
|---|---|
| Pokazuj slider sponsorów w menu | włącznik. Wyłączenie chowa pasek na każdej stronie serwisu — natychmiast, bez czekania |
| Co ile sekund pasek przesuwa się o jeden logotyp | liczba całkowita od 1 do 120 |
| Poziomy partnerów w sliderze | grupa pól wyboru, jedno na każdy poziom współpracy, z liczbą partnerów **z logotypem** na tym poziomie. Nic niezaznaczone = pokazuj partnerów każdego poziomu (tak zaczyna każdy konkurs) |

Skąd biorą się logotypy — **nie ma tu drugiej listy partnerów**:

1. **Pierwsza plansza to zawsze logotyp organizatora**, ten sam, co w stopce serwisu
   (`/cms/` → Ustawienia → Dane serwisu → Organizator). Bez ustawionego logotypu organizatora
   slider zaczyna od razu od pierwszego partnera. Jeśli organizacja stoi też jako partner pod tym
   samym adresem internetowym, jej znak nie pokazuje się drugi raz.
2. **Reszta to partnerzy strony `/partnerzy/`** (`/cms/` → Partnerzy), w tej samej kolejności —
   wyłącznie ci, którzy mają wgrany logotyp. Poziom, opis, adres i sam logotyp redaguje się
   **tam**; ten ekran wyłącznie filtruje poziomy i ustawia tempo.
3. **Podgląd pod formularzem** pokazuje każdego partnera z logotypem — także pominiętego w
   sliderze, z powodem (poziom niezaznaczony, brak logotypu, duplikat organizatora) — żeby
   od razu było wiadomo, czemu jakiegoś znaku nie widać w menu.
4. **Bez partnera z logotypem i bez logotypu organizatora slider nie pokazuje się wcale** — pusty
   pasek w menu wyglądałby jak usterka.
5. Na wąskim ekranie (telefon, tablet w pionie) i przy włączonym w systemie trybie ograniczonego
   ruchu slider albo znika z menu, albo stoi nieruchomo — nigdy nie miga i nie przewija się wbrew
   ustawieniom dostępności przeglądarki.

Zmiana zapisuje się od razu (audyt `site.sponsor_slider_updated` — włącznik, sekundy i poziomy,
bez treści komunikatów).

---

## 5. Wyniki

### 5.1 Reklamacje — `/appeals/`

Po otwarciu okna reklamacji uczestnik składa reklamację na własną pracę z zakładki **„Reklamacje”**
w swoim panelu. Rozpatruje je **komisja odwoławcza** (osobna flaga na profilu członka komitetu) na
ekranie **„Reklamacje do rozpatrzenia”**: widzi „Argument uczestnika”, „Oceny rundy 1” i zapisuje decyzję
z uzasadnieniem („Zapisz decyzję”). Autorzy recenzji rundy 1 tej pracy nie rozstrzygają jej reklamacji.
Uczestnik dostaje list z rozstrzygnięciem i uzasadnieniem.

### 5.2 Symulacja progu — `/coordinator/stages/<id>/simulation/`

Ekran **„Symulacja kwalifikacji: <etap>”** odpowiada na pytanie „co by było, gdyby próg wyglądał tak”.
Tryby: minimum punktów, N najlepszych, N na województwo, hybryda. Parametry jadą w adresie, więc wynik
da się odświeżyć, zapisać w zakładkach i wkleić w wiadomości do reszty komitetu.

Wynik: liczba zakwalifikowanych i poza progiem, **punkt odcięcia**, rozkład po województwach i pełna
tabela z odznaką „kwalifikuje się”. **Symulacja niczego nie zapisuje** i działa **w trakcie oceniania**
(praca bez oceny liczy się wtedy jako 0 punktów, a ekran pisze, ilu prac jeszcze nie rozliczono).

Przycisk **„Zastosuj tę regułę do etapu”** zapisuje regułę **i nic poza tym**: nikogo nie kwalifikuje
i niczego nie ogłasza.

### 5.3 Kwalifikacja ręczna (decyzja komitetu)

Formularz stoi **w wierszu tabeli symulacji** — decyzja zapada, patrząc na te same liczby. Wybór:
„kwalifikuje się” / „nie kwalifikuje się” / puste (niech rozstrzyga próg) plus **obowiązkowe uzasadnienie
o długości co najmniej 10 znaków**.

Regulamin zna sytuacje, których próg nie opisuje: zerwane łącze w trakcie rozmowy, praca oddana poza
systemem na polecenie organizatora, wynik unieważniony mimo wysokiej sumy. Dopóki tej drogi nie było,
jedynym wyjściem było majstrowanie przy punktach — czyli wpisanie do protokołu nieprawdy o tym, jak
pracę oceniono.

Decyzja **bije regułę punktową**, ale **nie podnosi zdyskwalifikowanego**. W ogłoszonej tabeli wiersz
dostaje odznakę **„kwalifikacja decyzją komitetu”** — wynik niezgodny z progiem, którego nie widać,
wygląda z zewnątrz jak błąd rachunkowy albo jak protekcja. W audycie zostaje decyzja i **długość**
uzasadnienia, nigdy jego treść.

### 5.4 Przeliczenie i publikacja — `/coordinator/stages/<id>/results/`

Ekran **„Wyniki: <etap>”**.

1. **„Przelicz wyniki (podgląd)”** — pełna tabela **z danymi osobowymi**, widoczna wyłącznie dla
   koordynatora. Niczego nie ogłasza. Nagłówek: „Podgląd wyników etapu <nazwa>”.
2. Po zamknięciu okna reklamacji i rozstrzygnięciu wszystkich spraw: **„Opublikuj wyniki”** z wyborem
   **trybu anonimizacji**:

| Tryb | Co pokazuje tabela | Kiedy |
|---|---|---|
| kod uczestnika | `OLM-XXXXXX`, punkty, miejsce, województwo | domyślny, każdy etap |
| inicjały i szkoła | inicjały + nazwa szkoły, z progiem k-anonimowości 3 | gdy organizator chce wyniku czytelnego dla szkół |
| pełne dane | imię i nazwisko | **tylko finał, tylko laureaci, tylko za zgodą** na publikację nazwiska |

3. **Ponowna publikacja nadpisuje** snapshot tego samego etapu i zostawia wpis w audycie.

Publikacja **przed** zamknięciem okna reklamacji jest odrzucana. Po publikacji: tabela jest jawna pod
`/results/<id>/`, każdy uczestnik etapu dostaje list z odnośnikiem, a jego własna informacja zwrotna
(punkty za zadania i komentarze recenzentów) staje się dostępna. Publiczne statystyki edycji liczą się
**wyłącznie z ogłoszonych snapshotów** i stoją pod `/statystyki/`.

**Zmiana oceny po publikacji** (korekta, decyzja reklamacyjna, kwalifikacja ręczna) nie wchodzi do tabeli
sama — panel ostrzega, że wymaga **ponownego przeliczenia i publikacji**.

### 5.5 Kalibracja recenzentów — `/coordinator/stages/<id>/calibration/`

Ekran czytany **po ocenianiu**, przy przygotowaniu instruktażu na kolejną edycję — nie w trakcie.
Pojedynczy rozjazd nie mówi nic o żadnym z recenzentów; informacją jest dopiero rozkład rozjazdów po
wszystkich pracach jednej osoby.

Wiersz na recenzenta: liczba wystawionych recenzji rundy 1, **średnie odchylenie ze znakiem** od oceny
drugiego recenzenta tej samej pracy, to samo wobec oceny uzgodnionej, udział rozjazdów i podpis
„surowy / łagodny / zgodny z komisją”. Znak jest tu całą istotą: średnia z wartości bezwzględnych
zrównałaby recenzenta chaotycznego z konsekwentnie surowym, a to są dwie różne rozmowy. Do rachunku nie
wchodzą szkice, recenzje anulowane ani runda rozjemcza. Poniżej 5 recenzji tabela pisze „za mało danych”.

### 5.6 Podobieństwo rozwiązań — `/coordinator/stages/<id>/similarity/`

Dotyczy **wyłącznie zadań oddawanych jako kod**. Przy dowodzie w PDF-ie plagiat widać gołym okiem; przy
programie wystarczy zmienić nazwy zmiennych, żeby dwa pliki przestały być podobne dla człowieka.

Przycisk **„Przelicz”** zleca zadanie w tle. W tabeli stoją pary powyżej progu z pola
**„Próg pokazywania”** (domyślnie 0,8). `…/similarity/<id>/` pokazuje obie prace obok siebie
(nagłówek „Porównanie: <kod> ↔ <kod>”). Przycisk **„Zgłoś do komitetu”** jest zakładką na parze do
obejrzenia na posiedzeniu — da się go cofnąć, a kolejne przeliczenie **przenosi** ten znacznik, bo to
decyzja człowieka, a nie wynik obliczenia.

> **Wysoki wynik jest przesłanką, nie dowodem.** Przy zadaniu z jednym oczywistym algorytmem dwie
> uczciwe prace potrafią wyjść bardzo podobnie. Sekcja „Jak to jest liczone” na ekranie mówi, co dokładnie
> mierzy liczba.

---

## 6. Komunikacja

| Narzędzie | Adres | Do czego |
|---|---|---|
| **Komunikaty** (listy do grupy) | `/coordinator/messages/` | jednorazowa wiadomość e-mail do wybranej grupy |
| **Ogłoszenia** (pasek w serwisie) | `/coordinator/announcements/` | zdanie widoczne na **każdej** stronie, także dla niezalogowanych |
| **Zgłoszenia** (support desk) | `/coordinator/support/` | kolejka spraw od ludzi, z wątkiem i odpowiedzią |
| **Forum uczestników** | `/coordinator/forum/` | rozmowa uczestników między sobą, moderowana przez Ciebie |
| **FAQ** | `/faq/` (redakcja w `/cms/`) | odpowiedzi, które mają wyprzedzić zgłoszenia |
| **Strona statusu** | `/status/` | „nie mogę wysłać pracy — to u was, czy u mnie?” |

### 6.1 Komunikaty — listy do grupy

Grupy odbiorców: uczestnicy bieżącej edycji, zapisani do etapu, zakwalifikowani do etapu, członkowie
komitetu, komitet jednego województwa, wklejona lista adresów. „Uczestnik edycji” znaczy „ktoś z wpisem
do któregokolwiek jej etapu”, a nie „ktoś, kto kiedykolwiek założył konto”. **Z wysyłki wypadają konta
zablokowane i bez potwierdzonego adresu.**

Ekran jest **dwustopniowy**: **„Podgląd”** pokazuje liczbę odbiorców i treść tak, jak pójdzie w liście,
i dopiero **„Wyślij”** wysyła. To jedyny moment, w którym pomyłkę („uczestnicy edycji” zamiast „zapisani
do etapu”) da się jeszcze cofnąć. **Adresów ekran nie pokazuje** — sprawdzasz rząd wielkości, nie wpisy.

Każdy odbiorca dostaje **osobną kopertę**. Wysyłka idzie porcjami, więc awaria jednej porcji nie kasuje
reszty. Każda wysyłka zostaje w sekcji **„Wysłane komunikaty”** (autor, data, grupa, temat, treść, liczba
odbiorców, stan) — **rejestr nie trzyma adresów**. Stan „przekazana do wysyłki” znaczy, że listy trafiły
do kolejki; o doręczeniu rozstrzyga serwer odbiorcy.

### 6.2 Ogłoszenia — pasek w serwisie

Sekcja **„Komunikaty w serwisie”**, formularz „Nowy komunikat” / „Zmiana komunikatu”. To inna wiadomość
niż aktualność: aktualność czyta ten, kto wejdzie na `/aktualnosci/`, a ogłoszenie („przedłużamy termin
do piątku”, „logowanie przez Google nie działa”) musi zobaczyć **każdy, kto jest w serwisie**.

Do 500 znaków zwykłego tekstu plus opcjonalny odnośnik jako **para pól** (adres + etykieta; sam adres bez
etykiety się nie pokaże). Trzy wagi: informacja, ostrzeżenie, alarm — ostatnia jest od razu odczytywana
przez czytniki ekranu. Widoczność wyznacza okno czasowe (puste „do” = do wyłączenia) plus wyłącznik jako
hamulec awaryjny. Pole „można zamknąć” decyduje, czy czytelnik może baner schować (wybór pamięta jego
przeglądarka, nie konto). Ogłoszenie pojawia się i znika **natychmiast** po zapisie.

### 6.3 Zgłoszenia (support desk)

| Kto | Gdzie |
|---|---|
| Zalogowany | „Zgłoś problem” w pasku konta → `/support/new/`, własne sprawy: `/support/` |
| **Bez konta** | `/support/new/` (odnośnik w stopce) — dodatkowe pole adresu e-mail i blok antyspamowy |
| Zgłaszający | `/support/<id>/` — wątek z formularzem dopisku |
| Koordynator | `/coordinator/support/` — kolejka; `/coordinator/support/<id>/` — wątek i odpowiedź |

Kategorie: konto i logowanie, wysyłka pracy, wyniki, rejestracja, inne. Stany:
**otwarte → odpowiedziane → zamknięte**. **Dopisek zgłaszającego wraca sprawę do „otwarte”** — bez tej
reguły sprawa, do której ktoś napisał „to nadal nie działa”, znikałaby z kolejki na zawsze. Zgłoszenie
zamknięte nie przyjmuje już wypowiedzi z żadnej strony. Filtry: `?status=`, `?category=`.

**Kontekst techniczny** (adres strony, przeglądarka, język, kod uczestnika, etapy, nazwa ostatniej
czynności z ostatnich 10 minut) zbiera się automatycznie i jest widoczny **tylko dla organizatora**.
Nie wchodzą tam ciasteczka, tokeny ani zawartość formularzy. Formularz mówi o tym wprost.

**Poczta.** Powiadomienie o nowym zgłoszeniu idzie na **adres kontaktowy organizatora** z ustawień
serwisu w `/cms/` (adres nadawcy `noreply@…` jest skrzynką, której nikt nie czyta). **Żaden z listów nie
niesie treści** — tylko informację, że sprawa albo odpowiedź jest, i odnośnik.

### 6.4 Forum uczestników — `/coordinator/forum/`

Forum jest **domyślnie wyłączone** i to nie jest ostrożność techniczna. Pod adresem rozmawiają osoby
**niepełnoletnie**, więc otwarcie forum jest zobowiązaniem do dyżuru moderacyjnego — a nie funkcją, która
ma się włączyć razem z wdrożeniem. Włącza je przełącznik konkursu `participant_forum`, a przestawia go
**operator platformy** (`/admin/ → Konkursy → <konkurs> → feature_flags`, `OPERACJE.md` § 6.4) — nie ma go
na ekranie „Ustawienia konkursu” celowo, bo otwarcie forum jest ustaleniem dyżuru moderacyjnego, a nie
polem do zaznaczenia obok koloru akcentu. Dopóki przełącznik jest wyłączony, adresów `/forum/…`
i `/coordinator/forum/…` **nie ma** (404), a w żadnym menu nie przybywa ani jedna pozycja.

| Kto | Gdzie |
|---|---|
| Uczestnik, komitet | „Forum” w pasku konta → `/forum/`; dział: `/forum/<dział>/`; wątek: `/forum/t/<id>/` |
| Autor | `/forum/mine/` — „Twoje wpisy”: stan każdej wypowiedzi i **Twoje uzasadnienie**, gdy ją odrzuciłeś |
| Koordynator | `/coordinator/forum/` — kolejka; `/coordinator/forum/t/<id>/` — wątek z **każdym** wpisem |
| Koordynator | `/coordinator/forum/threads/`, `/coordinator/forum/categories/`, `/coordinator/forum/settings/` |

**Zacznij od działu.** Wątki powstają wyłącznie w dziale, więc forum bez ani jednego działu jest forum,
na którym nikt nic nie napisze. Działu nie da się skasować — stoją w nim rozmowy — ale da się go zamknąć
na nowe wątki, a wtedy zostaje do czytania.

**Komplet startowych działów jednym poleceniem:** `manage.py seed_forum_categories --competition <slug>`
zakłada dział „Ogólne” i po jednym dziale na każdy warsztat z tabeli harmonogramu strony „Warsztaty” —
nazwa działu bierze temat bez dopisku prowadzącego, a termin i prowadzący trafiają do opisu. Polecenie
jest **idempotentne**: rozpoznaje istniejące działy po adresie (slugu), więc drugie uruchomienie —
po dopisaniu kolejnego warsztatu do harmonogramu — nic nie nadpisze w działach, które już zredagowałeś
w panelu. Argument `--competition` wolno pominąć wyłącznie na instalacji z jednym konkursem.

**Dwa tryby moderacji** (`/coordinator/forum/settings/`):

- **przed publikacją** (domyślny) — wpis widzą inni dopiero po Twoim zatwierdzeniu; do tego czasu widzi
  go **wyłącznie jego autor**, z dopiskiem „czeka na moderację”,
- **po publikacji** — wpis jest widoczny od razu, a Ty go ukrywasz. Wybieraj to tylko wtedy, gdy masz
  dyżur moderacyjny na żywo.

**W czasie etapu przyjmującego rozwiązania obowiązuje tryb „przed publikacją” — zawsze, niezależnie od
tego ustawienia.** To nie jest ostrożność, tylko regulamin: § 10 ust. 2 i § 17 zabraniają omawiania
rozwiązań zadań otwartego etapu, a wpis widoczny przez kwadrans, zanim go zdejmiesz, zdąży przeczytać
ktoś, kto jeszcze nie oddał pracy. Ekran ustawień mówi wprost, gdy wymuszenie działa. Formularz pisania
pokazuje wtedy uczestnikowi ostrzeżenie z nazwą etapu i cytatem z regulaminu.

**Kolejka** (`/coordinator/forum/`) trzyma trzy listy naraz: nowe wątki, nowe wpisy i zgłoszone
wypowiedzi. Treść stoi w niej wprost, żeby decyzja była jednym kliknięciem, a nie otwieraniem kart.
Zaznaczenie kilku pozycji i **„Zatwierdź zaznaczone”** publikuje je razem — **odrzucić zbiorczo się nie
da** i tak ma zostać: odrzucenie wymaga uzasadnienia, które zobaczy autor, a jedno zdanie wysłane do
dwudziestu osób naraz nie jest uzasadnieniem żadnej z tych decyzji.

**Zatwierdzenie wątku publikuje razem z nim jego pierwszy wpis** — to on jest treścią, którą właśnie
przeczytałeś. Dalsze wpisy tego wątku przechodzą kolejkę osobno.

**Zgłoszenie od uczestnika niczego nie ukrywa.** Rozstrzygasz Ty: „Rozpatrzone” zamyka sprawę i nie rusza
wypowiedzi. Automatyczne zdejmowanie po zgłoszeniu dałoby każdemu uczestnikowi przycisk „usuń cudzy
wpis”, a na forum, na którym toczy się rywalizacja, ktoś by go w końcu użył.

**Forum nie wysyła listów — ani do Ciebie, ani do autorów.** Decyzja jest świadoma: konkurs ma już dwa
kanały poczty (komunikaty i zgłoszenia), a trzeci, wyzwalany każdym akapitem nastolatka, zamieniłby Twoją
skrzynkę w kanał RSS i skończył się regułą „do kosza”. W zamian:

- **odznaka przy „Forum uczestników”** w menu panelu mówi, ile pozycji czeka. To jedyny sygnał, więc przy
  trybie „przed publikacją” zaglądaj do kolejki tak, jak zaglądasz do zgłoszeń,
- **autor znajduje Twoje uzasadnienie** na swoim ekranie `/forum/mine/` — i to jedyne miejsce, w którym
  się o odrzuceniu dowie. Odrzucenie bez uzasadnienia jest niemożliwe (formularz odmówi).

**Czego forum nie ma i w wersji pierwszej mieć nie będzie:** wiadomości prywatnych (rozmowa
niepełnoletnich bez świadków jest dokładnie tym, czego moderacja nie widzi), załączników i HTML-a
(wypowiedź jest tekstem, odnośniki stają się klikalne same), polubień i rankingów (zawody mają już jeden
ranking i jest anonimowy), awatarów i podpisów.

**Podpis pod wypowiedzią to imię i pierwsza litera nazwiska** — nigdy adres e-mail, szkoła ani kod
publiczny `OLM-…`. Kod jest kluczem anonimowego oceniania: jeden wątek „cześć, jestem Ania OLM-XXXXXX”
wystarczyłby, żeby powiązanie kod → osoba stało się publiczne dla wszystkich naraz. Wpisy członków
komitetu i Twoje noszą odznakę „Komitet” / „Organizator”, żeby uczestnik odróżnił zdanie kolegi od zdania
osoby rozstrzygającej o zawodach.

**Uczestnik może poprawić swój wpis przez 15 minut** od napisania; poprawka opublikowanego wpisu wraca
w trybie „przed publikacją” **do kolejki** (inaczej wystarczyłoby napisać zdanie nijakie, doczekać
zatwierdzenia i podmienić treść). **Usunięcie własnego wpisu jest miękkie**: dla czytelników znika
natychmiast, ale wiersz zostaje — żeby zgłoszona wypowiedź nie mogła zniknąć na żądanie autora.

**Każda Twoja decyzja zostaje w dzienniku zdarzeń** (`/coordinator/audit/`, zdarzenia `forum.*`) —
**bez kopii treści wypowiedzi**. RODO: forum ma własny wiersz w rejestrze czynności przetwarzania
(wersja 1.2), wpisy uczestnika wchodzą do paczki `/account/export/`, a anonimizacja konta zdejmuje podpis
(zostaje „Użytkownik usunięty”), zostawiając rozmowę czytelną.

### 6.5 FAQ

Strona `/faq/` z pytaniami pogrupowanymi w sekcje; każde pytanie ma **trwałą kotwicę**, więc odpowiedź na
zgłoszenie może odesłać do konkretnego pytania, a odnośnik przeżyje poprawkę sformułowania. Redakcja
dopisuje pytania w `/cms/`. Formularz zgłoszenia zaczyna się od odnośnika „Zanim zgłosisz: FAQ” — im
lepsze FAQ, tym krótsza kolejka.

---

## 7. Dokumenty i zgody

### 7.1 Zestaw zgód przy rejestracji

| Zgoda | Wymagana | Dokument |
|---|---|---|
| akceptacja regulaminu | zawsze | `/dokumenty/regulamin/` |
| przetwarzanie danych osobowych | zawsze | `/dokumenty/rodo/` |
| zgoda rodzica lub opiekuna prawnego | gdy uczestnik jest niepełnoletni | `/dokumenty/zgoda-opiekuna/` |
| publikacja imienia i nazwiska | **nie** | – |

Zestaw jest **jeden** dla wszystkich trzech dróg rejestracji (formularz, API, logowanie zewnętrzne).
Etykieta każdej zgody jest **odnośnikiem do dokumentu**, a nazwa organizatora pochodzi z ustawień
serwisu w `/cms/`.

**Odnośnik prowadzi do PDF-a, nie do podstrony** — bierzemy pierwszy załącznik PDF strony dokumentu.
**PDF-y wgrywa organizator w `/cms/`** (Strony → Dokumenty → pliki do pobrania); dopięcie pliku zmienia
adres w formularzu natychmiast, bez wydania aplikacji. Gdy przy dokumencie nie wisi jeszcze żaden PDF,
etykieta prowadzi do strony — zgoda bez odnośnika do treści nie jest zgodą świadomą.

**Rocznik rozstrzyga o zgodzie opiekuna** i liczymy go zachowawczo: osoba urodzona osiemnaście lat temu
może mieć jeszcze 17 lat, więc zgoda opiekuna jest od niej wymagana.

**Dowód zgody** to osobny wiersz w bazie: uczestnik, rodzaj, **wersja dokumentu**, data, droga
(formularz / API / logowanie zewnętrzne / panel) i ewentualne wycofanie. Pola na profilu są tylko
projekcją stanu bieżącego — do szybkiego odczytu, nie do dowodzenia. Historię widać na karcie uczestnika
i w eksporcie uczestników (§ 9.3).

**Zmiana wersji dokumentu** (nowy regulamin) wymaga zmiany stałej w kodzie — to świadomie **nie jest**
ustawienie w panelu: od tego momentu nowe zgody zapisują się pod nową wersją, a stare wpisy dalej mówią
prawdę o tym, co obowiązywało wtedy. Zamów tę zmianę u administratora razem z wgraniem nowego PDF-a.

**Wycofanie w portalu** dotyczy dokładnie jednej zgody: publikacji imienia i nazwiska. Pozostałe są
warunkiem udziału — ich wycofanie znaczy rezygnację z zawodów i jest sprawą do organizatora.

### 7.2 Uczestnicy niepełnoletni — zgoda opiekuna online

Normalną drogą jest **podpisany link**, a nie wydruk:

1. uczestnik podaje w `/me/` (zakładka **„Zgody”**, sekcja „Zgoda rodzica lub opiekuna prawnego”) adres
   e-mail opiekuna,
2. system wysyła na ten adres list z linkiem ważnym **14 dni**,
3. opiekun otwiera `/zgoda/<token>/` **bez logowania**, czyta treść w obowiązującej wersji, widzi
   **imię dziecka i szkołę**, zaznacza pole i potwierdza,
4. powstaje dowód zgody z adresem potwierdzającego i znacznikiem czasu; uczestnik dostaje list.

Dlaczego token, a nie konto dla opiekuna: opiekun ma w systemie jedną sprawę, a zakładanie mu konta
byłoby zebraniem większego zbioru danych, niż ten, po który przyszedł. **Zmiana adresu unieważnia
poprzedni link** — to jedyna droga „odwołania” wysłanej prośby. W liście do opiekuna **nie ma nazwiska**
dziecka (imię i szkoła wystarczą do rozpoznania, a list bywa wysłany pod adres z literówką).

Koordynator widzi stan **brak / oczekuje / potwierdzona `<data>`** na karcie i na ekranie edycji konta,
**wyłącznie do odczytu**: dowodem jest potwierdzenie z podpisanego linku, a nie kliknięcie w panelu
organizatora. Uczestnik ma przycisk „Wyślij ponownie” — powtórna wysyłka jest zamierzona, bo listy giną
w spamie.

**Wersja do wydruku** (PDF pod `/dokumenty/zgoda-opiekuna/`) zostaje jako droga zapasowa: dla rodzica bez
adresu e-mail albo dla szkoły, która chce zgody w teczce.

### 7.3 Dokumenty organizatora

Regulamin, polityka RODO, standardy ochrony małoletnich, skład komitetów, polityka cookies — wszystko
jako strony w `/dokumenty/` redagowane w `/cms/`, z załącznikami do pobrania (PDF, opcjonalnie DOCX).
Kolejność, treść i załączniki należą do **redakcji**; wdrożenie aplikacji ich nie nadpisuje.

### 7.4 Zgoda opiekuna — jak serwis ustala pełnoletność

Od wydania `v0.30.0` rejestracja pyta o **pełną datę urodzenia**, a nie o sam rocznik. Zmiana ma
jeden cel: wymagalność zgody rodzica albo opiekuna prawnego jest decyzją o podstawie prawnej
udziału w zawodach i ma być **rozstrzygnięciem**, a nie przybliżeniem.

**Reguła, słowo w słowo.** Uczestnik jest pełnoletni, gdy dzisiejsza data (liczona w strefie
Europe/Warsaw, czyli tak, jak liczy ją człowiek w Polsce) jest **co najmniej** dniem jego
osiemnastych urodzin. Dzień urodzin jest już dniem pełnoletności; dzień wcześniej — jeszcze nie.
Urodzony 29 lutego staje się pełnoletni **1 marca**: roku „+18” po roczniku przestępnym nigdy nie
ma w kalendarzu 29 lutego, a przy wątpliwości wolimy wymagać zgody o dzień za długo niż o dzień
za krótko.

**Co z uczestnikami zapisanymi wcześniej.** Konta założone przed tą zmianą znają wyłącznie
rocznik — dnia urodzin nikt im nie dopisze, bo zgadnięta data byłaby danymi wymyślonymi, a nie
uzupełnionymi. Dla nich obowiązuje stara, zachowawcza reguła: *rok bieżący − rocznik ≤ 18* znaczy
osobę niepełnoletnią. W praktyce jest to najwyżej o rok „za ostrożnie”, czyli jeden checkbox
więcej. Uczestnik może uzupełnić datę sam w **Mój panel → Edytuj dane**; panel przypomina mu o tym
jednym zdaniem, a uzupełnienie zostaje w audycie jako `participant.birth_date_completed`.

**Wiek nieznany znaczy „niepełnoletni”.** Dotyczy to konta bez daty i bez rocznika oraz konkursu,
którego profil rejestracji ma odznaczone „Data urodzenia wymagana”. Nigdy nie zgadujemy na korzyść
pominięcia zgody: zawyżenie kosztuje jedno zbędne pole wyboru, zaniżenie — zgodę pobraną od
dziecka bez wiedzy opiekuna. Te dwa błędy nie są równoważne.

**Gdzie ta reguła działa.** We wszystkich drogach zapisu naraz, bo jest zapisana w jednym miejscu
(`apps/accounts/consents.py`): rejestracja hasłem `/register/`, dokończenie rejestracji przez
Google/Facebooka, API rejestracji, import listy klasowej przez nauczyciela, przyjęcie zaproszenia
przez ucznia, edycja danych przez uczestnika i przez Ciebie w panelu. Skrypt w przeglądarce, który
odsłania wiersz zgody, **niczego nie rozstrzyga** — formularz wysłany bez JavaScriptu albo
z wyciętym polem dostaje tę samą odmowę z serwera.

**Co zobaczysz w panelu.** Karta uczestnika (`/coordinator/accounts/<id>/`) pokazuje datę
urodzenia, rocznik i stan zgody opiekuna: „niewymagana (uczestnik pełnoletni)”, „potwierdzona”,
„prośba wysłana” albo „brak adresu opiekuna”. Jeżeli po Twoim zapisie uczestnik wychodzi na osobę
niepełnoletnią bez potwierdzonej zgody, ekran mówi to wprost komunikatem po zapisie. Jest to
**ostrzeżenie, a nie blokada**: poprawienie danych nie może zależeć od oświadczenia, którego i tak
nie złożysz za nikogo. Zgodę zbiera uczestnik ze swojego panelu, a potwierdza ją opiekun
podpisanym linkiem (`/zgoda/<token>/`).

**Co z tego wychodzi na zewnątrz.** Nic. Eksporty dla odbiorców zewnętrznych (API integracji,
protokoły, listy dla kuratorium) nie niosą ani daty urodzenia, ani rocznika. Twój własny eksport
uczestników edycji ma obie kolumny — to są dane organizatora, nie odbiorcy.

---

## 8. Dyplomy i zaświadczenia — `/coordinator/stages/<id>/certificates/`

Ekran **„Dyplomy i zaświadczenia: <etap>”**. Cztery rodzaje dokumentów: **laureat**, **finalista**,
**uczestnik**, **opiekun**. Rodzaju **nie wyliczamy z punktów** — o tym, kto jest laureatem, rozstrzyga
komitet, a próg tytułu bywa inny niż próg kwalifikacji.

- **„Wystaw”** przy wierszu (rodzaj z listy) albo **„Wystaw wszystkim (ZIP)”** dla całego etapu,
- obie drogi są **idempotentne**: wpis, który ma już dokument tego rodzaju, **zachowuje swój numer** —
  uczestnik z dwoma numerami na ten sam tytuł miałby problem przy pierwszej rekrutacji, w której ten
  numer trzeba podać,
- nazwy plików w paczce to **numery dokumentów**, nigdy nazwiska,
- sekcja **„Opiekunowie szkolni”** wymienia wyłącznie tych, którzy **potwierdzili udział szkoły**
  w swoim panelu — sam adres wpisany przez ucznia jest przesłanką, a nie oświadczeniem nauczyciela.

W bazie jest **rejestr, nie plik**: edycja, odbiorca, rodzaj, numer, kod weryfikacyjny, data
i wystawiający. PDF powstaje przy każdym pobraniu, więc poprawka szablonu dotyczy także dokumentów już
wystawionych. Uczestnik pobiera swoje z `/me/certificates/`, opiekun — z dołu swojego panelu.

**Weryfikacja bez logowania:** `/dyplomy/<kod>/` potwierdza rodzaj, edycję, numer i datę, a **imienia
i nazwiska nie pokazuje**, dopóki odbiorca nie wyraził zgody na publikację pełnych danych. Zaświadczenie
opiekuna nie pokazuje nazwiska nigdy. Nieznany kod **nie daje 404** — strona wygląda tak samo i mówi
„takiego dokumentu nie ma”, bo rozróżnienie kodem HTTP zamieniłoby ten adres w narzędzie do sprawdzania
kodów maszynowo.

---

## 9. Dane, eksporty, RODO

### 9.1 Retencja — `/coordinator/retention/`

Ekran **„Retencja danych”**. Okres jest **ustawieniem edycji** (§ 2.1) i liczy się od **ostatniego
deadline'u etapu** tej edycji — nie od daty utworzenia edycji (bo edycja żyje rok) i nie od publikacji
wyników (bo tę się przesuwa).

Po terminie automat anonimizuje konta uczestników tej edycji **tą samą funkcją**, co żądanie usunięcia
danych: znikają imię, nazwisko, adres, telefon, szkoła i data urodzenia, zostaje pseudonimowy kod, województwo
i cała dokumentacja zawodów.

Ekran pokazuje dwie listy: **„Do anonimizacji (N)”** i **„Zostają (N)”** — z powodem:

| Powód | Znaczenie |
|---|---|
| późniejsza edycja | uczestnik startuje w edycji, której retencja jeszcze nie minęła (także bieżącej) |
| reklamacja w toku | sprawa jest sama w sobie podstawą przetwarzania |
| wyniki nieogłoszone | etap bez publikacji: zawody nie zostały domknięte |
| już zanonimizowane | konto przeszło już anonimizację |

**Edycja bieżąca nie wchodzi do przebiegu bezwarunkowo** — pomyłka w ustawieniu (retencja krótsza niż
kalendarz rocznika) nie może anonimizować startujących. Konta komitetu i koordynatora są poza zakresem.

Anonimizacja jest **nieodwracalna**, więc ekran jest planem, a przycisk **„Wykonaj teraz”** wykonuje ten
sam przebieg synchronicznie, z potwierdzeniem.

**Opiekunowie szkolni nie wchodzą (jeszcze) do tego automatu** (stan na 22.09.2026, patrz docstring
`apps/accounts/retention.py`). Reguła „czy wolno już wyczyścić to konto” jest tu napisana w
słowniku uczestnika (zgłoszenia, reklamacje, publikacja wyników) i nie umie dziś rozstrzygnąć
analogicznego pytania dla nauczyciela („czy nie prowadzi klasy w edycji, która jeszcze trwa”).
Konto opiekuna, które organizator chce mimo to wyczyścić, usuwa się **ręcznie** z listy kont
(`/coordinator/accounts/` → filtr „opiekunowie” → „Usuń konto”) — ten ekran już poprawnie
anonimizuje profil opiekuna (szkoła, telefon, zgody znikają; potwierdzenia udziału szkoły w
edycjach zostają, jeśli takie są — patrz § 10).

### 9.2 Rejestr czynności przetwarzania — `/coordinator/processing-register/`

Dokument wymagany art. 30 ust. 1 RODO, **gotowy do wydania na żądanie**. Obejmuje dziewięć czynności:
konta uczestników, dowody zgód, przyjmowanie i ocenianie prac, ogłaszanie wyników i dokumenty,
reklamacje, rozmowy kwalifikacyjne, konta komitetu, zgłoszenia i pomoc oraz utrzymanie serwisu.
Odbiorcy są wymienieni wprost (hosting, dostawca poczty, analityka wyłącznie po zgodzie). Dane
administratora (nazwa, adres, KRS, kontakt) dokłada się **z ustawień serwisu w `/cms/`**, więc ich
poprawka nie wymaga wydania aplikacji. `?format=csv` oddaje ten sam dokument jako plik otwierający się
w arkuszu kalkulacyjnym.

> Rejestr jest **danymi w kodzie**, nie arkuszem: zmiana w systemie, która zmienia przetwarzanie (nowy
> odbiorca, nowa kategoria danych, inny okres retencji), jest zmianą w repozytorium i przechodzi przez
> tę samą recenzję co kod. Zamawiając nową funkcję, zamawiaj razem z nią wiersz w rejestrze.

### 9.3 Eksport danych — `/coordinator/export/`

Ekran **„Eksport danych”**. Trzy zestawienia, każde w CSV i XLSX:

- **uczestnicy edycji** — kod publiczny, imię, nazwisko, e-mail, szkoła, klasa, województwo, rok
  urodzenia, telefon, stan konta oraz komplet zgód: stan, **wersja dokumentu** i data (z dowodów, nie
  z projekcji na profilu),
- **wyniki etapu** — miejsce, kod, imię i nazwisko, szkoła, województwo, punkty za każde zadanie, suma,
  status wpisu, kwalifikacja. Liczone na danych **bieżących**, więc eksport działa też w trakcie
  oceniania (praca bez oceny liczy się wtedy jako 0),
- **recenzje etapu** — id recenzji, kod uczestnika, zadanie, runda, adres recenzenta, stan, punkty, daty.
  Uczestnik **wyłącznie pod kodem**: ocenianie jest ślepe i zestawienie recenzji tego nie znosi.

CSV wychodzi ze znacznikiem BOM i średnikiem jako separatorem — polski Excel otwiera go bez kreatora
importu. W audycie zostaje rodzaj eksportu i liczba wierszy, **nigdy dane**.

**Eksport danych jednej osoby** (art. 20 RODO) wydaje przycisk na karcie konta
`/coordinator/accounts/<id>/` — paczka ZIP z `dane.json` i wgranymi plikami, identyczna z tą, którą
uczestnik pobiera sam. Wniosek z art. 20 przychodzi też listem, od osoby, która akurat nie może się
zalogować.

Na tej samej karcie konta przycisk **„Wyślij link do zmiany hasła”** wysyła dokładnie ten sam list,
co samoobsługowe „Nie pamiętasz hasła?” — nigdy nie zobaczysz ani nowego hasła, ani treści linku;
przycisk odmawia dla konta jeszcze nieaktywowanego, zablokowanego, bez hasła platformy i dla
własnego konta.

### 9.4 Audyt — `/coordinator/audit/`

Ekran **„Audyt”**: 100 wpisów na stronę, od najnowszego, z filtrami (fragment adresu wykonawcy, akcja,
typ obiektu, przedział dat — obustronnie domknięty). Wpisów **nie da się zmienić ani usunąć**.

**W szczegółach wpisu z zasady nie ma danych osobowych**: zamiast wartości zmienionych pól idą ich nazwy,
zamiast treści uzasadnienia jego długość, zamiast adresów liczniki. To jest reguła projektowa — audyt
czytają osoby, które nie muszą znać danych kontaktowych uczestników, a doklejenie nazwiska „dla
czytelności” zamieniłoby ślad techniczny w wyciąg z bazy osobowej.

---

## 10. Konta uczestników i opiekunów

| Ekran | Adres | Do czego |
|---|---|---|
| **Konta** | `/coordinator/accounts/` | wszystkie konta; wyszukiwarka `?q=`, filtr `?role=`, 50 na stronę |
| Uczestnicy / Opiekunowie szkolni | ta sama lista z filtrem roli | osobny ekran powtarzałby wyszukiwarkę i kolumny |
| Edycja konta | `/coordinator/accounts/<id>/` | dane, „Konto aktywne”, dla uczestnika także telefon, województwo, szkoła, klasa, **data urodzenia**; dla członka komitetu status, komisja odwoławcza i województwo |
| Usunięcie konta | `/coordinator/accounts/<id>/delete/` | strona potwierdzenia mówi, co się stanie |
| **Konta oczekujące na aktywację** | `/coordinator/activations/` | „Aktywuj ręcznie”, „Wyślij link ponownie” |
| **Karta uczestnika** | `/coordinator/participants/<id>/` | cały przebieg zawodów jednej osoby, wyłącznie do odczytu |

Trzy rzeczy, które panel robi inaczej niż samoobsługa:

- **adres e-mail zmienia się od razu, bez listu potwierdzającego** — dowodem jest decyzja organizatora,
  który zwykle właśnie dzwoni do uczestnika, bo do skrzynki z literówką nic nie dochodzi,
- **„Konto aktywne”** to wyłącznik logowania: odwracalny i nieniszczący. To pierwsze narzędzie przy
  sporze — usunięcia cofnąć się nie da,
- **konta koordynatora i superużytkownika są chronione**: widać je z odznaką, ale otwierają się tylko do
  odczytu i nie mają przycisku usunięcia. Dwóch koordynatorów mogłoby się inaczej nawzajem zablokować
  jednym kliknięciem.

**Ekran aktywacji jest obejściem na czas problemów z dostarczalnością poczty.** Lista pokazuje czas
pozostały do skasowania konta (link aktywacyjny i nieaktywowane konto żyją 24 godziny), a ręczna aktywacja
zostawia **inny wpis w audycie** niż kliknięcie linku przez użytkownika — bo adres został potwierdzony
czym innym.

**Karta uczestnika** zbiera w jednym miejscu: dane i stan konta ze zgodą opiekuna, rejestr zgód
(co, w jakiej wersji, kiedy, którą drogą), etapy z progiem i decyzją komitetu wraz z uzasadnieniem, zapis
na rozmowę, **każdą wersję każdej pracy**, recenzje najnowszej wersji, ocenę końcową z trybem ustalenia,
reklamacje, wiersz z **ogłoszonej** tabeli (miejsce i suma zamrożone w chwili publikacji, nie bieżące),
wystawione dyplomy, zgłoszenia i 50 ostatnich wpisów audytu.

**Opiekun szkolny** dostaje dostęp **od ucznia**, nie od organizatora: uczestnik wpisuje adres opiekuna
w swoim profilu i w każdej chwili może go wyczyścić. Nie ma tu ani zatwierdzania przez koordynatora, ani
przypisywania po nazwie szkoły — oba wyglądają porządniej, ale oba znaczyłyby, że nauczyciel dostaje
dostęp do danych ucznia bez jego udziału. Opiekun widzi kod, imię, nazwisko, szkołę i klasę ucznia oraz
ścieżkę statusu pracy; **nie widzi punktów przed ogłoszeniem wyników, prac ani komentarzy recenzentów**.

**Włączenie rejestracji opiekunów szkolnych.** Konto opiekuna zakłada się samoobsługowo pod adresem
`/register/supervisor/`, ale sam adres jest domyślnie **ukryty** (organizator wyłączył go w wydaniu
z 19.09.2026 na wyraźną prośbę — rola nie była jeszcze ogłaszana). Żeby go pokazać:

1. `/cms/` → **Ustawienia** → **Dane serwisu** → zaznacz **„Rejestracja opiekunów szkolnych”**
   (`SiteSettings.supervisor_registration_enabled`) → zapisz.
   Tuż pod przełącznikiem stoi pole **„wstęp nad formularzem rejestracji opiekunów”**
   (`supervisor_registration_intro`): domyślnie puste, czyli nad formularzem `/register/supervisor/`
   nie ma żadnego tekstu (decyzja organizatora z 22.09.2026); wpisany akapit pojawia się tam od razu,
   bez wdrożenia.
2. Od tej chwili, bez restartu i bez czekania: `/register/supervisor/` odpowiada formularzem zamiast
   404-ki; na `/register/` i na `/login/` pojawia się linia „Jesteś nauczycielem? Zarejestruj się jako
   opiekun szkolny”; profil uczestnika (`/me/` → Profil) pokazuje pole „Adres e-mail opiekuna
   szkolnego”; głównemu menu (dla niezalogowanego czytelnika) przybywa ostatnia pozycja „Dla
   nauczycieli”, prowadząca na ten sam adres (prośba organizatora z 22.09.2026).
3. **Wyłączenie chowa z powrotem wszystkie cztery** — adres rejestracji znów daje 404, odnośniki
   i pozycja menu znikają, pole profilu jest zdejmowane z formularza (nie tylko ukrywane). Konta
   opiekunów, które już powstały,
   **nie tracą dostępu**: panel `/supervisor/` działa dalej, a uczeń, który już wpisał adres opiekuna,
   nie traci tego wpisu.

**Co widzi nauczyciel po rejestracji.** Formularz zbiera imię, nazwisko, adres e-mail, hasło, szkołę
(wolny tekst — jeden nauczyciel bywa opiekunem uczniów z kilku placówek, więc nie ma tu wyszukiwarki SIO
jak u uczestnika), telefon kontaktowy (opcjonalny) oraz zgody na regulamin i RODO — te same dokumenty
i wersje, co u uczestnika. Po wysłaniu formularza konto **czeka na aktywację**, dokładnie jak konto
ucznia: list z linkiem aktywacyjnym, 4 godziny na kliknięcie, w razie potrzeby ponowna wysyłka z ekranu
logowania. Strona „Konto zostało założone” tłumaczy nauczycielowi, co dalej: panel „Moi uczniowie” będzie
pusty, dopóki uczniowie sami nie wpiszą jego adresu e-mail w swoim profilu — to oni decydują, kto widzi
ich postęp, nie organizator ani nauczyciel.

---

## 11. Kalendarz prowadzenia edycji — ściągawka

| Kiedy | Co zrobić | Gdzie |
|---|---|---|
| przed startem | okno rejestracji, retencja, terminy i formy etapów, nazwy | `/coordinator/registration/`, `/coordinator/stages/<id>/edit/` |
| przed startem | skala punktacji, zadania z treścią, rubryka, wzorcówka, szablony | `/coordinator/stages/<id>/scale/`, `…/problems/` |
| przed startem | zaproszenia do komitetu, zatwierdzenia, województwa | `/coordinator/committee/` |
| przed startem | dokumenty i PDF-y zgód, FAQ, wydarzenia linii czasu | `/cms/`, `/coordinator/events/` |
| w trakcie uploadu | obserwacja pulpitu; ewentualne wciąganie prac do oceny | `/coordinator/`, „Zablokuj oddane prace do oceny” |
| po deadline | zamknięcie etapu (albo automat), przydział recenzentów | karta etapu |
| w trakcie oceniania | postęp, przypomnienia, moderacja, zgłoszone problemy | `…/progress/`, `/coordinator/moderation/`, `/coordinator/issues/` |
| po ocenianiu | podobieństwo, symulacja progu, decyzje ręczne | `…/similarity/`, `…/simulation/` |
| okno reklamacji | rozpatrzenie reklamacji (komisja odwoławcza) | `/appeals/` |
| po zamknięciu okna | przelicz podgląd → opublikuj wyniki | `…/results/` |
| po publikacji | dyplomy i zaświadczenia, eksporty | `…/certificates/`, `/coordinator/export/` |
| po edycji | kalibracja recenzentów (instruktaż na kolejny rok) | `…/calibration/` |
| po terminie retencji | sprawdzenie planu anonimizacji | `/coordinator/retention/` |

---

## 12. Funkcje w przygotowaniu

Poniższe były **dopiero w budowie**, gdy powstawał ten podręcznik (gałąź `main`, wrzesień 2026). Opis
pochodzi z ich zamówienia, **nie z działającego ekranu** — zanim zaplanujesz edycję wokół którejkolwiek
z nich, sprawdź w panelu, co faktycznie wdrożono.

**Quiz / etap testowy (`apps/quiz`, w przygotowaniu).** Etap rozstrzygany zadaniami zamkniętymi,
ocenianymi automatycznie — jako trzecia forma etapu obok rozwiązań pisemnych i rozmowy kwalifikacyjnej.
Dla organizatora oznaczałoby to etap, w którym nie ma przydziału recenzentów ani moderacji, a wynik jest
znany zaraz po zamknięciu; reszta obiegu (progi, symulacja, reklamacje, publikacja) zostaje bez zmian.
Dopóki tego nie ma, etap wstępny prowadzi się jak zwykły etap z zadaniami albo poza systemem.

**Rejestracja grupowa (`apps/accounts/bulk_registration.py`, w przygotowaniu).** Zakładanie kont
uczestników **hurtem**, z listy przygotowanej przez szkołę albo organizatora — zamiast rejestracji
pojedynczej przez każdego ucznia. Pytanie, które ta funkcja musi rozstrzygnąć, jest prawne, a nie
techniczne: **zgody wyraża osoba, a nie szkoła**, więc konto założone hurtem i tak będzie musiało
przejść przez blok zgód i aktywację adresu przy pierwszym logowaniu. Do czasu wdrożenia jedyną drogą
jest otwarta rejestracja z `/register/`.

**Uwierzytelnianie dwuskładnikowe (`apps/accounts/twofactor.py`, w przygotowaniu).** Drugi składnik
logowania dla kont funkcyjnych — koordynatora i komitetu, czyli tych, które widzą dane osobowe i mogą
zmieniać oceny. Do tego czasu chroni je samo hasło i skrzynka pocztowa: trzymaj liczbę kont koordynatora
przy minimum i wymagaj od komitetu długich, unikatowych haseł.

**Integracje zewnętrzne (`apps/integrations`, w przygotowaniu).** Wymiana danych z systemami organizatora.
Cokolwiek się w niej znajdzie, będzie **nowym odbiorcą danych** — czyli wymaga wiersza w rejestrze
czynności przetwarzania (§ 9.2) i, jeśli dane wychodzą poza organizatora, przejrzenia polityki RODO
**przed** uruchomieniem.
