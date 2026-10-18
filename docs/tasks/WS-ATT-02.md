# WS-ATT-02: Import obecności na warsztatach z raportu Google Meet

Prośba organizatora z 18.10.2026: „dodaj integrację do importowania listy obecności na warsztatach
z arkuszy Google Meet”.

## 0. Cel i granice

Warsztaty online odbywają się w Google Meet (Google Workspace organizatora). Funkcja **„Śledzenie
obecności”** Meet po spotkaniu wysyła organizatorowi e-mail z **raportem obecności w Arkuszach Google**.
Dziś koordynator przepisuje go ręcznie do tabeli `/coordinator/workshops/attendance/` albo przerabia na
CSV `kod,warsztat` – czyli zamienia e-maile i nazwiska na kody uczestników. To jest dokładnie ta praca,
którą serwis może zrobić sam.

Zadanie dokłada do ekranu obecności **import raportu Meet**: plik (CSV/XLSX) albo link do arkusza,
dopasowanie osób z raportu do uczestników **tego konkursu**, podgląd i zatwierdzenie.

Czego zadanie **nie** robi:

- nie zmienia modelu `cms.WorkshopAttendance` (bez migracji) ani tabeli kratek,
- **stary import CSV `kod,warsztat` zostaje bez zmian** (ten sam formularz, ta sama akcja),
- nie używa OAuth ani kont serwisowych Google (rozszerzenie – § 8),
- nie zapisuje w bazie niczego z raportu poza wynikiem: parą „uczestnik – warsztat”.

## 1. Raport obecności Google Meet – co wiemy o formacie

Źródło: pomoc Google Meet „Śledzenie obecności i raport z transmisji na żywo”
(support.google.com/meet/answer/10090454, wersje PL i EN, stan 18.10.2026):

- dostępne w wersjach Workspace: Essentials, Business Plus, Enterprise Starter/Essentials/Standard/Plus,
  Education Plus, Teaching and Learning Upgrade; w Education Plus raport powstaje automatycznie po
  każdym spotkaniu z co najmniej 2 uczestnikami, w pozostałych – po włączeniu przełącznika,
- włączenie: w trakcie spotkania **Ustawienia gospodarza → Śledzenie obecności**, albo w Kalendarzu
  Google przy wydarzeniu: **Zmień ustawienia rozmowy wideo → Śledzenie obecności → Zapisz**,
- po spotkaniu **organizator** dostaje e-mail z arkuszem (plik leży w jego Dysku),
- pola: imię, nazwisko, adres e-mail, czas dołączenia, czas opuszczenia, czas trwania; osoba, która
  wychodziła i wracała, ma **jeden wiersz** z łącznym czasem (pierwsze wejście, ostatnie wyjście),
- pokoje podgrup są w **osobnej karcie** arkusza (pierwsza karta to spotkanie główne),
- uczestnicy z telefonu mają częściowo ukryty numer zamiast nazwiska.

Formatu czasu trwania dokumentacja nie podaje, a w obiegu są arkusze z interfejsu polskiego
i angielskiego, przerabiane ręcznie, łączone z kilku spotkań i eksportowane przez dodatki. Parser jest
więc **tolerancyjny**:

1. **Nagłówki** (bez wielkości liter, bez dopisków w nawiasach i dwukropków), kolejność dowolna:

   | Kolumna | Warianty |
   |---|---|
   | imię | Imię, Imie, First name, Given name |
   | nazwisko | Nazwisko, Last name, Surname, Family name |
   | imię i nazwisko | Imię i nazwisko, Uczestnik, Nazwa, Nazwa wyświetlana, Name, Full name, Participant, Display name |
   | e-mail | Adres e-mail, E-mail, E-maile, Email, Email address, Adres email, Mail |
   | czas trwania | Czas trwania, Łączny czas, Czas obecności, Duration, Total duration, Time in call, Attended duration |
   | dołączenie | Godzina dołączenia, Czas dołączenia, Dołączono, Time joined, Join time, Joined, First seen |
   | wyjście | Godzina wyjścia, Czas opuszczenia, Czas wyjścia, Time exited, Time left, Leave time, Exit time, Left, Last seen |

   Wiersz nagłówka to pierwszy wiersz (z pierwszych 30), który ma kolumnę osoby (e-mail, imię,
   nazwisko albo imię i nazwisko) **i** kolumnę czasu (czas trwania albo dołączenie). Wszystko nad nim
   to **nagłówek meta** (kod spotkania, tytuł, data) – czytany wyłącznie w poszukiwaniu daty (§ 3.2).
