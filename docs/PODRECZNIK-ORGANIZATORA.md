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
| **Ocenianie** | Moderacja, Zgłoszone problemy, Kalibracja recenzentów, Podobieństwo rozwiązań; *Ocena AI* (tylko przy włączonej fladze `ai_grading`, § 4.12) |
| **Uczestnicy i konta** | Uczestnicy, Wszystkie konta, Opiekunowie szkolni, Aktywacje, *Status ucznia* (tylko z włączonymi zaświadczeniami, § 10a) |
| **Komitet** | Członkowie, Zatwierdzenia, Zaproszenia, Województwa |
| **Komunikacja** | Komunikaty, Zgłoszenia, Ogłoszenia |
| **Raporty** | Eksport danych, Audyt, Symulacja kwalifikacji, Dyplomy, Retencja danych, Rejestr czynności; *Materiały z warsztatów* (tylko przy włączonej fladze `workshop_materials`, § 4.11); *Wpisowe* i *Płatności* (tylko przy włączonej fladze `fees`, § 10h) |
| **Ustawienia** | Rejestracja uczestników, Wydarzenia linii czasu, Skala punktacji, Slider sponsorów, Plakaty do pobrania |

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

#### Dowolne wartości ocen (od v0.35.0)

Na tym samym ekranie stoi przełącznik **„Jakie oceny wolno wystawić”**:

| Tryb | Co wpisuje recenzent | Rola wartości skali |
|---|---|---|
| **tylko wartości ze skali** (domyślny, także dla nowych etapów) | jedną z wartości skali, np. 0, 2, 5, 6 | lista do wyboru |
| **dowolna wartość od min do max (co 0,01)** | dowolną liczbę od najniższej do najwyższej wartości skali, z najwyżej dwoma miejscami po przecinku, np. **4,25** | **podpowiedź** obok pola („5 – rozwiązanie pełne z drobnymi usterkami”) |

Tryb dotyczy **całego etapu** – wszystkich zadań, także tych z własną skalą (dla nich granicami są
najniższa i najwyższa wartość ich skali). Ten sam tryb obowiązuje każdego, kto wpisuje punkty w tym
etapie: recenzentów, koordynatora (korekta recenzji, ocena końcowa, rozstrzygnięcie rozjazdu), komisję
odwoławczą, komisję rozmów i klientów API.

- **Przełączenie na dowolne wartości** jest zawsze możliwe – każda wystawiona ocena ze skali mieści się
  w jej zakresie.
- **Powrót do „tylko wartości ze skali”** system **odrzuca** (odmowa `FREE_VALUES_IN_USE`), dopóki
  w etapie jest choć jedna ocena spoza skali (recenzja, ocena końcowa, nowa punktacja z reklamacji,
  punkty z rozmowy) albo zadanie z samym maksimum. Komunikat podaje, ile ich jest. Popraw te oceny
  (albo dopisz ich wartość do skali – wartości skali są liczbami całkowitymi) i nadaj takim zadaniom
  skalę albo wyczyść ich maksimum, a potem przełącz tryb ponownie.
- W trybie dowolnym obniżenie maksimum skali poniżej wystawionej już oceny jest odmową
  (`SCALE_LOCKED`) – usunięcie pojedynczej wartości ze skali już nie, bo ocena 5 mieści się w 0–6
  także bez pozycji „5”.
- **Suma etapu** z ocen ułamkowych jest dokładna (4,25 + 3,5 = 7,75). Gdy konkurs liczy **wagi zadań**,
  suma ważona jest zaokrąglana **raz, na końcu, połówka w górę**: do **0,01** w etapie z dowolnymi
  wartościami i – jak dotąd – do **pełnego punktu** w etapie „tylko ze skali”. Ta sama reguła stoi
  w podglądzie wyników, publikacji, symulacji progu i w maksimum etapu.
- **Próg kwalifikacji** (i reguły przejścia) przyjmuje liczbę z przecinkiem, np. „co najmniej 38,5”.
- Punkty wyświetlają się bez zbędnych zer: „5”, „4,25”, „3,5” (w wersji angielskiej z kropką).
  **Eksport CSV** zapisuje je **z kropką** („4.25”) – plik czytają też skrypty i kuratoria, a przecinek
  dziesiętny w pliku rozdzielanym średnikami bywa odczytywany jako koniec kolumny. Otwierając CSV
  w polskim Excelu, wskaż w imporcie separator dziesiętny „.” – albo pobierz **XLSX**, w którym punkty
  są od razu liczbami.
- **Test online** (etap w formie testu) słucha tego samego przełącznika (od wersji po v0.35.0):
  w etapie „tylko ze skali” wynik testu wchodzi do tabeli wyników **zaokrąglony do pełnych punktów,
  połówka w górę** (7,5 → 8) – jak dotąd; w etapie z dowolnymi wartościami wchodzi **co do 0,01**
  (7,5 zostaje 7,5). Sam wynik podejścia (ekran wyników testu, eksport CSV testu) zawsze ma dwa
  miejsca po przecinku. Etap testu dostaje skalę tak samo jak każdy etap – przełącznik jest na tym
  samym ekranie `/coordinator/stages/<id>/scale/` (np. z pulpitu: „Skala punktacji”). Punkty pytania
  i punkty ujemne wpisuje się w edytorze pytań z przecinkiem albo kropką („0,5”, „0,25”; najwyżej dwa
  miejsca po przecinku, w obu trybach etapu) – tak samo w imporcie (`[pkt: 0,5]`, kolumna `punkty`).
  Ocena częściowa pytania wielokrotnego wyboru (proporcjonalnie) i każda kwota za pytanie są
  zaokrąglane do 0,01 **połówka w górę** (0,125 → 0,13) – tą samą metodą, co suma etapu; wcześniej
  kwota za pytanie szła zaokrągleniem bankierskim (0,125 → 0,12), co może zmienić o 0,01 wynik podejścia
  przeliczonego ponownie przyciskiem „Przelicz punkty”.
- **Rubryki** w etapie z dowolnymi wartościami przyjmują ułamki: maksimum kryterium (np. `2,5;Pomysł`)
  i punkty za kryterium (np. 1,75) – szczegóły w § 3 („Rubryka oceniania”). Powrót do „tylko wartości ze
  skali” jest odmawiany także wtedy, gdy któreś kryterium ma ułamkowe maksimum (komunikat poda, ile ich
  jest) – zaokrąglij je w rubryce zadania.

**Zadania mogą mieć różną liczbę punktów.** W etapie z dowolnymi wartościami zadanie może dostać
**samo „Maksimum punktów tego zadania”** – bez listy wartości skali – np. **7** albo **12,5**. Takie
zadanie ocenia się dowolną liczbą od **0** do swojego maksimum, co 0,01 (także gdy skala etapu ma punkty
ujemne – samo maksimum jest zakresem zadania, a nie wariantem skali etapu, więc przesunięcie skali go nie
dotyczy). Recenzent nie widzi przy nim podpowiedzi ze skali etapu, bo opisywałyby inny zakres. W etapie
„tylko ze skali” formularz zadania samego maksimum nie przyjmie – trzeba podać też skalę. Maksimum każdego
zadania widać:

- w **liście zadań** etapu (kolumna „Maks. punktów”, z dopiskiem „(własne)” i sumą maksimów pod tabelą),
- na **karcie zadania** i przy polu oceny u **recenzenta** („Punkty (max 12,5)”),
- w nagłówkach **tabel wyników** – ogłoszonej („Zad. 3 (max 12,5)”, „Razem (max 40)”) i w podglądzie
  koordynatora („Razem (max 40)”). Maksimum sumy to suma maksimów zadań (z wagami, gdy konkurs je ma),
  liczona tą samą arytmetyką, co suma uczestnika.

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
bo to byłaby zmiana decyzji recenzenta. W etapie z dowolnymi wartościami ocen (§ 2.3) suma musi
**mieścić się w zakresie** zadania. Zadanie bez kryteriów ocenia się dokładnie jak dotąd.

**Ułamki w rubryce** (od wersji po v0.35.0) zależą od trybu etapu:

| Tryb etapu | Maksimum kryterium (`punkty;tytuł`) | Punkty za kryterium (recenzent) |
|---|---|---|
| tylko wartości ze skali | liczba całkowita 1–1000, np. `2;Pomysł` (ułamek – błąd z wyjaśnieniem) | liczba całkowita od 0 do maksimum |
| dowolna wartość (co 0,01) | także ułamek od 0,01 do 1000, np. `2,5;Pomysł` albo `2.5;Pomysł` | dowolna liczba od 0 do maksimum co 0,01, np. 1,75 |

Suma kryteriów jest liczona dokładnie (1,75 + 2,5 = 4,25) i trafia do oceny recenzji bez zaokrąglania;
suma poza zakresem zadania to odmowa, tak jak w trybie skali. Formularz zadania pokazuje zapisaną
rubrykę bez zbędnych zer („4;Całość”, „2,5;Zapis”), a karta zadania – maksima w tej samej postaci.

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

### 2.7 Języki interfejsu — `/coordinator/competition/` (I18N-01)

Ekran **„Ustawienia konkursu”**, pola **„Język domyślny”** i **„Języki interfejsu”**. Do wyboru:
polski oraz dziesięć najczęściej używanych języków świata (English, 简体中文, हिन्दी, Español,
العربية, Français, বাংলা, Português, Русский, Bahasa Indonesia).

- **Jeden język** = brak przełącznika języka; strona zawsze w tym języku (tak stoi Olimpiada
  Kwantowa — tylko polski).
- **Więcej języków** = w pasku konta pojawia się glob z menu języków. Uczestnik dostaje swój język
  także w listach; kto nie wybrał, dostaje język przeglądarki (jeśli jest na liście), a w ostateczności
  język domyślny konkursu.
- Arabski wyświetla się od prawej do lewej.
- Tłumaczenia poza polskim i angielskim są **maszynowe** — przed szerszą komunikacją warto poprosić
  native speakera o przegląd (zgłoszenie do operatora, `docs/OPERACJE.md` § 26.3).
- **Nie tłumaczą się:** treści stron w `/cms/` (dla konkursu międzynarodowego piszemy je po angielsku),
  ekrany koordynatora i komisji. Tytuł i PDF zadania w wersji angielskiej (`title_en`, `statement_pdf_en`)
  widzi uczestnik w każdym języku poza polskim.

### 2.8 Kraje zamiast województw (REG-01)

Konkurs międzynarodowy dzieli uczestników na **kraje**. Przestawienie robi operator jedną komendą
(`manage.py regions_countries --competition <slug>`, `docs/OPERACJE.md` § 27): włącza własny podział
(`custom_regions`), zakłada listę 199 krajów (nazwy angielskie) i wyłącza z listy 16 województw
oraz „poza Polską” — nie kasuje ich, bo mogą na nie wskazywać profile z poprzednich lat.

Od tej chwili:

- formularze rejestracji, profilu, komisji i zaproszeń pytają o **„Kraj”** zamiast o województwo
  (lista aktywnych krajów konkursu); kod spoza listy jest odrzucany,
- karta uczestnika, eksporty, katalog czatu i listy pokazują **nazwę kraju** („Germany”), a nagłówki
  kolumn mówią „Kraj”,
- konflikt interesów recenzenta (etap regionalny) porównuje kraje,
- nazwę kraju i kolejność poprawia się w ekranie **„Regiony”** (`/coordinator/regions/`) — ponowne
  uruchomienie komendy nie nadpisze poprawek.

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

**Paczki ZIP z wyborem zakresu.** W konkursie, który zbiera zaświadczenia o statusie ucznia (§ 10a),
każdy przycisk pobrania prac — karta etapu na pulpicie, **„Pobierz prace (ZIP)”** na ekranie przydziałów,
paczka zadania (tabela reguł i karta zadania) oraz **„Pobierz zaznaczone (ZIP)”** — ma obok listę
wyboru: **„wszystkie prace”** (domyślnie, paczka jak dotąd) albo **„tylko uczniowie z potwierdzonym
statusem ucznia”** (w paczce zostają wyłącznie prace osób z **zaakceptowanym** zaświadczeniem w edycji
tego etapu; nazwa pliku dostaje dopisek `-status-potwierdzony`, a `README.txt` zdanie o filtrze).
Nazwy plików w paczce zostają anonimowe. Ten sam wybór ma komitet w swojej paczce „Pobierz moje prace
(ZIP)” (`PODRECZNIK-RECENZENTA.md` § 2). Bez włączonych zaświadczeń przyciski wyglądają jak zawsze.

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
   **Na własnym Jitsi olimpiady (`meet.<domena>`, od v0.39.0) linków się nie rozdaje**: Jitsi wpuszcza
   wyłącznie z przepustką wystawianą przez platformę w chwili kliknięcia. Uczestnik ma w panelu
   przyciski „Dołącz do rozmowy” (działa od 15 minut przed terminem do godziny po jego końcu)
   i „Sprawdź kamerę i mikrofon”; Ty w kolumnie „Link” masz **„dołącz jako gospodarz”** (wchodzisz
   z prawami moderatora — wyciszanie, poczekalnia, usuwanie z pokoju; uczestnik tych praw nie ma)
   i „test sprzętu”. **Adresu pokoju nie da się podyktować przez telefon** ani wysłać mailem — bez
   przepustki nie zadziała; uczestnik, który dzwoni, że nie może wejść, ma zalogować się i kliknąć
   przycisk w panelu. Link wpisany ręcznie przy terminie, prowadzący gdzie indziej (np. BBB uczelni),
   działa jak dawniej.
   **Rozmowy prowadzi komisja.** Każdy aktywny recenzent i członek komisji odwoławczej tego konkursu ma
   w swoim panelu kartę „Rozmowy kwalifikacyjne” (terminy z zapisami na dziś i dwa tygodnie naprzód,
   uczestnicy jako imię i inicjał) z przyciskiem „Dołącz jako gospodarz” — w tym samym oknie czasowym
   i z tymi samymi prawami, co Ty. Ty nadal możesz wejść do każdego pokoju z ekranu terminów. Komisja
   nie widzi tu surowych adresów pokoi spoza serwera olimpiady — te przekazujesz jej sam, jak dotąd.
   **Rozmowy w LiveKit (opcjonalnie).** W ustawieniach etapu („Dostawca wideo”) możesz zamiast Jitsi wybrać
   **„LiveKit (pokój na platformie)”** – opcja jest widoczna, gdy operator uruchomił serwer LiveKit (ten
   sam, co webinary). Wszystko inne zostaje: te same przyciski, te same okna czasowe i **te same
   uprawnienia** (komisja i koordynator jako gospodarze, uczestnik bez praw moderatora). Pokój otwiera się
   na stronie olimpiady; gospodarz ma przy osobach w pokoju „Odbierz głos” i „Usuń z pokoju”. Rozmowę
   w LiveKit możesz dodatkowo objąć **nadzorem zdalnym** (§ 10m) – uczeń wchodzi wtedy na rozmowę
   dopiero po zgodzie i sprawdzeniu sprzętu w konsoli nadzoru.
5. Tabela terminów pokazuje **dane osobowe** zapisanych (kod, imię i nazwisko, e-mail) — to obok podglądu
   wyników jedyny taki ekran w serwisie, stąd odznaka „dane osobowe”.
6. **Termin da się usunąć tylko dopóki nikt się na niego nie zapisał.**

Uczestnik zapisuje się z `/me/`, ma w etapie **jeden** termin, a „Zmień na ten termin” przenosi zapis
w jednej transakcji. Dzień wcześniej idzie automatyczne przypomnienie z linkiem i linkiem testowym
(na własnym Jitsi: z adresem przycisków w panelu — nigdy z adresem pokoju ani przepustką).

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

### 4.10 Plakaty do pobrania — `/coordinator/posters/`

Ekran **„Plakaty do pobrania”** (menu: Ustawienia → Plakaty do pobrania, zaraz pod sliderem
sponsorów). Plakaty i ulotki olimpiady, które nauczyciele i uczniowie pobierają **bez logowania**
ze strony **`/plakaty/`** — siatka kart z podglądem, tytułem, opisem, formatem i rozmiarem pliku
oraz przyciskiem „Pobierz”.

**Gdzie to widać.** Odnośnik „Plakaty do pobrania” pojawia się w **stopce każdej strony** i jako
przycisk „Plakaty do powieszenia w szkole” w **panelu opiekuna szkolnego** (`/supervisor/`) —
wyłącznie wtedy, gdy opublikowany jest choć jeden plakat. Bez opublikowanych plakatów strona
`/plakaty/` odpowiada „nie znaleziono”, a odnośników nie ma nigdzie. Zmiana (publikacja, zdjęcie,
nowy tytuł) widać w serwisie od razu — bez czekania na odświeżenie pamięci stron.

**Dodanie plakatu** — „Dodaj plakat”:

| Pole | Znaczenie |
|---|---|
| Tytuł | nagłówek karty, np. „Plakat olimpiady 2026/2027”. Z tytułu powstaje też nazwa zapisanego pliku (`plakat-olimpiady-2026-2027.pdf`) |
| Opis | jedna linia pod tytułem: format i przeznaczenie, np. „A4 pionowy”, „A3 do gabloty” |
| Karta (grupa plików) | opcjonalnie. Pliki z **identycznym** napisem (np. „A3 · 297×420 mm”) stają na stronie na **jednej karcie** z przyciskiem na każdy plik — patrz „Kilka plików na jednej karcie” niżej. Pole podpowiada karty, które już masz; puste = plik na własnej karcie |
| Napis na przycisku | tylko dla pliku z kartą, np. „PDF ze spadem 3 mm”. Puste = sam format pliku („JPG”, „PNG”, „PDF”) |
| Plik plakatu | **PDF, JPG albo PNG, najwyżej 50 MB**. Format rozpoznajemy po zawartości pliku, nie po rozszerzeniu — plik, który tylko udaje PDF (np. strona HTML przemianowana na `.pdf`), zostanie odrzucony |
| Własny podgląd | opcjonalny obrazek JPG/PNG (do 5 MB) na kartę. Dla plakatu JPG/PNG **podgląd powstaje sam** z pliku; dla PDF-a bez własnego podglądu karta pokazuje ikonę dokumentu |
| Opublikowany | bez zaznaczenia plakat jest **szkicem** — widać go tylko w panelu |

Na liście plakatów w każdym wierszu: strzałki **↑ ↓** (kolejność na stronie `/plakaty/`), **Zmień**,
**Opublikuj / Zdejmij** i **Usuń**. Plik szkicu możesz pobrać z ekranu edycji („pobierz, żeby
sprawdzić”) — to pobranie nie liczy się do statystyk. Podmiana pliku w edycji zachowuje statystyki
plakatu; stary plik jest usuwany.

**Kilka plików na jednej karcie.** Ten sam plakat w kilku plikach (JPG do podglądu, PDF do druku,
PDF ze spadem dla drukarni) nie musi stać na stronie jako kilka prawie identycznych kart. Wpisz
każdemu z tych plików tę samą **Kartę**, np. „A3 · 297×420 mm”, a na stronie powstanie jedna karta:

```
A3 · 297×420 mm
[podgląd]
[Pobierz JPG · 1,7 MB] [Pobierz PDF · 2,6 MB] [Pobierz PDF ze spadem 3 mm · 2,0 MB]
```

- **Nagłówek** karty to napis z pola „Karta”; tytułu pliku na stronie wtedy nie widać — nadal
  nazywa pobrany plik i wiersz w statystykach, więc warto, żeby mówił, co to za plik („A3 (PDF ze
  spadem)”).
- **Podgląd** to podgląd pierwszego pliku karty, który go ma (zwykle JPG/PNG — PDF bez własnego
  podglądu nie zabiera karcie obrazka). **Opis** — pierwszy niepusty opis w karcie.
- **Kolejność przycisków** to kolejność plików na liście (strzałki ↑ ↓), a **miejsce karty** na
  stronie to miejsce jej pierwszego pliku.
- Na karcie są wyłącznie **opublikowane** pliki — szkic z tą samą kartą nie pokaże się, dopóki go
  nie opublikujesz.
- Napis karty musi być **identyczny** (także spacje i znaki „·”, „×”), dlatego pole podpowiada karty,
  które już istnieją — wybierz z listy zamiast przepisywać.

Statystyki zostają **per plik**: w tabeli każdy plik ma swój wiersz (pod tytułem linia „Karta: … ·
przycisk „…””), a każdy przycisk na karcie prowadzi do pobrania swojego pliku. Eksport CSV ma
kolumny „karta (grupa)” i „przycisk”.

**Usuń a statystyki.** Plakat, którego **nikt jeszcze nie pobrał**, jest usuwany razem z plikiem.
Plakat, który **ma już pobrania**, trafia do **Archiwum** (sekcja pod listą): znika ze strony, ale
jego liczby zostają w tabeli, w sumie i w eksporcie. „Przywróć” wraca go na listę jako szkic.

**Statystyki — dwie liczby wszędzie.** Tabela pod listą pokazuje dla każdego plakatu i w wierszu
„Razem” **pobrania** oraz **unikalne adresy IP** w trzech okresach: **ostatnie 7 dni**, **ostatnie
30 dni** i **od początku**. Nad tabelą stoją cztery kafelki z sumami, a pod nią **wykres dzień po
dniu** za ostatnie 30 dni (oba szeregi; czipy nad wykresem zawężają go do jednego plakatu).

- **Pobrania** — każde pobranie pliku przez przeglądarkę. **Nie liczymy**: robotów wyszukiwarek,
  podglądów linków w komunikatorach (Messenger, WhatsApp, Slack…), narzędzi typu `curl`, zapytań
  sprawdzających (HEAD) ani pobrań przez koordynatora konkursu. Podwójne kliknięcie (ten sam plakat
  z tego samego adresu w ciągu 10 sekund) to jedno pobranie, a jeden adres może pobrać najwyżej
  30 plików na minutę – nadmiar dostaje komunikat „Zbyt wiele prób” i nie trafia do statystyk.
- **Unikalne IP** — liczba **różnych adresów IP w całym okresie kolumny** (nie w ciągu doby). W
  wierszu „Razem” adres, z którego pobrano dwa różne plakaty, liczy się **raz** — dlatego ta liczba
  bywa mniejsza niż suma kolumny. Cała szkoła za jednym routerem to zwykle jeden adres; nauczyciel,
  który pobrał plakat w szkole i w domu — dwa.
