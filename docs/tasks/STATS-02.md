# STATS-02: Lejek uczestnika i raporty wysyłek komunikatów

Lista organizatora z 18.10.2026, punkty 11 i 12. Kod: aplikacja `apps.coordinator_stats` (bez flagi –
ekrany zamówione dla Konkursu #1, jak „Przekazywanie rozwiązań”).

> **Zmiana 21.10.2026 (STATS-02a, decyzja organizatora: „to ma nie uwzględniać kolejności kroków”).**
> Tryb „lejek ścisły” usunięty – każdy krok liczy się niezależnie (kto spełnia dany warunek). Powód:
> na produkcji krok „zaświadczenie zaakceptowane” pokazywał 0 (niezależnie 73), bo poprzedzająca go
> „pierwsza wysłana praca” = 0 przed startem Etapu I. Odsetek „względem poprzedniego kroku” zniknął
> (przy krokach niezależnych przekracza 100 %, np. zgody 646 przy 602 zalogowanych) – zostaje
> wyłącznie % względem rejestracji (ekran, słupki, podwiersze, CSV). Stary adres z `?mode=strict`
> działa (parametr pomijany). Nazwa „Lejek uczestnika” zostaje w menu i adresie (rozpoznawalność,
> zakładki); tabela nazywa się „Kroki aktywności”, a opis mówi wprost, że kroki są niezależne.
> Klucz pamięci podręcznej dostał wersję (`CACHE_VERSION`), więc wynik sprzed zmiany nie wraca.
> Punkty 3, 6 i 7 poniżej opisują stan po zmianie.

## 1. Lejek uczestnika – `/coordinator/stats/funnel/`

1. **Populacja** – profile uczestników **tego** konkursu bez kont po anonimizacji, które należą do
   wybranej edycji (domyślnie bieżącej). „Uczestnik edycji” to ta sama definicja, co w komunikatach
   (`apps.accounts.messaging.current_edition_participants`): wpis do któregokolwiek etapu edycji
   **albo** konto założone od powstania edycji – dla edycji minionej dodatkowo przed powstaniem
   następnej (inaczej zeszłoroczny lejek rósłby o tegorocznych).
2. **Kroki** (kolejność wyświetlania – liczby od niej nie zależą):
   rejestracja (profil) → aktywacja (`User.email_verified_at`) → pierwsze logowanie
   (`User.last_login` niepuste) → zgody (RODO i regulamin, a dla niepełnoletniego – zgoda opiekuna;
   reguła wieku jak w `consents.is_minor`, przeliczona na warunek SQL i sprawdzona testem) → zapis do
   etapu edycji, z wierszami „w tym trening” i „w tym Etap I” (etap `ELIM`, a bez niego pierwszy etap
   zawodów w kolejności terminów) → pierwsza wysłana praca (zgłoszenie poza `REJECTED_INFECTED` albo
   zakończone podejście do testu online) → zaświadczenie o statusie ucznia zaakceptowane (wersja
   bieżąca w tej edycji) → obecność na ≥ 1 warsztacie (odhaczona w oknie edycji).
3. **Liczenie**: każdy krok niezależnie – liczy wszystkich uczestników edycji spełniających jego
   warunek, bez względu na pozostałe kroki (np. obecni na warsztacie bez zaakceptowanego
   zaświadczenia, zaświadczenie bez wysłanej pracy). Do 21.10.2026 był też domyślny tryb „ścisły”
   (krok wymagał wszystkich poprzednich) – usunięty, patrz adnotacja na górze. Przy każdym kroku
   i podwierszu liczba i % względem rejestracji; słupek CSS (`stat-bar`, klasy szerokości
   `width_class`, skala względem rejestracji) bez JavaScriptu.
4. **Filtry** (GET): edycja; województwo albo region konkursu (flaga `custom_regions`); szkoła
   (lista szkół z uczestnikami – ta sama, co w komunikatach, `school_choices`); grupa domeny adresu
   (§ 3); okres rejestracji (`User.date_joined`, daty lokalne, obie granice włącznie).
5. **„Gdzie odpadają”** – 10 województw i 10 szkół z **najniższą aktywacją** (aktywowani /
   zarejestrowani), wyłącznie grupy z co najmniej 5 rejestracjami (próg `K_ANONYMITY` ze STAT-01 –
   mniejsze grupy dają procenty bez znaczenia i zbliżają się do pojedynczych osób).
6. **Eksport CSV** – te same agregaty: kroki z liczbą i % względem rejestracji, opis filtrów, tabele
   „gdzie odpadają” (% aktywowanych w grupie – osobna kolumna). Żadnej kolumny o osobie. Pobranie to wpis audytu `export.generated`.
7. **Koszt** – stała liczba zapytań: jeden `aggregate` z `COUNT … FILTER` po warunkach (`Exists`
   zamiast złączeń – bez mnożenia wierszy), dwa grupowania „gdzie odpadają”, listy filtrów. Wynik
   w pamięci podręcznej 5 minut (klucz: wersja, konkurs, edycja, filtry, język, dzień).

## 2. Raport wysyłki komunikatu – historia i `/coordinator/messages/<id>/report/`

1. **Liczniki przy wysyłce** – `send_broadcast_chunk` po zakolejkowaniu porcji dopisuje do
   `BroadcastDomainTally` (komunikat × grupa domeny) liczbę zakolejkowanych i pominiętych (twarde
   odbicie, MAIL-02 § 2.7). Wyłącznie liczby: rejestr komunikatów dalej nie przechowuje adresów
   odbiorców. Błąd zapisu licznika nie zatrzymuje porcji (ponowienie porcji wysłałoby listy drugi raz).
2. **Powiązanie odbicia z komunikatem – decyzja: nagłówek.** Każdy list komunikatu dostaje
   `X-Olimpiada-Broadcast: <id>:<podpis>` (`django.core.signing.Signer`, sól aplikacji).
   Zawiadomienie relaya (DSN) niesie kopię listu albo jego nagłówki (`message/rfc822`,
   `text/rfc822-headers`), więc `bounces.original_headers` czyta stamtąd identyfikator. Odmowa relaya
   w trakcie wysyłki (`TrackingSMTPBackend`) czyta nagłówek z wysyłanego listu.
   Dlaczego nie „po adresie i czasie”: rejestr nie zna odbiorców komunikatu (celowo), a ten sam adres
   dostaje w tygodniu kilka komunikatów i list z aktywacji – dopasowanie byłoby zgadywaniem.
   Podpis: przez relay idą też listy przekazywane dalej (`postmaster@`), a ich nagłówki pisze nadawca
   z zewnątrz – bez podpisu mógłby dopisać odbicie do cudzego komunikatu. Odbicia listów sprzed
   wdrożenia (bez nagłówka) zostają nieprzypisane.
3. **Sygnał** `apps.email_delivery.signals.bounces_reported` (lista `Bounce`, nagłówki listu
   pierwotnego, źródło) – `email_delivery` nie zna komunikatów, odbiornik siedzi w
   `apps.coordinator_stats`. Wysyłany dla **każdego** odbicia z DSN, także niedotyczącego adresu
   (polityka, spam), bo to właśnie odrzucenia Microsoftu (`S3150`, kod `5.7.x`) organizator chce
   widzieć; `DeliveryStatus` dalej zapisuje wyłącznie odbicia dotyczące adresu (bez zmian MAIL-02).
4. **Rodzaje odbić** w raporcie: twarde (`HARD_STATUSES`), polityka serwera odbiorcy (`x.7.x`,
   `5.0.350`, tekst `S3150`), tymczasowe i inne. Wyróżnienie: odrzucenia Microsoftu `S3150` (osobna
   kolumna przy grupie Microsoft i ostrzeżenie na górze raportu).
5. **Raport**: odbiorców (`recipient_count`), zakolejkowane, pominięte, odbite po wysyłce, tabela
   i wykres CSS po grupach domen (wysłane vs odbite, bez adresów), na dole – lista odbitych adresów
   (adres, rodzaj, kod, powód skrócony) **tylko** dla koordynatora tego konkursu. Komunikat innego
   konkursu – 404. Komunikat sprzed wdrożenia (bez liczników) – „brak danych”, bez zgadywania.
6. **Historia komunikatów** dostaje kolumny „Pominięte” i „Odbite” oraz odnośnik „Raport” – liczniki
   wszystkich wierszy jednym zapytaniem grupującym.

## 3. Grupy domen (`apps.coordinator_stats.domains`)

Gmail (`gmail.com`, `googlemail.com`); WP/O2/Onet/Interia (rodziny domen tych portali); Microsoft
(`outlook.*`, `hotmail.*`, `live.*`, `msn.com`); szkolne (`*.edu`, `*.edu.pl`, `eduwarszawa.pl`);
inne. Jedna tablica wyrażeń regularnych działa w Pythonie (wysyłka, odbicia) i w PostgreSQL
(`iregex` – filtr lejka), więc obie strony liczą tę samą grupę. „Szkolne” i „inne” są osobno (lista
organizatora wymienia je razem) – to rozbicie nic nie kosztuje, a pokazuje, ile szkół ma własną pocztę.

## 4. RODO

- Lejek i jego CSV to wyłącznie agregaty; tabele „gdzie odpadają” od progu 5 rejestracji.
- `BroadcastDomainTally` – liczby, bez danych osobowych; zostaje razem z komunikatem.
- `BroadcastBounce` – adres, kod i skrócony powód odbicia przy komunikacie (cel i podstawa jak
  w czynności „Doręczalność poczty”, MAIL-02 § 2.8; wiersz rejestru czynności uzupełniony, wersja
  rejestru 1.23). Widzi go wyłącznie koordynator konkursu komunikatu. Retencja: 365 dni od odbicia
  (`services.RETENTION_DAYS` z MAIL-02), kasowanie codziennym zadaniem
  `apps.coordinator_stats.tasks.purge_broadcast_bounces`; liczniki w `BroadcastDomainTally` zostają.
  Zmiana adresu konta, anonimizacja i usunięcie konta kasują wiersze starego adresu (te same
  sygnały i ta sama zasada, co reset `DeliveryStatus`, MAIL-02 § 2.4).
- Nagłówek `X-Olimpiada-Broadcast` niesie wyłącznie numer komunikatu i podpis – żadnej danej odbiorcy.

## 5. Testy

`apps/coordinator_stats/tests`: grupy domen (Python i SQL), reguła wieku w SQL vs `is_minor`, każdy
krok lejka (liczony niezależnie), stary `?mode=strict`, filtry, izolacja konkursów i edycji, budżet zapytań niezależny od liczby
uczestników, CSV bez danych osobowych; nagłówek i podpis, `original_headers` na DSN relaya, liczniki
zakolejkowanych/pominiętych, powiązanie odbicia z komunikatem (DSN i odmowa relaya), S3150, raport
i historia (404 dla cudzego komunikatu), retencja.
