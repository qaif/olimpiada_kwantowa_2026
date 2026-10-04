# Podręcznik uczestnika

Krótka instrukcja dla osoby startującej w zawodach: jak założyć konto, jak oddać rozwiązanie i gdzie
szukać wyniku. Organizator może ją rozesłać w całości albo wkleić z niej fragmenty do własnego listu.

Wszystkie godziny w serwisie są **czasem polskim** i pochodzą z **zegara serwera** — o przyjęciu pliku
rozstrzyga on, a nie zegarek w telefonie. Bieżący czas serwera stoi na stronie `/status/`.

---

## 1. Konto

### Rejestracja — `/register/`

Formularz prosi o: adres e-mail, imię i nazwisko, hasło (dwa razy), telefon, województwo, **szkołę**,
**klasę**, **datę urodzenia** i zgody.

- **szkołę wybiera się z wyszukiwarki** (słownik szkół ponadpodstawowych). Gdy Twojej szkoły w niej nie
  ma, zaznacz „nie ma jej na liście” i wpisz nazwę ręcznie,
- **data urodzenia decyduje o zgodzie opiekuna**: osobie niepełnoletniej formularz dokłada wiersz zgody
  rodzica lub opiekuna prawnego. Pełnoletni jesteś **od dnia osiemnastych urodzin** — w dniu urodzin
  formularz już o zgodę opiekuna nie pyta, dzień wcześniej jeszcze pyta (urodzony 29 lutego: od 1 marca).
  Rozstrzyga serwer i dzisiejsza data w Polsce, a nie zegar Twojego komputera,
- **konto założone przed 22.09.2026** zna sam rocznik, więc zgoda opiekuna bywa tam wymagana przez cały
  rok, w którym kończysz 18 lat. Uzupełnij datę w **„Edytuj dane”** — panel przypomina o tym jednym
  zdaniem — a wiek policzy się dokładnie,
- każda zgoda ma **odnośnik do dokumentu** (regulamin, polityka RODO, wzór zgody opiekuna) — otwiera się
  w nowej karcie,
- zgoda na **publikację imienia i nazwiska** jest nieobowiązkowa i można ją później wycofać,
- formularz ma proste zadanie arytmetyczne do przepisania (ochrona przed rejestracją maszynową). Nie ma
  tu żadnej usługi zewnętrznej ani dodatkowych ciasteczek,
- rejestracja bywa otwarta w określonym oknie czasowym; przed jego otwarciem strona pokazuje datę startu.

Zamiast hasła można użyć **logowania przez Google albo Facebooka**, jeśli organizator je włączył —
przycisk pojawia się wtedy na `/login/` i `/register/`. Zgody wypełnia się i tak.

### Aktywacja adresu

Po rejestracji strona `/register/done/` mówi „sprawdź skrzynkę”. Na podany adres idzie list z linkiem.

> **Link jest ważny 24 godziny i tyle samo żyje nieaktywowane konto.** Po tym czasie konto znika,
> a adres wraca do puli — można zarejestrować się jeszcze raz.

- **listu nie ma?** Zajrzyj do spamu, a potem użyj **„Wyślij link ponownie”** (`/activate/resend/`,
  odnośnik stały na stronie logowania),
- **literówka w adresie?** Zarejestruj się ponownie po upływie 24 godzin albo napisz do organizatora
  (`/support/new/`) — on aktywuje konto ręcznie,
- konto z logowania przez **Google** jest aktywne od razu (Google potwierdza adres); z Facebooka — nie.

### Logowanie, hasło, dane

| Co | Gdzie |
|---|---|
| Logowanie | `/login/` |
| Nie pamiętasz hasła | `/password-reset/` — link z listu jest jednorazowy i ważny 24 h |
| **Zmiana hasła** (znasz aktualne) | `/account/password/` — kliknij swój adres e-mail w pasku konta → sekcja „Hasło” → „Zmień hasło” |
| Twoje dane (imię, nazwisko, telefon, województwo, szkoła, klasa, data urodzenia) | `/me/profile/` — przycisk „Edytuj dane” w panelu |
| **Adres e-mail opiekuna szkolnego** (pole opcjonalne) | `/me/profile/` — dopiero po jego wpisaniu nauczyciel widzi Twój postęp; da się je wyczyścić. Jeśli nauczyciel nie ma jeszcze konta, a organizator włączył tę rejestrację, założy je sam pod `/register/supervisor/` |
| Zmiana adresu e-mail | `/account/profile/` → `/account/email/` — wymaga **aktualnego hasła**; link potwierdzający idzie na **nowy** adres, a dotychczasowy dostaje powiadomienie |
| Język interfejsu i tryb wysokiego kontrastu | dwie ikony w pasku konta, na każdej stronie |
| **Pobranie wszystkich swoich danych** (art. 20 RODO) | `/account/export/` — paczka ZIP z `dane.json` i wgranymi plikami, jedna na 10 minut |
| Usunięcie konta | `/account/delete/` — patrz niżej |

**Zmiana hasła.** Podajesz aktualne hasło i dwa razy nowe (co najmniej 10 znaków, nie popularne, nie
same cyfry, niepodobne do imienia, nazwiska ani adresu). Na tym urządzeniu zostajesz zalogowany,
a **wszystkie inne urządzenia i aplikacje są wylogowane**. Na adres konta przychodzi list „Hasło do
konta zostało zmienione” — jeśli to nie Ty zmieniałeś hasło, od razu ustaw nowe przez „Nie pamiętasz
hasła?” i napisz do organizatora. Kto loguje się przez Google/Facebooka i nie ma hasła, zobaczy tam
przycisk **„Wyślij mi link do ustawienia hasła”** — hasło ustawia się dopiero pod linkiem z listu
(bez hasła nie zmienisz też adresu e-mail). Pięć błędnych haseł z rzędu wylogowuje — zaloguj się ponownie.

