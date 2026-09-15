# Polityka plików cookie

Serwis Olimpiady Kwantowej używa **plików cookie niezbędnych** do działania strony (utrzymanie sesji po zalogowaniu, ochrona formularzy) oraz – **wyłącznie za Twoją zgodą** – plików analitycznych Google Analytics 4, z których powstaje statystyka odwiedzin. Nie wyświetlamy reklam, nie profilujemy odwiedzających i nie przekazujemy danych sieciom reklamowym.

Pliki niezbędne zapisujemy zawsze, bo bez nich usługa nie działa. Pliki analityczne zapisujemy dopiero wtedy, gdy klikniesz **„Akceptuję wszystkie”** na pasku na dole strony. Po kliknięciu **„Tylko niezbędne”** nie powstaje żaden plik analityczny ani identyfikator. Zgodę można w każdej chwili wycofać odnośnikiem **„Ustawienia cookies”** w stopce.

Jeśli pasek na dole strony ma tylko przycisk **„Rozumiem”**, a nie pyta o zgodę – analityka jest w tym momencie **wyłączona** i serwis zapisuje wyłącznie pliki niezbędne wymienione w tabeli poniżej.

Administratorem danych jest Fundacja Quantum AI z siedzibą w Warszawie, ul. Sanocka 9/103, 02-110 Warszawa (KRS 0000808359), organizator Olimpiady Kwantowej. Kontakt w sprawach danych osobowych: contact@qaif.org. Zakres i podstawy przetwarzania danych opisuje [Polityka RODO Olimpiady Kwantowej](/dokumenty/rodo/).

## Jakie pliki cookie niezbędne zapisujemy

| Nazwa | Do czego służy |
|---|---|
| sessionid | Utrzymuje sesję zalogowanego użytkownika – bez niego każde kliknięcie w panelu wymagałoby ponownego logowania. Zapisywany po zalogowaniu, a także wtedy, gdy serwis musi przekazać komunikat między stronami (na przykład o błędnym haśle). Czas życia: 14 dni. Oznaczony HttpOnly (skrypty strony go nie odczytają), Secure (wysyłany wyłącznie po HTTPS) i SameSite=Lax. |
| csrftoken | Chroni formularze przed sfałszowaniem żądania z obcej strony (CSRF). Bez niego nie da się wysłać żadnego formularza: rejestracji, logowania, zgłoszenia rozwiązania ani reklamacji. Czas życia: około rok. Oznaczony Secure i SameSite=Lax; nie jest oznaczony HttpOnly (domyślne ustawienie Django). |
| wagtail_sidebar_collapsed | Wyłącznie w panelu redakcyjnym /cms/: pamięta, czy redaktor zwinął boczne menu. Ustawiany dopiero po zalogowaniu do panelu, więc zwykły odwiedzający nigdy go nie otrzymuje. |

Wszystkie trzy są plikami **własnymi** (domeny Olimpiady) i nie są przekazywane nikomu poza organizatorem. Do ich zapisania nie jest potrzebna zgoda – są niezbędne do świadczenia usługi, o którą prosisz.

## Cookies analityczne (Google Analytics 4)

Statystykę odwiedzin prowadzimy w usłudze Google Analytics 4. Służy ona wyłącznie temu, żeby organizator wiedział, ile osób odwiedza serwis, które strony czytają i z jakich urządzeń korzystają – i żeby na tej podstawie poprawiać stronę. **Nie** budujemy z tych danych profili, nie kojarzymy ich z kontem uczestnika i nie używamy ich do reklamy.

| Nazwa | Do czego służy |
|---|---|
| _ga | Odróżnia odwiedzających od siebie (losowy identyfikator przeglądarki). Bez niego każde wejście liczyłoby się jako nowa osoba. Czas życia: 2 lata. Plik własny, zapisywany przez skrypt Google'a na domenie Olimpiady. |
| _ga_<identyfikator strumienia> | Podtrzymuje stan pojedynczej wizyty (sesji pomiarowej) w tej konkretnej usłudze GA4. Czas życia: 2 lata. |