- **Eksport statystyk (CSV)** oddaje tę samą tabelę (średnik, polskie znaki poprawnie w Excelu):
  dla każdego okresu para kolumn „pobrania / unikalne IP” i wiersz „RAZEM” na końcu.

**RODO — co zapisujemy.** Adresu IP **nie zapisujemy**. Przy pobraniu zostaje wyłącznie jego
**pseudonim** (skrót HMAC-SHA256 z kluczem serwera), data i wskazanie pliku — bez nagłówka
przeglądarki i bez konta. Pseudonim jest **automatycznie kasowany po 12 miesiącach** (zadanie
nocne); samo pobranie zostaje w liczbie pobrań. Skutek: kolumna „od początku · unikalne IP”
obejmuje ostatnie 12 miesięcy, a „od początku · pobrania” — pełną historię. Czynność opisuje
rejestr czynności przetwarzania (§ 9.2, wiersz „Statystyka pobrań materiałów promocyjnych”;
podstawa: prawnie uzasadniony interes, art. 6 ust. 1 lit. f RODO).

Każda zmiana zostawia wpis w audycie (`promo.created`, `promo.updated`, `promo.published`,
`promo.unpublished`, `promo.reordered`, `promo.archived`, `promo.deleted`, `promo.restored`; eksport —
`export.generated`) z numerem plakatu i nazwami zmienionych pól, bez tytułu i nazwy pliku.

### 4.11 Materiały z warsztatów — `/coordinator/workshops/materials/`

Ekran **„Materiały z warsztatów”** (menu: Raporty → Materiały z warsztatów, zaraz pod „Obecnością na
warsztatach”; przycisk jest też na ekranie obecności). Nagrania zajęć, slajdy, notatniki i odnośniki,
które **ogląda się wyłącznie po zalogowaniu** na stronie **`/warsztaty/materialy/`**. Funkcja jest za
przełącznikiem konkursu `workshop_materials` — dopóki operator go nie włączy, ekranu, pozycji w menu
i strony dla uczestników nie ma.

**Gdzie uczestnik je znajdzie.** Odnośnik „Materiały z warsztatów” w pasku konta (każda zalogowana
osoba z rolą w konkursie), kafel na pulpicie uczestnika i ramka na stronie `/warsztaty/` — odnośnik
i kafel pojawiają się, gdy opublikujesz pierwszy materiał (i znikają, gdy zdejmiesz ostatni).

**Kto widzi materiały.** Każde zalogowane konto, które **w tym konkursie** jest uczestnikiem, opiekunem
szkolnym, recenzentem, członkiem komisji odwoławczej albo koordynatorem. Konto spoza konkursu (np.
uczestnik innej olimpiady na tej samej platformie) dostaje „brak dostępu”. Gość na stronie
`/warsztaty/` widzi tylko ramkę „Materiały z warsztatów — zaloguj się, aby obejrzeć” z liczbą
materiałów, bez tytułów i bez żadnego adresu pliku.

**Materiał przypina się do warsztatu z harmonogramu.** Ekran pokazuje każdy wiersz tabeli
„harmonogram” ze strony `/warsztaty/` (ten sam, z którego biorą się kolumny obecności) — także te bez
materiałów, bo przy nich jest przycisk **„Dodaj materiał do tego warsztatu”**. Wiersz bez wypełnionego
pola „termin (data)” nie ma tu miejsca — uzupełnij datę w `/cms/`.

**Dodanie materiału** — „Dodaj materiał”:

| Pole | Znaczenie |
|---|---|
| Warsztat | wiersz harmonogramu |
| Rodzaj | **film**, **plik** albo **odnośnik** |
| Tytuł, opis | nagłówek karty i kilka zdań pod nim (np. „od 12. minuty zadanie 3”). Z tytułu powstaje nazwa pobranego pliku |
| Plik | **film:** MP4 (H.264 + AAC) albo WebM, najwyżej **4 GB**. **Plik:** PDF, PPTX, DOCX, XLSX, ODP, ODT, ODS, ZIP, IPYNB, PNG, JPG — najwyżej **100 MB** |
| Adres odnośnika | tylko dla rodzaju „odnośnik”, np. nagranie niepubliczne w serwisie wideo; musi zaczynać się od `https://` |
| Opublikowany | bez zaznaczenia materiał jest **szkicem** — widzisz go tylko tutaj |

**Jak idzie wgrywanie filmu.** Po kliknięciu „Zapisz materiał” pod formularzem pojawia się **pasek
postępu**. Plik idzie z Twojej przeglądarki **prosto do magazynu plików**, kawałkami po 16 MB (serwer
tylko podpisuje kolejne kawałki), więc duży film nie obciąża serwisu, a chwilowe zerwanie połączenia
nie przerywa całości — kawałek jest wysyłany ponownie. **Nie zamykaj karty**, dopóki pasek nie dojdzie
do końca (przeglądarka ostrzeże przed zamknięciem). Na końcu serwer składa plik i sprawdza, czy to
naprawdę MP4/WebM; potem wracasz na listę. Godzinne nagranie z platformy wideo (ok. 0,5–1 GB) przy
łączu 20 Mb/s wgrywa się kilka minut. „Przerwij wgrywanie” kasuje wszystko, co już dotarło; wgrywanie
porzucone (zamknięta karta) znika samo po dobie. Wgrywanie wymaga włączonego JavaScriptu.

- **Format sprawdzamy po treści**, nie po rozszerzeniu. Plik **MOV** (QuickTime, np. z telefonu albo
  Maca) i **MKV** (np. z OBS-a) zostaną odrzucone z podpowiedzią, jak je przepakować do MP4 — część
  przeglądarek ich nie odtworzy. Najpewniejszy format: **MP4, wideo H.264, dźwięk AAC** (tak zapisują
  Zoom, Teams i Google Meet). Nagranie, które już jest takim MP4, nie wymaga żadnej obróbki.
- Film w H.265/HEVC przejdzie sprawdzenie (to też MP4), ale **nie odtworzy się w części przeglądarek**
  (m.in. Firefox) — jeśli masz wybór, eksportuj H.264.
- Serwis **nie przerabia** filmów (nie zmniejsza rozdzielczości, nie tnie) — oglądający dostaje dokładnie
  ten plik, który wgrałeś. Na zajęcia z ekranem prowadzącego wystarczy 720p, a plik jest wtedy kilka
  razy mniejszy.

**Pliki (PDF, prezentacje…)** przechodzą po wgraniu jeszcze **sprawdzenie antywirusowe** (zwykle
kilkanaście sekund; stan „sprawdzanie antywirusowe” na liście, odśwież stronę). Dopiero potem są widoczne.
Plik z wykrytym zagrożeniem jest **kasowany** i zostaje na liście jako „odrzucony” z nazwą zagrożenia.
Jeśli plik długo stoi w „sprawdzaniu”, użyj „Sprawdź ponownie”. **Filmy nie idą przez antywirusa**: są
za duże dla skanera, a skaner nie ma w nich czego szukać — bramką jest sprawdzenie, że to naprawdę film
MP4/WebM.

Na liście w każdym wierszu: strzałki **↑ ↓** (kolejność w obrębie warsztatu), **Podgląd** (otwiera
materiał także jako szkic; nie liczy się do statystyk), **Zmień** (tytuł, opis, warsztat, publikacja —
**pliku nie podmienisz**: dodaj nowy materiał i usuń stary), **Opublikuj / Zdejmij** i **Usuń** (kasuje
materiał razem z plikiem, nieodwracalnie). Materiał oznaczony jako opublikowany, który jeszcze się
sprawdza, pojawi się u widzów sam, gdy skaner go przepuści.

**Zmieniłeś temat albo datę warsztatu w harmonogramie?** Materiał jest przypięty do warsztatu kluczem
złożonym z **daty i tematu** — tym samym, co obecność. Po poprawce tematu (nawet literówki) albo daty
materiały „tracą” warsztat i trafiają na dół ekranu do sekcji **„Materiały bez warsztatu
w harmonogramie”**. Nic nie znika: uczestnicy nadal je widzą, pod dawnym tematem i datą. W sekcji
wybierz właściwy warsztat z listy (jeśli w harmonogramie jest dokładnie jeden warsztat z tą samą datą,
jest już wybrany) i kliknij **„Przepnij”** — cała grupa przechodzi naraz. Serwis nie przepina sam, bo
„ten sam dzień” nie zawsze znaczy „te same zajęcia”.

**Statystyki.** Przy każdym materiale: **Wyświetlenia** (otwarcie odtwarzacza, pobranie pliku, przejście
pod odnośnik) i **Widzowie** (ile różnych kont). Twoich wyświetleń nie liczymy. **Kto** oglądał — tego
serwis nie wie i nie pokaże: zapisujemy wyłącznie pseudonim (skrót HMAC pary „materiał–konto”, inny dla
każdego materiału), kasowany po 12 miesiącach. Obecność na zajęciach dalej odhacza się na ekranie
obecności — obejrzenie nagrania nie jest obecnością. Czynność opisuje rejestr czynności przetwarzania
(§ 9.2, wiersz „Statystyka wyświetleń materiałów z warsztatów” – tylko w konkursie z włączoną funkcją).

**Czego ta funkcja nie gwarantuje.** Film ogląda się w odtwarzaczu na stronie, adres pliku jest ważny
**2 godziny** i nie ma go nigdzie jako linku do skopiowania, a przycisk „Pobierz” w odtwarzaczu jest
wyłączony. To utrudnia rozsyłanie nagrania, ale **nie uniemożliwia** jego zapisania: zalogowana osoba,
która się uprze, wyciągnie adres z narzędzi przeglądarki albo nagra ekran. Jeżeli nagranie nie może
wyjść poza uczestników pod żadnym pozorem (np. wizerunek osób bez zgody na udostępnienie), nie wgrywaj
go. Odnośnik (rodzaj „odnośnik”) chroni jeszcze mniej: serwis wymaga logowania, żeby go **zobaczyć**,
ale sam adres działa dla każdego, kto go dostanie.

**Kopia zapasowa.** Materiały z warsztatów **nie wchodzą do nocnej kopii zapasowej** (pojedyncze
nagranie to gigabajty). Zachowaj oryginały u siebie — po awarii serwera trzeba je będzie wgrać ponownie.

Każda zmiana zostawia wpis w audycie (`workshop_material.created`, `.upload_started`, `.uploaded`,
`.upload_rejected`, `.updated`, `.attached`, `.published`, `.unpublished`, `.reordered`, `.deleted`)
z numerem materiału, rodzajem, formatem i rozmiarem — bez tytułu, opisu i nazwy pliku.
### 4.12 Ocena AI — `/coordinator/ai-grading/`

Prośby organizatora z 24.09.2026. Model językowy wybranego dostawcy — **Anthropic** (Claude),
**OpenAI** (GPT), **Google** (Gemini) albo **Meta** (Muse Spark) — czyta pracę uczestnika obok treści
zadania, rozwiązania wzorcowego, skali, rubryki i uwag dla recenzentów, a potem proponuje punkty
z krótkim uzasadnieniem. **To jest sugestia dla recenzenta, a nie ocena**: sama nigdy nie trafia do
punktacji, do tabeli wyników ani do dyplomu. Ocenę wystawia człowiek, tak jak dotąd.

**Kiedy ekran istnieje.** Funkcja jest za przełącznikiem konkursu `ai_grading`, **domyślnie wyłączonym**
— bez niego adresu nie ma (404), w menu nie ma pozycji „Ocena AI”, a karty zadań, panel recenzenta
i panel uczestnika wyglądają jak dotąd. Przełącznik zapala operator platformy (`OPERACJE.md` § 6.4).
Wszyscy czterej dostawcy są dostępni od razu (o ile serwer ma ich pakiety SDK); żaden dostawca nie
dostaje jednak **ani jednej pracy uczestnika**, dopóki nie ma **klucza API** i **potwierdzonej umowy
powierzenia** (niżej).