**Prośba nauczyciela o dopisanie jako opiekun szkolny** (od v0.38.7). Nauczyciel albo organizator,
który wgrywa listę uczniów, nie może już sam wpisać się w Twój profil. Jeśli na liście jest adres
Twojego konta, dostajesz list „Prośba o zgodę na opiekuna szkolnego” z imieniem, nazwiskiem i adresem
nauczyciela oraz linkiem `/opiekun/zgoda/…` (ważnym 14 dni). Strona po kliknięciu **wymaga zalogowania
na Twoje konto** — nikt inny, także nauczyciel, nie zgodzi się za Ciebie — i pokazuje, kto prosi, co
zobaczy (imię i nazwisko, kod, szkołę, klasę, stan prac; punkty dopiero po ogłoszeniu wyników) oraz,
jeśli masz już opiekuna, że nowy go **zastąpi**. Decydujesz przyciskiem **„Zgadzam się”** albo
**„Nie zgadzam się”**; samo otwarcie strony niczego nie zmienia, a zignorowanie listu też jest
odmową. Na kolejną prośbę tej samej osoby trzeba czekać co najmniej dobę. Decyzję zmienisz w każdej
chwili polem „Adres e-mail opiekuna szkolnego” w `/me/profile/`.

**Usunięcie konta.** Jeśli brałeś już udział w zawodach (zgłoszenie, praca, recenzja), konto jest
**anonimizowane**: znikają imię, nazwisko, adres, telefon, szkoła i data urodzenia, a w ogłoszonych tabelach
zostaje sam kod uczestnika. Konto bez takiego śladu jest kasowane w całości, a adres wraca do puli.
Operacja wymaga podania aktualnego hasła (albo przepisania własnego adresu, gdy logujesz się przez
Google/Facebooka) i **jest nieodwracalna**.

---

## 2. Panel — `/me/`

Nad wszystkim stoi nagłówek **„Co teraz”**: nazwa i stan bieżącego etapu, **jedna** rzecz do zrobienia,
najbliższy termin z odliczaniem i znaczniki stanu konta (konto aktywne, zgody kompletne, opiekun
potwierdził). „Na teraz nic nie musisz robić” jest **normalnym** stanem przez większą część roku.

Cztery zakładki — każda ma własny adres, więc da się je zapisać w zakładkach przeglądarki i wrócić do
nich przyciskiem „wstecz”:

| Zakładka | Adres | Co w niej jest |
|---|---|---|
| **Zadania** | `/me/` | karta etapu, zapis, karty zadań z wysyłką (albo wybór terminu rozmowy), zadania treningowe |
| **Wyniki** | `/me/?tab=wyniki` | punkty, kwalifikacja i komentarze recenzentów — **po ogłoszeniu wyników etapu** |
| **Reklamacje** | `/me/?tab=reklamacje` | formularz przy pracy podlegającej reklamacji i lista własnych zgłoszeń |
| **Zgody** | `/me/?tab=zgody` | zgoda opiekuna, historia zgód, przełącznik publikacji nazwiska |

Obok nich, w tym samym pasku, osobne ekrany: **Wiadomości** (z liczbą nieprzeczytanych rozmów –
rozdział 8a), **Kalendarz**, **Archiwum**, **Dyplomy**, **Profil**.

**Zapis do etapu.** Przycisk **„Zgłoś się do tego etapu”** (w treningu: „Zgłoś się do treningu”). Bez
wpisu nie ma ani zadań, ani wysyłki. Do etapów po eliminacjach zapisuje się **wyłącznie osoba
zakwalifikowana** w poprzednim.

**Wpisowe** (tylko w konkursie, który pobiera opłatę; Olimpiada Kwantowa jest bezpłatna). Kafel
„Wpisowe” pokazuje kwotę, stan i termin. Przycisk **„Zapłać online”** prowadzi do danych do faktury
(Twoje imię i nazwisko albo np. szkoła jako nabywca), potem do faktury pro forma i wyboru zapłaty:
kartą (na stronie operatora płatności – serwis nie widzi danych karty), Przelewy24 albo przelewem
z kodem referencyjnym w tytule. Po zaksięgowaniu wpłaty dostajesz potwierdzenie e-mailem, a fakturę
pobierzesz ze strony zamówienia.

---

## 3. Wysyłka rozwiązania

Każde zadanie ma własną kartę: **„Zadanie N: <tytuł>”**, odznakę stanu, termin, dozwolone formaty, limit
rozmiaru pliku i przycisk **„Treść zadania (PDF)”**.

**Olimpiada międzynarodowa (drużyny krajowe).** Gdy komisja zatwierdziła tłumaczenie zadania na język,
który przypisał Ci opiekun drużyny, na karcie jest też przycisk **„Treść w języku: …”** (np. „Deutsch”).
Pojawia się dopiero po starcie etapu; wersja oficjalna („Treść zadania (PDF)”) jest zawsze obok. Gdy w Twoim
języku nie ma zatwierdzonego tłumaczenia, dostajesz drugi język drużyny (z informacją na stronie), a gdy
wersja oficjalna zmieniła się po zatwierdzeniu tłumaczenia – ostrzeżenie, żeby sprawdzić wersję oficjalną.

### Jak wysłać

1. Przeciągnij plik na ramkę formularza **albo** wybierz go przyciskiem. Pod polem pojawi się
   „Wybrano: *nazwa* (*rozmiar*)” — albo odmowa („niedozwolony format”, „plik jest za duży”) jeszcze
   **przed** wysłaniem bajtów.
2. Zaznacz pole **„Potwierdzam, że to rozwiązanie zadania N i plik jest czytelny”**. Numer jest w treści
   celowo: najczęstsza pomyłka to plik wysłany pod zadanie obok, a druga to nieczytelny skan.