2. **Czas trwania:** „1 godz. 23 min”, „1 godzina 5 minut”, „2 godziny”, „1 hr 23 min”, „1 hour 5 mins”,
   „1h 5m”, „45 min”, „65 min”, „30 s”, „01:05:00”, „1:05” (godziny:minuty), sama liczba = minuty;
   w XLSX także komórka czasu (`time`/`timedelta`) i ułamek doby (format czasu trwania Arkuszy).
   Bez kolumny czasu trwania – różnica „wyjście − dołączenie” (godziny „10:02”, „10:02:33”, „10:02 AM”,
   także z datą; przejście przez północ = +24 h). Czas nieczytelny = 0 i adnotacja w podglądzie.
3. **Wiele wierszy jednej osoby** (raport z dodatku, sklejone karty, ręczne poprawki) – czasy się
   **sumują**. Osoba = znormalizowany e-mail (§ 4.1), a bez e-maila – znormalizowane imię i nazwisko.
4. **Kodowanie CSV:** BOM UTF-8/UTF-16, UTF-8, UTF-16 bez BOM (rozpoznane po bajtach zerowych),
   inaczej Windows-1250; separator `,` `;` albo tabulator (sniffer). **XLSX** – openpyxl, pierwsza
   karta z rozpoznanym nagłówkiem, ze strażnikiem bomby ZIP (`apps.core.xlsx`).
5. **Limity:** plik do 5 MB, do **2000** wierszy danych (więcej = odmowa „to nie wygląda na raport
   jednego spotkania”).

## 2. Link do arkusza Google

1. Akceptujemy wyłącznie `https://docs.google.com/spreadsheets/d/<ID>/…` (także `/spreadsheets/u/<n>/d/<ID>/…`),
   `<ID>` z `[A-Za-z0-9_-]{25,100}`, bez portu i bez danych logowania w adresie. Inne hosty (także
   `drive.google.com`, `sheets.googleusercontent.com` wklejone ręcznie), `http://`, link publikacji
   (`/spreadsheets/d/e/…`) – odmowa z komunikatem.
2. `gid` karty z zapytania (`?gid=`) albo z fragmentu (`#gid=`), tylko cyfry; bez niego `0` (pierwsza karta).
3. Serwer pobiera **wyłącznie** adres zbudowany przez siebie:
   `https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=<GID>` – nigdy adresu wklejonego.
4. Przekierowania obsługujemy ręcznie (`allow_redirects=False`), najwyżej 3, i tylko do `https://`
   na host kończący się na `.googleusercontent.com` (tam Google oddaje eksport) albo z powrotem na
   `docs.google.com/spreadsheets/…`. Przekierowanie na `accounts.google.com` (strona logowania) albo
   odpowiedź 401/403/HTML = **arkusz nie jest udostępniony**: komunikat „Udostępnij arkusz: każdy, kto ma
   link, może wyświetlać – albo pobierz go jako CSV (Plik → Pobierz → CSV) i wgraj plik”. Inny host
   w przekierowaniu = odmowa.
5. Limit **5 MB** (nagłówek `Content-Length` i liczenie czytanych bajtów), czas: 5 s na połączenie,
   10 s na odczyt, 20 s na całość. Przekroczenie = komunikat „pobierz jako CSV i wgraj plik”.
6. **Limit pobrań:** scope `meet_link`, 20 na godzinę **per konto** – serwer nie może być tanim
   „pobieraczem” na zlecenie. Limit liczy wyłącznie pobranie z linku; plik i zatwierdzenie go nie zużywają.
7. Biblioteka `requests` (już w zależnościach – Stripe, webhooki, LiveKit), bez nowych zależności.

## 3. Ekran i przebieg

`/coordinator/workshops/attendance/meet/` – osobny ekran, wejście przyciskiem z karty „Import z Google
Meet” na ekranie obecności. Tylko **koordynator tego konkursu** (`CoordinatorRequiredMixin`).

