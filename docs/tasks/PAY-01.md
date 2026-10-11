# PAY-01: Opłaty uczestnictwa płatne online (delegacje IQO i uczestnicy)

## 0. Cel i granice

Polecenie organizatora (4.10.2026): opłaty za udział płacone **online** – przez delegacje krajowe
w olimpiadzie międzynarodowej (IQO, waluta EUR) i, ogólnie, przez uczestników dowolnego konkursu
(PLN w konkursach polskich). Wszystko za istniejącą flagą `fees` (§ 1.5.1 etapu 2: „cennik,
zwolnienia, status płatności i dokumenty rozliczeniowe”) – Olimpiada Kwantowa jest bezpłatna i nie
widzi z tego zadania ani adresu, ani pozycji menu, ani zapytania do bazy.

**Co już jest i zostaje** (wydanie K, `apps/tenancy/fees.py`, `apps/integrations/inbound.py`):
cennik uczestnika `FeeSchedule` (edycja/kategoria), rejestr należności `ParticipantFee`
(DUE/PAID/EXEMPT/WAIVED/REFUNDED, zwolnienie i umorzenie z powodem i audytem), ekrany
`/coordinator/fees/…`, kafel „Wpisowe” na `/me/` i neutralny stub webhooka
`/api/v1/payments/<slug>/` (HMAC per konkurs). PAY-01 **buduje na tym**, nie zastępuje:
należność uczestnika dalej żyje w `ParticipantFee`, a wpłata online kończy się wywołaniem
`record_payment` / `record_refund` tego modułu.

**Co dochodzi** (nowa aplikacja `apps/payments`):

1. cennik **delegacji** per edycja: opłata za delegację, za ucznia, za opiekuna, za obserwatora;
   terminy „early” i „late” (inne ceny przed i po terminach); waluta per cennik,
2. zniżki i zwolnienia delegacji (koordynator, uzasadnienie obowiązkowe, audyt),
3. **zamówienie** (rozliczenie) jako przedmiot płatności – dla delegacji albo dla należności
   uczestnika – z **fakturą pro forma** przed zapłatą i **fakturą** po zapłacie (PDF, numeracja
   ciągła per konkurs, rodzaj i rok), dane sprzedawcy z konkursu, dane nabywcy od płacącego,
4. dostawcy płatności za jednym interfejsem: **Stripe** (karty, międzynarodowo – Checkout Session +
   webhook z weryfikacją podpisu), **Przelewy24** (PLN), **przelew tradycyjny** (kod referencyjny
   w tytule, koordynator oznacza wpłatę, opcjonalny dowód wpłaty skanowany ClamAV-em),
5. zwroty inicjowane przez koordynatora przez API dostawcy (albo zapisane ręcznie dla przelewu),
6. potwierdzenia e-mail w języku płacącego, pulpit koordynatora (kto zapłacił, zaległości, sumy
   per waluta) i eksport CSV dla księgowości.

**Decyzje etapu 2, które to zadanie zmienia:**

- **D18 (dostawca) – rozstrzygnięta:** Stripe i Przelewy24 + przelew. Stub `/api/v1/payments/<slug>/`
  zostaje bez zmian (integracja „własna” organizatora), nowe webhooki mają własne adresy.
- **D15 (faktury) – zmieniona częściowo poleceniem organizatora:** system **numeruje** pro formy
  i faktury (numeracja ciągła bez luk per konkurs/rodzaj/rok, licznik pod blokadą wiersza w tej
  samej transakcji, co dokument). **Nadal nie** prowadzi rejestru VAT/JPK, nie wylicza podatku
  i nie wystawia korekt – adnotację VAT (np. „zw.” albo „odwrotne obciążenie”) wpisuje
  organizator, a kwoty są kwotami brutto. Zgodność wzoru faktury z przepisami potwierdza księgowa
  organizatora przed pierwszym konkursem z opłatami (OPERACJE).

Czego zadanie **nie** robi: płatności częściowych i rat, przeliczeń walut, faktur korygujących,
rozliczania podatku, przechowywania danych kart (nigdy nie dotykają serwera – Checkout Stripe
i bramka P24 są stronami dostawcy), automatycznego dopasowania wyciągu bankowego.