3. Kliknij **„Wyślij rozwiązanie”**.

Przeciąganie i podpowiedzi to **dodatek, nie warunek** — zwykły wybór pliku z dysku działa tak samo,
także przy wyłączonych skryptach.

### Formaty

Formaty i limit ustawia organizator przy zadaniu; typowo są to PDF, zdjęcie **JPEG** (`.jpg`/`.jpeg`)
rozwiązania pisanego ręcznie oraz — przy zadaniach programistycznych — `.py` i `.ipynb`.
**O przyjęciu pliku decyduje jego treść, nie rozszerzenie**: przemianowanie pliku nic nie da.
Zdjęcie ma być **ostre, doświetlone i obrócone tak, jak się czyta** — recenzent ocenia to, co widzi.

### Nowa wersja zastępuje poprzednią

> **Każda wysyłka tworzy nową wersję, a do oceny idzie wyłącznie najnowsza.** Poprzednie zostają
> w historii.

Jeśli Twoja praca jest już czytana przez komitet, nad polem pliku stoi ostrzeżenie: **„Ta praca jest już
w ocenie. Wysłanie nowej wersji anuluje dotychczasową ocenę – zostanie oceniona od nowa.”** Wysyłka jest
wtedy nadal dozwolona — ale dotychczasowe recenzje i ocena przepadają, a praca wraca na początek kolejki.
Po ogłoszeniu wyników albo w trakcie reklamacji nowej wersji wysłać się już nie da.

### Po wysyłce

- **skan antywirusowy**: przy ostatniej wersji stoi jego stan („oczekuje na skan” → „czysty”). Plik
  odrzucony trzeba wysłać ponownie — dostaniesz o tym list,
- **podgląd ostatniej wersji**: liczba stron PDF-a i pierwsza strona, wymiary i sama fotografia dla JPEG-a,
  pierwsze wiersze dla pliku z kodem. Podgląd pojawia się **po** czystym skanie,
- **ścieżka oceniania**: `oddane → w ocenie → oceniona → wyniki`, a pod nią „Ogłoszenie wyników: …”
  (po publikacji jej data, wcześniej termin planowany). **Punktów ścieżka nigdy nie pokazuje**,
- **„Pobierz”** oddaje Twój własny plik pod Twoją nazwą — także wersję jeszcze nieprzeskanowaną,
- **historia wysyłek** jest zwinięta i pojawia się od drugiej wersji.

Listy, które dostajesz: potwierdzenie przyjęcia pliku, informacja o pliku odrzuconym przez antywirusa,
ogłoszenie wyników i rozstrzygnięcie reklamacji. **W listach nie ma punktów** — skrzynka pocztowa nie
jest kanałem zabezpieczonym; wynik jest w serwisie.

### Okna czasowe (olimpiada międzynarodowa)

W etapie zdalnym olimpiady międzynarodowej organizator może rozłożyć etap na kilka **okien czasowych**
(np. trzy starty w ciągu doby) z tym samym czasem pracy dla wszystkich. Twój kraj ma przydzielone jedno
okno; na zakładce „Zadania” stoi karta **„Twoje okno czasowe”** ze startem i końcem w Twojej strefie
czasowej (ustawia ją opiekun drużyny; domyślnie strefa Twojego kraju) i ewentualnym dodatkowym czasem.

- Zadania (karty, PDF) i test online otwierają się **dopiero na starcie Twojego okna**; nagłówek
  „Co teraz” odlicza do Twojego startu, a potem do Twojego terminu. Po terminie wysyłka jest zamknięta.
- Do końca **ostatniego** okna wszystkich krajów nie rozmawiaj o zadaniach – także poza serwisem. Forum
  i wiadomości są wtedy w premoderacji, a wyniki pojawią się dopiero po zakończeniu wszystkich okien.
- Godziny w całym panelu (także w kalendarzu) są w Twojej strefie – obok godziny stoi nazwa strefy.

---

## 4. Rozmowa kwalifikacyjna (gdy etap ma taką formę)

Etap w formie rozmowy nie ma zadań ani wysyłki plików. W karcie etapu w `/me/` wybierasz termin
z listy pogrupowanej po dniach (**„Wybierz termin rozmowy”**) i potwierdzasz.

- masz w etapie **jeden** termin; „Zmień na ten termin” **przenosi** zapis, więc nie trzeba najpierw
  rezygnować (i nikt nie zajmie w międzyczasie ostatniego miejsca),
- **„Zrezygnuj z terminu”** działa do chwili rozpoczęcia rozmowy,
- po zapisie dostajesz list z datą, godziną i **linkiem do pokoju**; link widzisz też w `/me/`
  (celowo **nie ma go** w pliku kalendarza — adres, pod który wchodzi się bez logowania, jest de facto
  poświadczeniem),
- obok linku stoi **„Sprawdź kamerę i mikrofon”** — pusty pokój o tej samej nazwie, w którym nikogo nie
  ma. Wejdź do niego wcześniej, z komputera, w Chrome, Edge albo Firefoksie,
- dzień wcześniej idzie automatyczne przypomnienie.

**Rozmowa na Jitsi olimpiady** (`meet.<domena>`). Jeśli organizator prowadzi rozmowy na własnym
serwerze wideo, w karcie nie ma adresu pokoju, tylko dwa przyciski:

- **„Dołącz do rozmowy”** — działa od **15 minut przed** terminem do godziny po jego końcu. Kliknięty
  wcześniej wraca do panelu z informacją, o której pokój się otworzy,
- **„Sprawdź kamerę i mikrofon”** — pusty pokój „na próbę”, dostępny wcześniej.

