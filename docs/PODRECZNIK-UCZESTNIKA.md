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
| Twoje dane (imię, nazwisko, telefon, województwo, szkoła, klasa, data urodzenia) | `/me/profile/` — przycisk „Edytuj dane” w panelu |
| **Adres e-mail opiekuna szkolnego** (pole opcjonalne) | `/me/profile/` — dopiero po jego wpisaniu nauczyciel widzi Twój postęp; da się je wyczyścić. Jeśli nauczyciel nie ma jeszcze konta, a organizator włączył tę rejestrację, założy je sam pod `/register/supervisor/` |
| Zmiana adresu e-mail | `/account/profile/` → `/account/email/` — potwierdzenie idzie na **dotychczasowy** adres |
| Język interfejsu i tryb wysokiego kontrastu | dwie ikony w pasku konta, na każdej stronie |
| **Pobranie wszystkich swoich danych** (art. 20 RODO) | `/account/export/` — paczka ZIP z `dane.json` i wgranymi plikami, jedna na 10 minut |
| Usunięcie konta | `/account/delete/` — patrz niżej |

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

Obok nich, w tym samym pasku, cztery osobne ekrany: **Kalendarz**, **Archiwum**, **Dyplomy**, **Profil**.

**Zapis do etapu.** Przycisk **„Zgłoś się do tego etapu”** (w treningu: „Zgłoś się do treningu”). Bez
wpisu nie ma ani zadań, ani wysyłki. Do etapów po eliminacjach zapisuje się **wyłącznie osoba
zakwalifikowana** w poprzednim.

---

## 3. Wysyłka rozwiązania

Każde zadanie ma własną kartę: **„Zadanie N: <tytuł>”**, odznakę stanu, termin, dozwolone formaty, limit
rozmiaru pliku i przycisk **„Treść zadania (PDF)”**.

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

**Reklamacja** — zakładka **„Reklamacje”**, przycisk **„Złóż reklamację”** przy pracy. Jest możliwa
wyłącznie w **oknie reklamacyjnym** wyznaczonym przez organizatora i dotyczy własnej, ocenionej pracy.
Rozpatruje ją komisja odwoławcza — **inne osoby** niż te, które pracę oceniały. Rozstrzygnięcie
z uzasadnieniem dostajesz listem i widzisz w tej samej zakładce.

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

**O decyzjach nie wysyłamy listów.** Jeżeli chcesz wiedzieć, co się stało z Twoją wypowiedzią, zajrzyj
na `/forum/mine/`: to jedyne miejsce, w którym zobaczysz stan wpisu i uzasadnienie, gdyby organizator go
odrzucił.

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
- forum **nie ma wiadomości prywatnych**. To jest decyzja, nie brak: rozmowa ma się toczyć tam, gdzie
  widzi ją moderator.

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