1. **Formularz:** warsztat (lista z harmonogramu tego konkursu, jak kolumny tabeli; pierwsza pozycja
   „rozpoznaj po dacie z raportu”), plik **albo** link (dokładnie jedno), minimalny czas obecności
   w minutach (domyślnie 30, 0–600).
2. **Podpowiedź po dacie:** data z nagłówka meta, z komórek „dołączenie” albo z nazwy pliku
   (`2026-11-12`, `12.11.2026`, `12 listopada 2026`, `12 lis 2026`, `Nov 12, 2026`, `12 Nov 2026`).
   Bez wybranego warsztatu: dokładnie jeden warsztat z tą datą = wybrany (podgląd mówi to wprost),
   inaczej błąd „wybierz warsztat”. Z wybranym warsztatem i inną datą w raporcie – ostrzeżenie w podglądzie.
3. **Próg:** domyślnie **30 minut** bezwzględnie, a nie procent czasu warsztatu. Powód: czas zajęć
   w harmonogramie jest tekstem redakcyjnym („17:00–18:30”, „do potwierdzenia”), a raport nie podaje
   długości spotkania – procent liczyłby się od zgadywanej podstawy. 30 minut to „był na zajęciach”,
   a nie „wszedł na chwilę” przy typowym warsztacie 60–90 minut. Podgląd pokazuje najdłuższy czas
   w raporcie, żeby koordynator mógł próg ocenić; zmiana progu = ponowny podgląd.
4. **Podgląd** (POST z plikiem/linkiem, nic nie zapisuje) – sekcje:
   - **dopasowani – zostaną oznaczeni obecni** (kratka zaznaczona, można odznaczyć; widać, czy po
     e-mailu, czy po nazwisku),
   - **poniżej progu** (dopasowani mają kratkę „oznacz mimo to”, domyślnie pustą),
   - **niejednoznaczni – wybierz z listy** (select z kandydatami: nazwisko, kod),
   - **nieznalezieni** (pole „kod uczestnika” do ręcznego wskazania),
   - **pominięci: nauczyciele** (adres opiekuna szkolnego tego konkursu),
   - **już oznaczeni** na tym warsztacie.
5. **Zatwierdzenie** (POST z tokenem) zapisuje **wyłącznie dokładając** obecności
   (`apps.cms.workshops.add_attendance`, `bulk_create(ignore_conflicts=True)`); istniejących nie kasuje.
   Po zapisie powrót do tabeli z komunikatem „dodano N obecności”.
6. Bez JavaScriptu (CSP) – sama forma HTML.

## 4. Dopasowanie

Krąg kandydatów = ten sam, co wiersze tabeli obecności: uczestnicy **tego konkursu** z wpisem do
dowolnego etapu bieżącej edycji, bez kont usuniętych (anonimizowanych).

1. **E-mail** – bez wielkości liter i spacji; dla `gmail.com`/`googlemail.com` bez kropek i bez `+tag`
   w części lokalnej (domena sprowadzona do `gmail.com`). Ta sama normalizacja po obu stronach.
2. E-mail **opiekuna szkolnego** tego konkursu (a nie uczestnika) → „nauczyciel – pominięty”.
   Obecność dotyczy wyłącznie uczestników (`WorkshopAttendance.participant`).
3. **Imię i nazwisko** – `apps.accounts.duplicates.normalize_text`, słowa posortowane (Meet bywa
   z zamienioną kolejnością), wśród kandydatów **niedopasowanych już po e-mailu**: jeden = dopasowany,
   kilku = niejednoznaczny, zero = nieznaleziony.
4. Dwie osoby z raportu dopasowane do tego samego uczestnika (np. telefon i laptop) – czasy się sumują.
5. Uczestnik innego konkursu z tym samym e-mailem nie jest kandydatem (`for_competition`).

## 5. Bezpieczeństwo podglądu