Do pokoju wchodzi się **wyłącznie tymi przyciskami, po zalogowaniu**: przycisk wystawia jednorazową
przepustkę na Twój pokój. Sam adres pokoju nie zadziała — nie da się go podyktować przez telefon ani
przesłać dalej. Listy (potwierdzenie i przypomnienie) prowadzą do tych przycisków w panelu. W pokoju
widać Cię jako imię i inicjał nazwiska. Gdy połączenie się zerwie po dłuższej przerwie, wróć do panelu
i kliknij „Dołącz do rozmowy” jeszcze raz.

**Rozmowa w pokoju na platformie (LiveKit).** Organizator może prowadzić rozmowy w pokoju na stronie
olimpiady zamiast na Jitsi – przyciski i zasady są te same („Dołącz do rozmowy”, „Sprawdź kamerę
i mikrofon”), tylko pokój otwiera się na naszej stronie. Jeśli rozmowa jest **nadzorowana zdalnie**,
w panelu zobaczysz kartę „Nadzór zdalny”: przed rozmową wyraź zgodę i sprawdź sprzęt w konsoli nadzoru
(§ 8d), inaczej pokój rozmowy się nie otworzy.

---

## 5. Zgoda opiekuna (dla niepełnoletnich)

W zakładce **„Zgody”** wpisujesz **adres e-mail rodzica lub opiekuna prawnego**. System wysyła na ten
adres list z linkiem ważnym **14 dni**; opiekun otwiera go **bez logowania i bez zakładania konta**,
czyta treść, widzi Twoje imię i szkołę, zaznacza pole i potwierdza. Ty dostajesz list, że zgoda wpłynęła.

- stan („brak / oczekuje / potwierdzona <data>”) widać w tej samej zakładce,
- **„Wyślij ponownie”** jest zamierzone — listy giną w spamie,
- **zmiana adresu unieważnia poprzedni link**; to jedyna droga odwołania wysłanej prośby,
- nie możesz być własnym opiekunem, a od osoby pełnoletniej zgody nie zbieramy w ogóle,
- jest też **wersja do wydruku** pod `/dokumenty/zgoda-opiekuna/`, jeśli opiekun nie ma adresu e-mail.

### 5a. Zaświadczenie o statusie ucznia — `/me/status-ucznia/`

Tylko w konkursach, które o nie proszą: jeśli na pulpicie nie ma kafla **„Zaświadczenie o statusie
ucznia”**, Twój konkurs go nie zbiera. Organizator chce wiedzieć, że **w tej edycji** (w tym roku
szkolnym) jesteś uczniem lub uczennicą szkoły. **Brak zaświadczenia niczego nie blokuje** — rozwiązania
wysyłasz tak samo z nim i bez niego.

1. **„Pobierz wzór (PDF)”** — kartka ma już wpisane Twoje imię i nazwisko, datę urodzenia, szkołę
   i rok szkolny z profilu. Jeśli coś się nie zgadza, popraw to najpierw w profilu (`/profile/`)
   i pobierz wzór jeszcze raz.
2. **Sekretariat szkoły** wpisuje klasę, przystawia pieczątkę, a dyrektor lub sekretarz podpisuje
   i wpisuje datę.
3. **Zeskanuj albo sfotografuj całą kartkę** (PDF, JPG albo PNG, do 10 MB — pieczątka i podpis muszą
   być czytelne), zaznacz potwierdzenie i kliknij **„Wyślij zaświadczenie”**.

Stan widać na tej samej stronie i na pulpicie:

| Stan | Co to znaczy |
|---|---|
| **brak** | nic jeszcze nie wysłałeś(-aś) |
| **oczekuje na weryfikację** | plik dotarł, koordynator go sprawdzi; możesz wgrać inny — zastąpi poprzedni |
| **zaakceptowane** | sprawa załatwiona; dostajesz o tym e-mail, formularza już nie ma |
| **odrzucone** | powód stoi na stronie i w e-mailu; popraw i wgraj nowy plik |

Plik sprawdza skaner antywirusowy. Oglądać go może **wyłącznie koordynator** konkursu — recenzenci
go nie widzą. Zaświadczenie dotyczy **jednej edycji**: w kolejnym roku szkolnym trzeba wgrać nowe.

---

## 6. Wyniki, informacja zwrotna, reklamacja

Po ogłoszeniu wyników etapu:

- **publiczna tabela** `/results/<id etapu>/` — domyślnie **bez nazwisk**, z kodem uczestnika
  (`OLM-XXXXXX`); Twój kod widzisz w panelu,
- **własna informacja zwrotna** `/me/stages/<id etapu>/feedback/` (odnośnik z zakładki „Wyniki”
  i z tabeli): punkty za każde zadanie, komentarze recenzentów, miejsce w tabeli, próg kwalifikacji
  i decyzja. **Recenzenci są anonimowi** — podpisani „Recenzent A/B”,
- **przed publikacją ten adres nie działa** i nie ma sposobu, żeby dowiedzieć się wyniku wcześniej,
- publiczne **statystyki edycji** (rozkłady punktów, średnie, progi): `/statystyki/`.
- **Punkty bywają ułamkami** („4,25”, „7,5”), jeśli organizator tak ustawił etap: dotyczy to ocen
  zadań, punktów za kryteria oceny i **testu online** – wtedy wynik testu wchodzi do tabeli co do
  setnej części punktu. W pozostałych etapach wynik testu jest zaokrąglany do pełnych punktów
  (od połowy w górę: 7,5 → 8), a pytania za ułamek punktu („0,5 pkt”) są oznaczone przy treści.
<!-- Dla organizatora: poniższy punkt rozsyłaj uczestnikom WYŁĄCZNIE wtedy, gdy w panelu włączono
     „Pokaż uczestnikom ocenę AI” dla etapu (PODRECZNIK-ORGANIZATORA.md § 4.12). Przy wyłączonym
     przełączniku uczestnik nie ma się z serwisu dowiedzieć, że ocena AI w ogóle powstała. -->