Szczegóły, które mają znaczenie:

- **Dostawca:** Google Ireland Limited, Gordon House, Barrow Street, Dublin 4, Irlandia. Dane mogą zostać przekazane do Stanów Zjednoczonych – podstawą jest decyzja wykonawcza Komisji Europejskiej z 10 lipca 2023 r. stwierdzająca odpowiedni stopień ochrony (EU–US Data Privacy Framework), a Google LLC figuruje na liście podmiotów uczestniczących w tym programie.
- **Zapisujemy je dopiero po zgodzie.** Skrypt Google Analytics wczytuje się na każdej stronie (tak wymaga Google), ale w **trybie zgody** (Consent Mode v2): zanim klikniesz „Akceptuję wszystkie”, nie zapisuje żadnego pliku cookie ani identyfikatora użytkownika. Do Google'a trafiają wtedy wyłącznie sygnały techniczne bez identyfikatora – adres oglądanej strony, typ przeglądarki i przybliżona lokalizacja z adresu IP (adres IP jest anonimizowany) – które nie pozwalają rozpoznać Cię przy kolejnej wizycie.
- **Anonimizacja adresu IP.** Adres IP jest skracany, zanim trafi do raportów; nie przechowujemy go w usłudze w pełnej postaci.
- **Bez funkcji reklamowych.** W konfiguracji wyłączone są Google Signals, personalizacja reklam i przekazywanie danych do remarketingu – zgoda przekazywana do narzędzia dotyczy wyłącznie statystyki (pozostałe kategorie pozostają odmówione).
- **Jak wycofać zgodę:** odnośnik **„Ustawienia cookies”** w stopce otwiera pasek ponownie. Kliknięcie „Tylko niezbędne” usuwa pliki `_ga` i `_ga_…` z przeglądarki i od tej chwili nic nie jest zbierane. Wycofanie zgody jest równie proste jak jej udzielenie i nie pociąga za sobą żadnych konsekwencji – serwis działa tak samo.
- **Podstawa prawna:** zgoda – art. 6 ust. 1 lit. a RODO oraz art. 173 Prawa telekomunikacyjnego (którego odpowiednik zawiera obowiązujące Prawo komunikacji elektronicznej).

## Pamięć przeglądarki (localStorage)

Pasek cookie zapisuje w pamięci lokalnej przeglądarki (nie w plikach cookie) trzy klucze – zależnie od tego, w której roli występuje:

| Klucz | Znaczenie |
|---|---|
| cookie-consent | Twoja decyzja: **all** (zgoda na analitykę) albo **necessary** (tylko pliki niezbędne). |
| cookie-consent-at | Data i godzina podjęcia tej decyzji – żebyśmy mogli wykazać, kiedy zgoda została udzielona. |
| cookie-notice-ack | Używany tylko wtedy, gdy analityka jest wyłączona i pasek jest wyłącznie informacją: zapamiętuje, że komunikat został zamknięty. |

Wartości zostają w przeglądarce, **nie są wysyłane na serwer** ani z niczym powiązane. Poza tymi kluczami serwis nie korzysta z pamięci lokalnej przeglądarki.

## Czego nie robimy

- **Brak reklam i profilowania** – nie zapisujemy plików reklamowych, nie tworzymy profili odwiedzających, nie przekazujemy danych sieciom reklamowym.
- **Brak wtyczek społecznościowych** – na stronach nie ma przycisków „lubię to”, pikseli ani widżetów serwisów społecznościowych.
- **Kroje pisma są hostowane u nas** – pliki fontów leżą na serwerze Olimpiady, więc otwarcie strony nie łączy się z Google Fonts ani z innym dostawcą krojów.
- **Zabezpieczenie antyspamowe jest nasze** – obrazek z działaniem arytmetycznym w formularzach rejestracji rysuje serwer Olimpiady, a nie zewnętrzna usługa (nie używamy reCAPTCHA, hCaptcha ani Cloudflare Turnstile). Nie zapisuje on żadnego pliku cookie i nie wysyła nikomu informacji o odwiedzającym; samo wyzwanie znika z naszej bazy w chwili rozwiązania, a najpóźniej po dziesięciu minutach.