## 1. Model (`apps/payments/models.py`)

- `PaymentSettings` (1:1 z konkursem; brak wiersza = wartości domyślne): NIP/VAT ID sprzedawcy,
  rachunek (IBAN, SWIFT, bank), prefiks numeracji (domyślnie slug wielkimi literami), adnotacja
  VAT, uwagi na fakturze, termin płatności pro formy (dni), włączone metody (karta, P24, przelew).
  Nazwa, adres i dane rejestrowe sprzedawcy – z pól `Competition.organizer_*`.
- `PriceList` (1 na edycję, waluta, `early_until`, `late_from`, aktywny) + `PriceItem`
  (rodzaj × okres → kwota). Rodzaje: `DELEGATION`, `STUDENT`, `LEADER`, `OBSERVER`; okresy:
  `EARLY` (data ≤ `early_until`), `REGULAR`, `LATE` (data ≥ `late_from`). Brak ceny okresu =
  cena `REGULAR`; brak `REGULAR` = pozycja bezpłatna.
- `BillingProfile` – dane nabywcy (instytucja albo osoba, nazwa, adres, kraj, VAT ID opcjonalnie,
  e-mail) dla delegacji (+ liczba obserwatorów deklarowana przez opiekuna) albo dla uczestnika.
- `FeeAdjustment` – zniżka kwotowa albo zwolnienie całej delegacji; powód obowiązkowy, cofnięcie
  z powodem, audyt obu.
- `Order` + `OrderLine` – zamówienie: delegacja **albo** należność uczestnika (więz XOR), waluta,
  suma (liczona na serwerze), okres cenowy, migawka nabywcy, język płacącego, kod referencyjny
  (unikalny, do tytułu przelewu), stan `OPEN`/`PAID`/`CANCELLED`/`REFUNDED`, kwota zwrócona.
- `Payment` – próba zapłaty: dostawca, stan (`PENDING`/`SUCCEEDED`/`FAILED`/`CANCELLED`/
  `MISMATCH`), kwota i waluta **kopiowane z zamówienia**, identyfikatory u dostawcy, dla przelewu –
  data wpływu, notatka i dowód wpłaty (klucz w storage, skrót, wynik skanu).
- `Refund` – zwrot: kwota, powód, stan, identyfikator u dostawcy.
- `ProviderEvent` – dziennik doręczeń webhooków: `(dostawca, id zdarzenia)` unikalne = idempotencja.
- `BillingDocument` + `DocumentCounter` – pro forma / faktura: numer `PREFIKS/PF|FV/ROK/NNNN`,
  migawka (sprzedawca, nabywca, pozycje, sumy, płatność) – PDF składany z migawki przy każdym
  pobraniu (jak dyplom), więc za rok wychodzi ten sam papier.

## 2. Naliczanie delegacji

Skład: 1 delegacja, uczniowie (`Participant.delegation`), opiekunowie (`DelegationLeader`),
obserwatorzy (deklaracja w `BillingProfile`). **Pokrycie** = ilości w zamówieniach `OPEN` i `PAID`.
Nowe zamówienie obejmuje wyłącznie **niepokryty** przyrost składu, wyceniony w okresie z dnia
wystawienia (strefa konkursu) – uczeń dopisany po terminie „late” płaci cenę „late”, a wcześniej
opłaceni zostają przy swojej cenie. Zniżka zmniejsza kolejne zamówienia (linia ujemna, do wysokości
sumy), zwolnienie – blokuje wystawianie. Zamówienia są niezmienne (pro forma ma numer); zmianę
składu przed zapłatą robi się anulowaniem zamówienia (opiekun albo koordynator) i wystawieniem
nowego. Nadpłata (uczeń wypisany po zapłacie) jest decyzją koordynatora o zwrocie.

Uczestnik (tryb OPEN): zamówienie z jedną pozycją = `ParticipantFee.amount`/`currency`; zapłata →
`record_payment(fee, external_reference=<kod>)`, pełny zwrot → `record_refund`. Zwolnienie
i umorzenie uczestnika – istniejące czynności rejestru wpisowego.

## 3. Płatność