- **ocena AI** — tylko jeśli organizator ją udostępnia dla danego etapu: pod ocenami recenzentów
  pojawia się sekcja **„Ocena AI (sugestia, niewiążąca)”** z krótkim podsumowaniem i proponowanymi
  punktami przygotowanymi przez model językowy (Claude) jako pomoc dla komitetu. To **nie jest
  ocena**: obowiązują wyłącznie punkty wystawione przez recenzentów, a reklamacja dotyczy oceny
  oficjalnej, nie sugestii.

**Reklamacja** — zakładka **„Reklamacje”**, przycisk **„Złóż reklamację”** przy pracy. Jest możliwa
wyłącznie w **oknie reklamacyjnym** wyznaczonym przez organizatora i dotyczy własnej, ocenionej pracy.
Rozpatruje ją komisja odwoławcza — **inne osoby** niż te, które pracę oceniały. Rozstrzygnięcie
z uzasadnieniem dostajesz listem i widzisz w tej samej zakładce. Punkty — także te po reklamacji —
widać dopiero **po ogłoszeniu wyników etapu**, a okno reklamacyjne zamyka się wcześniej (od v0.38.7
tak samo w panelu i w API aplikacji).

---

## 7. Kalendarz, archiwum, dyplomy

| Ekran | Adres | Co w nim jest |
|---|---|---|
| **Kalendarz** | `/me/calendar/` | wszystkie terminy: etapy, wydarzenia organizatora, okno rejestracji, warsztaty oraz **Twój termin rozmowy** |
| Kalendarz do subskrypcji | `/me/calendar.ics` | plik dla Google Calendar / Outlooka; wpisy aktualizują się, a nie dublują |
| **Archiwum** | `/me/archive/` | zadania **zakończonych** edycji z treścią PDF, arkusz treningowy, harmonogram warsztatów |
| **Dyplomy** | `/me/certificates/` | Twoje dyplomy i zaświadczenia do pobrania |
| Weryfikacja dokumentu | `/dyplomy/<kod>/` | strona **bez logowania** potwierdzająca rodzaj, edycję, numer i datę |

Dokument pobiera się w PDF-ie; strona weryfikacyjna **nie pokazuje imienia i nazwiska**, dopóki nie
wyraziłeś zgody na publikację pełnych danych.

**Olimpiada międzynarodowa (medale).** W konkursie z medalami (`iqo`) w „Dyplomach” znajdziesz dyplom
medalowy (złoty, srebrny, brązowy medal albo wyróżnienie) i zaświadczenie o udziale **w języku, który
wybrałeś w ustawieniach konta** — język jest zapisywany w chwili wystawienia dokumentu. Medale i ranking
krajów są pod odnośnikami „Medale” i „Ranking krajów” na stronie wyników etapu.

### Materiały z warsztatów — `/warsztaty/materialy/`

Nagrania warsztatów, slajdy i pliki do ćwiczeń są pod adresem `/warsztaty/materialy/`. Trafisz tam
na trzy sposoby: odnośnikiem **„Materiały z warsztatów”** w pasku konta (u góry każdej strony, obok
„Mój panel”), kaflem **„Materiały z warsztatów”** na swoim pulpicie (`/me/`) albo ramką na stronie
**Warsztaty**. Odnośnik i kafel pojawiają się dopiero wtedy, gdy organizator opublikuje pierwszy
materiał — jeśli ich nie widzisz, materiałów jeszcze nie ma. **Trzeba być zalogowanym** — gość
widzi tylko zaproszenie do logowania. Materiały widzą uczestnicy, ich opiekunowie i komitet tego
konkursu. Jeśli strony nie ma („nie znaleziono”), ten konkurs nie udostępnia materiałów.

- Materiały są pogrupowane po warsztatach, w kolejności harmonogramu.
- **Film** oglądasz na stronie, w odtwarzaczu — można go przewijać, zatrzymywać i włączyć na pełny
  ekran. Filmu nie da się pobrać przyciskiem; jeśli odtwarzanie zatrzyma się po dłuższej przerwie
  (ponad dwie godziny), **odśwież stronę**.
- **Plik** (PDF, prezentacja, notatnik) otwiera się albo pobiera przyciskiem „Otwórz” / „Pobierz”.
- **Odnośnik** prowadzi do nagrania w innym serwisie.

Materiały są przeznaczone **wyłącznie dla uczestników olimpiady** — nie nagrywaj ich i nie udostępniaj
dalej. Serwis nie zapisuje, kto co oglądał; organizator widzi tylko liczbę wyświetleń.

---

## 8. Forum — `/forum/`

Forum jest tylko w niektórych konkursach: jeżeli w pasku konta nie ma pozycji **„Forum”**, to znaczy,
że ten konkurs go nie prowadzi. Czytać i pisać mogą **wyłącznie zalogowani uczestnicy tego konkursu**
oraz komitet i organizator — z zewnątrz nie widać ani jednego zdania i wyszukiwarki forum nie indeksują.

| Ekran | Adres | Co w nim jest |
|---|---|---|
| Działy | `/forum/` | spis działów i ostatnie rozmowy |
| Dział | `/forum/<dział>/` | wątki jednego działu, przypięte na górze |
| Wątek | `/forum/t/<id>/` | rozmowa, 20 wpisów na stronę, formularz odpowiedzi |
| **Twoje wpisy** | `/forum/mine/` | stan każdej Twojej wypowiedzi i **uzasadnienie organizatora**, jeżeli którąś odrzucił |

**O rozwiązaniach zadań otwartego etapu nie wolno rozmawiać.** Zabrania tego regulamin (§ 10 ust. 2
i § 17), a próba uzyskania albo podania rozwiązania jest podstawą do dyskwalifikacji. Dopóki trwa etap
przyjmujący prace, **każdy wpis czeka na zatwierdzenie przez organizatora** — formularz mówi o tym wprost
i podaje nazwę etapu, którego zakaz dotyczy.

