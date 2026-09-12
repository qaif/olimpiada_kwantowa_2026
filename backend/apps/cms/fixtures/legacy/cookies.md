# Polityka plików cookie

Serwis Olimpiady Kwantowej używa **wyłącznie plików cookie niezbędnych** do działania strony: do utrzymania sesji po zalogowaniu i do ochrony formularzy. Nie prowadzimy analityki, nie wyświetlamy reklam i nie profilujemy odwiedzających.

Administratorem danych jest Fundacja Quantum AI z siedzibą w Warszawie, ul. Sanocka 9/103, 02-110 Warszawa (KRS 0000808359), organizator Olimpiady Kwantowej. Kontakt w sprawach danych osobowych: contact@qaif.org. Zakres i podstawy przetwarzania danych opisuje [Polityka RODO Olimpiady Kwantowej](/dokumenty/rodo/).

## Jakie pliki cookie zapisujemy

| Nazwa | Do czego służy |
|---|---|
| sessionid | Utrzymuje sesję zalogowanego użytkownika – bez niego każde kliknięcie w panelu wymagałoby ponownego logowania. Zapisywany po zalogowaniu, a także wtedy, gdy serwis musi przekazać komunikat między stronami (na przykład o błędnym haśle). Czas życia: 14 dni. Oznaczony HttpOnly (skrypty strony go nie odczytają), Secure (wysyłany wyłącznie po HTTPS) i SameSite=Lax. |
| csrftoken | Chroni formularze przed sfałszowaniem żądania z obcej strony (CSRF). Bez niego nie da się wysłać żadnego formularza: rejestracji, logowania, zgłoszenia rozwiązania ani reklamacji. Czas życia: około rok. Oznaczony Secure i SameSite=Lax; nie jest oznaczony HttpOnly (domyślne ustawienie Django). |
| wagtail_sidebar_collapsed | Wyłącznie w panelu redakcyjnym /cms/: pamięta, czy redaktor zwinął boczne menu. Ustawiany dopiero po zalogowaniu do panelu, więc zwykły odwiedzający nigdy go nie otrzymuje. |

Wszystkie trzy są plikami **własnymi** (domeny Olimpiady) i nie są przekazywane nikomu poza organizatorem.

## Pamięć przeglądarki (localStorage)

Pasek informujący o plikach cookie zapisuje w pamięci lokalnej przeglądarki jeden klucz: **cookie-notice-ack**. Zapamiętuje on wyłącznie to, że komunikat został zamknięty, żeby nie pokazywać go przy każdym wejściu. Wartość zostaje w przeglądarce i **nie jest wysyłana na serwer** ani z niczym powiązana. Poza tym kluczem serwis nie korzysta z pamięci lokalnej przeglądarki.

## Czego nie robimy

- **Brak analityki i statystyk** – nie używamy Google Analytics ani żadnego innego narzędzia pomiarowego.
- **Brak reklam i profilowania** – nie zapisujemy plików reklamowych, nie tworzymy profili odwiedzających, nie przekazujemy danych sieciom reklamowym.
- **Brak wtyczek społecznościowych** – na stronach nie ma przycisków „lubię to”, pikseli ani widżetów serwisów społecznościowych.
- **Kroje pisma są hostowane u nas** – pliki fontów leżą na serwerze Olimpiady, więc otwarcie strony nie łączy się z Google Fonts ani z innym dostawcą krojów.

Dwie biblioteki odpowiadające za interaktywność stron (htmx i Alpine.js) są pobierane z publicznych repozytoriów bibliotek (cdnjs, jsDelivr), z kontrolą sumy kontrolnej pliku i bez przekazywania adresu strony, z której nastąpiło wejście. Biblioteki te **nie zapisują żadnych plików cookie**.

Serwis nie osadza dziś materiałów z serwisów zewnętrznych. Redakcja ma techniczną możliwość wstawienia w treść filmu z YouTube'a albo Vimeo; gdyby taki materiał pojawił się na stronie, odtwarzacz dostawcy może zapisać własne pliki cookie – wtedy uzupełnimy tę politykę przed publikacją.

## Jak zablokować pliki cookie

Pliki cookie można zablokować albo usunąć w ustawieniach przeglądarki – nie prosimy o zgodę i nie warunkujemy niczego jej udzieleniem. Trzeba jednak liczyć się ze skutkami: bez pliku sessionid **nie da się zalogować** ani korzystać z panelu uczestnika, recenzenta i komitetu, a bez csrftoken **żaden formularz nie zostanie przyjęty**. Części informacyjnej serwisu – opisów, harmonogramu, dokumentów i opublikowanych wyników – można czytać z całkowicie zablokowanymi plikami cookie.

## Podstawa prawna

Wymienione pliki cookie są **niezbędne do świadczenia usługi drogą elektroniczną** żądanej przez użytkownika: bez sesji nie istnieje konto uczestnika, a bez tokena ochronnego nie da się bezpiecznie przyjąć formularza. Dla takich plików przepisy nie wymagają zgody – wystarcza poinformowanie o ich stosowaniu (zasada wyrażona w art. 173 Prawa telekomunikacyjnego, którego odpowiednik zawiera obowiązujące Prawo komunikacji elektronicznej). Pasek na stronie jest więc **informacją**, a nie pytaniem o zgodę: nie ma tu plików, których można by się zrzec bez utraty samej usługi.

Gdyby serwis kiedykolwiek zaczął używać plików innych niż niezbędne, poprosimy wcześniej o zgodę – osobno, przed ich zapisaniem, z możliwością odmowy bez żadnych konsekwencji.

## Kontakt i aktualizacje

Pytania o pliki cookie i o przetwarzanie danych: contact@qaif.org.

Wersja: 1.0 z 12 września 2026 r. Dokument aktualizujemy, gdy zmieni się zestaw używanych plików cookie albo wykorzystywanych usług.

## Dokumenty powiązane

- [Polityka RODO Olimpiady Kwantowej](/dokumenty/rodo/)
- [Regulamin Olimpiady Kwantowej](/dokumenty/regulamin/)