- `start_checkout(order, provider)`: zamówienie `OPEN`, płatne (należność nierozliczona, delegacja
  bez zwolnienia), dostawca skonfigurowany (zmienne środowiskowe) i włączony w ustawieniach
  konkursu, waluta obsługiwana (P24 – tylko PLN; waluty dwumiejscowe). Kwota **wyłącznie
  z zamówienia** – formularz nie przesyła kwoty. Przed nową próbą (i przed anulowaniem) poprzednia
  otwarta sesja Stripe jest wygaszana (`/expire`); gdy Stripe odmówi, `GET` sesji rozstrzyga: wygasła =
  zamknięta, otwarta albo zakończona = płatność w drodze (odmowa). Próba Stripe bez identyfikatora
  sesji młodsza niż czas rozmowy z dostawcą też jest „w toku”. Trwająca transakcja P24 blokuje nową na
  czas limitu transakcji. Rozmowy z dostawcą idą **bez blokad wierszy**; pod blokadą zamówienia
  sprawdzamy, że nic nie czeka. Identyfikator nowej sesji zapisuje się warunkowo (`status=PENDING`) –
  gdy równoległe żądanie zdążyło próbę zamknąć, nowa sesja jest od razu wygaszana. Adres
  przekierowania sprawdzany względem listy hostów dostawcy.
- Stan „zapłacone” ustawia webhook z poprawnym podpisem, koordynator (przelew) albo sprzątanie
  (`sweep_payments`, beat co 15 min): sesja Stripe zapłacona, której webhook zginął, jest odczytywana
  **naszym kluczem** z API i rozliczana tak samo jak zdarzenie. Adres powrotu niczego nie zapisuje.
  Kwota i waluta porównywane z `Payment`; rozbieżność → `MISMATCH`, zamówienie zostaje otwarte, pulpit
  pokazuje „do wyjaśnienia”. Zdarzenie z innego trybu (live przy kluczu testowym i odwrotnie) jest
  zapisywane i pomijane.
- Zapłata (`apply_success`, blokady zamówienie → wpłata, idempotentnie): zamówienie `PAID`, faktura
  z numerem, potwierdzenie e-mail (płacący i nabywca, język płacącego), dla uczestnika `record_payment`.
  Wpłata na zamówienie **anulowane** albo już zapłacone → `MISMATCH` do zwrotu (pozycje mogło objąć
  nowsze zamówienie); przelew zapisuje się wyłącznie na zamówienie otwarte (stan sprawdzany pod
  blokadą – podwójne kliknięcie nie zapisze dwóch wpłat).

## 4. Webhooki

`POST /payments/webhooks/stripe/`, `/payments/webhooks/przelewy24/`,
`/payments/webhooks/przelewy24/refund/` – bez sesji i CSRF (tożsamością jest podpis), limit
`payment_webhooks` (600/min per IP), podpis **obowiązkowy** (brak sekretu w środowisku = 404), weryfikacja z surowych bajtów:
Stripe `Stripe-Signature` (HMAC-SHA256 `t.payload`, tolerancja 300 s, kilka sekretów po przecinku –
rotacja), P24 – SHA-384 pól + CRC i obowiązkowe `transaction/verify`. Idempotencja: wiersz
`ProviderEvent` w tej samej transakcji co skutek; duplikat → 200 bez zmian; błąd przetwarzania →
wycofanie i 5xx (dostawca ponowi). Płatność odnajdywana po identyfikatorze dostawcy (globalnie,
nie po domenie żądania) – jeden adres webhooka na instalację.

## 5. Zwroty

Koordynator wskazuje **pozycje i ilości** (`RefundLine`); kwota = ich suma, przycięta do tego, co
z wpłaty zostało (zniżka). Zwrócone ilości przestają pokrywać skład – zastępca wypisanego ucznia płaci.
Wpłata `MISMATCH` (podwójna, rozbieżna, po anulowaniu) wraca w całości, bez pozycji. Powód obowiązkowy,
audyt. Stripe `/v1/refunds` (klucz idempotencji `refund-<uuid.hex>`), P24 `transaction/refund`
(`requestId`/`refundsUuid` = `uuid.hex`, 32 znaki; wynik asynchronicznie na adres zwrotów), przelew –
zapis ręczny „zwrócono przelewem”. Brak odpowiedzi, timeout, 5xx/429 → zwrot zostaje `PENDING`
i sprzątanie zleca go ponownie z tym samym kluczem; odmowa (4xx) → `FAILED`. Webhook zwrotu odnajduje
zwrot po identyfikatorze dostawcy albo po `metadata.refund_uuid`. Pełny zwrot → zamówienie `REFUNDED`
(i jego zniżka wraca do puli), dla uczestnika `record_refund`. List do płacącego w jego języku.