**Zwykle wpis nie pojawia się od razu.** W trybie moderacji wstępnej widzisz go tylko Ty, z dopiskiem
„czeka na moderację”, dopóki organizator go nie przepuści. Nie pisz go drugi raz — jest na miejscu.

**Powiadomienia e-mail.** Wątek, który założysz albo w którym coś napiszesz, **obserwujesz
automatycznie**; pod tematem każdego wątku jest przycisk **„Obserwuj wątek”** / **„Przestań
obserwować”**. Napiszemy do Ciebie, gdy:

- w obserwowanym wątku pojawią się **nowe odpowiedzi** — najwyżej jeden list o wątku na kilka godzin,
  a kilka wątków w jednym liście; temat listu mówi wprost, gdy odpowiedział **organizator** albo
  **komitet**,
- organizator **zatwierdzi albo odrzuci** Twój wpis czekający na moderację — przy odrzuceniu z jego
  uzasadnieniem.

W liście **nie ma treści wpisów** — tylko temat wątku i odnośnik; przeczytasz je po zalogowaniu. Nigdy nie
dostaniesz listu o własnym wpisie ani o czymś, czego jeszcze nie opublikowano. Częstotliwość ustawisz na
ekranie **„Edycja danych”** (`/me/profile/`, blok „Powiadomienia z forum”): **na bieżąco** (domyślnie),
**raz dziennie** (jedno podsumowanie rano) albo **nigdy**. Każdy list ma też link „wypisz się”, który
działa bez logowania.

Stan każdego wpisu i uzasadnienie organizatora zawsze znajdziesz na `/forum/mine/` — także wtedy, gdy
listy wyłączysz.

**Kilka reguł, które warto znać:**

- podpisem jest **imię i pierwsza litera nazwiska**. Nie podawaj na forum swojego kodu `OLM-…`, adresu
  e-mail ani nazwy szkoły — kod jest kluczem anonimowego oceniania i ma nim zostać,
- wpis to **zwykły tekst**: znaczników HTML i załączników forum nie przyjmuje, a odnośniki stają się
  klikalne same,
- **własny wpis poprawisz przez 15 minut** od napisania. Poprawka wpisu, który był już opublikowany,
  wraca w trybie moderacji wstępnej do kolejki,
- **„Usuń”** zdejmuje Twój wpis z wątku natychmiast,
- **„Zgłoś”** przy cudzym wpisie wysyła jedno zdanie do organizatora. Zgłoszenie widzi wyłącznie on
  i samo w sobie niczego nie ukrywa — decyduje człowiek,
- forum **nie ma wiadomości prywatnych** – na forum rozmowa toczy się tam, gdzie widzi ją moderator.
  Rozmowy 1:1 z organizatorem (i, jeśli organizator je włączył, z innymi uczestnikami) prowadzisz
  w osobnym module **Wiadomości** – patrz rozdział 8a.

## 8a. Wiadomości — `/me/messages/`

Rozmowy 1:1 na platformie: z **organizatorem** zawsze, a z **innymi uczestnikami** — jeżeli organizator
je włączył. Pozycja **„Wiadomości”** stoi w pasku konta i w pasku panelu `/me/`; liczba obok mówi, w ilu
rozmowach czeka coś nieprzeczytanego. Wiadomości czytasz w serwisie — e-mail mówi tylko, **że** i **od
kogo** coś przyszło (bez treści), i możesz go wyłączyć.

| Ekran | Adres | Co w nim jest |
|---|---|---|
| Skrzynka | `/me/messages/` | lista rozmów (kropka = nieprzeczytane) i otwarty wątek |
| Napisz do organizatora | `/me/messages/organizer/` | Twoja rozmowa z zespołem organizatora — zawsze jedna |
| Nowa rozmowa | `/me/messages/new/` | katalog uczestników, którzy zgodzili się, żeby do nich pisać |
| Szyfrowanie | `/me/messages/key/` | klucz do rozmów szyfrowanych (gdy organizator je włączył) |

**Organizator** odpowiada jako zespół — przy wiadomości zobaczysz „Organizator · Imię N.”.

**Katalog jest dobrowolny.** Domyślnie **nie ma Cię** w katalogu i nikt z uczestników nie może zacząć z Tobą
rozmowy. Włączysz to przełącznikiem „Inni uczestnicy mogą mnie znaleźć i do mnie napisać” w sekcji
**Wiadomości** na ekranie „Edycja danych” (`/me/profile/#wiadomosci`, odnośnik „Ustawienia wiadomości”
w skrzynce). W katalogu widać tylko imię, pierwszą literę nazwiska i województwo — nigdy e-mail, szkołę
ani kod `OLM-…`. Rozmowę, która już trwa, możesz prowadzić dalej także po wypisaniu się z katalogu.

**Kto czyta Twoje rozmowy z innymi uczestnikami** — to zależy od trybu wybranego przez organizatora
i zawsze jest napisane **nad formularzem**:

- „Wiadomości mogą być czytane przez organizatora w ramach moderacji” — w premoderacji wiadomość dochodzi
  dopiero po akceptacji organizatora (do tego czasu widzisz ją z etykietą „czeka na akceptację”; odrzuconą
  — z uzasadnieniem), w postmoderacji dochodzi od razu, a organizator może ją ukryć,
- „Organizator widzi tylko zgłoszone wiadomości” — bez moderacji,
- **w czasie etapu przyjmującego rozwiązania** każda wiadomość do innego uczestnika czeka na akceptację
  organizatora. O zadaniach otwartego etapu nie wolno rozmawiać (regulamin § 10 ust. 2 i § 17).