Wynik dopasowania jedzie do zatwierdzenia w **podpisanym tokenie** (`django.core.signing`, sól
własna, kompresja, TTL **30 min**) w ukrytym polu formularza – a nie w sesji (sesje są w bazie)
ani w osobnej tabeli. Token zawiera **wyłącznie** identyfikatory: konkurs, konto koordynatora, edycja,
klucz warsztatu, źródło, próg, listy identyfikatorów uczestników (dopasowani, poniżej progu, kandydaci
niejednoznacznych) i liczby. **Bez e-maili i nazwisk z raportu.** Zatwierdzenie sprawdza: podpis i wiek,
konkurs i konto (token z innego konkursu albo od innego koordynatora = odmowa), że zaznaczone osoby
należą do list z tokenu, a kody wpisane ręcznie – do kręgu kandydatów; warsztat musi nadal być
w harmonogramie.

## 6. RODO i audyt

1. Raport (e-maile, nazwiska, czasy) jest przetwarzany **wyłącznie w pamięci** w czasie żądania
   podglądu i pokazywany koordynatorowi na stronie podglądu; nie trafia do bazy, logów, cache'u ani
   tokenu. Plik nie jest zapisywany na dysku (`MemoryFileUploadHandler` do 2,5 MB, większy – plik
   tymczasowy Django usuwany po żądaniu).
2. Zapisuje się tylko wynik: `WorkshopAttendance(participant, workshop_key, created_by)`.
3. Rejestr czynności (`apps/accounts/processing_register.py`): obecności na warsztatach **nie było**
   w rejestrze, choć jest podstawą zaświadczeń. Wiersz „Ogłaszanie wyników i wystawianie dokumentów”
   dostaje kategorię „obecność na warsztatach online” oraz źródło (raport obecności Google Meet
   organizatora, przetwarzany przejściowo) i środek (raport nie jest przechowywany). Google nie jest
   nowym odbiorcą – raport powstaje w Workspace organizatora, serwis go tylko odczytuje. Wersja rejestru
   rośnie do 1.23.
4. Audyt `workshop.attendance_imported` przy edycji: klucz warsztatu, źródło (`file`/`link`), próg,
   liczby (wiersze raportu, dopasowani, poniżej progu, niejednoznaczni, nieznalezieni, nauczyciele,
   już oznaczeni, wybrani ręcznie, dodani). **Bez e-maili, nazwisk, ID arkusza i adresu linku.**

## 7. Testy

- `apps/cms/tests/test_meet_report.py` (bez bazy): nagłówki PL/EN, meta-nagłówek, czasy trwania
  wszystkich postaci z § 1, „wyjście − dołączenie”, sumowanie wierszy, CSV cp1250/UTF-16/średnik, XLSX,
  limit wierszy, brak nagłówka; walidacja linku (inne hosty, `http`, port, dane logowania, link
  publikacji, ID i gid z zapytania i fragmentu); pobieranie z atrapą HTTP: CSV, przekierowanie do
  googleusercontent, strona logowania (HTML, przekierowanie na accounts.google.com), przekierowanie na
  obcy host, za duży plik (nagłówek i strumień), timeout; data spotkania.
- `apps/web/tests/test_coordinator_meet_import.py` (z bazą): e-mail, normalizacja gmail, imię
  i nazwisko, niejednoznaczność, inny konkurs, nauczyciel, próg, już oznaczeni, zapis bez kasowania
  istniejących, wybór niejednoznacznego i ręczny kod, token z innego konkursu/konta, uprawnienia,
  audyt bez danych osobowych, podpowiedź warsztatu po dacie, limit pobrań z linku.

## 8. Możliwe rozszerzenia (poza zakresem)

- **OAuth Google (Drive/Sheets API, zakres `drive.readonly` albo `spreadsheets.readonly`)** – koordynator
  łączy konto Workspace i wybiera raport z listy bez udostępniania „każdy z linkiem”. Wymaga projektu
  w Google Cloud, weryfikacji aplikacji i przechowywania tokenów odświeżania (nowa czynność w rejestrze).
- **Konto serwisowe z delegacją domeny** albo **Meet REST API** (`conferenceRecords.participants`) –
  automatyczny import po każdym spotkaniu z kalendarza warsztatów; wymaga uprawnień administratora
  Workspace.
- Przeliczanie progu w podglądzie bez ponownego wgrania (wymagałoby niesienia czasów osób w tokenie).