Dwie biblioteki odpowiadające za interaktywność stron (htmx i Alpine.js) są pobierane z publicznych repozytoriów bibliotek (cdnjs, jsDelivr), z kontrolą sumy kontrolnej pliku i bez przekazywania adresu strony, z której nastąpiło wejście. Biblioteki te **nie zapisują żadnych plików cookie**.

Serwis nie osadza dziś materiałów z serwisów zewnętrznych. Redakcja ma techniczną możliwość wstawienia w treść filmu z YouTube'a albo Vimeo; gdyby taki materiał pojawił się na stronie, odtwarzacz dostawcy może zapisać własne pliki cookie – wtedy uzupełnimy tę politykę przed publikacją.

## Jak zablokować pliki cookie

Pliki analityczne najprościej odrzucić na samym pasku („Tylko niezbędne”) albo wycofać zgodę odnośnikiem „Ustawienia cookies” w stopce. Wszystkie pliki cookie można ponadto zablokować albo usunąć w ustawieniach przeglądarki. Trzeba jednak liczyć się ze skutkami: bez pliku sessionid **nie da się zalogować** ani korzystać z panelu uczestnika, recenzenta i komitetu, a bez csrftoken **żaden formularz nie zostanie przyjęty**. Części informacyjnej serwisu – opisów, harmonogramu, dokumentów i opublikowanych wyników – można czytać z całkowicie zablokowanymi plikami cookie.

## Podstawa prawna

Pliki wymienione w tabeli plików niezbędnych są **konieczne do świadczenia usługi drogą elektroniczną** żądanej przez użytkownika: bez sesji nie istnieje konto uczestnika, a bez tokena ochronnego nie da się bezpiecznie przyjąć formularza. Dla takich plików przepisy nie wymagają zgody – wystarcza poinformowanie o ich stosowaniu (zasada wyrażona w art. 173 Prawa telekomunikacyjnego, którego odpowiednik zawiera obowiązujące Prawo komunikacji elektronicznej).

Pliki analityczne Google Analytics 4 **nie są** niezbędne, więc zapisujemy je wyłącznie na podstawie **zgody udzielonej wcześniej**: art. 173 Prawa telekomunikacyjnego (odpowiednio – Prawa komunikacji elektronicznej) dla samego zapisu w urządzeniu oraz art. 6 ust. 1 lit. a RODO dla przetwarzania danych, które z niego wynika. Zgoda jest dobrowolna, jej brak nie ogranicza dostępu do żadnej części serwisu, a wycofanie jej jest tak samo łatwe jak udzielenie (art. 7 ust. 3 RODO) i nie wpływa na zgodność z prawem przetwarzania sprzed wycofania.

## Kontakt i aktualizacje

Pytania o pliki cookie i o przetwarzanie danych: contact@qaif.org.

Wersja: 1.1 z 15 września 2026 r. Dokument aktualizujemy, gdy zmieni się zestaw używanych plików cookie albo wykorzystywanych usług. Zmiana w wersji 1.1: dodanie statystyki odwiedzin (Google Analytics 4) zapisywanej wyłącznie za zgodą, opis sposobu jej wycofania oraz nowe klucze pamięci lokalnej przeglądarki.

## Dokumenty powiązane

- [Polityka RODO Olimpiady Kwantowej](/dokumenty/rodo/)
- [Regulamin Olimpiady Kwantowej](/dokumenty/regulamin/)