**Kto jest w katalogu.** Domyślnie rozmawiasz wyłącznie z osobami z **tej samej grupy wiekowej**
(niepełnoletni z niepełnoletnimi, pełnoletni z pełnoletnimi) – tak ustawia to organizator. Jeśli ktoś
w trakcie rozmowy skończy 18 lat, rozmowa zostanie zamknięta („Ta rozmowa została zamknięta zgodnie
z zasadami konkursu”). Nowych rozmów możesz zacząć najwyżej kilka na dobę (limit ustala organizator,
zwykle 5); odpowiadać w trwających rozmowach możesz bez tego limitu.

**Zgłoś i Zablokuj.** „Zgłoś” przy wiadomości drugiej osoby wysyła organizatorowi jedno zdanie i tę
wiadomość. „Zablokuj” w rozmowie sprawia, że ta osoba nie napisze do Ciebie ani nie zacznie nowej rozmowy
(zobaczy tylko „Nie można wysłać wiadomości do tej osoby”); „Odblokuj” stoi w tym samym miejscu.
Organizatora zablokować się nie da.

**Rozmowy szyfrowane end-to-end** (tylko gdy organizator je włączył, tylko między uczestnikami): na
`/me/messages/key/` ustawiasz **hasło do wiadomości** — inne niż hasło do konta. Treść takich rozmów znają
tylko Wasze przeglądarki; serwer i organizator widzą szyfrogram. Na nowym urządzeniu odblokujesz rozmowy
tym hasłem; „Zablokuj wiadomości” i wylogowanie usuwają odblokowany klucz z przeglądarki. **Hasła nie
znamy i nie przypomnimy** — „Utwórz nowy klucz” oznacza, że starych wiadomości szyfrowanych już nie
odczytasz (druga strona zobaczy informację o zmianie klucza; odciski kluczy porównacie w „Szczegółach
szyfrowania”). Jeśli zgłosisz wiadomość szyfrowaną, jej odszyfrowana treść trafi do organizatora.

## 8b. Webinary — `/webinars/`

Gdy organizator prowadzi webinary (wykłady, konsultacje, omówienia zadań), w pasku panelu `/me/` jest
pozycja **„Webinary”**. Na tej stronie widzisz webinary przeznaczone dla Ciebie – z datą w strefie
konkursu (i w Twojej strefie, jeśli jest inna).

- **„Dołącz”** działa od ok. 15 minut przed początkiem, gdy prowadzący rozpocznie webinar. Pokój otwiera
  się w nowej karcie, na stronie olimpiady – nic nie instalujesz. Najlepiej Chrome, Edge albo Firefox.
- Wchodzisz jako **widz**: widzisz i słyszysz prowadzących, piszesz na czacie. Chcesz zadać pytanie
  głosem? **„Podnieś rękę”** – gdy prowadzący da Ci głos, włączysz mikrofon (i kamerę).
- W pokoju widać Twoje **imię i pierwszą literę nazwiska**. Webinar może być nagrywany – jeśli trwa
  nagrywanie, pokój o tym informuje.
- **Nagrania** opublikowane przez organizatora są na tej samej stronie („Odtwórz nagranie”).
- **Listy**: zaproszenie i przypomnienie o webinarze – możesz je wyłączyć na dole strony
  („Wyłącz listy o webinarach”).

---

## 8e. Przegląd tłumaczeń — `/translations/` (dla tłumaczy-wolontariuszy)

Ten rozdział jest dla osób, którym organizator nadał rolę **tłumacza** (np. kierownik delegacji).
Napisy serwisu są tłumaczone maszynowo z polskiego; Ty sprawdzasz je w swoim języku.

1. **Lista napisów** — `/translations/` → swój język. Każdy wiersz: tekst źródłowy (polski), wersja
   angielska (odniesienie), obecne tłumaczenie i stan: *maszynowe*, *przejrzane*, *brak*. Filtr
   „Pokaż” i wyszukiwanie po dowolnym z tych tekstów.
2. **Napis** — kliknij, żeby zobaczyć kontekst (gdzie w serwisie występuje, uwagi dla tłumacza) i:
   - **zaproponować poprawkę** – zmienne w nawiasach (np. `%(name)s`, `{name}`) i znaczniki HTML
     przepisz dokładnie; zamiast prostych cudzysłowów `"` użyj typograficznych („…”, «…», “…”),
     prosty apostrof zamieni się na ’ sam,
   - **poprzeć** cudzą propozycję („Popieram”) – recenzent widzi liczbę głosów,
   - jako **recenzent**: „Zatwierdź”, „Odrzuć”, „Obecne tłumaczenie jest poprawne” albo „Przywróć
     tłumaczenie z katalogu”. Zatwierdzona poprawka jest w serwisie po kilku sekundach.
3. **Zgłoś tłumaczenie** — odnośnik w stopce każdej strony w Twoim języku. Wpisz fragment źle
   przetłumaczonego tekstu i jak powinien brzmieć; zapisujemy samą ścieżkę strony (bez parametrów
   adresu). Zgłoszenia czyta recenzent tłumaczeń Twojego języka.

Inni tłumacze nie widzą, kto zaproponował poprawkę ani kto zgłosił błąd.

---

## 8c. Absolwenci i mentoring — `/me/alumni/`

Pojawia się w pasku konta, gdy organizator włączył sieć absolwentów.

- **Dołączenie** jest dobrowolne i wymaga osobnej zgody. Mogą dołączyć osoby **pełnoletnie** z
  osiągnięciem w zakończonej edycji (próg ustala organizator). Osiągnięcia biorą się z ogłoszonych
  wyników – nie wpisuje się ich samemu.
- **Profil:** wszystkie pola są opcjonalne. Inni zalogowani uczestnicy widzą „Imię N.” (pełne imię
  i nazwisko tylko, jeśli to zaznaczysz i masz w konkursie zgodę na publikację nazwiska), uczelnię,
  kierunek, miasto, kraj, opis, zainteresowania i odnośniki LinkedIn/GitHub. Nigdy adres e-mail,
  szkołę ani kod uczestnika. Na publicznej ścianie `/alumni/` jesteś tylko wtedy, gdy to zaznaczysz
  – i widać tam wyłącznie podpis, osiągnięcia, uczelnię i kierunek.