> **Zanim potwierdzisz umowę z dostawcą — warunki prawne (do rozstrzygnięcia przez organizatora, nie
> przez system).** Każdy dostawca to **osobny podmiot przetwarzający**, a prace uczestników — w
> większości osób niepełnoletnich — trafiają do niego i (w całości albo częściowo) poza EOG.
> Potwierdzenie w panelu jest oświadczeniem organizatora, że dla **tego** dostawcy:
>
> 1. zawarta jest **umowa powierzenia (DPA)** — z organizacji/konta założonego **przez organizatora**,
>    a nie prywatnie przez koordynatora,
> 2. jest **podstawa przekazania poza EOG** (rozdział V RODO) — mechanizm wskazany w DPA (standardowe
>    klauzule umowne albo decyzja stwierdzająca odpowiedni stopień ochrony),
> 3. sprawdzony jest **okres przechowywania** danych wejściowych i wyjściowych API u dostawcy (także na
>    potrzeby wykrywania nadużyć) i to, że dostawca **nie uczy modeli** na danych z API; w razie
>    potrzeby — złożony wniosek o **brak retencji** (*zero data retention*),
> 4. **polityka prywatności** (art. 13 RODO) wymienia tego dostawcę jako odbiorcę, cel pomocniczy
>    (sugestia oceny dla komitetu), przekazanie poza EOG i to, że decyzja o ocenie **nie** zapada w
>    sposób zautomatyzowany (art. 22) — ocenia człowiek,
> 5. **regulamin** mówi, że komitet może korzystać z narzędzi AI jako pomocy przy ocenianiu, wiążąca
>    jest wyłącznie ocena komitetu, reklamacja dotyczy oceny oficjalnej, i prosi, żeby **nie podpisywać
>    prac** imieniem i nazwiskiem (plik idzie do dostawcy taki, jaki wgrał uczestnik),
> 6. potwierdzona jest **podstawa prawna** — rejestr czynności proponuje prawnie uzasadniony interes
>    (art. 6 ust. 1 lit. f); administrator ma ją zatwierdzić (albo wybrać inną) po teście równowagi
>    interesów, także z uwagi na wiek uczestników,
> 7. warunki dostawcy **dopuszczają** takie użycie (niżej: ograniczenia wieku u Google i Mety).
>
> Co sprawdzić u poszczególnych dostawców (stan stron z 24.09.2026 — **pozycje do weryfikacji przez
> organizatora/IOD**, nie rozstrzygnięcia systemu):
>
> | Dostawca | Umowa i DPA | Retencja i trenowanie | Do sprawdzenia szczególnie |
> |---|---|---|---|
> | **Anthropic** | [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) włączają [DPA](https://www.anthropic.com/legal/data-processing-addendum) | wg [centrum prywatności](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) wejścia i wyjścia API kasowane do 30 dni (dłużej przy naruszeniu zasad — do 2 lat); Commercial Terms: bez trenowania na treściach klienta; brak retencji — po osobnym uzgodnieniu | umowa zawarta na organizację organizatora |
> | **OpenAI** | [Services Agreement](https://cdn.openai.com/osa/openai-services-agreement.pdf) i [DPA](https://cdn.openai.com/pdf/openai-data-processing-addendum.pdf) (klienci z EOG — OpenAI Ireland Ltd.; przekazanie: SCC albo decyzja o adekwatności); [podwykonawcy](https://openai.com/policies/sub-processor-list/) | [Your data](https://developers.openai.com/api/docs/guides/your-data): dane API nie służą do trenowania (bez zgody), logi nadużyć do 30 dni; brak retencji (ZDR) i przetwarzanie w EOG (`eu.api.openai.com`) — po zgodzie OpenAI | serwis wysyła `store: false`; pliki i obrazy są skanowane pod kątem CSAM (oznaczone zostają mimo ZDR); regionalne przetwarzanie +10% ceny |
> | **Google** | [Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) + [Cloud Data Processing Addendum](https://business.safety.google/processorterms/) (obejmuje „Gemini API Paid Services” — [lista](https://business.safety.google/services/)) | [zasady użycia](https://ai.google.dev/gemini-api/docs/usage-policies): prompty i odpowiedzi przechowywane 55 dni na potrzeby wykrywania nadużyć; [brak retencji](https://ai.google.dev/gemini-api/docs/zdr) w Gemini API ograniczony (pełny — Vertex AI); dla użytkowników z EOG obowiązują warunki danych warstwy płatnej | **warunki Gemini API wymagają ukończonych 18 lat i zakazują usług „skierowanych do” osób poniżej 18 lat** — do oceny prawnika (konto i użytkownicy narzędzia to dorośli z komitetu, ale serwis jest dla uczniów); Polska na [liście regionów](https://ai.google.dev/gemini-api/docs/available-regions) |
> | **Meta** | [Meta Model API Terms](https://dev.meta.ai/legal/terms-of-service) (EOG — Meta Platforms Ireland Ltd.) włączają [Meta Global Processor Terms](https://www.facebook.com/legal/terms/Meta-Global-Processor-Terms) i [Data Security Terms](https://www.facebook.com/legal/terms/data_security_terms) | warstwa standardowa: Meta jako podmiot przetwarzający, bez trenowania; retencja „tyle, ile potrzeba” (bez okresu); [brak retencji](https://dev.meta.ai/help/policies-and-privacy/zero-data-retention) tylko dla kwalifikowanych kont, a treści oznaczone jako naruszenie do 2 lat | **§ 10.1 warunków: użytkownicy końcowi 18+ i zakaz produktów kierowanych do osób poniżej 18 lat** — do oceny prawnika; usługa w wersji zapoznawczej; [polityka geograficzna](https://dev.meta.ai/legal/geographic-use-policy) nie publikuje listy krajów — dostępność w Polsce sprawdzić kluczem; warstwę `-contributor` (Meta może uczyć na danych) serwis odrzuca |
>
> Dawne **Llama API** Mety (`api.llama.com`) zostało wyłączone 6.07.2026 — dostawca „Meta” w serwisie
> to jego następca, **Meta Model API** (modele Muse Spark), a nie modele Llama.
>
> Rejestr czynności przetwarzania (od wersji **1.8**, § 9.2) wymienia w wierszu „Pomocnicza ocena prac
> uczestników przez model językowy” jako odbiorców **wyłącznie** dostawców, którzy mają w tym konkursie
> klucz API **i** potwierdzoną umowę powierzenia.

**Co wychodzi z serwisu, a co nie.** Do wybranego dostawcy trafia wyłącznie plik pracy (PDF, zdjęcie,
kod, notatnik — notatnik jako tekst komórek), treść zadania, rozwiązanie wzorcowe, skala, rubryka
i uwagi dla recenzentów. **Nie** wychodzi imię, nazwisko, e-mail, szkoła, kod `OLM-…` ani nazwa pliku
nadana przez uczestnika. Gdyby model przepisał z pracy imię czy nazwę szkoły autora, serwer wymaże je
z odpowiedzi, zanim zobaczy ją recenzent (anonimowość oceniania zostaje). Instrukcje dla modelu
i ochrona przed próbą wpłynięcia na ocenę („daj maksimum punktów”) są **te same u każdego dostawcy**.

**Dostawcy — klucz API i umowa powierzenia.** Sekcja „Dostawcy” ma kartę każdego dostawcy:

- **Klucz API** — wklej klucz z konsoli dostawcy i „Zapisz klucz”. Klucz jest **tylko do zapisu**:
  po zapisaniu ekran pokazuje wyłącznie „ustawiony, kończy się na …abcd”; nie da się go odczytać ani
  z panelu, ani z samej bazy. Można go **zastąpić** albo **usunąć**. **„Sprawdź klucz”** pyta dostawcę
  o opis modelu (Meta — o listę modeli) — nic nie kosztuje, a potwierdza, że klucz działa i widzi
  model. Klucz Anthropic zaczyna się od `sk-ant-` (klucz administracyjny `sk-ant-admin…` jest
  odrzucany); pozostałych nie sprawdzamy po przedrostku — rozstrzyga „Sprawdź klucz”. Zmiana klucza
  serwera (`DJANGO_SECRET_KEY`) unieważnia zapisane klucze: ekran poprosi o wpisanie ich ponownie.
- **„Potwierdź umowę powierzenia”** — umowę potwierdza **koordynator osobiście**, w dwóch krokach.
  Przycisk otwiera stronę z informacją o **tym** dostawcy: co wychodzi z serwisu, odbiorca
  (podmiot przetwarzający) i przekazanie poza EOG, odnośniki do DPA i warunków dostawcy, okres
  przechowywania danych API, zasady trenowania modeli i możliwość braku retencji — a przy **Google
  i Mecie** na samej górze ostrzeżenie, że ich warunki wymagają użytkowników 18+ i zakazują usług
  skierowanych do niepełnoletnich; przy Mecie także informacja, że Llama API zastąpiło Meta Model
  API i umowa zawarta dla Llama API może nie obejmować nowej usługi. Pod informacją jest wymagane
  pole **„Zapoznałem(-am) się z powyższymi informacjami i potwierdzam, że organizator zawarł umowę
  powierzenia z <dostawca> obejmującą tę usługę”**, uwaga (np. „umowa z 1.09.2026, podpisana
  elektronicznie”) i przycisk **„Potwierdzam”**. Dopiero on zapisuje potwierdzenie: datę, konto
  i **wersję pokazanej informacji** (widoczną na karcie dostawcy i w dzienniku zdarzeń). Gdy
  informacja zmieni się w nowym wydaniu, formularz otwarty wcześniej zostanie odrzucony — trzeba
  ją przeczytać ponownie. Potwierdzenie można **wycofać** jednym kliknięciem (z pytaniem
  „Wycofać…?”) — prace czekające w kolejce do tego dostawcy skończą się wtedy błędem, **zanim**
  zostaną wysłane. Komenda operatora (`OPERACJE.md` § 17.6) istnieje wyłącznie na sytuacje
  wyjątkowe; zwykłą drogą jest potwierdzenie koordynatora.
- Plakietka przy dostawcy: **gotowy do ocen prac** (klucz + umowa), **tylko prace testowe** (klucz, bez
  umowy), **brak klucza**, **niedostępny** (serwer nie ma pakietu SDK — sprawa dla operatora).

**Dostawca i model domyślny.** Sekcja „Dostawca i model domyślny” — lista modeli każdego dostawcy
(stan z 24.09.2026) i pole **„Inny identyfikator modelu”** z wyborem dostawcy: identyfikatory modeli
zmieniają się szybciej niż wydania serwisu, więc nowy model wpisuje się z dokumentacji dostawcy.
Domyślny jest wyłącznie **wstępnie zaznaczony** przy zleceniu. Modele z listy:

| Dostawca | Modele (pierwszy — domyślny) |
|---|---|
| Anthropic | `claude-opus-5` (dokładniejszy), `claude-sonnet-5` (tańszy) |
| OpenAI | `gpt-6-astra` (najdokładniejszy), `gpt-6-sol`, `gpt-6-luna` (najtańszy) |
| Google | `gemini-3.8-flash` (stabilny), `gemini-3.1-pro-preview` (wersja zapoznawcza), `gemini-3.5-flash-lite` (najtańszy) |
| Meta | `muse-spark-1.3`, `muse-spark-1.2` (ta sama cena; tańszej warstwy standardowej nie ma) |

Ograniczenia plików zależą od dostawcy i są sprawdzane **przed** wysyłką (praca nie jest obcinana —
dostaje błąd z nazwą limitu): Anthropic — 32 MB żądania, 600 stron PDF, zdjęcie do 5 MB; OpenAI —
pliki żądania razem do 50 MB; Google — **20 MB** całego żądania, 1000 stron; Meta — pliki do 50 MB,
**PDF najwyżej 50 stron** (dłuższy Meta czytałaby tylko częściowo, więc go odrzucamy).

**Ceny modeli i limit wydatków.** Tabela **„Ceny modeli”** ma stawki w USD za milion tokenów wejścia
i wyjścia, wypełnione z cenników dostawców z 24.09.2026 i **do poprawienia** przez koordynatora (np.
stawki `gemini-3.8-flash` rosną 1.01.2027). Ostatni wiersz dodaje cenę modelu spoza listy. Pusta cena
znaczy **koszt nieznany**: taki model liczy się wyłącznie w tokenach, ekran pisze „koszt nieznany”,
a sekcja „Zużycie” pokazuje liczbę wywołań bez ceny. **Limit wydatków (USD)** jest bezpiecznikiem: po
jego osiągnięciu nowe zlecenia są odrzucane, a oceny czekające w kolejce kończą się błędem zamiast
wołać API. Limit liczy tylko to, co ma cenę — dlatego **przy ustawionym limicie model bez ceny jest
niedostępny** (wpisz jego cenę albo zdejmij limit). Zalecamy ustawić limit przed pierwszym zleceniem.
Rozliczenie wystawia dostawca — jego faktura jest prawdą, a liczby w panelu są szacunkiem.

**Zlecenie.** Na **karcie zadania** (`/coordinator/problems/<id>/`) jest sekcja **„Ocena AI”**:

- **„Wygeneruj ocenę AI”** z wyborem **dostawcy i modelu** (lista zawiera wyłącznie dostawców z kluczem
  i potwierdzoną umową) — dla wszystkich najnowszych wersji prac zadania, które nie mają jeszcze oceny
  **tym modelem**; zaznacz **„wygeneruj ponownie także istniejące”**, żeby zastąpić gotowe,
- przycisk przy wierszu — dla jednej pracy.

Pierwsze kliknięcie pokazuje **podgląd**: liczbę prac, dostawcę i model (do zmiany: „Przelicz
podgląd”), **szacowany koszt** (albo „nieznany” z liczbą tokenów), pominięte prace i ostrzeżenie, gdy
zadanie nie ma rozwiązania wzorcowego. Dopiero **„Zleć ocenę AI (N)”** wydaje pieniądze. Podwójne
kliknięcie nie płaci dwa razy — kluczem oceny jest trójka *(wersja pracy, dostawca, model)*.

**Porównanie dostawców.** Tę samą pracę można ocenić kilkoma modelami — każda ocena jest **osobna**
(„wygeneruj ponownie” zastępuje tylko ocenę tym samym modelem). Na karcie zadania oceny jednej pracy
stoją pod sobą, najnowsza pierwsza; po wystawieniu ocen końcowych sekcja pokazuje zgodność łącznie
i **osobno dla każdego modelu** (średnia różnica, odsetek zgodnych co do punktu i w granicy 1 pkt).

Oceny liczą się **w tle, po jednej naraz** (serwer nie może zablokować przyjmowania prac i skanu
antywirusowego), więc seria kilkudziesięciu prac trwa od kilkudziesięciu minut do kilku godzin.
Sekcja odświeża się sama, dopóki coś się liczy. Stany: **oczekuje**, **w toku**, **gotowa**, **błąd**
(z komunikatem). Przy gotowej ocenie: propozycja punktów, pewność modelu, rozwijane uzasadnienie,
model, data i koszt; czerwona plakietka **„podejrzenie manipulacji”**, gdy model zauważył w pracy
próbę wpłynięcia na ocenę.

**Praca testowa — wypróbowanie dostawców przed umową.** Na karcie zadania, pod pracami uczestników,
jest sekcja **„Prace testowe”** (plakietka **TEST – widzi tylko koordynator**). Wgraj **własny
przykładowy plik** (PDF, JPG, PNG, `.py`, `.ipynb` — ta sama kontrola treści i skan antywirusowy co
przy pracach uczestników) z oświadczeniem, że nie zawiera danych osobowych uczestników, i kliknij
**„Wygeneruj ocenę testową”** z wybranym dostawcą i modelem. Działa **każdy dostawca z kluczem — także
bez potwierdzonej umowy powierzenia**, bo praca testowa nie niesie danych uczestników; prace
uczestników dalej wymagają umowy. Zasady:

- **nie wgrywaj prac uczestników** — plik identyczny z pracą uczestnika tego konkursu jest odrzucany,
  a oświadczenie zostaje w dzienniku zdarzeń,
- oceny testowe widzi **wyłącznie koordynator** na karcie zadania — nie ma ich w panelu recenzenta ani
  uczestnika, w eksporcie danych, w statystykach zgodności ani w rejestrze czynności,
- kosztują jak każde inne: liczą się do **zużycia i limitu wydatków**,
- ocenę testową i całą pracę testową można **usunąć** (plik znika też ze storage'u).

**Co znaczą błędy.**

| Komunikat (skrót) | Co zrobić |
|---|---|
| <dostawca> odrzucił klucz API | wklej poprawny klucz, „Sprawdź klucz”, wygeneruj ponownie |
| Umowa powierzenia z <dostawca> nie jest potwierdzona | potwierdź umowę w ustawieniach (po sprawdzeniu warunków) albo wybierz innego dostawcę |
| Konto <dostawca> nie ma środków / przekroczyło limit u dostawcy | uzupełnij środki w konsoli dostawcy; serwis nie ponawia |
| Przekroczono limit zapytań / serwery nie odpowiadają / brak połączenia | serwis sam ponawia kilka razy; gdy ocena skończy się błędem — wygeneruj ponownie później |
| Model odmówił oceny / zablokował ją filtr bezpieczeństwa (kategoria: …) | oceń bez sugestii AI albo innym dostawcą (Anthropic próbuje sam modelu zastępczego) |
| Odpowiedź przekroczyła limit długości / nie pasuje do schematu | wygeneruj ponownie; gdy się powtarza — oceń bez sugestii |
| Materiały przekraczają limit <dostawca> (MB, strony, zdjęcie) | to limity dostawcy — praca nie zostanie obcięta; wybierz innego dostawcę albo oceń bez sugestii |
| Model … nie ma ceny, a ustawiony jest limit wydatków | wpisz cenę modelu w tabeli cen albo zdejmij limit |
| Dostawca niedostępny: brak pakietu na serwerze | sprawa dla operatora (`OPERACJE.md` § 17.2) |
| Praca nie ma pliku po czystym skanie | poczekaj na skan antywirusowy |
| Osiągnięto limit wydatków | podnieś albo zdejmij limit w ustawieniach |
| Ocena została przerwana | serwer zrestartował się w trakcie; wygeneruj ponownie |

**Recenzent** widzi gotowe sugestie przy **tej wersji pracy, którą ma przydzieloną** — każdą
w osobnym, zwiniętym panelu **„Ocena AI – <dostawca> <model> (sugestia, niewiążąca)”**, najnowszą
pierwszą. Formularz oceny nie wypełnia się sam; przycisk „Wstaw punkty AI jako punkt wyjścia” jedynie
zaznacza najbliższą wartość skali – a w etapie z dowolnymi wartościami ocen wpisuje do pola samą
propozycję, przyciętą do zakresu zadania i sprowadzoną do 0,01 (tak samo dla każdego dostawcy; przy
zadaniu z rubryką przycisku nie ma). Szczegóły: `PODRECZNIK-RECENZENTA.md` § 3a.

**Uczestnicy — domyślnie nie widzą niczego.** Sekcja „Widoczność dla uczestników” ma przy każdym
etapie bieżącej edycji przycisk **„Pokaż uczestnikom ocenę AI”** (domyślnie wyłączony). Po włączeniu
uczestnik zobaczy na stronie informacji zwrotnej **podsumowanie i proponowane punkty najnowszej
sugestii** (z nazwą dostawcy i modelu) — dopiero po **ogłoszeniu wyników** etapu, w osobnej sekcji
pod oficjalnymi ocenami, z podpisem „sugestia AI”. Listy błędów ani kryteriów uczestnik nie dostaje.
Przy wyłączonym przełączniku uczestnik nie dowiaduje się z panelu, tabeli wyników, dyplomów ani
reklamacji, że ocena AI powstała.

**Eksport danych uczestnika (art. 15/20 RODO).** Paczka `/account/export/` zawiera zawsze sekcję
`oceny_ai` z **faktem** przekazania pracy — **osobno dla każdego dostawcy**, do którego praca trafiła:
zadanie, wersja, data, dostawca, model i odbiorca (pełna nazwa podmiotu przetwarzającego) — bo
informacja o odbiorcach danych przysługuje osobie z art. 15 ust. 1 lit. c niezależnie od ustawień
ekranu. **Treść** sugestii jest w paczce tylko wtedy, gdy uczestnik widzi ją też w panelu. Gdyby
uczestnik zażądał formalnie dostępu do treści sugestii przed publikacją albo przy wyłączonym
przełączniku, rozstrzyga administrator (IOD) — treść jest dostępna koordynatorowi na karcie zadania.

**Usunięcie danych.** Anonimizacja konta uczestnika (na żądanie albo po upływie retencji edycji,
§ 9.1) **kasuje** oceny AI jego prac u wszystkich dostawców (po stronie serwisu) — w przeciwieństwie
do samej pracy i ocen komitetu nie są one dokumentacją zawodów. Liczniki kosztu w ustawieniach zostają.

**Audyt.** Zapis i usunięcie klucza (bez wartości, z nazwą dostawcy), sprawdzenie klucza,
potwierdzenie i wycofanie umowy powierzenia (kto, kiedy, uwaga, panel czy komenda), zmiana dostawcy,
modelu, cen i limitu, zmiana widoczności etapu, każde zlecenie (z dostawcą, modelem i liczbą prac)
oraz wgranie, ocena i usunięcie pracy testowej zostawiają wpis `ai_grading.*`.

---

## 5. Wyniki

### 5.1 Reklamacje — `/appeals/`

Po otwarciu okna reklamacji uczestnik składa reklamację na własną pracę z zakładki **„Reklamacje”**
w swoim panelu. Rozpatruje je **komisja odwoławcza** (osobna flaga na profilu członka komitetu) na
ekranie **„Reklamacje do rozpatrzenia”**: widzi „Argument uczestnika”, „Oceny rundy 1” i zapisuje decyzję
z uzasadnieniem („Zapisz decyzję”). Autorzy recenzji rundy 1 tej pracy nie rozstrzygają jej reklamacji.
Uczestnik dostaje list z rozstrzygnięciem i uzasadnieniem.

**Uczestnik nie widzi punktów w oknie reklamacji** (od v0.38.7 także w API aplikacji – decyzja
właściciela: oceny dopiero po ostatecznym zatwierdzeniu). Punkty, również te po reklamacji, pokazują
się dopiero po **„Opublikuj wyniki”**, a publikacja jest możliwa wyłącznie po zamknięciu okna. Kto
reklamuje, wie więc tylko, że praca jest oceniona – jeśli regulamin zakłada reklamację „od znanej
oceny”, trzeba to rozstrzygnąć osobno (np. przekazać punkty wstępne innym kanałem).

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
| imię i nazwisko awansujących, za zgodą | imię i nazwisko przy osobach, które awansowały (w finale — laureatach); reszta pod kodem | **każdy etap zawodów** (nie trening), **tylko za zgodą** na publikację nazwiska |
| imię i nazwisko wszystkich, za zgodą | imię i nazwisko przy każdym, kto wyraził zgodę, niezależnie od wyniku; reszta pod kodem | **każdy etap zawodów** (nie trening), **tylko za zgodą** na publikację nazwiska |

   Niepełnoletni uczestnik pojawia się z nazwiskiem tylko wtedy, gdy jest też **zgoda opiekuna**.
   Pole **„Opublikuj tylko listę awansujących”** (łączy się z każdym trybem) ogłasza samą listę osób
   zakwalifikowanych do następnego etapu — w finale listę laureatów. Pozostałych uczestników w
   ogłoszeniu nie ma wcale; swoje punkty nadal widzą w panelu.

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
| **Pokoje wideo** | `/coordinator/video-rooms/` | pokoje na Jitsi olimpiady poza terminami rozmów: zebrania komisji, konsultacje, goście bez konta (§ 6.4b) |
| **Webinary** | `/coordinator/webinars/` | spotkania z terminem w pokoju na platformie (LiveKit), z nagraniami i listą obecności (§ 10i) |
| **FAQ** | `/faq/` (redakcja w `/cms/`) | odpowiedzi, które mają wyprzedzić zgłoszenia |
| **Strona statusu** | `/status/` | „nie mogę wysłać pracy — to u was, czy u mnie?” |

### 6.1 Komunikaty — listy do grupy

Menu **Komunikacja → Komunikaty**. Ekran nie jest za żadną flagą — działa w każdym konkursie.

| Grupa odbiorców | Kto dostaje list | Trzeba wskazać |
|---|---|---|
| **wszyscy uczestnicy konkursu** (pierwsza na liście) | uczestnicy **bieżącej edycji**: zapisani do któregokolwiek jej etapu albo zarejestrowani w niej (konto założone po utworzeniu edycji), także jeszcze bez zapisu do etapu | — (opcjonalnie „także uczestnicy poprzednich edycji”) |
| uczestnicy bieżącej edycji (zapisani do etapu) | ktoś z wpisem do któregokolwiek etapu bieżącej edycji | — |
| zapisani do etapu | wpis do wskazanego etapu | etap |
| zakwalifikowani do etapu | wpis ze statusem „zakwalifikowany” | etap |
| **zapisani do etapu, bez wysłanej pracy** | wpis „zarejestrowany” albo „zakwalifikowany”, bez żadnej pracy w tym etapie (praca odrzucona przez antywirusa się nie liczy) — przypomnienie przed terminem | etap |
| uczestnicy z wybranego województwa (regionu) | uczestnicy bieżącej edycji z tym województwem w profilu; przy włączonym własnym podziale (`custom_regions`) — region, łącznie z profilami sprzed włączenia | województwo albo region (opcjonalnie „także uczestnicy poprzednich edycji”) |
| uczestnicy z wybranej szkoły (placówki) | uczestnicy bieżącej edycji z jednej szkoły; lista pokazuje **tylko szkoły, z których są uczestnicy** tego konkursu (w nawiasie ich liczba ze wszystkich edycji); szkoła z wykazu stoi z miejscowością, nazwa wpisana ręcznie — osobno | szkoła (opcjonalnie „także uczestnicy poprzednich edycji”) |
| uczestnicy z wybranej klasy | uczestnicy bieżącej edycji z tą klasą w profilu; na liście tylko klasy, w których ktoś jest | klasa (opcjonalnie „także uczestnicy poprzednich edycji”) |
| uczestnicy obecni na wybranym warsztacie | osoby odhaczone w tabeli obecności (`/coordinator/workshops/attendance/`) | warsztat |
| **opiekunowie szkolni (nauczyciele)** | konta opiekunów tego konkursu z aktywną rolą | — |
| członkowie komitetu / komitet jednego województwa | aktywni recenzenci tego konkursu | — / województwo |
| wklejona lista adresów | adresy z pola (nigdzie niezapisywane) | lista |

**Bieżąca edycja to ustawienie domyślne** (decyzja organizatora z 24.09.2026). „Wszyscy uczestnicy
konkursu” oraz grupy regionu, szkoły i klasy obejmują uczestnika bieżącej edycji, czyli kogoś, kto
jest zapisany do któregokolwiek jej etapu **albo** założył konto po utworzeniu tej edycji (zarejestrował
się, ale do etapu jeszcze się nie zapisał). Osoba z poprzedniego roku, która w tej edycji nigdzie się
nie zapisała, listu **nie** dostaje — chyba że zaznaczysz **„także uczestnicy poprzednich edycji”**
(domyślnie odznaczone). Bez bieżącej edycji grupy te są puste, dopóki pola nie zaznaczysz. Wybór jest
widoczny w podglądzie i w historii („bieżąca edycja” / „także poprzednie edycje”), a zmiana go po
podglądzie unieważnia podgląd tak samo jak zmiana grupy. „Uczestnicy bieżącej edycji (zapisani do
etapu)” zostają węższą grupą: wyłącznie osoby z wpisem do etapu. Po wybraniu grupy ekran pokazuje **tylko pole, którego ta grupa wymaga** (bez JavaScriptu
widać wszystkie — liczy się wyłącznie pole wybranej grupy). **Z wysyłki wypadają konta zablokowane
i bez potwierdzonego adresu**, a jedna osoba dostaje jeden list, choćby pasowała do grupy kilka razy.
Każda grupa obejmuje wyłącznie osoby **tego** konkursu — uczestnik innej olimpiady na tej samej
platformie nie dostanie listu, nawet jeśli ma tu konto.

Rocznika (roku urodzenia) jako grupy nie ma celowo: datę urodzenia zbieramy wyłącznie do ustalenia,
czy potrzebna jest zgoda opiekuna. Adresu **rodzica/opiekuna prawnego** komunikat też nie dostaje
w kopii — jest podawany wyłącznie do potwierdzenia zgody.

Ekran jest **dwustopniowy**: **„Podgląd”** pokazuje liczbę odbiorców i treść tak, jak pójdzie w liście,
i dopiero **„Wyślij”** wysyła. Podgląd mówi też, **do kogo** („uczestnicy z wybranej szkoły: XIV LO…,
Warszawa”). To jedyny moment, w którym pomyłkę („uczestnicy edycji” zamiast „zapisani do etapu”) da się
jeszcze cofnąć. **Adresów ekran nie pokazuje** — sprawdzasz rząd wielkości, nie wpisy. Jeśli po podglądzie
zmienisz grupę, jej parametr, pole „także uczestnicy poprzednich edycji”, temat albo treść, „Wyślij”
**nic nie wyśle** — pokaże podgląd na nowo
(„…zmieniły się od podglądu”) i dopiero kolejne „Wyślij” wysyła.

Każdy odbiorca dostaje **osobną kopertę**. Wysyłka idzie porcjami, więc awaria jednej porcji nie kasuje
reszty. Każda wysyłka zostaje w sekcji **„Wysłane komunikaty”** (autor, data, grupa **z wybranym etapem,
regionem, szkołą, klasą albo warsztatem**, temat, treść, liczba odbiorców, stan) — **rejestr nie trzyma
adresów**. Ten sam opis grupy trafia do audytu (`broadcast.sent`). Stan „przekazana do wysyłki” znaczy,
że listy trafiły do kolejki; o doręczeniu rozstrzyga serwer odbiorcy.

**Załączników nie ma.** Plik (regulamin, instrukcja) wstaw do biblioteki dokumentów w `/cms/`
i wklej do treści komunikatu odnośnik — jeden plik na serwerze zamiast kilku tysięcy kopii
w skrzynkach i bez ryzyka, że duży załącznik zatrzyma list w filtrze antyspamowym.

### 6.1a Slider na stronie głównej — plakaty i aktualności

Nagłówek strony głównej (tam, gdzie stoi „Przyszłość ma naturę kwantową.”) jest sliderem. Kolejność
plansz: **plakaty** redakcji → plansza z hasłem serwisu i przyciskami → **trzy najnowsze aktualności**.
Plansze zmieniają się same co 7 s (pauza pod kursorem, przy fokusie i przyciskiem „Wstrzymaj”; przy
ograniczonym ruchu w systemie slider stoi), a bez JavaScriptu przewija się je palcem lub kółkiem.

Edycja: `/cms/` → **Strona główna** → panel **„Slider”**:

| Pole | Znaczenie |
|---|---|
| Plakaty w sliderze | plansze w stylu afisza: nadtytuł (taśma), hasło (2–4 słowa, wielkimi literami), tekst, pieczątka w kółku, przycisk z adresem (`/register/` albo `https://…`), kolorystyka (czerwony / granatowy / papier) i **„pokaż koniec rejestracji”** – pasek „Rejestracja trwa do …” z datą zamknięcia rejestracji ustawioną w panelu koordynatora (zmiana terminu w panelu zmienia plakat sama) |
| Obraz (JPG) w sliderze | gotowa grafika z biblioteki obrazów (JPG, PNG, WebP; najlepiej poziomo, ok. 1600×700 px), pokazywana w całości bez przycinania. Wymagany **opis obrazu** (co widać i co jest napisane — czyta go czytnik ekranu); opcjonalnie adres po kliknięciu i „tylko przy otwartej rejestracji” |
| Aktualności w sliderze | włącza trzy najnowsze aktualności jako kolejne plansze |
| Plansza z hasłem w sliderze | plansza „Przyszłość ma naturę kwantową.” z wprowadzeniem, przyciskami „Zarejestruj się / Zaloguj się”, datą końca rejestracji i bieżącym etapem. Wyłączona znika ze slidera (hasło zostaje na stronie jako niewidoczny nagłówek dla wyszukiwarek i czytników ekranu); wraca sama, gdy slider nie ma żadnej innej planszy |

Po wdrożeniu slider ma już jeden plakat: **„Rozpoczęliśmy rejestrację!”** z przyciskiem „Zarejestruj
się”. Gdy rejestracja się zamknie, zdejmij go albo zmień treść — plakat jest tekstem redakcyjnym i nie
zależy od okna rejestracji.

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

**Powiadomienia e-mail (od 25.09.2026) — zbiorcze, nigdy „za każdy wpis”.** Pierwsza wersja forum nie
wysyłała listów wcale, żeby Twoja skrzynka nie zamieniła się w kanał RSS. Listy doszły z tym samym
warunkiem: każdy z nich zbiera wszystko, co się uzbierało, i ma limit.

| List | Do kogo | Kiedy | Co zawiera |
|---|---|---|---|
| **Wpisy czekają na moderację** | koordynatorzy **tego** konkursu (komitet nie — moderujesz tylko Ty) | pierwszy, gdy najstarsza pozycja czeka **10 min**; kolejne najwyżej **co 3 godz.**, dopóki coś czeka | same liczby (wątki, odpowiedzi, zgłoszenia), od kiedy czeka najstarsza pozycja, odnośnik do kolejki — **bez tematów i treści** |
| **Nowe odpowiedzi w obserwowanym wątku** | każdy, kto wątek obserwuje (autor wątku i każdy, kto w nim pisał, obserwują automatycznie; przycisk „Obserwuj wątek” / „Przestań obserwować” pod tematem) | najwyżej jeden list o wątku **co 4 godz.**; kilka wątków jedzie w jednym liście | temat wątku, liczba nowych wpisów, odnośnik; osobny temat listu, gdy odpowiedział **organizator** albo **komitet** |
| **Decyzja organizatora w sprawie wpisu** | autor wpisu albo wątku z kolejki | przy najbliższym przebiegu (co 2 min); zatwierdzenie zbiorcze to **jeden** list na autora | zatwierdzenie z tematem i odnośnikiem albo odrzucenie z **Twoim uzasadnieniem** — bez tematu odrzuconego wątku |

Czego list **nie niesie nigdy**: treści wpisów (także opublikowanych — forum czyta się wyłącznie po
zalogowaniu, a list bywa przekazany dalej), imion piszących i czegokolwiek, co nie jest opublikowane.
Stan sprawdzamy w chwili wysyłki: wpis zatwierdzony i zaraz ukryty nie wyjdzie. Konta nieaktywne,
z niepotwierdzonym adresem, po anonimizacji i osoby bez roli w konkursie nie dostają nic. Temat każdego
listu zaczyna się od prefiksu konkursu i słowa „Forum:” (np. `[Olimpiada Kwantowa] Forum: …`) — po tym
da się je odfiltrować. Język listu to język konta odbiorcy, a bez wyboru — język konkursu.

**Ustawienia** ma każdy na ekranie „Edycja danych” (`/account/profile/` albo `/me/profile/`, blok
„Powiadomienia z forum”): listy o wątkach i decyzjach **na bieżąco** (domyślnie), **raz dziennie**
(jedno podsumowanie rano, ok. 7:00 czasu letniego) albo **nigdy**; koordynator ma dodatkowo przełącznik
listów o kolejce (domyślnie włączony). **Każdy list ma link „wypisz się”**, działający bez logowania,
i nagłówek `List-Unsubscribe` (klient poczty pokaże przycisk „Anuluj subskrypcję”).

Rytm zmienia administrator instalacji zmiennymi środowiskowymi: `FORUM_MODERATION_DIGEST_DELAY_MINUTES`
(10), `FORUM_MODERATION_DIGEST_INTERVAL_HOURS` (3), `FORUM_THREAD_NOTIFY_INTERVAL_HOURS` (4),
`FORUM_DAILY_DIGEST_HOUR_UTC` (5).

Listy są dodatkiem, nie jedynym sygnałem:

- **odznaka przy „Forum uczestników”** w menu panelu nadal mówi, ile pozycji czeka — przy trybie „przed
  publikacją” zaglądaj do kolejki tak, jak zaglądasz do zgłoszeń, także gdy listy o kolejce wyłączysz,
- **autor znajduje Twoje uzasadnienie** na swoim ekranie `/forum/mine/` także wtedy, gdy listy wyłączył.
  Odrzucenie bez uzasadnienia jest niemożliwe (formularz odmówi). Ukrycie wpisu już opublikowanego nie
  idzie listem — autor widzi je na `/forum/mine/`.

**RODO:** listy są kontaktem w ramach forum, z którego ta osoba korzysta — ten sam cel i ta sama podstawa
(art. 6 ust. 1 lit. f), więc bez nowej czynności w rejestrze; wiersz „Forum uczestników” dostał w wersji
**1.9** rejestru nowego odbiorcę (dostawca poczty wychodzącej) i kategorię danych (obserwowane wątki,
ustawienia powiadomień). Wypis jednym kliknięciem jest formą prawa sprzeciwu z art. 21.

**Czego forum nie ma i w wersji pierwszej mieć nie będzie:** wiadomości prywatnych na forum (rozmowa
niepełnoletnich bez świadków jest dokładnie tym, czego moderacja nie widzi – rozmowy 1:1 prowadzi osobny
moduł Wiadomości z własnymi trybami moderacji, § 6.4a), załączników i HTML-a
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

### 6.4a Wiadomości — `/coordinator/chat/`

Rozmowy 1:1 na platformie, jak w komunikatorze LinkedIn: lista rozmów po lewej, wątek po prawej.
Wiadomości czyta się **w serwisie** — e-mail jest tylko sygnałem „masz nową wiadomość” (bez treści)
i każdy może go wyłączyć. Dwa kanały:

| Kanał | Kto zaczyna | Moderacja |
|---|---|---|
| **Organizator ↔ uczestnik** | Ty („Napisz wiadomość” na karcie uczestnika) albo uczestnik („Napisz do organizatora”) | nigdy |
| **Uczestnik ↔ uczestnik** | uczestnik – do osoby z katalogu, gdy tryb nie jest „wyłączone” | zależnie od trybu |

**Skrzynka organizatora jest wspólna.** Każdy koordynator konkursu widzi każdą rozmowę organizatorską
i odpowiada w imieniu zespołu — uczestnik widzi podpis „Organizator · Imię N.”. Otwarcie wątku oznacza go
jako przeczytany dla wszystkich. Tutaj widzisz **pełne** dane uczestnika (imię, nazwisko, kod) i odnośnik
do jego karty. Odznaka przy „Komunikacja → Wiadomości” liczy nieprzeczytane rozmowy i kolejkę moderacji.

| Kto | Gdzie |
|---|---|
| Uczestnik | „Wiadomości” w pasku konta i w pasku panelu `/me/` (z liczbą nieprzeczytanych) → `/me/messages/` |
| Koordynator | `/coordinator/chat/` — skrzynka (filtr „Nieprzeczytane”); `/coordinator/chat/<id>/` — wątek |
| Koordynator | `/coordinator/chat/moderation/` — kolejka; `/coordinator/chat/settings/` — ustawienia |

**Stan i przypisanie rozmowy.** Każda rozmowa organizatorska ma stan: **otwarta** (czeka na zespół),
**czeka na uczestnika**, **zamknięta**. Przejścia są automatyczne: wiadomość uczestnika otwiera rozmowę
(także zamkniętą), Twoja odpowiedź przestawia ją na „czeka na uczestnika”, a przycisk **„Odpowiedz
i zamknij”** – na „zamknięta”. W wątku możesz ją **przypisać do siebie** albo innemu koordynatorowi
i ręcznie zmienić stan (każda zmiana w audycie `chat.assigned` / `chat.status_changed`). Skrzynka ma
filtry stanu (domyślnie „otwarta”) i „Moje / Nieprzypisane / Nieprzeczytane” z licznikami; odznaka w menu
liczy otwarte rozmowy z nieprzeczytaną wiadomością uczestnika (plus kolejkę moderacji). Uczestnik nie
widzi ani stanu, ani przypisania – dla niego zawsze pisze „Organizator”.

**Szablony odpowiedzi** — `/coordinator/chat/templates/` (przycisk „Szablony odpowiedzi” w skrzynce):
gotowe teksty na powtarzające się pytania. W wątku „Wstaw szablon” wkleja treść w miejscu kursora,
a znacznik `{imie}` zamienia się na imię uczestnika tej rozmowy. Szablony działają wyłącznie w kanale
organizatora.

**Tryby rozmów między uczestnikami** (`/coordinator/chat/settings/`, domyślnie **wyłączone**):

- **wyłączone** — katalogu nie ma, nowych rozmów nie da się zacząć, istniejące są tylko do odczytu,
- **premoderacja** — każda wiadomość czeka na Twoją akceptację; odbiorca nie widzi jej wcześniej.
  Odrzucenie wymaga notatki, którą zobaczy nadawca,
- **postmoderacja** — wiadomość dochodzi od razu, a Ty przeglądasz ją po fakcie: „Przejrzane” albo „Ukryj”
  (odbiorca widzi wtedy „Wiadomość ukryta przez moderatora”),
- **bez moderacji** — wiadomość dochodzi od razu i nikt jej nie czyta; widzisz **wyłącznie zgłoszone**.

**W czasie etapu przyjmującego rozwiązania** (poza treningiem) tryby „postmoderacja” i „bez moderacji”
zamieniają się same w **premoderację** — ta sama reguła, co na forum: rozmowa 1:1 w trakcie zawodów jest
najprostszą drogą do zmowy. Ekran ustawień mówi wtedy wprost, który etap wymusił zmianę, a uczestnik
widzi to samo zdanie nad formularzem. „Wyłączone” zostaje wyłączone.

**Kolejka moderacji** ma trzy listy: czekające (premoderacja; „Akceptuj”, „Odrzuć” z notatką,
„Akceptuj zaznaczone”), do przejrzenia (postmoderacja) i zgłoszone (każdy tryb; „Ukryj wiadomość” —
zamyka też zgłoszenie — albo „Zamknij zgłoszenie”). Przy pozycji stoi nadawca, odbiorca i kilka
poprzednich wiadomości rozmowy. Każda decyzja trafia do dziennika zdarzeń (`chat.*`) **bez treści**.

**Prywatność — czego nie zobaczysz.** Nie ma ekranu „przeglądaj rozmowy uczestników”. Treść rozmowy
między uczestnikami widzisz wyłącznie w kolejce i wyłącznie w zakresie, o którym nadawca wiedział, pisząc:
nad formularzem czyta „Wiadomości mogą być czytane przez organizatora w ramach moderacji” (premoderacja,
postmoderacja) albo „Organizator widzi tylko zgłoszone wiadomości” (bez moderacji). Wiadomości wysłane
bez moderacji nie wchodzą nawet do kontekstu zgłoszenia innej wiadomości tej samej rozmowy.

**Grupa wiekowa** (ustawienia, domyślnie **„tylko w tej samej grupie wiekowej”**): niepełnoletni
rozmawiają wyłącznie z niepełnoletnimi, pełnoletni – z pełnoletnimi. Pełnoletność liczymy **dziś**
z daty urodzenia, a przy samym roczniku ostrożnie: osoba jest niepełnoletnia przez cały rok, w którym
kończy 18 lat. Zasada działa w katalogu, przy zaczęciu rozmowy i przy **każdej** wiadomości – gdy
ktoś w trakcie rozmowy skończy 18 lat, rozmowa zamyka się z neutralnym zdaniem „Ta rozmowa została
zamknięta zgodnie z zasadami konkursu” (wieku drugiej osoby nie pokazujemy nigdzie). „Bez ograniczeń
wieku” wybieraj świadomie – ekran ostrzega, że dorośli będą mogli rozmawiać 1:1 z niepełnoletnimi.

**Dzienny limit nowych rozmów** (ustawienia, domyślnie **5**, zakres 1–50): ile nowych rozmów z innymi
uczestnikami jedna osoba może zacząć w ciągu ostatnich 24 h. Odpowiedzi w trwających rozmowach
i rozmowy z organizatorem limitu nie mają (ogranicza je ogólny limit żądań).

**Katalog uczestników jest dobrowolny.** Do nowej rozmowy można zaprosić tylko osobę, która sama włączyła
„Inni uczestnicy mogą mnie znaleźć i do mnie napisać”. Katalog pokazuje imię, pierwszą literę nazwiska
i województwo — nigdy e-mail, szkołę ani kod `OLM-…`. Uczestnik może **zablokować** drugiego uczestnika
(ten dostaje neutralne „Nie można wysłać wiadomości do tej osoby”) i **zgłosić** wiadomość — zgłoszenie
działa w każdym trybie, także w rozmowie, która po wyłączeniu kanału jest tylko do odczytu.

**Szyfrowanie end-to-end (opcjonalne).** Przełącznik na ekranie ustawień, dostępny **tylko w trybie „bez
moderacji”** — zmiana trybu przy włączonym szyfrowaniu jest odrzucana („najpierw wyłącz szyfrowanie”).
Nowe rozmowy między uczestnikami są wtedy szyfrowane w przeglądarkach (WebCrypto): serwer i Ty widzicie
wyłącznie szyfrogram. Uczestnik ustawia własne **hasło do wiadomości** (inne niż hasło konta; nie znamy
go i nie zresetujemy — „Utwórz nowy klucz” oznacza utratę dostępu do starych wiadomości szyfrowanych).
Rozmowy szyfrowane są **tylko do odczytu** w czasie etapu wymuszającego premoderację i po wyłączeniu
szyfrowania. Zgłoszenie wiadomości szyfrowanej niesie treść odszyfrowaną przez zgłaszającego — przy
pozycji stoi dopisek, że serwer nie może potwierdzić jej autentyczności. Kanał organizatora nie jest
szyfrowany nigdy.

**Wyłączenie modułu** („Wiadomości włączone” na ekranie ustawień) chowa go uczestnikom (pozycje menu
znikają, adresy odpowiadają 404); rozmowy zostają w bazie. Pozycja w Twoim menu prowadzi wtedy do ustawień.

**RODO:** Wiadomości mają własny wiersz w rejestrze czynności („Wiadomości na platformie”, wersja 1.10),
wysłane wiadomości wchodzą do paczki `/account/export/` (szyfrowane — jako szyfrogram z adnotacją), a
anonimizacja konta zostawia wiadomości w rozmowie drugiej strony z podpisem „Użytkownik usunięty”
i usuwa profil katalogu, klucz szyfrowania, blokady i ustawienia powiadomień.

### 6.4b Pokoje wideo — `/coordinator/video-rooms/`

Menu **Komunikacja → Pokoje wideo**. Pozycja jest tylko wtedy, gdy olimpiada ma własne Jitsi
z przepustkami (administrator: `docs/OPERACJE.md` § 25) — inaczej ekranu nie ma.

**Założenie pokoju.** Wpisujesz krótką **etykietę** („zebranie komisji okręgowej” — bez nazwisk gości:
etykieta wchodzi do nazwy pokoju i widać ją na stronie zaproszenia), wybierasz **ważność** z listy
**1 / 7 / 30 / 60 dni** i zaznaczasz, czy pokój ma być **dostępny dla członków komisji** (wtedy widzą go
w swoim panelu z przyciskiem „Dołącz”) i czy wchodzą **jako gospodarze**. Pełnej nazwy pokoju nie
wpisuje się — powstaje sama z etykiety i losowej końcówki.

**Dwa linki-zaproszenia.** Po założeniu dostajesz od razu:

- **link gospodarza** — wchodzi się z prawami moderatora (wyciszanie, poczekalnia, usuwanie z pokoju);
  dla prowadzącego, nie do rozsyłania,
- **link gościa** — bez tych praw; ten wysyłasz uczestnikom spotkania, także osobom bez konta.

Link prowadzi na stronę platformy (`/zaproszenie/wideo/…`), na której gość wpisuje swoje imię
i klika „Dołącz” — dopiero wtedy platforma wpuszcza go do pokoju. Linki zobaczysz ponownie
przyciskiem **„Pokaż linki”** przy pokoju (każde pokazanie zostaje w dzienniku zdarzeń). Kopiujesz je
z pola (zaznacz, Ctrl+C).

**Zamykanie i wymiana linku działają od razu.** „Zamknij pokój” sprawia, że nikt — ani z panelu, ani
z linku — już do niego nie wejdzie; „Wygeneruj nowy link gościa/gospodarza” unieważnia stary link
(np. gdy trafił tam, gdzie nie powinien) bez zamykania pokoju. Osoby, które są w tej chwili
w rozmowie, zostają w niej do wyjścia — Jitsi nie wyrzuca nikogo w trakcie. Po upływie ważności pokój
zamyka się sam.

**Linki przekazuj jak hasło**: do końca ważności (albo do zamknięcia/wymiany) wpuszczają każdego,
kto je ma. Dlatego ważność jest ograniczona (Ty: najwyżej 60 dni, komisja: najwyżej 30).

**Uprawnienia komisji** (sekcja „Kto z komisji może zakładać pokoje”). Członkowi komisji (recenzent
albo komisja odwoławcza **tego** konkursu) możesz nadać prawo zakładania własnych pokoi — dostaje
wtedy w swoim panelu ekran „Moje pokoje wideo” z tym samym formularzem (ważność 1 / 7 / 30 dni). Widzi
i zamyka **tylko swoje** pokoje; Ty widzisz na liście **wszystkie** pokoje konkursu (kto założył, kiedy,
do kiedy, stan) i możesz każdy zamknąć, pokazać jego linki albo je wymienić. **Odebranie
uprawnienia** (albo zawieszenie członka komisji) od razu zamyka mu ten ekran i wejście z panelu do jego
pokoi; jego **linki-zaproszenia działają dalej**, dopóki nie zamkniesz jego pokoi — przycisk
„Zamknij pokoje tej osoby” stoi w tym samym wierszu (to osobna decyzja, bo goście mogą być umówieni).

Co zostaje w dzienniku zdarzeń (bez linków, przepustek i nazw gości): założenie pokoju, pokazanie
linków, wymiana linku, zamknięcie, każde wejście (rola: koordynator, komisja, autor, link gospodarza,
link gościa), nadanie i odebranie uprawnienia.

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

### 7.2a Wzór zaświadczenia o statusie ucznia — tekst na papierze

Wzór, który uczestnik pobiera w `/me/status-ucznia/` (§ 10a), jest **imienny**: ma wpisane imię
i nazwisko, datę urodzenia (albo miejsce do wpisania, gdy profil zna sam rocznik), szkołę, rok szkolny
wyjęty z oznaczenia edycji („I edycja 2026/2027” → „2026/2027”), a puste pola na **klasę**, **pieczątkę
szkoły**, **miejscowość i datę** oraz **podpis dyrektora lub sekretarza**. Na dole ramka dla ucznia:
jak i gdzie wgrać skan, adres panelu i kod uczestnika.

Tekst (tytuł, zdanie główne, linia podpisu, dopisek) ma dzisiejsze brzmienie wbudowane. Z włączonymi
**Szablonami dokumentów** (flaga `document_templates`) zmienia się go na ekranie
`/coordinator/documents/STUDENT_STATUS/` jak każdy inny dokument — z wersjami i podglądem. Poza
znacznikami wspólnymi działają dwa własne: `{birth_date}` i `{school_year}`. Klasy nie podstawiamy
nigdy — ma ją wpisać szkoła.

### 7.3 Dokumenty organizatora

Regulamin, polityka RODO, standardy ochrony małoletnich, skład komitetów, polityka cookies — wszystko
jako strony w `/dokumenty/` redagowane w `/cms/`, z załącznikami do pobrania (PDF, opcjonalnie DOCX).
Kolejność, treść i załączniki należą do **redakcji**; wdrożenie aplikacji ich nie nadpisuje.

### 7.3a Strony serwisu po przełączeniu na django CMS

Część informacyjna serwisu (strona główna, regulamin w `/dokumenty/`, FAQ, aktualności i pozostałe
strony redakcyjne) może zostać przeniesiona z Wagtaila (`/cms/`) do **django CMS**. Przełączenie
robi administrator jednym poleceniem, **po zgodzie organizatora**, dla wszystkich konkursów naraz
(`PODRECZNIK-ADMINISTRATORA.md` § 5.2). Adresy stron się nie zmieniają; logowanie, panele,
zgłoszenia, wyniki i terminy działają dokładnie tak jak wcześniej — zmienia się wyłącznie miejsce,
w którym redaguje się strony.

**Przed przełączeniem (administrator poda termin):**

- dokończ zmiany w `/cms/` – końcowy import bierze treść stron **z chwili przełączenia**,
- **nie redaguj** stron w podglądzie django CMS – końcowy import je nadpisze. Jeśli redakcja
  Twojego konkursu już tam pracuje, powiedz o tym administratorowi: przełączy Twój konkurs bez
  importu (jego treść w django CMS zostanie taka, jak jest).

**Po przełączeniu:**

| Co | Gdzie |
|---|---|
| strony (tekst, zdjęcia, menu, nowe podstrony) | **django CMS** – wejście wyłącznie z `/cms/` swojego konkursu, pozycją menu „Edytuj w django CMS” (niżej) |
| tabela warsztatów (`/warsztaty/`) i partnerzy (`/partnerzy/`, slider sponsorów) | **nadal `/cms/`** – django CMS pokazuje je na bieżąco z aplikacji |
| ustawienia serwisu (dane organizatora, logotyp), obrazy, dokumenty PDF | **nadal `/cms/`** |
| terminy, zadania, wyniki, komunikaty | bez zmian – panel koordynatora |

Pozostałe strony w `/cms/` są **tylko do odczytu**: na górze panelu stoi baner „Edycja treści
przeniesiona do django CMS”, a przyciski edycji, publikacji, przenoszenia i usuwania stron są
ukryte (próba zapisu kończy się odmową).

**Jak wejść do django CMS.** Zaloguj się do `/cms/` **swojego** konkursu (pod jego adresem, np.
`https://fizyczna.olimpiadakwantowa.pl/cms/`), wybierz w menu **„Edytuj w django CMS”**, a potem
„Przejdź do django CMS” – serwis zaloguje Cię tym samym kontem, bez osobnego hasła, i otworzy listę
stron Twojego konkursu. Kont w django CMS nikt nie zakłada ręcznie i nie ma do nich haseł.

- W django CMS możesz dokładnie to, co w `/cms/`: kto w `/cms/` edytuje i publikuje strony
  konkursu, ten edytuje i publikuje je w django CMS; kto tylko edytuje – zapisuje wersje robocze,
  a publikuje ktoś z prawem publikacji. Uprawnienia nadaje się **w `/cms/`** (jak dotąd) – django CMS
  przejmuje je przy każdym wejściu.
- Widzisz i edytujesz wyłącznie strony, pliki i przekierowania **swojego** konkursu. Pliki
  wgrywaj do folderu `Konkurs: <nazwa konkursu> (…)`; folder „Wspólne” jest tylko do odczytu.
  Każdy wgrany plik jest publiczny – nie wgrywaj niczego poufnego.
- Sesja w django CMS trwa do 4 godzin od wejścia; potem wróć do `/cms/` i wejdź ponownie.
- Nie widzisz pozycji „Edytuj w django CMS”? Twoje konto nie ma w `/cms/` prawa edycji stron tego
  konkursu (poproś osobę, która zarządza redakcją) albo administrator jeszcze nie włączył przejścia.
- Ktoś odszedł z redakcji: odbierz mu prawa w `/cms/` – w django CMS stracą ważność przy jego
  następnym wejściu, a najpóźniej po 4 godzinach. Gdy trzeba natychmiast (np. wyciek hasła),
  poproś administratora o zablokowanie konta w django CMS (`PODRECZNIK-ADMINISTRATORA.md`,
  `docs/OPERACJE.md` § 22.3).

**Gdyby trzeba było wrócić do Wagtaila** (decyzja organizatora, administrator robi to w kilka
sekund): odwiedzający zobaczą strony w stanie z **chwili przełączenia** – zmiany zrobione później
w django CMS do Wagtaila **nie wracają**. Dopóki administrator nie odblokuje edycji w `/cms/`,
stron nie da się tam poprawiać; przy ponownym przejściu na django CMS wraca treść z django CMS.

Zmieniając adres (slug) strony w django CMS, pamiętaj, że aplikacja linkuje do regulaminu
i dokumentów zgód (`/dokumenty/…`) oraz do `/warsztaty/` – takie strony zostaw pod dotychczasowym
adresem; stary adres innej strony przekierowuje się na nowy sam.

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

**Skany zaświadczeń o statusie ucznia** (§ 10a) znikają ze storage razem z terminem retencji edycji —
także u osób, których konto zostaje, bo startują w edycji późniejszej (nocne zadanie
`apps.student_status.tasks.purge_expired_scans`; zapis decyzji bez pliku zostaje). Plik zastąpiony
nowszym albo odrzucony przez skaner antywirusowy znika od razu, a anonimizacja lub usunięcie konta
zabiera zaświadczenia w całości.

**Pseudonimy adresów IP przy pobraniach plakatów** (§ 4.10) mają **własny, stały termin**:
12 miesięcy od pobrania, niezależnie od ustawień edycji. Kasuje je nocne zadanie
`apps.promo.tasks.clear_expired_ip_hashes` — ten ekran ich nie pokazuje i nie trzeba go do tego
uruchamiać; liczba pobrań zostaje, znika tylko możliwość policzenia unikalnych adresów sprzed roku.

### 9.2 Rejestr czynności przetwarzania — `/coordinator/processing-register/`

Dokument wymagany art. 30 ust. 1 RODO, **gotowy do wydania na żądanie**. Obejmuje jedenaście czynności:
konta uczestników, dowody zgód, konta opiekunów szkolnych, przyjmowanie i ocenianie prac, ogłaszanie
wyników i dokumenty, reklamacje, rozmowy kwalifikacyjne, konta komitetu, zgłoszenia i pomoc,
utrzymanie serwisu oraz statystykę pobrań plakatów (wersja 1.6 z 23.09.2026 — pseudonim adresu IP
przy pobraniu plakatu, kasowany po 12 miesiącach, § 4.10). Wersja 1.7 z 24.09.2026 dokłada trzy
wiersze **warunkowe** — każdy stoi w rejestrze wyłącznie przy włączonej funkcji, tak jak forum:
**„Weryfikacja statusu ucznia (zaświadczenie ze szkoły)”** (flaga `student_status_certificate`,
§ 10a), **„Statystyka wyświetleń materiałów z warsztatów”** (flaga `workshop_materials`, § 4.11 —
pseudonim pary materiał–konto, kasowany po 12 miesiącach) i **„Pomocnicza ocena prac uczestników przez
model językowy”** (flaga `ai_grading`, § 4.12 — nowy podmiot przetwarzający i przekazanie danych poza
EOG). Wersja **1.8** z 24.09.2026 (inni dostawcy AI) zmienia w tym wierszu odbiorców: zamiast stałego
„Anthropic” rejestr konkursu wymienia **każdego dostawcę, który ma klucz API i potwierdzoną umowę
powierzenia** (Anthropic, OpenAI, Google, Meta) — a gdy takiego nie ma, mówi wprost, że prace nie
opuszczają serwera.
Wersja **1.9** z 25.09.2026 (powiadomienia e-mail z forum) nie dodaje celu przetwarzania: wiersz
forum dostaje nowego odbiorcę (dostawca poczty wychodzącej – temat wątku i sam fakt udziału w rozmowie,
nigdy treść wpisu) i nową kategorię danych (obserwowane wątki, ustawienia powiadomień, znaczniki
wysyłki; wchodzą do eksportu danych konta i znikają przy jego usunięciu).
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

**Zmiana hasła przez samego użytkownika** (AUTH-01b) — każda rola, także Ty: kliknij swój adres
e-mail w pasku konta (prowadzi do ustawień konta) → „Hasło” → **„Zmień hasło”** (`/account/password/`).
Wymaga aktualnego hasła; po zmianie inne urządzenia i aplikacje są wylogowane, a na adres konta idzie
list „Hasło do konta zostało zmienione”. Konto bez hasła (Google/Facebook) dostaje tam przycisk
wysyłający link do ustawienia hasła na **własny** adres. W audycie: `password.changed`,
`password.change_failed` (złe aktualne hasło — seria takich wpisów przy jednym koncie to sygnał, że
ktoś zgaduje z otwartej sesji) i `password.set_link_sent`; żadnych haseł ani adresów w szczegółach.
Pytanie „zmieniłem hasło i wylogowało mnie na telefonie” — to zamierzone. Wyjątek: otwarta sesja
**edytora django CMS** na innym urządzeniu nie kończy się od razu (wygasa sama po kilku godzinach) —
przy podejrzeniu przejęcia poproś administratora o zablokowanie konta w django CMS. Hasła i adresu
nie zmienia się w `/cms/` (ekran konta Wagtaila nie ma już tych pól) ani w `/admin/` — zawsze
w ustawieniach konta serwisu; **zmiana adresu e-mail wymaga aktualnego hasła**, a pięć błędnych
haseł z rzędu kończy sesję (`diff.session_ended` w audycie).

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
| **Konta** | `/coordinator/accounts/` | wszystkie konta; wyszukiwarka `?q=`, filtr `?role=`, sortowanie nagłówkami, 50 na stronę (§ 10.1) |
| Uczestnicy / Opiekunowie szkolni | ta sama lista z filtrem roli | osobny ekran powtarzałby wyszukiwarkę i stronicowanie; uczestnicy mają własne kolumny profilu (§ 10.1) |
| Edycja konta | `/coordinator/accounts/<id>/` | dane, „Konto aktywne”, dla uczestnika także telefon, województwo, szkoła, klasa, **data urodzenia**; dla członka komitetu status, komisja odwoławcza i województwo |
| Usunięcie konta | `/coordinator/accounts/<id>/delete/` | strona potwierdzenia mówi, co się stanie |
| **Konta oczekujące na aktywację** | `/coordinator/activations/` | „Aktywuj ręcznie”, „Wyślij link ponownie” |
| **Karta uczestnika** | `/coordinator/participants/<id>/` | cały przebieg zawodów jednej osoby, wyłącznie do odczytu |

### 10.1 Sortowanie list i konta usunięte

**Sortowanie.** Na liście kont (`/coordinator/accounts/`) i na liście uczestników (pozycja menu
„Uczestnicy”, czyli ta sama lista z `?role=participant`) **każdy nagłówek kolumny z ikoną ↕ jest
odnośnikiem**: pierwsze kliknięcie sortuje rosnąco (▲), drugie malejąco (▼). Data rejestracji i liczba
prac zaczynają od malejącego — od najnowszych i od najbardziej aktywnych. Sortuje serwer, więc porządek
obejmuje **całą** listę, a nie tylko bieżącą stronę; przejście na kolejną stronę, nowa fraza
w wyszukiwarce i zmiana filtra roli zachowują wybrany porządek (jest w adresie: `?sort=nazwisko`,
`?sort=-zalozone`). Bez wyboru lista stoi jak dotąd — po adresie e-mail.

| Lista | Kolumny sortowalne |
|---|---|
| Wszystkie konta | e-mail, imię i nazwisko (po nazwisku), kod publiczny, stan, data założenia |
| Uczestnicy | kod, nazwisko, imię, e-mail, szkoła, województwo, klasa, zgoda opiekuna, prace, stan, data założenia |

Lista uczestników ma własne kolumny profilu: szkoła, województwo, klasa, zgoda opiekuna („tak”, gdy jest
potwierdzona) i **Prace** — liczba zadań, do których uczestnik oddał cokolwiek w **bieżącej edycji**
(kilka wersji tego samego zadania to jedna praca). Kolumny „Rola” nie da się sortować — rola jest
wyliczana z kilku źródeł naraz.

**Konta usunięte.** Uczestnik albo recenzent, który usunął konto (albo którego konto usunął koordynator
czy retencja), a zostawił ślad w zawodach, nie znika z bazy: zostaje pseudonimowy wiersz z kodem
publicznym, bo pod tym kodem stoi w wynikach, recenzjach i odwołaniach. Takie konta są **domyślnie
schowane** na liście kont, liście uczestników, w wyszukiwarce panelu, na liście członków komisji,
w tabeli obecności na warsztatach i w arkuszu „Uczestnicy edycji” (`/coordinator/export/`). Nad każdą
z tych list stoi przycisk **„Pokaż usunięte konta (N)”** — N to liczba kont usuniętych pasujących do
bieżącego wyszukiwania i filtrów. Po kliknięciu lista pokazuje je z podpisem **„Konto usunięte”**
i kodem publicznym, a przycisk zmienia się w **„Ukryj usunięte konta”**. Stan przełącznika jest
w adresie (`?usuniete=1`), więc przeżywa stronicowanie, sortowanie i wyszukiwanie; na stronie eksportu
przycisk decyduje, czy pobrany arkusz ma zawierać także konta usunięte.

Tam, gdzie konto usunięte **musi** zostać — przydziały prac, moderacja, recenzje, wyniki, karta
uczestnika, odwołania, audyt, eksport recenzji — wiersz zostaje, ale zamiast technicznego adresu
`deleted-…@invalid.…` stoi „Konto usunięte” (przy pracy obok stoi kod publiczny). Kolejki do załatwienia
(zatwierdzenia na ekranie „Komitet”, plakietka w menu, naliczanie wpisowego) pomijają konta usunięte
bez przełącznika — osoby, której konta już nie ma, nie da się zatwierdzić ani obciążyć opłatą.
Liczniki na ekranach słowników (placówki, regiony, kategorie) **liczą** konta usunięte, bo odpowiadają
na pytanie „czy ten wiersz wolno skasować”, a profil usuniętego konta nadal się do niego odwołuje.

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

**Import listy uczniów a konta, które już istnieją (od v0.38.7, decyzja właściciela platformy).**
Import — nauczyciela (`/supervisor/import/`) i Twój (`/coordinator/accounts/import/`, kolumna
„e-mail opiekuna szkolnego”) — **zakłada** konta zaproszonych tak jak dotąd: nowy uczeń dostaje adres
opiekuna od razu, bo i tak uruchamia konto sam, przyjmując zaproszenie. Adresu, który ma już konto,
import **nie zmienia**: do v0.38.5 nadpisywał uczniowi opiekuna adresem z pliku, a przy otwartej
rejestracji opiekunów każdy mógł w ten sposób dopisać się do dowolnego ucznia i wypchnąć jego
prawdziwego nauczyciela. Teraz uczeń **tego** konkursu dostaje list z prośbą o zgodę (link ważny
14 dni, strona wymaga zalogowania jako ten uczeń, dwa przyciski „Zgadzam się” / „Nie zgadzam się”)
i dopiero jego zgoda zapisuje adres. Zasada obowiązuje **także Twój import**: kolumna opiekuna
przypisuje nauczyciela od razu wyłącznie nowym kontom, a pusta komórka u istniejącego ucznia
**nie czyści** już opiekuna, którego wpisał sam. Szczegóły dla Ciebie:

- **podgląd mówi o każdym zajętym adresie jednym zdaniem** („adres ma już konto – jeśli to uczeń
  tego konkursu, dostanie prośbę o zgodę”) — bez odróżniania ucznia od recenzenta czy koordynatora,
  a komunikat po zapisie podaje trzy liczby: zaproszeni, adresy z istniejącym kontem, pominięci.
  Ile próśb naprawdę wyszło, mówi wpis audytu `accounts.students_imported` (`consent_requested`),
- **jedna prośba na parę (uczeń, nauczyciel) na dobę**; import nauczyciela ma limit żądań
  (scope `upload`, każdy podgląd i każde zatwierdzenie),
- **ślad w audycie** karty uczestnika: `participant.supervisor_consent_requested` (kto wgrał plik),
  `participant.supervisor_consented` albo `participant.supervisor_consent_refused` (uczeń) — bez
  adresów, z polem `via` (`supervisor` / `coordinator`),
- **dowiązania sprzed v0.38.7 zostały w bazie bez zmian** (bez migracji danych). Każde z nich ma
  w audycie wpis `participant.supervisor_email_set`, którego wykonawcą jest osoba wgrywająca plik,
  a nie uczeń — jeśli trzeba je przejrzeć, poproś operatora o listę.

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
pusty, dopóki uczniowie sami nie wpiszą jego adresu e-mail w swoim profilu albo nie zgodzą się na jego
prośbę z importu listy — to oni decydują, kto widzi ich postęp, nie organizator ani nauczyciel.

---

## 10a. Zaświadczenia o statusie ucznia — `/coordinator/student-status/`

**Tylko w konkursie z włączonymi zaświadczeniami** (przełącznik `student_status_certificate`, włącza go
operator — `OPERACJE.md` § 15). Bez niego żadnego z tych ekranów nie ma, a paczki ZIP wyglądają jak
zawsze.

**Po co.** Organizator chce mieć potwierdzenie ze szkoły, że uczestnik **w tej edycji** jest uczniem.
Status **nie blokuje** niczego po stronie uczestnika — prace oddaje się tak samo — a służy jako filtr
paczek ZIP dla komitetu (§ 4.1): „wszystkie prace” albo „tylko uczniowie z potwierdzonym statusem”.
Status jest **per edycja**: zeszłoroczne zaświadczenie nie potwierdza tego roku.

**Droga uczestnika.** Pulpit przypomina o zaświadczeniu, dopóki nie jest zaakceptowane. Uczestnik
pobiera imienny wzór (§ 7.2a), szkoła go stempluje i podpisuje, uczestnik wgrywa skan albo zdjęcie
(PDF/JPG/PNG do 10 MB, format sprawdzany po treści, skan antywirusowy). Może wgrać nowy plik, dopóki
zaświadczenie nie jest zaakceptowane — nowy zastępuje poprzedni (plik poprzedni znika, zapis zostaje
w historii).

**Ekran koordynatora.** Menu → *Uczestnicy i konta* → **Status ucznia**. Na górze cztery liczniki
(zarazem filtr): **oczekujące**, **zaakceptowane**, **odrzucone**, **brak** — liczone z całej edycji.
„Brak” to uczestnicy zapisani do któregokolwiek etapu edycji, którzy nic nie wgrali. Obok wybór edycji
(domyślnie bieżąca) i wyszukiwarka (nazwisko, e-mail, kod, szkoła). W wierszu:

- **„Podgląd”** otwiera skan w nowej karcie, **„Pobierz”** zapisuje go na dysk — oba dopiero po
  czystym skanie antywirusowym (do tego czasu wiersz mówi „trwa skan antywirusowy”); każde otwarcie
  zostawia wpis w audycie (`student_status.viewed`),
- **„Akceptuj”** — uczestnik dostaje e-mail i widzi „zaakceptowane”; formularz wgrania znika,
- **„Odrzuć”** z **obowiązkowym powodem** — powód zobaczy uczestnik w panelu i w e-mailu, więc pisz go do
  niego („brak pieczątki szkoły”, „nieczytelne zdjęcie — zrób je przy świetle dziennym”). Odrzucić
  wolno też zaakceptowane (pomyłka) — tylko tak uczestnik może wgrać papier ponownie; zaakceptować
  wolno też odrzucone.

Plik zainfekowany jest odrzucany **automatycznie** (powód: „odrzucony przez skaner antywirusowy”)
i usuwany — w kolumnie decyzji stoi wtedy „skaner antywirusowy”.

**Karta uczestnika** (`/coordinator/participants/<id>/`) ma sekcję **„Status ucznia”** ze stanem
w bieżącej edycji, podglądem skanu i historią wersji; decyzje zapadają na liście (odnośnik z karty
otwiera ją od razu z wyszukanym kodem uczestnika).

**Kto widzi skany.** Wyłącznie koordynator. Recenzenci i komisja odwoławcza nie mają wstępu do tych
adresów (403) i w swoich paczkach dostają sam filtr — anonimowe pliki, bez skanu, nazwiska i szkoły.

**Audyt.** `student_status.uploaded`, `.viewed`, `.accepted`, `.rejected`, `.infected` — bez treści
powodu odrzucenia i bez nazwy pliku od uczestnika.

**RODO.** Rejestr czynności dostaje przy włączonej funkcji wiersz „Weryfikacja statusu ucznia” (§ 9.2);
eksport danych uczestnika (art. 15/20) niesie sekcję `zaswiadczenia_statusu_ucznia` i same pliki;
retencja i usunięcie konta — § 9.1.

---

## 10b. Delegacje krajowe — `/coordinator/delegations/`

**Tylko w konkursie z trybem rejestracji „przez delegacje krajowe”** (olimpiada międzynarodowa `iqo`;
`OPERACJE.md` § 28). W każdym innym konkursie — także w Olimpiadzie Kwantowej — tego ekranu nie ma,
a uczestnicy rejestrują się sami jak dotąd.

**Jak to działa.** Uczniów nie rejestruje uczeń, tylko **opiekun drużyny narodowej** (team leader).
Zapraszasz opiekuna adresem e-mail i krajem; opiekun zakłada konto z zaproszenia i zgłasza uczniów
swojego kraju; każdy uczeń dostaje list z linkiem, ustawia hasło i **sam** składa zgody. Kraj może mieć
kilku opiekunów — prowadzą jedną drużynę, z jednym limitem, i widzą tych samych uczniów.

**Lista delegacji.** Kraj, liczba opiekunów, zaproszenia oczekujące, uczniowie / limit, stan. Formularz
„Zaproś opiekuna” zakłada delegację kraju przy pierwszym zaproszeniu. Ponowne zaproszenie tego samego
adresu wysyła nowy link (stary przestaje działać). „Eksport CSV” – opiekunowie i uczniowie wszystkich
krajów, jeden wiersz na osobę (zdarzenie w audycie).

**Ekran delegacji.** Limit uczniów (nie niższy niż liczba zgłoszonych), stan „otwarta/zamknięta”
(zamknięta zamraża listę: opiekun nie dodaje, nie poprawia i nie usuwa uczniów), notatka koordynatora
(opiekun jej nie widzi). Opiekunowie (przycisk „Odwołaj z delegacji” – konto zostaje, uczniowie zostają
w drużynie), zaproszenia nieprzyjęte („Wyślij ponownie”, „Cofnij”), uczniowie ze stanem konta
(zaproszone / aktywne) i informacją, który opiekun ich zgłosił.

**Co może opiekun.** Dodać ucznia (imię, nazwisko, e-mail, data urodzenia, szkoła, klasa, opcjonalnie
e-mail rodzica), poprawić dane **przed** aktywacją konta ucznia, wypisać ucznia **przed startem
pierwszego etapu** edycji, wysłać link ponownie. Wypisanie ucznia, który **uruchomił już konto**, nie
usuwa konta: uczeń trafia do sekcji „Wypisani przez opiekuna – czekają na decyzję” na ekranie delegacji
(i do kolumny „Wypisani” na liście), dostaje o tym wiadomość, a o dalszym losie konta decydujesz Ty. Nie widzi prac, ocen ani uczniów innych krajów.
Okno rejestracji edycji (`/coordinator/registration/`) obowiązuje także opiekunów – pulpit pokazuje,
czy jest teraz otwarte.

**Czego opiekun nie może.** Zgłosić adresu, który ma już konto w serwisie (uczeń z istniejącym kontem
trafia do drużyny przez organizatora), złożyć zgód za ucznia ani potwierdzić zgody rodzica — tę uczeń
niepełnoletni zbiera sam po uruchomieniu konta (zgoda opiekuna online).

## 10c. Statystyki szkół — `/coordinator/school-stats/`

**Tylko w konkursie z włączoną funkcją** (przełącznik `school_statistics`, włącza go operator —
`OPERACJE.md` § 29). Menu → *Raporty* → **Statystyki szkół**.

**Co jest na ekranie.** Wybór edycji (domyślnie bieżąca) i kolejności (liczba uczestników, wyniki,
nazwa), a pod nim:

- **ranking szkół** — liczba uczestników edycji, zmiana wobec poprzedniej edycji, a dla każdego etapu
  z **ogłoszonymi** wynikami: średnia punktów i liczba awansujących. Szkoła spoza wykazu RSPO
  (nazwa wpisana ręcznie) ma znaczek „spoza wykazu” i nie ma raportu PDF,
- **województwa** — liczba szkół i uczestników (słupek) oraz średnia i awanse w pierwszym etapie
  z pełną publikacją,
- **szkoły do odzyskania** — miały uczestników w poprzedniej edycji, w tej nie mają nikogo; to lista
  adresatów akcji promocyjnej (z numerem RSPO do korespondencji seryjnej, osobny **Eksport CSV**).
  Konta usunięte (zanonimizowane) nie liczą się do żadnej szkoły.

**Próg 5.** Średnia i liczba awansujących grupy mniejszej niż 5 wpisów są ukryte („<5”) — na ekranie,
w **Eksporcie CSV** i w raporcie PDF, bo oba pliki zwykle wędrują dalej (kuratorium, szkoła). Ukryte są
też szkoły, których wynik dałoby się odczytać z różnicy (województwo minus pokazane szkoły ≤ 4 osoby;
szkoła, w której poza uczniami jej opiekunów są 1–4 osoby), a średnia pokazuje się od 5 wyników.
Statystyka etapu jest **zamrożona w chwili publikacji** — późniejsza zmiana szkoły ucznia jej nie zmienia.
Liczba uczestników jest widoczna zawsze. Punkty pochodzą z ogłoszonej tabeli (stan z chwili
publikacji); etap ogłoszony jako „tylko awansujący” nie ma średnich.

**Raport PDF szkoły** (odnośnik „PDF” w wierszu) — jedna kartka dla dyrektora: etapy edycji, szkoła na
tle województwa i całej olimpiady, udział szkoły w kolejnych edycjach. Bez nazwisk i kodów
uczestników. Pobranie CSV i PDF zostaje w audycie (`export.generated`,
`school_stats.report_downloaded`).

**Co widzi opiekun szkolny** (`/supervisor/statistics/`, przycisk na jego pulpicie): uczniów, którzy
wskazali jego adres (ta sama reguła co pulpit — § 10), z zapisem, oddaniem i terminem w każdym etapie,
a po ogłoszeniu wyników także punkty i awans; porównanie z województwem i całą olimpiadą oraz wykres
średnich w kolejnych edycjach. **Agregat szkoły i raport PDF** dostaje wyłącznie opiekun ze szkołą
wybraną z wykazu i **zweryfikowaną** przez organizatora (karta opiekuna, pole „dane szkoły
zweryfikowane”). U opiekuna próg ma drugi warunek: grupa jest ukryta także wtedy, gdy poza jego
uczniami jest w niej od 1 do 4 osób — inaczej z różnicy dałoby się wyliczyć wynik „obcego” ucznia.

**RODO.** Rejestr czynności dostaje przy włączonej funkcji wiersz „Statystyki szkół i opiekunów
szkolnych” (§ 9.2). Nowych danych funkcja nie zbiera.

## 10d. Logistyka finału — `/coordinator/logistics/`

**Tylko w konkursie z delegacjami i włączoną logistyką** (`OPERACJE.md` § 31). Opiekunowie drużyn
uzupełniają dane każdej osoby z delegacji – uczniów, siebie i dopisanych przez siebie obserwatorów
i gości: dokument podróży (do zaproszenia wizowego), przyjazd i wyjazd, zakwaterowanie, (za decyzją
organizatora) wyżywienie i zdrowie, rozmiar koszulki, kontakt alarmowy i zdjęcie do identyfikatora.

**Kto co widzi.** Każdy koordynator widzi zakładkę „Przegląd” (ile osób w kraju ma braki w każdej
sekcji, ostatnie przypomnienie, obecność) i „Ustawienia i dostęp”. **Dane osób** (paszporty, zdrowie,
przyloty, pokoje) widzi wyłącznie **oficer logistyki** – koordynator z przydziałem. Pierwszego oficera
nadaje dowolny koordynator, kolejnych – superkoordynator albo oficer; każde nadanie i odebranie jest
w dzienniku zdarzeń. **Obsługa rejestracji** (wolontariusze z kontem w serwisie) dostaje osobny
przydział od oficera i widzi tylko ekran skanowania: imię, nazwisko, kraj, rolę i zdjęcie.

**Terminy.** Każda z pięciu sekcji ma własny termin. Po terminie opiekun widzi sekcję tylko do odczytu;
poprawki wprowadza oficer na karcie osoby (zmiana zostaje w historii karty – nazwy pól, bez wartości).
„Przypomnij” (oficer) wysyła opiekunom kraju e-mail w ich języku z listą osób i sekcji z brakami
(bez danych) i terminami ze strefą czasową. Dopóki finał nie ma ostatniego dnia, serwis nie przyjmuje
danych paszportowych ani o zdrowiu. Goście delegacji zamykają się razem z terminem dokumentu podróży.

**Zakładki oficera.**
- *Osoby* – wszyscy z brakami, filtr kraju, karta osoby (pełne dane, zdjęcie, pokój, nowy identyfikator,
  list imienny, historia zmian), „Identyfikatory PDF” dla wybranego kraju, „Eksport pełny CSV”
  (z paszportami – w audycie).
- *Przyjazdy* – tablica przylotów i odlotów per dzień, zgrupowana po godzinie, lotnisku i numerze lotu
  (jeden wiersz = jeden odbiór), CSV.
- *Pokoje* – pokoje (budynek, numer, liczba miejsc, płeć: kobiety / mężczyźni / dowolna – tylko
  dorośli), przydziały, nieprzydzieleni. Serwis nie pozwoli: przekroczyć liczby miejsc, położyć
  **niepełnoletniego w pokoju z dorosłym**, osoby niepełnoletniej w pokoju „dowolna płeć” ani osoby
  innej płci w pokoju z płcią. Wiek liczony na pierwszy dzień finału; uczeń bez daty urodzenia jest
  traktowany jak niepełnoletni; niepełnoletni z płcią „inna” mieszka sam. Zmiana płci, daty urodzenia
  albo „bez noclegu” zdejmuje niepasujący przydział; zmiana daty finału oznacza pokoje z naruszeniem
  (czerwona etykieta, kolumna w CSV). Rooming list CSV dla hotelu.
- *Wyżywienie* – liczby diet, lista alergii i uwag do diety dla kuchni (CSV bez uwag medycznych).
- *Koszulki* – rozmiar × rola, CSV dla drukarni.
- *Listy wizowe* – „Wystaw list dla delegacji” (osoby z kompletnym dokumentem podróży) albo imienny
  z karty osoby; numer `PREFIKS/ROK/NNNN`, rejestr z datą i wystawcą. List to PDF z angielskim tekstem
  (albo szablon „list zapraszający (wiza)” na ekranie „Szablony dokumentów”), tabelą osób, podpisami
  z szablonu graficznego dyplomów i pieczęcią elektroniczną, jeśli jest skonfigurowana. Opiekun pobiera
  listy swojej delegacji ze swojego panelu.

**Dane o zdrowiu.** Sekcja „Wyżywienie i zdrowie” pojawia się dopiero po włączeniu „zbieraj potrzeby
szczególne” (`/coordinator/venues/`). Opiekun zapisuje ją wyłącznie po zaznaczeniu, że osoba (albo jej
rodzic) wyraziła wyraźną zgodę; zgodę można wycofać – dane znikają od razu.

**Po finale.** Po ostatnim dniu finału i okresie retencji (domyślnie 30 dni) wszystkie dane osób,
zdjęcia i dane paszportowe z listów są usuwane automatycznie; zostaje rejestr numerów listów.

**Wnioski o listy zapraszające (VISA-01).** Opiekun drużyny nie musi pisać do organizatora o list:
na stronie „Listy zapraszające (wiza)” w swoim panelu zaznacza osoby z kompletnym dokumentem podróży,
wybiera język listu (angielski albo – jeśli konkurs ma taki język interfejsu – polski, hiszpański,
francuski, portugalski, rosyjski, indonezyjski) i składa wniosek. Oficer logistyki widzi wnioski
w zakładce *Wnioski o listy* (filtry: kraj, stan; eksport CSV bez danych paszportowych):

- zaznacza wnioski i klika **„Zatwierdź i wystaw listy”** – każdy wniosek to list imienny z nowym
  numerem i kodem weryfikacyjnym; wcześniejszy ważny list imienny osoby zostaje unieważniony
  („zastąpiony listem …”) **tylko wtedy, gdy zmienił się numer paszportu, nazwisko albo obywatelstwo** –
  kolumna „List” pokazuje to przed kliknięciem („unieważni list …”). Ta sama reguła obowiązuje przy
  liście imiennym wystawionym z karty osoby. List delegacji z nieaktualnymi danymi rejestr oznacza
  „nieaktualne dane: …” – nowy list i ewentualne unieważnienie starego to decyzja oficera. Wniosek, którego nie da się zatwierdzić (opiekun skasował numer paszportu,
  finał nie ma dat), zostaje oczekujący, a ekran mówi dlaczego,
- albo wpisuje powód i klika **„Odrzuć zaznaczone”** – powód dostaje opiekun e-mailem i widzi go
  w panelu; po poprawce składa nowy wniosek.

Opiekunowie delegacji dostają **jeden e-mail na decyzję** (przy decyzji hurtowej – zbiorczy), każdy
w swoim języku. W e-mailu nie ma danych paszportowych.

**Weryfikacja i unieważnienie.** Na każdym liście jest ramka „Verification” z kodem QR i 12-znakowym
kodem (np. `ABCD-EFGH-JKMN`). Konsulat skanuje kod albo wpisuje go na stronie `/visa/verify/` i widzi:
numer i datę listu, stan (**ważny** albo **unieważniony**), wydarzenie z datami oraz imię i nazwisko
i obywatelstwo osób z listu – **bez** numeru paszportu i daty urodzenia. W rejestrze listów (*Listy
wizowe*) przy każdym liście jest kod, język i przycisk **„Unieważnij”** (powód obowiązkowy – widzi go
opiekun, nie konsulat). Unieważnionego listu nie da się już pobrać; strona weryfikacji od razu mówi
„unieważniony”. Poprawiony list to zawsze nowy numer i nowy kod. Wypisanie osoby z delegacji (także
usunięcie gościa) unieważnia jej listy imienne samo, z powodem „osoba wypisana z delegacji”.
Strona weryfikacji działa także po wyłączeniu logistyki finału – dopóki konkurs ma wystawione listy.
Po zmianie domeny albo prefiksu konkursu poproś operatora o przekierowania (`OPERACJE.md` § 31.8).

## 10e. Okna czasowe etapu — `/coordinator/stages/<id>/windows/`

**Tylko w konkursie z włączonymi oknami czasowymi** (flaga `stage_time_windows`, włącza operator –
`OPERACJE.md` § 32). Pozycja „Okna czasowe” stoi pod etapem w menu (nie ma jej przy rozmowach
i treningu).

**Po co.** Etap zdalny olimpiady międzynarodowej rozkładasz na kilka startów w ciągu doby (np. trzy
okna co 8 godzin), każdy z **tym samym** czasem pracy (np. 5 h). Kraje trafiają do okien według swojej
strefy, więc nikt nie pisze w środku nocy.

**Włączenie.** Przed otwarciem etapu (po otwarciu treść zadań była już jawna dla wszystkich): czas pracy,
start pierwszego okna (czas polski), liczba okien, odstęp i „preferowana godzina startu w kraju”
(domyślnie 10:00). Okna z dodatkowym czasem uczniów muszą mieścić się w ramie etapu
(otwarcie – termin oddania); rama nadal decyduje o zamknięciu etapu, recenzjach, reklamacjach
i publikacji wyników. Ramy nie da się potem zawęzić tak, żeby wycięła okno.

**Przydział.** Kraj trafia domyślnie do okna, którego start w strefie jego stolicy jest najbliżej
godziny preferowanej. Strefę kraju wielostrefowego (USA, Kanada, Rosja, Brazylia, Australia, Meksyk,
Indonezja…) poprawisz w tabeli „Kraje”; okno kraju – tamże. Uczeń bez delegacji trafia do **ostatniego**
okna – konto niepodpięte do drużyny nie może zobaczyć zadań wcześniej niż ktokolwiek (inne okno ustawisz mu
wyjątkiem). Od startu pierwszego okna przydział domyślny krajów jest zapisywany na stałe (ekran pokaże go
jako „ręcznie”) – późniejsza zmiana strefy albo mapy stref nie przenosi kraju. Zadania przed końcem
ostatniego okna widzi wyłącznie uczeń **zgłoszony do etapu**.

**Wyjątki uczniów.** Po kodzie uczestnika: inne okno i/lub dodatkowy czas (dostosowanie, awaria łącza),
zawsze z powodem. **W powodzie nie wpisuj danych o zdrowiu** („dostosowanie wg decyzji komisji” wystarczy –
dokumentacja zostaje poza platformą).

**Reguły czasu (pilnuje ich serwer, nie tylko ekran).**
- okna, czas pracy, godzina preferowana – do startu pierwszego okna (okna nie mogą na siebie nachodzić);
  wyłączenie trybu – do otwarcia etapu,
- przydział kraju lub ucznia – tylko gdy **ani stare, ani nowe** okno się jeszcze nie zaczęło,
- dodatkowy czas – do końca obecnego terminu ucznia,
- zmiana strefy kraju w trakcie zawodów nie przesuwa krajów w etapach już rozpoczętych.
Każda zmiana zostaje w dzienniku zdarzeń (`time_windows.*`).

**Co widzi uczeń.** Kartę „Twoje okno” (start i koniec w jego strefie), odliczanie do **swojego** startu
i terminu, zadania (karty, PDF) dopiero od startu swojego okna, test online tylko w swoim oknie
(dodatkowy czas wydłuża też podejście do testu). Godziny w panelu ucznia są w jego strefie (ustawia ją
opiekun drużyny; domyślnie strefa kraju) – wyłącznie w panelu uczestnika i wyłącznie dla konta bez roli
personelu; Twój panel koordynatora jest zawsze w czasie polskim. Test, który pokazuje wynik „od razu”,
ekran okien oznacza ostrzeżeniem – przy oknach ustaw „po zamknięciu testu”.

**Ochrona przed przeciekiem.** Strona „Zadania”, API i archiwum pokazują treść dopiero po końcu
ostatniego okna (z dodatkowym czasem); forum i wiadomości są przez cały czas okien w premoderacji
(od startu pierwszego okna); wyników nie opublikujesz przed końcem ostatniego okna; wynik testu
„po zamknięciu” też dopiero wtedy.

**Ryzyko jednego zestawu zadań.** Wszystkie okna mają te same zadania. Platforma zamyka swoje kanały,
ale nie powstrzyma ucznia z okna A przed przekazaniem treści uczniowi z okna C poza nią (komunikator,
telefon). Środki organizacyjne: oświadczenie uczestnika, krótkie odstępy między oknami, kontrola
podobieństwa prac po etapie. **Faza 2 (nie zbudowana):** zestawy wariantowe – osobny
zestaw zadań na okno (wariant przy zadaniu i przy oknie, ta sama skala), zwykle przy dwóch–trzech oknach.

**Liczby na żywo.** Oś czasu pokazuje dla każdego okna: kraje, uczniów, „teraz piszą” i „oddali”; niżej
lista „kto w którym oknie” ze źródłem przydziału (kraj, ręcznie, wyjątek).

## 10f. Tłumacze interfejsu — `/coordinator/translators/`

Tylko w konkursie z **więcej niż jednym językiem interfejsu** (np. IQO); pozycja „Tłumacze interfejsu”
w sekcji „Ustawienia”. Tłumaczenia poza angielskim są maszynowe – ten ekran pozwala oddać ich przegląd
ludziom, którzy znają język, np. kierownikom delegacji.

- **Nadaj rolę:** adres e-mail konta, język (do wyboru są języki interfejsu Twojego konkursu), poziom
  „tłumacz”. Konto musi należeć do Twojego konkursu (członkostwo albo profil uczestnika); w innym
  wypadku ekran odpowie, że konta nie znaleziono.
- **Rola należy do konkursu:** widzisz (i możesz odebrać) każde nadanie swojego konkursu, także
  wykonane przez innego koordynatora. Gdy osoba przestaje należeć do konkursu (wypisanie, odebranie
  roli), rola tłumacza przestaje działać sama – wiersz zostaje na liście, żebyś mógł go usunąć.
- **Tłumacz** widzi pod `/translations/` listę napisów swojego języka (tekst polski, angielski, obecne
  tłumaczenie, miejsce w kodzie), proponuje poprawki i głosuje na cudze. W stopce ma „Zgłoś tłumaczenie”.
- **Recenzent tłumaczeń** (zatwierdza poprawki) – tę rolę nadaje wyłącznie **superkoordynator**, bo
  zatwierdzona poprawka zmienia napis na **całej platformie**, we wszystkich konkursach.
- **Odbierz** usuwa rolę od razu. Tłumacze nie widzą nawzajem swoich kont ani danych uczestników.

Poprawki trafiają do repozytorium okresowo (operator, `docs/OPERACJE.md` § 33.3). Napisy ekranów
koordynatora zostają po polsku i nie są przedmiotem przeglądu.

---

## 10g. Tłumaczenia zadań — `/coordinator/translations/`

**Tylko w konkursie z delegacjami krajowymi** (menu „Etapy → Tłumaczenia zadań”; `OPERACJE.md` § 34).
Opiekunowie drużyn tłumaczą zadania z wersji oficjalnej (angielskiej) na języki swoich delegacji,
a Ty (komisja) zatwierdzasz tłumaczenia. Po otwarciu etapu uczeń widzi zadanie w zatwierdzonym języku
swojej drużyny i zawsze także wersję oficjalną.

**Okno tłumaczeń.** Dla każdego etapu ustalasz otwarcie i zamknięcie okna — zamknięcie najpóźniej
w chwili otwarcia etapu (gdy przesuniesz etap wcześniej, okno zgaśnie razem z jego otwarciem). Tylko
w oknie opiekun widzi treść zadań. Wybierasz też tryb: **osobne** (każda delegacja ma własne
tłumaczenie) albo **wspólne** (delegacje jednego języka, np. Niemcy i Austria, pracują na jednym
tekście). Tryb ustala się przed pierwszym tłumaczeniem.

**Wersja oficjalna.** PDF i tytuł zmieniasz jak dotąd na ekranie zadań; przy zadaniu jest też
„Tekst oficjalny” (Markdown, wzory w `$…$`), który tłumacz widzi obok edytora i może skopiować.
Każda zmiana podnosi numer wersji — tłumaczenia starszej wersji dostają znacznik „nieaktualne”,
a ich opiekunowie list. Nieaktualnego tłumaczenia nie zatwierdzisz: zwróć je do aktualizacji.

**Przegląd.** „Do przeglądu” na ekranie głównym: tłumaczenie obok wersji oficjalnej, różnice wobec
poprzedniej wysłanej wersji, historia. „Zatwierdź” blokuje tłumaczenie; „Zwróć do poprawy” wymaga
komentarza (opiekun czyta go w panelu — list mówi tylko, że jest zwrot). Zwrot zatwierdzonego
tłumaczenia nie zabiera go uczniom, dopóki nie zatwierdzisz nowej wersji.

Jeśli opiekun wyśle nową wersję, gdy masz otwarty ekran przeglądu, „Zatwierdź”/„Zwróć” odmówi
i pokaże bieżącą wersję — decyzja zawsze dotyczy tekstu, który widzisz.

**Eksport na finał.** Ekran etapu → „Eksport do druku”: dla każdego języka (w trybie osobnym — każdej
delegacji) **PDF** złożony z zatwierdzonych wersji albo **Widok do druku** w przeglądarce. Tekst ze
wzorami albo po chińsku, w hindi, bengalsku czy arabsku drukuj z widoku do druku („Zapisz jako PDF”).

**Poufność.** Każde otwarcie, pobranie i eksport jest w „Audycie” (akcje `translation.…`); pliki PDF
pobrane przez opiekunów mają znak wodny kraju.

---

## 10h. Płatności — `/coordinator/payments/`

**Tylko w konkursie z włączonymi opłatami** (flaga `fees`; `OPERACJE.md` § 35). Olimpiada Kwantowa jest
bezpłatna i tego ekranu nie ma.

**Kto płaci.** W olimpiadzie międzynarodowej (tryb delegacji) płaci **delegacja**: opiekun drużyny
wystawia w swoim panelu fakturę pro forma i płaci kartą (Stripe), przez Przelewy24 (tylko PLN) albo
przelewem. W konkursie z rejestracją otwartą płaci **uczestnik** – należność nalicza ekran „Wpisowe”
(`/coordinator/fees/`), a uczestnik dostaje na kaflu „Wpisowe” przycisk „Zapłać online”.

**Cennik i ustawienia** (`Cennik i ustawienia`). Najpierw dane sprzedawcy: NIP/VAT ID, rachunek
(IBAN, SWIFT, bank), prefiks numeracji, adnotacja VAT (np. podstawa zwolnienia – **system nie liczy
podatku**; brzmienie ustala księgowa), uwagi na dokumentach, termin płatności pro formy i włączone
metody. Nazwa, adres i dane rejestrowe sprzedawcy pochodzą z danych organizatora konkursu. Potem cennik
delegacji edycji: waluta (dla IQO – EUR), „cena wczesna do” i „cena późna od” oraz siatka cen: opłata za
delegację, za ucznia, za opiekuna i za obserwatora, w trzech okresach. Puste pole okresu = cena
podstawowa; puste pole ceny podstawowej = pozycja bezpłatna. Zmiana cennika **nie zmienia** wystawionych
pro form.

**Jak liczymy delegację.** Skład = 1 delegacja + uczniowie zgłoszeni w panelu + opiekunowie + obserwatorzy
zadeklarowani przez opiekuna. Pro forma obejmuje to, czego nie obejmują wcześniejsze zamówienia (otwarte
albo zapłacone): drużyna dopisująca ucznia po terminie „late” zapłaci za niego cenę późną, a wcześniej
opłaceni zostają przy swojej cenie. Zmiana składu przed zapłatą: opiekun (albo Ty) anuluje zamówienie
i wystawia nowe; anulowana pro forma zostaje w rejestrze ze swoim numerem.

**Zniżki i zwolnienia** (ekran delegacji). Zniżka kwotowa zmniejsza kolejne zamówienia (raz); zwolnienie
blokuje wystawianie nowych zamówień (otwarte anuluj, zapłacone zwróć). Uzasadnienie jest obowiązkowe,
cofnięcie wymaga osobnego powodu; obie decyzje są w audycie. Zwolnienie i umorzenie **uczestnika** –
na ekranie „Wpisowe”, jak dotąd.

**Pulpit.** Sumy osobno dla każdej waluty (wystawiono, zapłacono, zwrócono, czeka na wpłatę, jeszcze
niewystawione), delegacje ze stanem (rozliczona, czeka na wpłatę, do wystawienia, zwolniona), lista
zamówień i **„Do wyjaśnienia”**: wpłata, której kwota albo waluta nie zgadza się z zamówieniem, podwójna
wpłata tego samego zamówienia albo wpłata na zamówienie anulowane (do zwrotu) i zwroty nieudane albo
w toku. Opiekun odwołany z delegacji traci wgląd w zamówienia i dokumenty swojej delegacji. „Eksport CSV dla księgowości” –
jeden wiersz na zamówienie z nabywcą, VAT ID, numerami pro formy i faktury.

**Zamówienie.** Pozycje, nabywca, dokumenty (PDF), wpłaty i zwroty. **Wpływ przelewu**: gdy na wyciągu
jest przelew z kodem zamówienia na właściwą kwotę – data wpływu, notatka i opcjonalnie dowód wpłaty
(skanowany antywirusowo; do pobrania po werdykcie „czysty”). Zapis tworzy fakturę i wysyła płacącemu
potwierdzenie w jego języku. Przelew zapisujesz wyłącznie na zamówienie **otwarte** – przelew
z kodem anulowanej pro formy zwróć płacącemu w banku. **Zwrot**: wskazujesz **pozycje i ilości**
(np. 1 × uczeń, gdy uczeń zrezygnował) i powód – kwotę liczy system; zwrócone miejsce przestaje być
opłacone, więc zastępca tego ucznia zapłaci za siebie. Wpłatę „do wyjaśnienia” zwracasz w całości.
Karta i Przelewy24 – zwrot zlecany u operatora (gdy operator nie odpowie, system ponowi go sam),
przelew – zapis zwrotu wykonanego przez Ciebie w banku. Pełny zwrot przywraca pozycje do zapłaty,
a u uczestnika ustawia należność jako „zwrócone”.

**Faktury.** Pro forma powstaje przy wystawieniu zamówienia, faktura – automatycznie po wpłacie.
Numeracja ciągła, osobno dla pro form i faktur, per konkurs i rok (`IQO/FV/2026/0001`). Dokument jest
w języku płacącego (języki bez czcionek w PDF – chiński, hindi, arabski, bengalski – po angielsku).
**System nie jest programem księgowym** (decyzja D15 po zmianie z 4.10.2026): numeruje dokumenty, ale
nie liczy VAT, nie prowadzi rejestru VAT/JPK i nie wystawia faktur korygujących – korektę po zwrocie
wystawia księgowość organizatora. Wzór dokumentu zatwierdź z księgową przed pierwszym konkursem z opłatami.

**Czego nie ma.** Płatności częściowych i rat, przeliczeń walut, automatycznego dopasowania wyciągu
bankowego (przelew zapisujesz ręcznie po kodzie). Danych kart system nie widzi – płacący wpisuje je
na stronie operatora płatności.

---

## 10i. Webinary — `/coordinator/webinars/`

Menu **Komunikacja → Webinary** (gdy operator włączył webinary w konkursie). Webinar to spotkanie
z terminem w **pokoju na platformie**: obraz, dźwięk, udostępnianie ekranu, czat i podniesiona ręka
działają w przeglądarce, bez instalowania czegokolwiek. Jeśli ekran mówi „Serwer LiveKit nie jest
skonfigurowany”, poproś operatora o uruchomienie serwera (`docs/OPERACJE.md` § 36).

**Nowy webinar.** Tytuł, opis (stoi w zaproszeniu), początek (strefa konkursu), czas trwania, **odbiorcy**:
wszyscy uczestnicy konkursu, uczestnicy bieżącej edycji, uczestnicy wybranego etapu (bez
zdyskwalifikowanych), komisja (recenzenci i komisja odwoławcza), kapitanowie drużyn (gdy konkurs ma
drużyny); opcjonalnie „także komisja”. **Współprowadzący** – inni koordynatorzy albo członkowie komisji
(np. wykładowca) – wchodzą jako prowadzący. **Nagrywanie** (czy wolno nagrywać), **przypomnienie
e-mailem** (raz, około godziny przed startem), **link dla gości bez konta** (domyślnie wyłączony).

**Przebieg.** Odbiorcy widzą webinar na stronie „Webinary” (pasek panelu `/me/`, karta w panelu
komisji). „**Rozpocznij i wejdź do pokoju**” otwiera pokój; odbiorcy wchodzą od 15 minut przed
początkiem, ale dopiero gdy webinar jest rozpoczęty. **Widzowie nie nadają obrazu ani dźwięku** –
podnoszą rękę, a Ty na liście uczestników klikasz „**Daj głos**” (i „Odbierz głos” po pytaniu).
„Usuń z pokoju” wyprasza osobę i nie wpuszcza jej z powrotem, dopóki na liście obecności nie klikniesz
„Wpuść ponownie” (gościa z nową sesją zatrzyma dopiero „Wygeneruj nowy link”). „**Zakończ webinar**”
zamyka pokój dla wszystkich.

**Nagrania.** „Nagrywaj” (w pokoju albo na ekranie webinaru) – plik MP4 pojawia się kilka minut po
zatrzymaniu („gotowe”). Odbiorcy widzą nagranie dopiero po „**Opublikuj**”; „Wycofaj” je chowa,
„Usuń” (z zaznaczonym potwierdzeniem) kasuje plik na zawsze. Nagranie, które wisi w stanie „nagrywa”, sprawdzisz przyciskiem
„Sprawdź / oznacz jako nieudane”. Uczestnicy widzą przed wejściem informację, że webinar może być
nagrywany, a w trakcie nagrania – czerwony znacznik. Nagrania i lista obecności są kasowane
automatycznie po roku od webinaru (ustawienie operatora).

**Transmisja na YouTube.** Na ekranie trwającego webinaru wklej klucz transmisji z YouTube Studio (albo
pełny adres `rtmp(s)://…`) i „Włącz transmisję”. Klucza nie zapisujemy – przy kolejnej transmisji wpisz
go ponownie.

**Zaproszenie e-mailem** – jednorazowo, do wszystkich odbiorców, którzy nie wyłączyli listów o
webinarach. List nie zawiera żadnego „magicznego linku” – prowadzi na stronę webinarów po zalogowaniu.

**Lista obecności** – kto wszedł, kiedy pierwszy raz i ile minut był w pokoju (z danych serwera
wideo); posłuży też do zaświadczeń o udziale.

W dzienniku zdarzeń: założenie, zmiany, rozpoczęcie, zakończenie, odwołanie, wejścia (rola), danie
i odebranie głosu, nagrania, transmisja, zaproszenie – bez tokenów, kluczy i nazw gości.

---

## 10j. Motyw serwisu — `/coordinator/competition/theme/`

**Tylko w konkursie z włączonym przełącznikiem `themes`** (włącza operator — `OPERACJE.md` § 30). Bez
niego ekranu nie ma, a serwis wygląda jak zawsze (motyw „Klasyczny”).

**Co zmienia motyw.** Kolory, kroje, zaokrąglenia, nagłówek, planszę strony głównej i stopkę stron
publicznych. Panele (uczestnika, recenzenta, Twój) biorą z motywu **kolory i kroje**, ale układ
i formularze zostają takie same — funkcje paneli od motywu nie zależą. Tryb wysokiego kontrastu
wybrany przez uczestnika zawsze wygrywa z motywem.

**Ekran.** Menu → *Ustawienia* → **Motyw serwisu**. Każda karta galerii to jedna wersja motywu
z katalogu platformy (zrzut ekranu, autor, schemat kolorów). Na karcie:

- **warianty układu** (np. nagłówek „minimal” albo „split”) — tylko te, które motyw przewiduje,
- **„Akcent w kolorze marki konkursu”** — kolor z „Ustawień konkursu” zastępuje akcent motywu
  (przyciski, wyróżnienia); obwódka zaznaczenia klawiaturą zostaje kolorem motywu, bo musi być
  widoczna na jego tle,
- **„Podgląd”** — otwiera stronę główną w tym motywie i z tymi opcjami **tylko dla Ciebie** (pasek
  „Podgląd motywu” na górze; inni odwiedzający i Twoje kolejne strony widzą motyw dotychczasowy).
  Podgląd niczego nie zapisuje i wygasa po dobie,
- **„Aktywuj”** — od tej chwili dla wszystkich. Zmiana zostaje w audycie (`theme.activated`).

**Cofnięcie** to aktywacja poprzedniej wersji albo karty **„Klasyczny”** — wersje motywów nie znikają
po wgraniu nowszej.

**Nowy motyw albo poprawka motywu** to paczka ZIP wgrywana przez operatora platformy
(superkoordynatora) w **„Katalogu motywów platformy”** — nie w tym ekranie. Paczka przechodzi
kontrolę bezpieczeństwa i antywirusową; odrzucona zostaje w katalogu z raportem błędów.

### Menu serwisu — przycisk „Menu serwisu”

Menu stron publicznych bez wgrywania czegokolwiek i bez zmian w `/cms/`. Tabela pokazuje każdą
pozycję menu:

- **Pozycja** i przyciski **„W górę” / „W dół”** — kolejność (przyciski od razu zapisują tabelę),
- **Widoczna** — odznacz, żeby zdjąć pozycję z menu (strona dalej działa pod swoim adresem; znika
  też z paska, który przykleja się przy przewijaniu),
- **Nazwa w menu** — osobno dla każdego języka interfejsu konkursu; puste pole = nazwa domyślna
  (tytuł strony albo jego tłumaczenie, np. „Wyniki” → „Results”),
- **Grupa** — przenosi pozycję do listy rozwijanej (jeden poziom; strona główna i pozycje, które
  same mają listę rozwijaną, np. „Dokumenty”, nie wchodzą do grup).

Pod tabelą: **„Dodaj własny odnośnik”** (nazwa w językach, adres `https://…` albo ścieżka serwisu
zaczynająca się od `/`, albo wybór strony serwisu z listy; opcja „otwieraj w nowej karcie”)
i **„Dodaj grupę”**. Adresów `javascript:` ani innych niż `http(s)` system nie przyjmie.
**„Przywróć menu domyślne”** usuwa wszystkie zmiany menu. Strona dodana później w `/cms/` pojawia się
na końcu menu — przesuń ją na właściwe miejsce. Zmiana obowiązuje od razu dla wszystkich.

### Kolory i opcje motywu — przycisk „Kolory i opcje motywu”

Dla motywów z paczki (nie dla „Klasycznego”) — dostosowanie wybranej wersji motywu:

- **Schemat kolorów** — ciemny, jasny albo „jak w systemie odwiedzającego”, jeśli motyw ma obie palety,
- **Logo w nagłówku** i **Kroje pisma** — warianty, które przygotował autor motywu,
- **warianty układu** (te same, co na karcie galerii),
- **kolory** — każdy kolor palety motywu (tło, powierzchnie, tekst, przyciski, akcent…); obok widać
  wartość domyślną motywu.

System sprawdza **kontrast** (WCAG AA): jeśli Twoja zmiana sprawi, że tekst albo obwódka
zaznaczenia będą słabo widoczne, zapis i podgląd zostaną zablokowane z opisem, która para kolorów
jest za słaba. **„Podgląd”** pokazuje stronę główną z tymi ustawieniami tylko Tobie; **„Zapisz”**
— od razu dla wszystkich (wpis w audycie `theme.customized`); **„Przywróć domyślne”** wraca do
kolorów, logo i krojów z paczki. Ustawienia zapamiętywane są osobno dla każdej wersji motywu: możesz
przygotować kolory nowej wersji przed jej aktywacją, a powrót do poprzedniej wersji przywraca jej kolory.
Tryb wysokiego kontrastu wybrany przez odwiedzającego zawsze ma pierwszeństwo.

## 10k. Medale — `/coordinator/medals/`

**Tylko w konkursie z flagą `medals`** (olimpiada międzynarodowa `iqo`; `OPERACJE.md` § 37). Olimpiada
Kwantowa nagradza dalej tytułem laureata i finalisty (§ 8).

**Progi.** Wybierz etap będący rankingiem ostatecznym (zwykle finał). Domyślnie jak na IPhO: złoto —
najlepsze 8 % uczestników, srebro — kolejne 17 %, brąz — kolejne 25 % (łącznie połowa pola). Pula jest
zaokrąglana w górę (8 % z 20 osób to 2 złote medale), zdyskwalifikowani nie liczą się do pola, a wynik
0 nie daje nagrody. **Ten sam wynik zawsze daje tę samą nagrodę** — remis na granicy puli idzie w całości
w górę („na korzyść uczestników”) albo w dół („w granicach puli”), zależnie od ustawienia; kryteria
rozstrzygania remisów etapu ustawiają miejsca, ale nie dzielą medali. Przy polityce „w granicach puli”
na małym albo remisowym polu ekran ostrzega, gdy jakiejś nagrody nie dostaje nikt. **Wyróżnienie** dostaje uczestnik
bez medalu z wynikiem ≥ X % najlepszego wyniku (domyślnie 50 %; puste pole wyłącza kryterium) albo —
jak na IMO — z pełnym rozwiązaniem choć jednego zadania.

**Podgląd i ręczne zmiany.** Tabela pokazuje pule, progi punktowe, liczności i rzeczywiste odsetki,
a przy każdym uczestniku nagrodę wyliczoną i ostateczną. „Zmień nagrodę” wymaga uzasadnienia (nie wpisuj
danych osobowych — widzą je wszyscy koordynatorzy, a uczestnik dostaje je w eksporcie swoich danych).
Zdyskwalifikowanemu ręcznej nagrody nie da się wpisać.

**Ogłoszenie.** „Ogłoś medale” działa dopiero po publikacji wyników etapu i zamraża nagrody, tabelę
publiczną i ranking krajów; potem progów ani zmian nie da się edytować. „Odmroź medale” (z uzasadnieniem)
zdejmuje stronę publiczną do ponownego ogłoszenia. Ogłoszenie odmawia, gdy tabela zmieniła się po publikacji
wyników (sumy, nowy wpis, dyskwalifikacja) — opublikuj wtedy wyniki ponownie. Jeśli po ogłoszeniu opublikujesz wyniki ponownie,
ekran ostrzeże, że medale zostały przy poprzedniej tabeli.

**Strony publiczne.** `/results/<etap>/medals/` — miejsce, podpis wiersza z tabeli wyników (nazwisko
wyłącznie za zgodą), kraj, suma i medal, z filtrem kraju; kraj stoi przy wierszu tylko w trybie
„kod uczestnika” albo przy nazwisku opublikowanym za zgodą (nie przy „inicjałach i szkole”).
`/results/<etap>/countries/` — nieoficjalny ranking krajów: wyłącznie liczby (uczestnicy, złoto, srebro,
brąz, wyróżnienia; suma i średnia punktów oraz miejsce tylko dla krajów z co najmniej 3 wynikami).

**Dokumenty.** „Wystaw dokumenty” — dyplomy medalowe dla nagrodzonych i (opcjonalnie) zaświadczenia
o udziale dla wszystkich, w **języku ucznia** (arabski od prawej do lewej, chiński, hindi, bengalski…).
Język jest przypinany przy wystawieniu (gdy serwer nie składa pisma ucznia — angielski i ostrzeżenie
z numerami). Numer, kod weryfikacyjny, pieczęć i strona `/dyplomy/<kod>/` — jak przy każdym dyplomie;
dyplom niezgodny z ogłoszoną nagrodą strona weryfikacji oznacza jako nieaktualny, a uczeń go nie widzi. Grafikę (tło,
logo, podpisy, osobne tło np. dla złotego medalu) ustawiasz w „Dyplomy: szablony”; nagłówek dokumentu to
nazwa konkursu, chyba że szablon wpisuje własny. Tekst organizatora z „Szablonów dokumentów” obowiązuje
w języku domyślnym konkursu; pozostałe języki mają tłumaczenia wbudowane (maszynowe — przejrzyj przed galą).

**Gala.** „Lista na galę (PDF)” — kolejność wręczania (wyróżnienia, brąz, srebro, złoto), w grupie po
kraju i nazwisku; „Eksport CSV” — cały ranking z nazwiskami. Oba pliki zawierają dane osobowe i każde
pobranie jest zapisywane w audycie.

---

## 10l. Absolwenci i mentoring — `/coordinator/alumni/`

Sieć byłych uczestników (za flagą `alumni` – włącza ją operator, `docs/OPERACJE.md` § 38).

- **Kto dołącza:** pełnoletni uczestnik z osiągnięciem w **zakończonej** edycji na poziomie co
  najmniej tym z ustawień (laureat, finalista, awans do kolejnego etapu albo każdy uczestnik).
  Osiągnięcia liczą się wyłącznie z **ogłoszonych** wyników i wystawionych dyplomów – cofnięcie
  publikacji zdejmuje je z profili. Edycja bieżąca liczy się jako zakończona, gdy wszystkie jej
  etapy mają ogłoszone wyniki.
- **Zgoda:** osobna, z własną treścią; wycofanie jednym przyciskiem usuwa profil od razu.
  Na liście widzisz pełne dane osoby (jak na karcie uczestnika) i możesz **ukryć** profil
  (np. niestosowne bio) – znika z katalogu i ze ściany.
- **Publiczna ściana** `/alumni/` – tylko profile, które absolwent sam oznaczył jako publiczne;
  pokazuje podpis, osiągnięcia, uczelnię i kierunek.
- **Mentoring** (`/coordinator/alumni/mentoring/`): uczestnik bieżącej edycji prosi mentora,
  mentor przyjmuje, rozmowa otwiera się w Wiadomościach. Gdy uczestnik jest **niepełnoletni**:
  przy zasadzie Wiadomości „ta sama grupa wiekowa” każdą wiadomość akceptujesz przed doręczeniem
  (kolejka moderacji Wiadomości); przy „bez ograniczeń” wiadomości dochodzą od razu, ale trafiają
  do przeglądu (postmoderacja). Przy wyłączonych rozmowach uczestników mentoring działa wyłącznie
  z akceptacją każdej wiadomości. Treści rozmów nie ma na ekranie mentoringu – czytasz ją tylko
  w kolejce moderacji. Możesz zakończyć każdą relację (notatka obowiązkowa, obie strony dostają list)
  i zamykać zgłoszenia problemów – zgłoszenie przychodzi do Ciebie e-mailem od razu.
- **Zaproszenia** (`/coordinator/alumni/invitations/`): warsztaty, webinar (wklej adres wydarzenia),
  jury. Filtry: edycje, najniższe osiągnięcie, zainteresowania, tylko mentorzy. „Policz odbiorców”
  niczego nie wysyła. List idzie w języku odbiorcy, z odnośnikiem wypisu; w historii zostaje liczba
  odbiorców, nie ich lista.
- **Ochrona małoletnich:** notatka prośby małoletniego czeka na Twoją akceptację (mentor widzi do tego
  czasu sam temat; notatka nigdy nie idzie e-mailem); opis i odnośniki mentora małoletni widzi dopiero
  po Twojej akceptacji (sekcja „Opisy mentorów do akceptacji”, każda zmiana treści – ponownie);
  notatki i opisy z możliwymi danymi kontaktowymi (telefon, e-mail, @nazwa, komunikatory) mają
  czerwony znacznik, a notatka zakłada automatyczne zgłoszenie. Wiek mentee liczy się ostrożnie z daty
  zapisanej przy akceptacji – zmiana daty urodzenia w trakcie relacji daje zgłoszenie i wpis w audycie,
  ale nie łagodzi kanału. Przy zasadzie „bez ograniczeń” pierwsze 5 wiadomości nowej pary
  dorosły–małoletni i tak czeka na akceptację.
- **Ukrycie profilu mentora** kończy jego trwające relacje i odrzuca czekające prośby. Zakończonej
  relacji nie da się wznowić – uczestnik może wysłać nową prośbę.
- **Gdzie są teraz** (`/coordinator/alumni/stats/`): kraje, uczelnie, kierunki, zainteresowania
  i najwyższe osiągnięcie – grupy mniejsze niż 5 osób są łączone w „inne” (razem z najmniejszymi
  grupami, jeśli „inne” byłoby mniejsze niż 5), liczebność sieci jest zaokrąglona, profile ukryte
  się nie liczą, a przy sieci mniejszej niż 5 osób rozkładów nie ma wcale.

## 10m. Nadzór zdalny etapów online — `/coordinator/proctoring/`

**Tylko w konkursie z przełącznikiem `proctoring`** (włącza operator) i na serwerze LiveKit (ten sam, co
webinary). Nadzór włączasz **osobno dla każdego etapu online** (rozwiązania pisemne, test albo rozmowa
prowadzona w pokoju **LiveKit** – § 4.7; rozmowy na Jitsi nadzoru nie mają).

**Ustawienia etapu:** udostępnienie ekranu i mikrofon (domyślnie wyłączone – to dodatkowe dane), zdjęcie
dokumentu (wył./opcjonalne/wymagane; zdjęcia znikają zaraz po etapie), **nagrywanie kamer (domyślnie
wyłączone)**, zachowanie przy awarii serwera nadzoru – **domyślnie „zamknij”** (uczeń czeka na Twoją
decyzję o innej formie nadzoru); „pozwól” wpuszcza do etapu tylko przy awarii po stronie serwera (albo
po kilku nieudanych połączeniach) – sesja dostaje znacznik z powodem, a odmowa kamery nigdy nie
wystarcza. Dopisek z instrukcją dla uczniów. **Zmiana nagrywania, mikrofonu, ekranu albo zdjęcia po
zebraniu zgód unieważnia je** – uczniowie zgodzą się jeszcze raz, widząc nowe warunki.

**Nadzorujący:** koordynatorzy widzą zawsze wszystkich uczniów. Dodaj członków komisji (widzą uczniów
sobie przydzielonych) i – w olimpiadzie z delegacjami – **opiekunów drużyn**, którzy widzą **wyłącznie
uczniów swojej delegacji**: każda delegacja ma osobny pokój LiveKit, a token opiekuna otwiera tylko jej
pokój. Uczeń delegacji przydzielony członkowi komisji przechodzi do pokoju tego członka komisji (jego
opiekun go wtedy nie widzi). „Rozdziel uczniów bez nadzorującego” przydziela uczniów delegacji ich
opiekunom, a pozostałych po równo komisji; uczniowie przenoszeni w trakcie etapu łączą się ponownie
sami. Usunięcie nadzorującego albo odwołanie opiekuna z delegacji **wyprasza go z pokoju od razu**.
Gdy w grupie „bez przydziału” jest ponad 250 uczniów, ekran ostrzega – rozdziel ich.

**W trakcie etapu** (`/proctoring/<etap>/`): siatka po 12/16/24 kafle (obraz pobierany tylko dla widocznej
strony), wiadomość do ucznia, „pokaż pokój”, „pokaż dokument”, obecny/nieobecny, **incydent** (kategoria,
waga, notatka, czas). Kafel pokazuje też **późny start** (nadzór włączony ponad 15 minut po otwarciu
okna ucznia) i **pracę bez nadzoru z powodem**. Uczeń bez kamery prosi o alternatywę – zatwierdzasz ją
(z ustaleniem, np. „nadzór telefoniczny o 9:00”) albo odrzucasz. **Raport ucznia** i **eksport
incydentów (CSV, z późnym startem i pracą bez nadzoru)** widzą koordynatorzy i komisja odwoławcza; każde
otwarcie raportu i nagrania jest w audycie. W trakcie etapu treść zadań widzą wyłącznie uczniowie etapu
z włączonym nadzorem oraz koordynatorzy i komisja – nikt inny, także niezalogowany.

**Retencja:** zdjęcia dokumentu – po etapie; nagrania, dziennik połączeń, wiadomości i uwagi do próśb –
30 dni po ogłoszeniu wyników i zamknięciu okna reklamacji (najpóźniej 180 dni po etapie). Gdy sprawa
ucznia jest w toku – wpisz „powód wstrzymania usunięcia” przy uczniu. Incydenty, obecność i zgody
zostają w dokumentacji zawodów.

### Ocena skutków dla ochrony danych (DPIA) – nota dla organizatora

Nadzór zdalny to przetwarzanie **wysokiego ryzyka** (art. 35 RODO: systematyczne monitorowanie, wizerunek
osób w większości niepełnoletnich, w ich domach) – **przed pierwszym włączeniem** organizator (administrator
danych) przeprowadza i dokumentuje ocenę skutków. Punkty, które platforma dostarcza do tej oceny:

- **Cel i niezbędność:** samodzielność pracy w etapie online; nadzór tylko dla etapów, w których nie da
  się go zastąpić etapem stacjonarnym; decyzja per etap.
- **Podstawa prawna (do potwierdzenia przez prawnika organizatora):** wyraźna zgoda ucznia (art. 6
  ust. 1 lit. a; dowód: wersja, skrót treści **wraz z ustawieniami etapu**, czas, IP). U osoby
  niepełnoletniej (art. 8 RODO stosowany odpowiednio) – potwierdzona online zgoda opiekuna z rejestracji
  (mechanizm platformy, sprawdzana przy każdym wejściu; jej wycofanie gasi zgodę na nadzór) **oraz**
  oświadczenie ucznia, że opiekun zna informację o nadzorze i się zgadza. Platforma nie wysyła opiekunowi
  osobnej prośby o zgodę na nadzór – **dopisz nadzór zdalny do wzoru zgody opiekuna i polityki
  prywatności** albo wybierz inną podstawę (np. art. 6 ust. 1 lit. e/f – realizacja zadań olimpiady
  i jej rzetelność) i opisz test równowagi. Brak zgody nie wyklucza z zawodów: jest droga alternatywna.
- **Minimalizacja:** brak automatycznej analizy obrazu i śledzenia przeglądarki; kamera 320×240, 10 kl./s;
  ekran, mikrofon i zdjęcie dokumentu – tylko gdy włączysz; nagrywanie domyślnie wyłączone i tylko kamera;
  uczeń nie widzi innych uczniów; pseudonimy w pokojach; wynik sprawdzenia sprzętu bez odcisku urządzenia;
  powód prośby o alternatywę z listy (uwaga tekstowa kasowana z nośnikami).
- **Dostęp:** nadzorujący tylko w swoim zakresie (opiekun – własna delegacja, wymuszone tokenem; odwołanie
  wyprasza z pokoju); raporty i nagrania – koordynator i komisja odwoławcza; audyt dostępu
  (`proctoring.*` w `/coordinator/audit/`).
- **Retencja:** jak wyżej; usuwanie automatyczne; anonimizacja konta kasuje nośniki i wyprasza z pokoju.
- **Odbiorcy:** hosting platformy, serwer LiveKit operatora (najlepiej własna maszyna w UE).
- **Ryzyka do oceny przez organizatora:** obraz domu i osób trzecich w kadrze (instrukcja dla ucznia:
  kadr bez domowników), nadmierne zbieranie przy ekranie/mikrofonie, nierówność dostępu do sprzętu
  (alternatywa), błędne oskarżenie (incydent to notatka człowieka, decyzję podejmuje komisja z prawem
  ucznia do wyjaśnień), praca bez nadzoru przy awarii (domyślnie `block`).
- Wpis w rejestrze czynności („Nadzór zdalny etapów online”) pojawia się sam po włączeniu przełącznika.

## 10n. Notatniki kwantowe — `/coordinator/notebooks/`

Zadanie z notatnikiem Jupytera w przeglądarce (JupyterLite – uczestnik niczego nie instaluje).
Ekran istnieje przy włączonej fladze konkursu `quantum_notebooks` (włącza operator, `docs/OPERACJE.md`
§ 40). Lista pokazuje zadania bieżącej edycji; „Dodaj notatnik” / „Ustawienia” przy zadaniu.

> **⚠ Bezpieczeństwo – przeczytaj, zanim włączysz notatniki.** Laboratorium (JupyterLab
> w przeglądarce) wykonuje kod z notatnika **w domenie serwisu, w przeglądarce osoby, która je
> otworzyła**. Polityka bezpieczeństwa blokuje z niego panele, API i formularze serwisu, ale to nie
> jest pełna izolacja. Dlatego:
>
> - laboratorium otwiera się **wyłącznie na koncie uczestnika bez żadnej roli personelu**.
>   Koordynator, recenzent, członek komisji, opiekun szkolny, opiekun delegacji, superużytkownik –
>   w **którymkolwiek** konkursie na tym serwerze – zamiast laboratorium dostaje **podgląd tylko do
>   odczytu** (kod się nie wykonuje, wyjścia HTML/JavaScript są pominięte);
> - **nigdy nie otwieraj notatnika uczestnika w laboratorium** – ani swoim kontem (nie da się), ani
>   „na chwilę” kontem uczestnika. Prace sprawdzasz podglądem („Podgląd notatnika (tylko do odczytu)”
>   w ekranie oceny i w wynikach), a notatnik startowy i wzorcowy – podglądem w ustawieniach zadania;
> - zadanie przetestuj jako uczestnik na **osobnym koncie testowym bez ról** (nie nadawaj mu potem
>   roli – laboratorium z niego zniknie);
> - uprzedź uczestników (komunikat, regulamin), żeby **nie wklejali do notatnika kodu od innych
>   osób** – wykonuje się na ich koncie.
>
> Operator może przenieść laboratorium na **osobny adres** (`NOTEBOOK_LAB_HOST`, np.
> `lab.olimpiadakwantowa.pl` albo osobna domena – `docs/OPERACJE.md` § 40.7). Wtedy kod z notatnika
> nie widzi sesji, ciasteczek ani danych serwisu, a serwis odrzuca wysyłane z niego żądania; ramka
> ostrzeżenia w ustawieniach notatników podaje ten adres. Ograniczenia ról wyżej zostają do
> osobnej decyzji po odbiorze. Przełączaj **przed** etapem: praca uczniów zapisana w przeglądarce
> pod starym adresem nie będzie widoczna pod nowym.

**Tryby:**

- **Notatnik swobodny** – uczestnik dostaje notatnik startowy przy zadaniu, oddaje plik `.ipynb`,
  ocenia komisja jak każdą pracę (podgląd kodu w ekranie recenzenta działa jak dotąd).
- **Sprawdzanie automatyczne** – jak wyżej, a dodatkowo każda najnowsza wersja pracy jest w ciągu
  minuty od czystego skanu antywirusowego wykonywana na serwerze w piaskownicy i liczona testami.
  Punkty z testów są **podpowiedzią** dla komisji (panel „Testy automatyczne” w ekranie oceny,
  tabela „Wyniki”), a nie oceną – skalę zadania stosują recenzenci. Zadanie musi przyjmować pliki
  `.ipynb` (format w ustawieniach zadania).

**Ustawienia:** język treści notatnika (polski/angielski – szablon notatnika startowego i komunikaty
testów; interfejs JupyterLab jest angielski), limit czasu (5–60 s) i pamięci, kto widzi wynik testów
ukrytych (domyślnie **tylko organizator i komisja**; „po zamknięciu etapu”; „od razu” – tylko na
etapach treningowych, bo daje uczestnikowi wyrocznię), notatnik startowy (własny `.ipynb` do 1 MB
albo szablon; wyjścia komórek są czyszczone) i notatnik wzorcowy.

**Środowisko uczestnika:** `from qiskit import QuantumCircuit` działa, ale to **zgodny podzbiór
Qiskita** (symulator `qclab`, wektor stanu do 20 kubitów, `Operator` do 10), a nie pełny Qiskit –
pełnego nie da się uruchomić w przeglądarce (`docs/tasks/QC-01.md` § 1). Są: bramki
`x y z h s sdg t tdg sx rx ry rz p u cx cy cz ch cp crx cry crz swap iswap rxx ryy rzz ccx ccz cswap
mcx mcp unitary`, pomiar, reset, bariera, parametry, `compose`, `inverse`, `to_gate`, `QFT`,
`Statevector`, `Operator`, `SparsePauliOp`, `StatevectorSampler`/`StatevectorEstimator`,
`BasicSimulator`/`AerSimulator`, rysunek tekstowy, histogram tekstowy, NumPy. Nie ma: sprzętu IBM,
szumu, transpilacji do bazy, OpenQASM, `if_test`, matplotlib. Zadanie układaj tak, żeby nie wymagało
niczego spoza tej listy – i sprawdź to notatnikiem wzorcowym.

**Testy** to lista obiektów JSON (pola „Testy widoczne” i „Testy ukryte”). Przykład:

```json
[
  {"id": "bell", "name": "Stan Bella", "points": 2, "target": "qc",
   "check": "statevector", "expected": {"00": "1/sqrt(2)", "11": "1/sqrt(2)"}},
  {"id": "ghz", "name": "GHZ dla n=4", "points": 2, "target": {"call": "ghz", "args": [4]},
   "check": "probabilities", "expected": {"0000": 0.5, "1111": 0.5}},
  {"id": "depth", "name": "Płytki obwód", "points": 1, "target": "qc",
   "check": "circuit", "max_depth": 2, "allowed_gates": ["h", "cx"]}
]
```

- `target` – nazwa zmiennej z notatnika (`"qc"`) albo wywołanie funkcji ucznia
  (`{"call": "ghz", "args": [4]}`; argumenty w JSON-ie),
- `check`: `statevector` (lista amplitud albo `{"etykieta": amplituda}`; do fazy globalnej, chyba że
  `"global_phase": false`), `probabilities` (`{"00": 0.5}`), `counts` (rozkład wyników pomiaru;
  z obwodu liczony dokładnie, ze słownika zliczeń – z tolerancją odległości, domyślnie 0,05),
  `unitary` (macierz do 6 kubitów), `value` (liczba, lista, napis, wartość logiczna),
  `circuit` (`num_qubits`, `max_depth`, `max_size`, `max_gates` np. `{"cx": 2}`, `allowed_gates`,
  `required_gates`, `measurements`: `required`/`forbidden`),
- liczby można pisać wyrażeniami: `"1/sqrt(2)"`, `"exp(i*pi/4)"`, `"-0.5j"`; `tolerance` – dopuszczalna
  różnica (domyślnie 1e-6),
- etykiety bitów jak w Qiskicie: kubit 0 **z prawej** (`"01"` = kubit 0 w stanie 1),
- test zaliczony = pełne `points`, niezaliczony = 0; wynik pracy = suma testów ukrytych (bez
  ukrytych – widocznych).

**Uczciwość:** testy ukryte nie trafiają ani do przeglądarki, ani do piaskownicy z kodem ucznia –
piaskownica dostaje tylko listę celów („co odczytać”), oczekiwania zna wyłącznie serwer. Testy
widoczne są w notatniku uczestnika (ostatnia komórka) – traktuj je jak przykład, nie jak ocenę.
Błąd w jednej komórce nie przerywa pozostałych (przypadkowe `plot_histogram` nie zeruje zadania).

**Sprawdź testy na wzorcu:** wgraj notatnik wzorcowy i kliknij „Sprawdź testy na wzorcu” – po kilku
sekundach (odświeżenie strony) widać wynik każdego testu i błędy komórek. „Pobierz notatnik jak
uczestnik” daje plik dokładnie w tej postaci, w jakiej dostanie go uczestnik; „Podgląd” pokazuje
notatnik startowy i wzorcowy bez wykonywania.

**Testy na zliczeniach** (`counts` ze słownikiem zliczeń ucznia) mają tolerancję co najmniej
`√(liczba wyników / liczba strzałów)` – poprawne rozwiązanie z 1024 strzałami nie obleje testu przez
szum losowania; poniżej 100 strzałów test nie przechodzi. Gdy potrzebujesz ciasnej tolerancji,
testuj **obwód** (`"target": "qc"`) – jego rozkład serwer liczy dokładnie. Obwody bardzo duże
(np. 20 kubitów i tysiące bramek) serwer odrzuca bez liczenia („obwód za duży do oceny”).

**Komunikaty testów** (różnice, limity bramek) widzisz tylko Ty – w wynikach i szczególe
przebiegu. Recenzent i uczestnik widzą nazwy testów i punkty.

**Wyniki** (`…/results/`): najnowsza wersja każdego uczestnika, punkty za każdy test, suma, CSV,
szczegół przebiegu (błędy komórek, początek wyjścia programu). Po zmianie testów wiersze dostają
znacznik „testy zmienione” – „Przelicz wszystko” liczy je od nowa (wpis w audycie).

## 10o. Deklaracja dostępności — `/dokumenty/deklaracja-dostepnosci/`

Serwis ma w stopce odnośnik „Deklaracja dostępności” (wymóg wzoru deklaracji z ustawy o dostępności
cyfrowej; organizator stosuje ją dobrowolnie, miarą jest WCAG 2.1 AA). Treść przygotował zespół
techniczny jako **projekt** po audycie z października 2026 r. Co zrobić:

1. W `/cms/` → Dokumenty → „Deklaracja dostępności” otwórz podgląd wersji roboczej.
2. Uzupełnij pola w nawiasach kwadratowych: **datę publikacji serwisu**, **osobę kontaktową**,
   adres siedziby (sekcja „Dostępność architektoniczna”). Sprawdź listę „Treści niedostępne” –
   to zobowiązanie wobec czytelnika (np. „opis rysunków na żądanie”), więc ma być prawdziwe.
3. Usuń ramkę „Projekt – do zatwierdzenia”, zmień pole „status” (np. „Obowiązuje od …”) i **opublikuj**.
   Odnośnik w stopce pojawi się sam zaraz po publikacji (wcześniej go nie ma).
4. Raz w roku i po każdej większej zmianie serwisu zaktualizuj datę „ostatniej istotnej aktualizacji”.
   Na żądania zapewnienia dostępności (np. „proszę o opis rysunku w zadaniu 3”) odpowiada się w 7 dni.

Konkurs międzynarodowy (IQO) ma deklarację po angielsku – ten sam układ.

## 10p. Bezpieczeństwo logowania — `/coordinator/security/2fa/`

Logowanie dwuskładnikowe (2FA) to sześciocyfrowy kod z aplikacji w telefonie (Aegis, FreeOTP,
Google Authenticator, menedżer haseł) podawany po haśle. Od SEC-01 serwis **wymaga** go od personelu
konkursów, które przechowują dane wrażliwe: paszporty i dane o zdrowiu delegacji, płatności
i faktury, nagrania nadzoru zdalnego. Ekran jest w menu „Raporty → Bezpieczeństwo logowania”, o ile
operator włączył funkcję (`TWO_FACTOR_ENABLED`, `docs/OPERACJE.md` § 41).

**Kto musi mieć 2FA.**

- zawsze: superkoordynator i konta z dostępem do `/admin/` (ustawienie platformy),
- w konkursie z delegacjami, płatnościami, logistyką finału albo nadzorem zdalnym (tryb
  „automatycznie”): koordynatorzy (także oficer logistyki), opiekunowie drużyn narodowych i osoby
  z przydziałem w logistyce finału (także obsługa rejestracji),
- w trybie „wybrane role”: dokładnie role zaznaczone przez superkoordynatora (np. komitet i komisja
  odwoławcza),
- **nigdy uczestnicy** – mogą włączyć 2FA sami, ale serwis nigdy go od nich nie żąda.

**Okres przejściowy.** Osoba objęta wymogiem ma domyślnie 14 dni od pierwszego wejścia: na każdej
stronie widzi baner z terminem i odnośnikiem „Włącz teraz”. Po terminie serwis wpuszcza ją wyłącznie
na ekran konfiguracji. Okres jest jednorazowy – wyłączenie 2FA go nie odnawia.

**Co widzi koordynator na ekranie.** Obowiązujące role (platformy i konkursu), funkcje wrażliwe
konkursu, długość okresu przejściowego, oraz listę personelu: kto ma 2FA, kto nie i do kiedy ma czas.
Politykę **zmienia wyłącznie superkoordynator** – koordynator mógłby nią poluzować wymóg wobec samego
siebie.

**Twoje konto.** `Twoje konto → Logowanie dwuskładnikowe` (`/account/2fa/`): kod QR, potwierdzenie
kodem, dziesięć kodów zapasowych pokazanych raz (wydrukuj, schowaj poza telefonem). Tam też: nowy
komplet kodów zapasowych i wyłączenie – oba wymagają hasła **i** bieżącego kodu. Na własnym
komputerze możesz zaznaczyć „Nie pytaj o kod na tym urządzeniu do …” (domyślnie 7 dni) – nigdy na
komputerze wspólnym. Po pięciu błędnych kodach z rzędu logowanie kodem jest wstrzymane na 15 minut,
a właściciel konta dostaje list. List przychodzi też po włączeniu, wyłączeniu, nowych kodach
i użyciu kodu zapasowego – jeśli to nie Ty, zmień hasło i daj znać organizatorowi.

**„Zgubiłem telefon”.** Najpierw kod zapasowy (wpisuje się go w to samo pole, co kod z aplikacji).
Bez kodu: na ekranie konta w panelu „Zdejmij drugi składnik”. Konto personelu resetuje **wyłącznie
superkoordynator** (koordynator zobaczy w tym miejscu tylko informację), konto uczestnika lub opiekuna
szkolnego – koordynator. Zanim klikniesz, potwierdź tożsamość inną drogą niż e-mail z tego konta
(telefon, wideo): prośba z przejętej skrzynki wygląda tak samo jak prawdziwa. Reset zostaje w audycie
pod Twoim nazwiskiem, a właściciel dostaje list i po zalogowaniu hasłem od razu konfiguruje 2FA
na nowym telefonie.

### Adresy niedoręczalne — `/coordinator/undeliverable-emails/` (MAIL-02)

**Raporty → „Adresy niedoręczalne”**: konta tego konkursu, na których adres poczta nie dochodzi – serwer
odbiorcy odpowiedział, że skrzynka albo domena nie istnieje (np. `…@gmail.com` z kodem 5.1.1 albo domena
z literówką, której relay nie znalazł). Kolumny: adres (z oznaczeniem konta nieaktywnego – typowy przypadek:
link aktywacyjny nie dotarł), osoba, data pierwszego odbicia, kod i powód z serwera odbiorcy, liczba
odbić chwilowych. **„Pobierz CSV”** – ta sama lista do arkusza (wpis w audycie).

Co robić z wierszem: zadzwonić albo napisać inną drogą, ustalić poprawny adres i wpisać go w **Kontach**
(edycja konta) – zmiana adresu kasuje wiersz sama; konto nieaktywne można przy okazji aktywować ręcznie.
**„Oznacz jako doręczalny”** – gdy uczestnik potwierdził, że adres jest dobry (np. właśnie założył
skrzynkę): wysyłka wraca, a jeśli list znowu odbije, wiersz wróci.

Na adresy z tej listy **nie wychodzą listy nieobowiązkowe** – komunikaty grupowe (licznik komunikatu
liczy je jako obsłużone), powiadomienia forum, wiadomości, webinarów i sieci absolwentów. Aktywacja,
reset hasła, zgody, wyniki i rozmowy idą zawsze. Uczestnik sam widzi po zalogowaniu baner „Nie możemy
dostarczyć poczty na adres …” z przyciskami „Zmień adres e-mail” i „Mój adres jest poprawny”.

**Literówki przy wpisywaniu.** Formularze adresu (rejestracje, zmiana adresu, edycja konta w panelu,
zaproszenie opiekuna drużyny) pytają „Czy chodziło Ci o …?” przy domenach typu `gmial.com`, `o2.plo`,
`.con` – zaznaczasz poprawkę albo wysyłasz formularz jeszcze raz, żeby zostawić adres. Domeny, która nie
istnieje (bez serwera poczty), formularz nie przyjmie. Szczegóły techniczne: `docs/OPERACJE.md` § 52.

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

**Uwierzytelnianie dwuskładnikowe** – już nie „w przygotowaniu”: działa za wyłącznikiem operatora
(`TWO_FACTOR_ENABLED`) i od SEC-01 jest wymagane od personelu konkursów z danymi wrażliwymi – § 10p.

**Integracje zewnętrzne (`apps/integrations`, w przygotowaniu).** Wymiana danych z systemami organizatora.
Cokolwiek się w niej znajdzie, będzie **nowym odbiorcą danych** — czyli wymaga wiersza w rejestrze
czynności przetwarzania (§ 9.2) i, jeśli dane wychodzą poza organizatora, przejrzenia polityki RODO
**przed** uruchomieniem.