## 6. Ekrany

- opiekun (gettext, 10 języków): `/delegation/payments/` – zestawienie, dane nabywcy
  i obserwatorzy, „Wystaw pro formę”, zamówienia, dokumenty; `/payments/orders/<id>/` – płatność
  (karta / Przelewy24 / przelew z danymi rachunku i kodem), anulowanie;
- uczestnik: kafel „Wpisowe” dostaje „Zapłać online” → `/me/fees/pay/` (dane nabywcy) → zamówienie;
- koordynator (po polsku, I18N-01 § 0): `/coordinator/payments/` – sumy per waluta, delegacje (kto
  zapłacił, zaległości), zamówienia, „do wyjaśnienia”, eksport CSV; cennik i ustawienia
  `/coordinator/payments/prices/`; delegacja (zniżki/zwolnienia); zamówienie (oznacz wpłatę
  przelewem z dowodem, anuluj, zwrot). Pozycja „Płatności” w menu w bloku flagi `fees`.

## 7. Wymagania przekrojowe

Izolacja (`for_competition`, obiekt cudzego konkursu → 404), role w serwisach, audyt każdej
czynności (bez danych kart; kwoty tak, bo to rejestr rozliczeń), throttling POST-ów (`checkout`
per konto), CSRF na wszystkich formularzach, sekrety wyłącznie ze środowiska (`STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `P24_MERCHANT_ID`, `P24_POS_ID`, `P24_API_KEY`, `P24_CRC`, `P24_SANDBOX`),
CSP bez inline JS (ekrany bez JS), RODO: czynność „Płatności” w rejestrze (warunkowo – flaga `fees`),
sekcja w eksporcie danych konta (zamówienia wystawione przez konto, profil nabywcy uczestnika
i profile nabywcy delegacji ostatnio zmienione przez to konto). Anonimizacja i usunięcie konta kasują
profil nabywcy uczestnika i odpinają autora profili delegacji; **zamówienia i faktury** (migawka
nabywcy) zostają jako dokumentacja księgowa (art. 6 ust. 1 lit. c, 5 lat), autor zamówienia odpinany.

## 8. Testy

Bez sieci (dostawcy podmienieni `unittest.mock`): cennik i okresy, pokrycie i przyrosty, zniżka
i zwolnienie, numeracja ciągła i niezależna per konkurs/rodzaj/rok, Stripe (tworzenie sesji –
kwota z serwera, podpis poprawny/zły/przeterminowany/brak sekretu, idempotencja, rozbieżność kwoty,
wygaśnięcie, zwrot), P24 (podpisy, verify, zwrot), przelew z dowodem (skan), uczestnik
(record_payment/record_refund), izolacja i uprawnienia ekranów (opiekun innego kraju, cudzy konkurs,
uczestnik w panelu koordynatora), Konkurs #1 bez flagi – 404 i menu bez zmian.

## 9. Realizacja (4.10.2026) – gdzie co jest, odstępstwa, luki

Gdzie: `apps/payments/models.py` (modele), `pricing.py` (zestawienie i pokrycie), `services.py` (wszystkie
czynności), `documents.py` (numeracja, migawka, PDF), `providers/` (`base.py` interfejs, `stripe.py`,
`przelewy24.py`), `views/` (`payer.py`, `coordinator.py`, `webhooks.py`), `notifications.py`, `tasks.py`
(skan dowodu), `rodo.py` (czynność w rejestrze), `admin.py` (podgląd tylko do odczytu), `urls.py`
(rozwinięte na końcu `apps/web/urls.py`).

Odstępstwa (z powodem):
1. **Stripe bez SDK** – cztery wywołania REST przez `requests` (zależność już w obrazie) i weryfikacja
   podpisu według dokumentacji Stripe; SDK byłoby nową zależnością obrazu dla tych samych linijek.
2. **Jeden adres webhooka na instalację** (`/payments/webhooks/stripe/`), a nie per konkurs: sekrety są
   w środowisku instalacji (wymaganie „sekrety tylko z env”), płatność odnajdujemy po identyfikatorze
   sesji, a konkurs bierzemy z płatności. `STRIPE_WEBHOOK_SECRET` przyjmuje kilka sekretów po przecinku.
   Stub `/api/v1/payments/<slug>/` (sekret per konkurs w bazie) zostaje bez zmian.
3. **Ceny „early/late” tylko w cenniku delegacji.** Opłata uczestnika ma jedną kwotę z `FeeSchedule`
   (rejestr wpisowego z wydania K) – nie dublujemy cennika uczestnika.
4. **Zniżki i zwolnienia przez `FeeAdjustment` dotyczą delegacji**; uczestnika zwalnia i umarza istniejący
   rejestr wpisowego (`mark_exempt`, `waive_fee`).
5. **Obserwatorzy są deklaracją opiekuna** (liczba w danych nabywcy) – nie mają kont ani modelu (LOG-01
   może je dodać; wtedy `pricing.composition` liczy z bazy).
6. **Faktura bez korekt i bez VAT** – D15 zmieniona tylko w zakresie numeracji. Kwoty brutto, adnotacja VAT
   tekstem organizatora; korektę po zwrocie wystawia księgowość.
7. **Dokument w języku płacącego, z odwrotem na angielski** dla zh-Hans, hi, ar, bn (kroje DejaVu w PDF nie
   mają tych znaków). Ekrany i listy – we wszystkich 10 językach.
8. **PDF nie jest załącznikiem listu** – list zawiera dane wpłaty i numer faktury, plik leży za logowaniem.
9. **Wpłata „nie w porę” jest zapisywana** jako `MISMATCH` do zwrotu – po anulowaniu zamówienia i po
   zapłacie inną próbą (poprawka po przeglądzie, L5: wcześniej anulowane zamówienie stawało się
   zapłacone). Pieniądze są faktem; odrzucenie ukryłoby je przed koordynatorem.
10. **Poprawki po przeglądzie (4.10.2026):** dostęp wyłącznie czynnego opiekuna (H1) i skład liczący
   czynnych opiekunów (L2); warunkowy zapis sesji i blokada prób bez identyfikatora (M1); `GET` sesji
   po nieudanym `expire` i sprzątanie beatem (M2); zwroty pozycjami (M3); przelew pod blokadą (M4);
   zwrot o nieznanym wyniku ponawiany tym samym kluczem i odwrót webhooka na `metadata.refund_uuid`
   (M5); kolejność blokad zamówienie → wpłata → zwrot, bez HTTP pod blokadą (L1); `uuid.hex` dla P24
   (L3); `livemode` (L4); odmowa przelewu na anulowane (L5); RODO profilu (L6); osobny limit webhooków
   (L7).

Znane luki:
- adapter Przelewy24 przetestowany na atrapie HTTP, **nie** na sandboxie dostawcy (OPERACJE § 35.3),
- brak listy dozwolonych adresów IP powiadomień P24 (podpis SHA-384 + `verify` są obowiązkowe),
- rozbieżna kwota (`MISMATCH`) nie ma czynności „przyjmij mimo to” – zwrot i ponowna płatność,
- zwrot Stripe w stanie `pending` czeka na webhook `refund.updated` (sprzątanie ponawia wyłącznie zwroty
  **bez** identyfikatora dostawcy); zwrotu P24 bez powiadomienia nie odpytujemy,
- zniżka wraca do puli dopiero po zwrocie **całego** zamówienia (zwrot częściowy jej nie dzieli),
- pulpit liczy zestawienie per delegacja osobnymi zapytaniami (kilkadziesiąt krajów – akceptowalne),
- zamówienie wystawione przez koordynatora w imieniu opiekuna dostaje język koordynatora (ekran tego nie
  oferuje – ścieżka tylko z serwisu),
- tłumaczenia maszynowe (do przeglądu).