- **Wycofanie zgody** usuwa profil od razu; relacje, w których jesteś mentorem, się kończą. Działa także
  wtedy, gdy organizator wyłączył sieć absolwentów (`/me/alumni/` pokazuje wtedy tylko ten przycisk).
- **Zmiana treści zgody:** gdy organizator zmieni treść zgody, profil jest niewidoczny, dopóki nie
  potwierdzisz nowej treści na `/me/alumni/`.
- **Mentoring:** uczestnik bieżącej edycji wybiera w katalogu `/me/alumni/directory/` mentora z
  wolnym miejscem i wysyła prośbę z krótką notatką (bez danych kontaktowych – przeczyta ją mentor
  i organizator; jeśli jesteś niepełnoletni, mentor przeczyta ją dopiero po akceptacji organizatora).
  Jeśli jesteś niepełnoletni, opis mentora zobaczysz dopiero po akceptacji organizatora. Prośby są
  ograniczone (najwyżej 5 dziennie i 2 tygodniowo do tej samej osoby). Po akceptacji rozmowa otwiera się w **Wiadomościach**. Jeśli jesteś
  niepełnoletni, organizator czyta wiadomości tej rozmowy – zależnie od zasad konkursu przed
  doręczeniem albo po nim. Każda strona może zakończyć relację albo **zgłosić problem**
  organizatorowi (zgłoszenie trafia do niego od razu); pojedynczą wiadomość zgłaszasz w Wiadomościach.
- **Zaproszenia** od organizatora (warsztaty, webinary, jury) przychodzą e-mailem; wyłączysz je w
  profilu albo odnośnikiem wypisu w każdym liście.

---

## 8d. Nadzór zdalny etapu online — `/me/proctoring/<etap>/`

Niektóre etapy online organizator może prowadzić **z nadzorem zdalnym**. Wtedy na stronie etapu widzisz
kartę „Nadzór zdalny”, a treść zadań (PDF), wysyłka rozwiązań i start testu otwierają się dopiero po
włączeniu nadzoru w **konsoli nadzoru**:

1. **Informacja i zgoda** – przeczytaj, kto widzi obraz z Twojej kamery, czy jest nagrywany i jak długo
   przechowujemy dane, i zaznacz zgodę. Jeśli masz mniej niż 18 lat, najpierw Twój rodzic lub opiekun
   prawny musi potwierdzić online zgodę na Twój udział (zakładka „Zgody” → „Poproś opiekuna o zgodę”).
2. **Sprawdzenie sprzętu** – kamera (i mikrofon albo udostępnienie ekranu, jeśli etap tego wymaga).
   Najlepiej aktualny Chrome, Edge, Firefox albo Safari.
3. **Zdjęcie dokumentu** – tylko jeśli etap tego wymaga: legitymacja szkolna przed kamerą (zasłoń PESEL
   i adres).
4. **„Włącz nadzór”** – kamera nadaje mały obraz (bez dźwięku, chyba że etap go wymaga). Gdy serwer
   potwierdzi nadawanie, kliknij „Przejdź do etapu” – etap otworzy się w **nowej karcie**. **Kartę konsoli
   zostaw otwartą do końca etapu.** Jeśli połączenie się zerwie, zobaczysz czerwony komunikat w konsoli
   i pasek na stronie etapu; konsola łączy się ponownie sama.

Osoba nadzorująca może napisać do Ciebie albo poprosić o pokazanie pokoju lub dokumentu – wiadomość
pojawi się w konsoli; kliknij „Rozumiem”. Nie ma automatycznej analizy obrazu ani śledzenia tego, co
robisz w przeglądarce. **Nie masz kamery albo nie chcesz jej używać?** Na dole konsoli: „Poproś o inną
formę nadzoru” – organizator odpowie w tej samej konsoli. Zgodę możesz wycofać przyciskiem w konsoli.

---

## 9. Coś nie działa

1. **Sprawdź `/status/`** — strona mówi, czy działa baza, magazyn prac i kolejka zadań, i podaje
   **czas na serwerze**. To odpowiedź na pytanie „to u was, czy u mnie?”.
2. **Zajrzyj do FAQ** — `/faq/`.
3. **Zgłoś problem** — `/support/new/` („Zgłoś problem” w pasku konta). Zgłoszenie da się złożyć także
   **bez konta**. Swoje sprawy śledzisz pod `/support/`; dopisanie się do sprawy wraca ją do kolejki.

Najczęstsze sytuacje:

| Objaw | Co zrobić |
|---|---|
| **403 „nie udało się zweryfikować formularza”** po wysłaniu | najczęściej w innej karcie zalogowano się lub wylogowano, albo formularz stał otwarty bardzo długo. Kliknij „odśwież” na stronie błędu i wyślij ponownie |
| List aktywacyjny nie dotarł | spam → `/activate/resend/` → zgłoszenie do organizatora |
| „Plik jest za duży” / „niedozwolony format” | limit i formaty są wypisane w karcie zadania, nad polem pliku |
| Praca stoi w „oczekuje na skan” | skan trwa chwilę; podgląd pojawi się po jego zakończeniu. Praca jest **już przyjęta** |
| Nie ma przycisku wysyłki | termin minął albo nie masz zapisu do etapu |
| Nie widzę punktów | punkty są dopiero **po ogłoszeniu wyników etapu**; ścieżka oceniania ich nie pokazuje nigdy |

**Nie zostawiaj wysyłki na ostatnie minuty.** Termin jest twardy, liczy się czas serwera, a skan
antywirusowy i wolne łącze potrafią zabrać kilka minut.
