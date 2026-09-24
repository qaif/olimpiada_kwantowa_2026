# Historia zmian

Jedna linia na wydanie — treść pochodzi z opisu commitu oznaczonego tagiem (`git log --tags`).
Numeracja jest `v<major>.<minor>.<patch>`, a tag wydania jest zarazem wartością `APP_VERSION`
wpisywaną przez `scripts/deploy.sh`, więc numer widoczny w stopce serwisu i na `/status/` odpowiada
dokładnie jednemu wierszowi tej tabeli.

Pełny opis każdej funkcji: [`../README.md`](../README.md). Stan prac i dług techniczny:
[`BACKLOG.md`](BACKLOG.md).

## [Unreleased] – inni dostawcy AI

Prośby organizatora z 24.09.2026: „Pozwól też na użycie innych dostawców AI, jak OpenAI, Google
i Meta.” oraz „włącz wszystkich dostawców dla testów”. Ocena AI (flaga `ai_grading`) przestaje być
wyłącznie Claude'em: koordynator wybiera dostawcę i model przy każdym zleceniu, a tę samą pracę może
ocenić kilkoma modelami, żeby je porównać.

- **Dostawcy** (`apps.ai_grading.providers`): wspólny kontrakt – jedno neutralne wejście (prompt
  systemowy, materiały zadania, praca), jeden wynik (JSON wg tego samego schematu, zużycie z cache,
  identyfikator żądania, odmowa/ucięcie w słowniku Anthropic) i rodzaje błędów (auth / rate limit /
  transient / permanent / refusal / too large), od których zależy ponowienie. **Anthropic** – żądanie
  bajt w bajt jak w v0.34.0 (`client.py` bez zmian w wywołaniu). **OpenAI** – Responses API
  (`responses.stream`, `text.format` json_schema strict, `store: false`, `reasoning.effort: high`,
  PDF `input_file`, obraz `input_image`, `prompt_cache_key`). **Google** – `google-genai`
  (`generate_content`, `response_json_schema`, `thinking_level: high` dla `gemini-3*`, pliki inline,
  blokady `SAFETY`/`PROHIBITED_CONTENT`… jako odmowa). **Meta** – Meta Model API przez SDK OpenAI
  (Chat Completions, `response_format` json_schema, PDF jako część `file`); dawne Llama API Meta
  wyłączyła 6.07.2026. Limity plików per dostawca sprawdzane przed wysyłką (Google 20 MB, Meta PDF do
  50 stron). Modele z list (stan 24.09.2026) plus „inny identyfikator modelu”; warstwa Meta
  `-contributor` odrzucana.
- **Klucze i umowy powierzenia per dostawca** (`AiProviderAccount`): klucz tylko do zapisu (ten sam
  Fernet), „Sprawdź klucz” bez kosztu, oraz **„Potwierdzam zawarcie umowy powierzenia (DPA)
  z <dostawca>”** z datą i osobą (dziennik zdarzeń). Bez potwierdzenia dostawca **nie dostaje prac
  uczestników**; wycofanie zatrzymuje prace czekające w kolejce przed wysyłką.
- **Tryb testowy** (`apps.ai_grading.sandbox`): praca testowa koordynatora (PDF/JPG/PNG/py/ipynb,
  walidacja i skan jak prace uczestników, oświadczenie o braku danych uczestników, odmowa pliku
  identycznego z pracą uczestnika) oceniana **każdym dostawcą z kluczem, także bez umowy**. Oceny
  testowe widzi tylko koordynator (plakietka TEST), nie wchodzą do eksportu ani statystyk, liczą się
  do zużycia i limitu wydatków, można je usunąć.
- **Porównanie**: kluczem oceny jest (wersja pracy, dostawca, model); panel recenzenta pokazuje
  osobne panele „Ocena AI – <dostawca> <model> (sugestia, niewiążąca)”, najnowszy pierwszy; karta
  zadania – zgodność z oceną końcową osobno dla każdego modelu; uczestnik (gdy włączone) – najnowszą.
- **Ceny**: tabela cen per model w ustawieniach (domyślne z cenników z 24.09.2026, do nadpisania).
  Model bez ceny – koszt „nieznany”, liczone tokeny i licznik wywołań bez ceny; przy ustawionym limicie
  wydatków taki model jest odrzucany (`AI_PRICE_UNKNOWN`).
- **RODO**: rejestr czynności **1.8** – odbiorcy wiersza „ocena AI” liczeni dynamicznie (tylko dostawcy
  z kluczem i potwierdzoną umową); eksport danych uczestnika wymienia dostawcę i podmiot przetwarzający
  przy każdej ocenie; podręcznik organizatora § 4.12 – co sprawdzić u każdego dostawcy (DPA, SCC,
  retencja, trenowanie, brak retencji, **ograniczenia wieku w warunkach Google i Mety**).
- **Operator**: komenda `confirm_ai_provider_dpa` (OPERACJE § 17.6) – potwierdzenie umowy jak z panelu,
  idempotentne. **Po wdrożeniu żaden dostawca, także Anthropic, nie ma potwierdzonej umowy** – migracja
  celowo tego nie domniemywa; komendę trzeba uruchomić dla `kwantowa` (organizator potwierdził umowy
  24.09.2026).

Migracje: `ai_grading.0002_providers` (klucz Anthropic przeniesiony do `AiProviderAccount` bez
odszyfrowania, `AiAssessment` z konkursem, dostawcą i modelem zamówionym, `AiTestWork`). Nowe
zależności: `openai>=3.19,<4`, `google-genai>=2.25,<3` (import leniwy). Nowa trasa Celery:
`apps.ai_grading.tasks.scan_ai_test_work` → kolejka `scan`.

## v0.34.0 – 2026-09-24

Wydanie zbiorcze z próśb i zgłoszeń organizatora z 24.09.2026. Dwie zmiany działają od wdrożenia,
w każdym konkursie i bez flagi: **listy koordynatora** (konta usunięte schowane, sortowanie kolumn)
i **wysyłka komunikatów do grup uczestników**. Trzy nowe funkcje stoją za flagami konkursu
**domyślnie wyłączonymi** – `student_status_certificate`, `workshop_materials`, `ai_grading` – więc
konkurs z domyślnymi przełącznikami nie zmienia się o ani jeden adres, pozycję menu ani zapytanie
(budżety zapytań w `test_invariants.py` bez zmian). Zapalenie każdej z nich jest decyzją organizatora
poprzedzoną krokiem operatora: `OPERACJE.md` § 6.4 oraz § 15 (status ucznia), § 16 (materiały
z warsztatów), § 17 (ocena AI).

Migracje: `accounts.0033_broadcast_target`, `accounts.0034_clear_anonymised_profile_data` (dane),
`tenancy.0009_document_kind_student_status`, `student_status.0001_initial`,
`workshop_materials.0001_initial`, `ai_grading.0001_initial`. Nowe zadania beat:
`student-status-purge-expired-scans` (doba), `workshop-materials-cleanup` (godzina), `ai-grading-pump`
(5 min). Nowa zależność `anthropic>=1.8,<2` (import leniwy). Rejestr czynności przetwarzania
**1.7** – jedna wersja z trzema wierszami **warunkowymi** (każdy widoczny wyłącznie przy włączonej
fladze): „Weryfikacja statusu ucznia”, „Statystyka wyświetleń materiałów z warsztatów”, „Pomocnicza
ocena prac uczestników przez model językowy”.

### Listy koordynatora: konta usunięte i sortowanie (bez flagi)

Zgłoszenie organizatora: „Koordynator widzi skasowanych użytkowników jako ‚deleted’ – to błąd.
Koordynator przeglądając uczestników powinien mieć możliwość ich sortowania po różnych polach.”

- **Konta usunięte schowane domyślnie.** Jedna reguła rozpoznania konta po anonimizacji
  (`apps.accounts.anonymised`: domena `@invalid.`, a nie puste imię ani `is_active`) – w Pythonie
  (`is_anonymised`) i w zapytaniu (`anonymised_q`, `User.objects.exclude_anonymised()`,
  `Participant.objects.exclude_anonymised()`); retencja korzysta z tej samej funkcji. Odsiewają ją:
  lista kont i uczestników, wyszukiwarka panelu, lista członków komisji, tabela obecności na
  warsztatach, arkusz „Uczestnicy edycji”, a od tego wydania także **przyjazdy i potrzeby** oraz
  **obecność na etapie stacjonarnym** (razem z liczbami do zamówienia i listami PDF, które idą za
  przełącznikiem) – każda z przyciskiem **„Pokaż usunięte konta (N)”** / „Ukryj usunięte konta”
  (`?usuniete=1`, przeżywa stronicowanie, sortowanie i filtry; N to liczba schowanych na bieżącej
  liście). Bez przełącznika, bo przeglądania tam nie ma: lista „Status ucznia” i jej liczniki, listy
  wyboru szkół i klas przy komunikatach, kolejki ekranu „Komitet” i ich plakietka w menu, naliczanie
  wpisowego.
- **„Konto usunięte” zamiast `deleted-…@invalid.…`** tam, gdzie wiersz musi zostać: przydziały,
  moderacja, kalibracja, zgłoszenia, karta problemu, rozmowy, dyplomy, karta uczestnika i członka
  komisji, audyt, zgłoszenia pomocy, nagłówki edycji i usunięcia konta, eksport recenzji, historia
  komunikatów, decyzje o zaświadczeniach, ustawienia oceny AI (filtry szablonu `person`,
  `account_email`, `is_deleted_account` w `coordinator_extras`). Wyniki zostają pod kodem publicznym.
- **Sortowanie kolumn** listy kont i listy uczestników (`?role=participant`, nowe kolumny: szkoła,
  województwo, klasa, zgoda opiekuna, prace w bieżącej edycji): nagłówki z `aria-sort` i strzałką,
  sortowanie po stronie serwera wyłącznie po kluczach z listy dopuszczonych (`apps.web.list_controls`,
  nieznany klucz = porządek domyślny, bez 500), remis rozstrzyga `pk`, stan w adresie przeżywa
  stronicowanie i filtry. Liczba prac – jedno zapytanie zbiorcze na stronę; przy okazji lista kont
  przestała robić dwa zapytania na wiersz (`is_protected` czyta grupy z prefetchu).
- Pasek konta w nagłówku pokazuje zalogowanego (`request.user`), a nie konto z kontekstu widoku
  (karta członka komisji wypisywała tam adres oglądanej osoby).

### Usunięcie konta czyści resztę danych profilu (bez flagi)

Decyzja organizatora z 24.09.2026. `anonymise_account` wyciera odtąd także adres e-mail rodzica
(`guardian_email`), adres opiekuna szkolnego (`supervisor_email`), nazwę placówki wpisaną ręcznie
(`institution_name`) i dowiązanie do słownika placówek organizatora (`custom_institution_ref`), uwagę
tekstową i potrzeby szczególne (dieta, dostępność) z formularzy przyjazdu oraz pseudonimy widza
materiałów z warsztatów; zaświadczenia o statusie ucznia i oceny AI tej osoby znikają razem z kontem
(z plikami). Skutek widoczny: **usunięty uczeń znika z panelu nauczyciela „Moi uczniowie”** i z jego
liczników (dopasowanie szło po adresie opiekuna; `students_of` filtruje też `exclude_anonymised()`
dla profili wytartych wcześniej), a zaświadczenia z warsztatów nie są wystawiane kontom usuniętym.
Migracja danych `accounts.0034` wyrównuje do tej reguły profile zanonimizowane przed wydaniem.

### Wysyłka komunikatów do grup uczestników (bez flagi)

Prośba organizatora: „koordynator dostaje funkcję wysyłania maili do poszczególnych grup uczestników,
w tym do wszystkich”. Rozbudowa ekranu **`/coordinator/messages/`** (Komunikacja → Komunikaty):

- **nowe grupy odbiorców** (`BroadcastGroup`): **„wszyscy uczestnicy konkursu”** – pierwsza na liście,
  uczestnicy bieżącej edycji także bez wpisu do etapu; „zapisani do etapu, bez wysłanej pracy” (wpis
  zarejestrowany/zakwalifikowany bez żadnej pracy w etapie, praca odrzucona przez antywirusa się nie
  liczy – przypomnienie przed terminem); uczestnicy z wybranego **województwa** (przy fladze
  `custom_regions` – **regionu**, łącznie z profilami sprzed flagi); z wybranej **szkoły** (lista
  wyłącznie szkół, z których są uczestnicy tego konkursu, z liczbą w nawiasie; wykaz SIO, słownik
  organizatora i nazwa wpisana ręcznie jako osobne pozycje); z wybranej **klasy**; **obecni na
  wybranym warsztacie** (`cms.WorkshopAttendance`, warsztaty z harmonogramu tego konkursu);
  **opiekunowie szkolni** (profil `SchoolSupervisor` tego konkursu + rola `supervisor`, z członkostwami
  przy `memberships_enforced`). Dotychczasowa grupa edycyjna zmienia etykietę na „uczestnicy bieżącej
  edycji (zapisani do etapu)”;
- **domyślnie bieżąca edycja** (decyzja organizatora): „wszyscy uczestnicy konkursu” oraz grupy
  województwa/regionu, szkoły i klasy obejmują wyłącznie uczestników bieżącej edycji – profil tego
  konkursu **i** (wpis do etapu bieżącej edycji **albo** konto założone nie wcześniej niż
  `Edition.created_at`; `apps.accounts.messaging.current_edition_participants`). Pole „także uczestnicy
  poprzednich edycji” (domyślnie odznaczone) zdejmuje zawężenie; wybór wchodzi do podpisu podglądu,
  do `MessageBroadcast.target` i do audytu. Bez bieżącej edycji te grupy są puste, dopóki pole nie
  jest zaznaczone;
- **parametr grupy w historii i audycie**: nowe pole `MessageBroadcast.target` (JSON, migracja
  `accounts.0033_broadcast_target`) – identyfikator i etykieta etapu/regionu/szkoły/klasy/warsztatu
  z chwili wysyłki; kolumna „Grupa” w „Wysłanych komunikatach”, podgląd („Odbiorcy: …”) i wpis
  `broadcast.sent`. Adresów nadal nigdzie nie zapisujemy; pole wypełnione, ale nienależące do
  wybranej grupy, jest ignorowane;
- **zakres konkursu**: każda grupa liczona w obrębie `request.competition`, a konto wybierane wyłącznie
  po identyfikatorze profilu z tego konkursu. Przy okazji naprawione dwa przecieki: grupa „członkowie
  komitetu” nie miała zakresu konkursu w ogóle, a wiersz rejestru brał konkurs z odwrotu
  `default_competition` zamiast z żądania; nieużywane `recent_broadcasts()` wymaga odtąd konkursu;
- **podpis podglądu**: „Wyślij” przechodzi wyłącznie z ukrytym podpisem (HMAC) grupy, jej parametru,
  tematu i treści z ostatniego podglądu – zmiana czegokolwiek po podglądzie niczego nie wysyła, tylko
  pokazuje podgląd na nowo (wcześniej „Wyślij” z poprzedniego podglądu wysyłał to, co akurat stało
  w formularzu);
- formularz pokazuje wyłącznie pole wymagane przez wybraną grupę (`static/js/broadcast-groups.js`;
  bez JavaScriptu widać wszystkie pola); odbiorcy – jedno zapytanie z półzłączeniem na grupę, listy
  wyboru szkół i klas – po jednym zapytaniu grupującym (bez kont usuniętych).

Świadomie **bez** załączników, bez kopii do adresu opiekuna prawnego (`guardian_email` służy wyłącznie
zgodzie – RODO), bez grupy „rocznik”, bez grupy „zapisani na warsztat, ale nieobecni” i bez wpisów
drużynowych w grupach etapowych. Podręcznik organizatora § 6.1.

### Zaświadczenie o statusie ucznia (flaga `student_status_certificate`)

Nowa aplikacja `apps.student_status` (model `StudentStatusCertificate` – wersje per uczestnik
**i edycja**, bo zaświadczenie potwierdza rok szkolny; migracje `student_status.0001_initial`
i `tenancy.0009_document_kind_student_status`).

- **Uczestnik** (`/me/status-ucznia/`): imienny wzór PDF do podstemplowania (`wzor.pdf` – imię
  i nazwisko, data urodzenia, szkoła, rok szkolny z edycji, puste miejsca na klasę, pieczątkę szkoły,
  datę i podpis dyrektora/sekretarza; skład ReportLab na krojach DejaVu, tekst z nowego rodzaju
  dokumentu `STUDENT_STATUS` w „Szablonach dokumentów” ze znacznikami `{birth_date}`
  i `{school_year}`), wgranie skanu PDF/JPG/PNG do 10 MB (format po treści, prywatny storage
  `student-status/…`, skan ClamAV kolejką `scan`, zainfekowany – odrzucony i usunięty), ponowne
  wgranie zastępuje oczekujące/odrzucone (plik poprzedni usuwany, historia zostaje), stan brak /
  oczekuje / zaakceptowane / odrzucone z powodem; przypomnienie na pulpicie do czasu akceptacji –
  **nie blokuje** oddawania prac.
- **Koordynator** (`/coordinator/student-status/`, *Uczestnicy i konta* → „Status ucznia”): liczniki
  i filtry oczekujące / zaakceptowane / odrzucone / brak, wybór edycji, wyszukiwarka, podgląd skanu
  (po czystym skanie; `nosniff`, obrazy z `CSP: sandbox`; audyt `student_status.viewed`),
  **Akceptuj** / **Odrzuć z powodem** (e-mail do uczestnika, audyt `student_status.accepted/rejected`
  bez treści powodu), sekcja „Status ucznia” na karcie uczestnika.
- **Paczki ZIP**: przy każdym pobraniu prac koordynatora (etap, zadanie, zaznaczone) i komitetu
  (`/review/download/` oraz `GET /api/grading/reviews/download/`) parametr `students=all|verified` –
  „wszystkie prace” (domyślnie) albo „tylko uczniowie z potwierdzonym statusem ucznia”; nazwy plików
  dalej anonimowe, recenzent nigdy nie widzi skanu; przy wyłączonej fladze `verified` to 404
  z powodem, a przyciski wyglądają jak dotąd.
- **RODO**: wiersz rejestru „Weryfikacja statusu ucznia (zaświadczenie ze szkoły)” (§ 9.2), eksport
  danych z sekcją `zaswiadczenia_statusu_ucznia` i plikami, anonimizacja i usunięcie konta kasują
  wiersze i pliki, zadanie beat `student-status-purge-expired-scans` usuwa pliki edycji po terminie
  retencji; storage dostaje `delete()`. Tłumaczenia EN panelu uczestnika i listów.
- Dokumentacja: PODRĘCZNIK-UCZESTNIKA § 5a, PODRĘCZNIK-ORGANIZATORA § 4.1, 7.2a, 9.1, 9.2, 10a,
  PODRĘCZNIK-RECENZENTA § 2, OPERACJE § 6.4 i § 15.

### Materiały z warsztatów (flaga `workshop_materials`)

Prośba organizatora: „Koordynator dostaje możliwość wgrywania materiałów z warsztatów, w tym filmów.
Filmy powinny być możliwe do obejrzenia tylko na stronie po zalogowaniu.” Nowa aplikacja
`apps.workshop_materials` (migracja `workshop_materials.0001_initial`: modele `WorkshopMaterial`
i `WorkshopMaterialViewer`).

- **Koordynator** – `/coordinator/workshops/materials/` (Raporty → Materiały z warsztatów, odnośnik
  także z ekranu obecności): lista warsztatów z harmonogramu (blok `schedule` strony „Warsztaty”), pod
  każdym materiały – **film** (MP4/WebM do 4 GB), **plik** (PDF, PPTX, DOCX, XLSX, ODP/ODT/ODS, ZIP,
  IPYNB, PNG/JPG do 100 MB) albo **odnośnik** (`https://`); tytuł, opis, kolejność w obrębie
  warsztatu, publikacja, podgląd szkicu, usunięcie razem z obiektem w magazynie; audyt
  `workshop_material.*` bez tytułu i nazwy pliku. Materiał jest przypięty kluczem warsztatu (data +
  temat, jak obecność) z **migawką** tematu i daty – po zmianie wiersza w harmonogramie materiał nie
  znika u widzów, a koordynator widzi sekcję „Materiały bez warsztatu w harmonogramie” z przepięciem
  całej grupy.
- **Wgrywanie bez gunicorna**: przeglądarka wysyła plik częściami po 16 MB prosto do MinIO na adresy
  podpisane przez serwer (`static/js/workshop-material-upload.js`, trzy części naraz, ponowienia,
  pasek postępu, „Przerwij”); krok „zakończ” składa plik, sprawdza rozmiar i **format po treści**
  (MP4 z marką ISO BMFF albo WebM; MOV/MKV odrzucane z podpowiedzią przepakowania), pod blokadą
  wiersza. Pliki – skan ClamAV (kolejka `scan`, zagrożenie → obiekt skasowany, materiał
  „odrzucony”); filmy bez ClamAV (uzasadnienie w `apps/workshop_materials/tasks.py`). Bez
  transkodowania.
- **Oglądanie po zalogowaniu**: `/warsztaty/materialy/` (lista po warsztatach),
  `/warsztaty/materialy/<id>/` (odtwarzacz `<video controlslist="nodownload">` z adresem podpisanym na
  2 h, przewijanie `Range`), `/warsztaty/materialy/<id>/pobierz/` (plik – przekierowanie na podpis na
  5 min; odnośnik – na adres zewnętrzny). Widzi każde konto z rolą **w tym konkursie**; inne konto –
  403, anonim – logowanie. Wszystkie odpowiedzi `no-store`, żadna z tych ścieżek nie jest na
  allow-liście pamięci stron. Gość na `/warsztaty/` widzi ramkę „zaloguj się, aby obejrzeć” z liczbą
  materiałów, bez adresów.
- **Odnośniki**: „Materiały z warsztatów” w pasku konta i kafel na pulpicie uczestnika `/me/` – tylko
  przy włączonej fladze i co najmniej jednym opublikowanym, gotowym materiale (pamięć podręczna per
  konkurs, kasowana sygnałem; przy wyłączonej fladze zero zapytań – nowy test w `test_invariants.py`).
- **Statystyki**: wyświetlenia i liczba różnych widzów na materiał; widz zapisany wyłącznie jako
  pseudonim HMAC pary (materiał, konto), kasowany po 12 miesiącach albo przy usunięciu konta;
  koordynator nie jest liczony. Wiersz rejestru „Statystyka wyświetleń materiałów z warsztatów”.
- **Infrastruktura**: `deploy/minio/policy-submissions.json` + uprawnienia wgrywania wieloczęściowego
  (na produkcji: `docker compose run --rm minio-init`); `scripts/backup.sh` pomija prefiks
  `workshop-materials/` w kopii nocnej; zadanie beat `workshop-materials-cleanup` (co godzinę:
  porzucone wgrywania > 24 h, pseudonimy > 12 mies.); ustawienia `WORKSHOP_VIDEO_MAX_MB`,
  `WORKSHOP_FILE_MAX_MB`. Caddy i CSP bez zmian.
- Dokumentacja: `OPERACJE.md` § 16; `PODRECZNIK-ORGANIZATORA.md` § 4.11;
  `PODRECZNIK-UCZESTNIKA.md` § 7.

### Ocena AI – sugestia punktów dla komitetu (flaga `ai_grading`)

Nowa aplikacja `apps.ai_grading` (modele `AiGradingSettings`, `AiStageVisibility`, `AiAssessment`,
migracja `ai_grading.0001_initial`), zależność `anthropic>=1.8,<2`. Ocenę wystawia wyłącznie
człowiek; sugestia jest niewiążąca.

- **Klucz API per konkurs** na ekranie `/coordinator/ai-grading/` (Ocenianie → Ocena AI): zaszyfrowany
  w bazie (Fernet z `DJANGO_SECRET_KEY`, własna etykieta), tylko do zapisu – ekran pokazuje
  „ustawiony, kończy się na …abcd”; zastąp / usuń / „Sprawdź klucz” (`models.retrieve`, bez kosztu);
  klucz administracyjny odrzucany; nigdy w logach, audycie, argumentach zadań Celery ani
  w szablonach. Wybór modelu `claude-opus-5` (domyślny) / `claude-sonnet-5`, limit wydatków w USD,
  liczniki zużycia i szacowany koszt (Opus 5: 5/25 USD, Sonnet 5: 2/10 USD za MTok, odczyt cache
  0,1×, zapis 1,25×).
- **Zlecenie z karty zadania** (`/coordinator/problems/<id>/`, sekcja „Ocena AI”): dla wszystkich
  najnowszych wersji prac bez oceny AI (opcja „wygeneruj ponownie także istniejące”) albo dla jednej
  pracy; dwustopniowe – podgląd z liczbą prac i szacowanym kosztem, potem „Zleć”. Idempotentne,
  blokada doradcza per zadanie; stany oczekuje / w toku / gotowa / błąd, sekcja odświeżana htmx,
  zgodność AI z oceną końcową (średnia różnica, % zgodnych, % w granicy 1 pkt).
- **Kolejka z ogranicznikiem**: do brokera trafia najwyżej `AI_GRADING_MAX_CONCURRENCY` (domyślnie 1)
  zadań naraz – koniec oceny wypuszcza następną; beat `ai-grading-pump` (5 min) domyka oceny
  osierocone przez restart workera. Ponowienia wyłącznie 429/5xx/sieć (maks. 4, `retry-after`
  przycięty do 15 min); odmowa i `max_tokens` nie są ponawiane; twardy limit zadania 16 min; limit
  wydatków zatrzymuje kolejkę bez wołania API.
- **Żądanie do modelu**: `client.beta.messages.stream(...)` + `get_final_message()`, `max_tokens`
  32000, `thinking: adaptive`, `output_config` z `effort: high` i schematem JSON, beta
  `server-side-fallback-2026-07-01` z `fallbacks: "default"`; materiały zadania przed pracą,
  `cache_control` na ostatnim stałym bloku; praca jako dokument PDF, obraz albo tekst, bez nazwy pliku
  i danych uczestnika; limity API sprawdzane przed wysyłką. Prompt po polsku z osłoną przed
  wstrzyknięciem poleceń; odpowiedź walidowana, punkty przycinane do skali, dane osobowe autora
  wymazywane; `stop_reason` sprawdzany przed treścią, `request_id` w logu.
- **Panel recenzenta**: zwinięty panel „Ocena AI (sugestia, niewiążąca)” – wyłącznie przy
  przydzielonej wersji pracy, bez danych uczestnika; formularz nigdy nie wypełnia się sam, przycisk
  „Wstaw punkty AI jako punkt wyjścia” tylko zaznacza najbliższą wartość skali (przy rubryce go nie ma).
- **Uczestnik**: przełącznik etapu **„Pokaż uczestnikom ocenę AI”, domyślnie wyłączony** (decyzja
  organizatora); po włączeniu i ogłoszeniu wyników – podsumowanie i proponowane punkty w osobnej
  sekcji informacji zwrotnej z podpisem „sugestia AI”. Przy wyłączonym nic o ocenie AI nie trafia na
  ekrany uczestnika, do tabel wyników, dyplomów, reklamacji ani API uczestnika (test).
- **RODO**: wiersz rejestru „Pomocnicza ocena prac uczestników przez model językowy” (Anthropic jako
  podmiot przetwarzający, przekazanie poza EOG, brak decyzji zautomatyzowanej); eksport danych konta –
  sekcja `oceny_ai`; anonimizacja konta kasuje oceny AI prac tej osoby. Audyt `ai_grading.*` bez
  wartości klucza.
- Dokumentacja: `PODRECZNIK-ORGANIZATORA.md` § 4.12 (z listą warunków prawnych przed włączeniem),
  `PODRECZNIK-RECENZENTA.md` § 3a, `PODRECZNIK-UCZESTNIKA.md` § 6, `OPERACJE.md` § 6.4 i § 17.

## Niewydane (po `v0.31.1`)

| Wersja | Data | Zmiana |
|---|---|---|
| **v0.34.0** | 2026-09-24 | **wydanie zbiorcze z 24.09.2026** (pełny opis w sekcji „v0.34.0 – 2026-09-24” wyżej): **listy koordynatora** – konta usunięte schowane domyślnie za przyciskiem „Pokaż usunięte konta (N)” na każdej liście osób (także przyjazdy i obecność na etapie stacjonarnym), „Konto usunięte” zamiast `deleted-…@invalid`, sortowanie kolumn listy kont i uczestników; **usunięcie konta** czyści też adres rodzica, adres opiekuna szkolnego, placówkę i dane szczególne logistyki – usunięty uczeń znika z panelu „Moi uczniowie” (migracja danych `accounts.0034`); **wysyłka komunikatów do grup** (wszyscy uczestnicy, bez pracy w etapie, województwo/region, szkoła, klasa, obecni na warsztacie, opiekunowie; domyślnie bieżąca edycja; podpis podglądu; `accounts.0033`); za flagami **domyślnie wyłączonymi**: **zaświadczenie o statusie ucznia** (`student_status_certificate`, filtr paczek ZIP „tylko z potwierdzonym statusem”; `OPERACJE.md` § 15), **materiały z warsztatów** (`workshop_materials`, filmy i pliki dla zalogowanych, wgrywanie częściami prosto do MinIO; § 16) i **ocena AI** (`ai_grading`, sugestia punktów Claude'a dla komitetu, przełącznik etapu „Pokaż uczestnikom ocenę AI” domyślnie wyłączony, zależność `anthropic`; § 17); rejestr czynności **1.7** z trzema wierszami warunkowymi |
| **v0.33.0** | 2026-09-23 | **plakaty zgrupowane w karty** (prośba organizatora z 23.09.2026: „jedna karta na format, kilka przycisków” zamiast osobnej karty na każdy plik „A3 (JPG)”, „A3 (PDF)”, „A3 (PDF ze spadem 3 mm)”…): `PromoMaterial` dostaje dwa pola (migracja `promo.0002_group_variant_label`) – **`group`** („Karta (grupa plików)”, np. „A3 · 297×420 mm”: pliki jednego konkursu z identyczną, niepustą grupą stają na `/plakaty/` na **jednej karcie** – nagłówek to grupa, podgląd to pierwszy podgląd w grupie, opis pierwszy niepusty, pod spodem przycisk na każdy plik w kolejności koordynatora; karta stoi tam, gdzie jej pierwszy plik) i **`variant_label`** („Napis na przycisku”, np. „PDF ze spadem 3 mm”; puste = sam format JPG/PNG/PDF). Przycisk „Pobierz JPG · 1,7 MB” prowadzi do **własnego** adresu pobrania pliku, więc liczenie pobrań, limit, pseudonim IP i statystyki zostają per plik; nazwa dostępna przycisku niesie grupę („Pobierz A3 · 297×420 mm – PDF ze spadem 3 mm”). Plik bez grupy wygląda jak dotąd. Karty składa Python z tej samej jednej listy (`apps.promo.cards.build_cards`) – liczba zapytań `/plakaty/` bez zmian (test). Ekran koordynatora: oba pola w formularzu (z podpowiedzią `<datalist>` grup tego konkursu), linia „Karta: … · przycisk „…”” pod tytułem w tabeli, dwie nowe kolumny w eksporcie CSV („karta (grupa)”, „przycisk”); podręcznik organizatora § 4.10 |
| **v0.32.0** | 2026-09-23 | **plakaty do pobrania** (prośba organizatora z 23.09.2026): nowa aplikacja `apps.promo` (modele `PromoMaterial` i `PromoDownload`, migracja promo.0001), strona publiczna **`/plakaty/`** (siatka kart: podgląd, tytuł, opis, format i rozmiar, „Pobierz”; 404, gdy konkurs nie ma opublikowanych plakatów; na allow-liście pamięci stron, unieważnianej przy każdym zapisie plakatu) i pobranie `/plakaty/<id>/pobierz/` (plik z prywatnego storage jako załącznik przez aplikację, `Cache-Control: no-store`, nigdy w pamięci stron); plik PDF/JPG/PNG do 50 MB rozpoznawany **po treści** (sygnatury `%PDF-`, `FF D8 FF`, PNG), miniatura JPG/PNG robiona automatycznie (Pillow), dla PDF-a opcjonalny własny podgląd albo ikona; odnośnik „Plakaty do pobrania” w stopce każdej strony i przycisk w panelu opiekuna szkolnego – tylko gdy jest opublikowany plakat (flaga w Redisie, unieważniana przy zapisie; budżety zapytań `/`, `/me/`, `/coordinator/` +1 na zimno, na ciepło zero). Ekran koordynatora **`/coordinator/posters/`** (Ustawienia → Plakaty do pobrania): dodanie, edycja, publikacja, kolejność, usunięcie (plakat z pobraniami trafia do archiwum ze statystykami), eksport CSV, audyt `promo.*`; statystyki **podwójne** – pobrania i **unikalne adresy IP** w oknach 7 dni / 30 dni / od początku (unikalność w całym oknie i w sumie między plakatami), kafelki, wykres dzienny obu szeregów (CSS, bez JS), eksport z tymi samymi kolumnami. Nie liczymy robotów, podglądów linków, `HEAD` ani koordynatora; podwójne kliknięcie (ten sam plakat i adres w 10 s) to jedno pobranie, a pobieranie ma limit 30/min na adres IP (scope `poster_download`, 429 bez zapisu pobrania); `HEAD` na plik brakujący w storage daje 404 jak `GET`. Adresu IP nie zapisujemy: zostaje **pseudonim** HMAC-SHA256 z kluczem z `SECRET_KEY`, zerowany po 12 miesiącach nowym zadaniem beat `promo-clear-expired-ip-hashes`; rejestr czynności przetwarzania 1.6 – nowa czynność „Statystyka pobrań materiałów promocyjnych” (art. 6 ust. 1 lit. f) |
| **v0.31.2** | 2026-09-22 | strona rejestracji opiekuna szkolnego (`/register/supervisor/`) bez zaszytego w szablonie wstępu nad formularzem (organizator, 22.09.2026: „usuń tylko ten tekst nad formularzem”; „czy to intro mogę edytować z poziomu CMS”) – w jego miejsce pole `SiteSettings.supervisor_registration_intro` (`/cms/` → Ustawienia → Dane serwisu, sekcja „Rejestracja”; migracja cms.0027): domyślnie puste, czyli akapitu nie ma, a wpisany tekst (pogrubienie, kursywa, odnośnik) pojawia się nad formularzem bez wdrożenia; wyjaśnienie, skąd bierze się lista uczniów, zostaje w pustym stanie pulpitu opiekuna |

Nie zlecone: drzewo CMS dla konkursu w trybie prefiksu ścieżki (uwaga T43), edytor przebiegu
przenoszący „przypisz kategorie” do warstwy serwisów. Forum w wersji pierwszej świadomie **nie ma**
powiadomień e-mail, wiadomości prywatnych, załączników, polubień ani rankingów — uzasadnienie
każdej z tych decyzji stoi w `PODRECZNIK-ORGANIZATORA.md` § 6.4.

## Wydania

| Wersja | Data | Zmiana |
|---|---|---|
| **v0.31.1** | 2026-09-22 | osobna pozycja głównego menu „Dla nauczycieli” (prośba organizatora z 22.09.2026): odnośnik do `/register/supervisor/` stoi teraz jako ostatnia pozycja menu, za drzewem CMS (albo za listą zapasową), a nie tylko na `/register/` i na `/login/` jak dotąd; widoczna wyłącznie niezalogowanemu czytelnikowi i wyłącznie na witrynie, która ma dziś włączony przełącznik `SiteSettings.supervisor_registration_enabled` (`apps/cms/context_processors.py::_supervisor_menu_item`) – ten sam warunek, co reszta odnośników do tej roli. Budżet zapytań strony głównej (`apps/tenancy/tests/test_invariants.py::QUERY_BUDGET["/"]`) rośnie o jedno zapytanie: menu, w przeciwieństwie do leniwego procesora `supervisor_registration`, musi znać wynik przełącznika od razu, żeby wiedzieć, czy w ogóle dołożyć pozycję (nadal trzydziestosekundowa pamięć podręczna na proces, nie zapytanie na żądanie)
| **v0.31.0** | 2026-09-22 | wydajność: test obciążeniowy z 22.09.2026 pokazał, że pod ASGI (gunicorn + `UvicornWorker`) w 100% synchroniczna aplikacja serializowała widoki na jeden wątek na proces – 3 workery dawały maks. 3 równoległe żądania, ok. **7 req/s** w nasyceniu przy p95 **2,3 s** (5 użytkowników), 300–600% CPU z samego przełączania wątków. Usługa `web` przechodzi na **WSGI + worker `gthread`** (`config.wsgi`, `--workers`/`--threads`, domyślnie 4×4 – concurrency procesu to teraz iloczyn, nie sama liczba workerów; ta sama zmiana w `backend/Dockerfile`, żeby `docker run` bez compose zgadzał się z compose); żaden widok, zadanie ani middleware nie wymagał ASGI (bez `async def`, bez `channels`, bez websocketów). Wątek roboczy `gthread` żyje w puli workera zamiast ginąć po żądaniu, więc trwałe połączenia z bazą znów mają sens: `DB_CONN_MAX_AGE` wraca z 0 na **60 s** i dochodzi `CONN_HEALTH_CHECKS` (budżet: 4×4 web + 2 worker + 1 beat ≈ 19–20 z 100 możliwych połączeń Postgresa – `WEB_THREADS` dochodzi do `.env.example` i do szablonu `.env` w `scripts/deploy.sh`, istniejące `.env` na produkcji zostaje nietknięte, `WEB_WORKERS=3` × domyślne `WEB_THREADS=4` daje 12 równoległych żądań bez żadnej ręcznej zmiany). Pliki statyczne (`{% static %}`, WhiteNoise `CompressedManifestStaticFilesStorage`, nazwy z odciskiem treści) dostają w Caddy'm `Cache-Control: public, max-age=31536000, immutable` na `/static/*`. Nowe zadanie Celery `apps.core.tasks.captcha_clean` (godzinowe) sprząta wygasłe wiersze `captcha.CaptchaStore`, których `django-simple-captcha` samo nigdy nie kasuje. Higiena kontenerów: log każdej usługi w compose ograniczony do 50 MB × 5 plików (`json-file`, wspólna kotwica YAML), limity pamięci (`mem_limit`, bo `deploy.resources` nie działa poza Swarmem) – `web` 2g (cztery workery plus stary i nowy naraz przy rotacji `--max-requests`), `clamav` 3g (przy przeładowaniu sygnatur ClamAV trzyma przez chwilę dwie bazy), `worker` 768m; bez limitów CPU (dławienie rdzeni tylko wydłużyłoby czas odpowiedzi pod szczytem ruchu). `scripts/deploy.sh` dostaje po kroku 8/8 nowy, ostatni krok „Porządki: stare obrazy”: po wdrożeniu zostają tylko bieżący i poprzedni tag `olimpiada/web` (rollback bez ponownego budowania) plus `docker image prune -f` dla warstw bez tagu. Opis pełnego budżetu współbieżności i połączeń oraz kroków rollbacku: `docs/OPERACJE.md` § 11 **Wyszukiwarka szkół:** wyszukiwarka szkół (`GET /api/schools/`, `GET /api/schools/cities/`) po indeksach GIN + `pg_trgm` zamiast pełnego przejścia po tabeli: statystyki produkcji (22.09.2026, okno 15 dni) pokazały 864 sekwencyjne skany `schools_school` (7,0 mln przeczytanych wierszy) wobec 247 tys. skanów indeksowych, bo `search_text__contains`/`city_search__contains` (koniunkcja tokenów, dzielnica po separatorze) to dopasowanie **w środku** napisu, którego zwykły B-tree nie obsłuży – stąd 130–185 ms na zapytanie. Migracja `schools.0006_pg_trgm_search_indexes` włącza rozszerzenie `pg_trgm` (`TrigramExtension`, bez uprawnień superużytkownika – zaufane od PostgreSQL 13) i zakłada `schools_search_trgm_idx`/`schools_city_trgm_idx` (`GinIndex`, `gin_trgm_ops`); prefiks (`city_search__startswith`) zostaje przy istniejącym indeksie `varchar_pattern_ops`. Przy okazji zdjęte trzy indeksy z zerem skanów na produkcji w tym samym oknie: `schools_city_kind_idx` (porządek listy liczy wyrażenie `Case` w Pythonie, nie kolumnę `kind` – indeks nigdy nie mógł posłużyć sortowaniu) oraz para spod `db_index=True` na `search_text` (`schools_school_search_text_…` i jej bliźniak `_like`), zastąpiona przez indeks trigramowy. Wyniki, kolejność i ranking wyszukiwarki bez zmian – zmienił się wyłącznie plan zapytania (dowód: `apps/schools/tests/test_search_indexes.py`, porównanie wierszy między planem z indeksem a wymuszonym `Seq Scan`); zmierzone lokalnie na pełnym wykazie (8118 wierszy): `search_text__contains='lice'` 5,1 ms/547 buforów → 3,9 ms/220 buforów **Cache stron publicznych:** cache całych stron publicznych dla anonimowych GET-ów (profilowanie produkcji z 22.09.2026: 250–500 ms CPU na odsłonę, głównie renderowanie szablonu, przy identycznej treści dla każdego anonimowego gościa danej witryny) – nowa warstwa `apps.web.page_cache.PageCacheMiddleware`, ostatnia przed widokiem, wyłącznie dla adresów z allow-listy (`/`, `/harmonogram/`, `/warsztaty/`, `/dokumenty/…`, `/faq/`, `/partnerzy/`, `/kontakt/`, `/aktualnosci/…`, `/wyniki/`, `/archiwum/…`, `/statystyki/`); nonce CSP i token CSRF (ten drugi w `hx-headers` na **każdej** stronie, patrz `templates/base.html`) trzymane w cache'u jako placeholder i podmieniane na świeże przy każdym trafieniu, więc żaden skrypt nie traci nonce'u, a HTMX nie dostaje nieważnego tokenu; klucz niesie wersję (globalną i witryny konkursu – `INCR`, bez wyliczania wpisów), język interfejsu, ścieżkę i `?page=`; TTL 120 s (`PAGE_CACHE_SECONDS`, `0` wyłącza), włącznik `PAGE_CACHE_ENABLED` (domyślnie włączony poza `DEBUG`, wyłączony w testach); nigdy nie cache'uje zalogowanych, żądań spoza `GET`/`HEAD`, odpowiedzi z `Set-Cookie`, sesji zmienionej w trakcie obsługi (przełącznik kontrastu gościa), komunikatu organizatora **wyświetlonego** w tym żądaniu (sprawdzenie niezależne od `session.modified`, bo `MessageMiddleware` zapisuje skonsumowaną kolejkę do sesji dopiero w swojej fazie odpowiedzi, czyli już po tej warstwie) ani odpowiedzi większej niż 512 KiB; `Cache-Control: private, no-store` na każdej odpowiedzi HIT/MISS z tej warstwy, żeby ewentualny przyszły CDN przed Caddym nigdy nie zbuforował materializowanego nonce'u/tokenu; awaria Redisa degraduje do normalnego renderowania zamiast pięćsetki (`IGNORE_EXCEPTIONS` w `CACHES["default"]` plus własne opakowanie wywołań cache'a w warstwie); unieważnianie przy publikacji/wycofaniu/przeniesieniu/skasowaniu strony, zapisie `SiteSettings`, komunikacie organizatora, zmianie edycji/etapu/wydarzenia i ogłoszeniu wyników; `manage.py page_cache_clear` do ręcznego gaszenia; nagłówek `X-Page-Cache: HIT/MISS/BYPASS` wyłącznie do weryfikacji **Koordynator:** koordynator resetuje hasło cudzego konta z karty `/coordinator/accounts/<id>/` — przycisk „Wyślij link do zmiany hasła” (`CoordinatorPasswordResetView`) wysyła dokładnie ten sam list, co samoobsługowy formularz „Nie pamiętasz hasła?” (`PasswordResetForm.save()` z tymi samymi szablonami i kontekstem listu), a koordynator nie widzi ani hasła, ani treści linku; odmowa bez wysyłki i bez wpisu audytowego dla konta jeszcze nieaktywowanego, zablokowanego, bez hasła platformy (logowanie przez zewnętrznego dostawcę) i dla konta własnego koordynatora (od tego jest „Nie pamiętasz hasła?” na stronie logowania); throttle `password_reset` (dla anonima z formularza publicznego) tego żądania nie dotyczy — koordynator jest już zalogowany; wpis audytowy `password.reset_sent` bez adresu i bez tokenu |
| **v0.30.1** | 2026-09-22 | rejestracja opiekuna szkolnego (prośba organizatora z 22.09.2026: rola istniała od wydania z 19.09, ale bez żadnego odnośnika) staje się **widoczna**, gdy przełącznik `supervisor_registration_enabled` jest włączony **na tej witrynie**: pole „Jesteś nauczycielem?” na `/register/` i na `/login/`, odnośnik powrotny „Jesteś uczniem?” na `/register/supervisor/` (bez zapytania na stronach, które go nie pokazują – leniwa wartość w `apps.web.context_processors.supervisor_registration`, per witryna jak `apps.cms.analytics`); formularz rejestracji zbiera odtąd też **zgody** (regulamin, RODO – te same dokumenty i wersje, co u uczestnika), zapisywane jako `ConsentRecord` (kolumna `supervisor`, migracja `accounts.0032`, ograniczenie „dokładnie jeden właściciel wpisu”); ten sam dowód wchodzi do eksportu danych konta (art. 20 RODO) i do anonimizacji (art. 17) – szkoła, telefon i zgody opiekuna znikają, a potwierdzenia udziału szkoły w edycji (`SchoolParticipation`) liczą się teraz do „śladu w zawodach”, więc samoobsługowe usunięcie takiego konta anonimizuje, a nie kasuje wiersza; strona „Konto zostało założone” tłumaczy nauczycielowi, co dalej (aktywacja, uczniowie wpisują jego adres w profilu, panel „Moi uczniowie”); lista `/coordinator/accounts/` pokazuje przy roli „opiekun szkolny” też nazwę szkoły, a `/admin/` dostał ekran opiekunów z podglądem dowodów zgód. Automat retencji (`/coordinator/retention/`) świadomie **nie** obejmuje jeszcze opiekunów (dług udokumentowany w `apps/accounts/retention.py` i w podręczniku organizatora § 9.1). Rejestr czynności przetwarzania w wersji **1.5**. Wdrożenie: okres rozruchu healthchecku `web` wydłużony z 40 s do 180 s (migracje i collectstatic przed startem gunicorna przekraczały go po większych wydaniach i `deploy.sh` przerywał się na „web unhealthy”) |
| **v0.30.0** | 2026-09-22 | rejestracja pyta o **pełną datę urodzenia** zamiast samego rocznika (zgłoszenie organizatora) i z niej rozstrzyga pełnoletność: dorosły = ma już za sobą dzień osiemnastych urodzin, liczony datą lokalną Europe/Warsaw, 29 lutego → 1 marca. Jedno miejsce reguły (`accounts.consents.is_minor`) obsługuje obie postacie danych: pełną datę dokładnie, a sam rocznik – starą, zachowawczą regułą „rok bieżący − rocznik ≤ 18”, bo profile sprzed tej zmiany dnia urodzin nie mają i nie będą miały (`Participant.birth_date` nullowalne, migracja `accounts.0031` bez backfillu; `birth_year` zostaje `NOT NULL` i jest liczony z daty przy każdym zapisie). Pole „Data urodzenia” (`<input type="date">`, zapis ISO i `DD.MM.RRRR`) w obu formularzach rejestracji, w edycji profilu i na ekranie koordynatora; `register-age.js` odsłania zgodę opiekuna według daty z serwera (`data-current-date`); API rejestracji przyjmuje `birth_date` i nadal `birth_year`; import listy klasowej przyjmuje kolumnę „data urodzenia” (ISO albo `DD.MM.RRRR`) i po staremu „rok urodzenia”; eksport koordynatora ma obie kolumny, eksporty dla podmiotów zewnętrznych nadal nie niosą wieku w ogóle; panel uczestnika prosi o uzupełnienie brakującej daty (audyt `participant.birth_date_completed`), anonimizacja czyści ją całą; `RegistrationProfile.require_birth_year` wreszcie działa i znaczy „data urodzenia wymagana” – wyłączony zostawia wiek nieznany, czyli traktuje każdego jak osobę niepełnoletnią. Rejestr czynności przetwarzania w wersji **1.3**. Na produkcji: `seed_legacy_content --only zgoda-opiekuna` (wzór oświadczenia prosi teraz o datę, a nie o rocznik) |
| **v0.29.2** | 2026-09-22 | link aktywacyjny i nieaktywowane konto żyją **24 godziny** zamiast czterech (`ACTIVATION_MAX_AGE`; decyzja organizatora) – list, komunikaty, FAQ, rejestr czynności i podręczniki mówią to samo. Na produkcji FAQ wymaga `seed_legacy_content --only faq` (o ile strona nie była redagowana w /cms/) |
| **v0.29.1** | 2026-09-22 | logotypy w sliderze mieszczą się w plakietkach (jednakowe plakietki 120×36 px, obraz skalowany w dół z zachowaniem proporcji); przeciąganie taśmy myszą/palcem przesuwa ją zamiast zaczynać natywne „przeciągnij i upuść” obrazka lub odnośnika, a kliknięcie po przeciągnięciu nie otwiera strony partnera |
| **v0.29.0** | 2026-09-21 | slider sponsorów w menu, po prawej stronie „FAQ” (uwaga organizatora z 21.09.2026, „jak na Olimpiadzie Biologicznej”): taśma logotypów (organizator zawsze pierwszy, potem partnerzy z `/partnerzy/` z logotypem, w kolejności strony) przesuwa się o jeden co `sponsor_slider_seconds` sekund, w nieskończonej pętli, bez skoku po okrążeniu (`static/js/sponsor-slider.js`, klasyczna sztuczka podwójnej taśmy, zero bibliotek); włącznik `sponsor_slider_enabled` i filtr poziomów współpracy `sponsor_slider_levels` na `cms.SiteSettings` (migracja `cms.0026`), ekran koordynatora `/coordinator/sponsor-slider/` (checkbox włącznika, sekundy, poziomy z liczbą partnerów przy każdym, podgląd oznaczający wpisy pominięte i dlaczego, audyt `site.sponsor_slider_updated`); partner pod tym samym adresem co organizator nie dubluje się w taśmie; ładunek z pamięcią podręczną per witryna (5 minut, unieważniana przy publikacji „Partnerzy” i przy zapisie ustawień) – warstwa zapytań kosztuje zero przy odświeżeniu w tym samym oknie; poniżej 900 px i przy `prefers-reduced-motion: reduce` slider nie przewija się (statyczny rząd pierwszych logotypów) |
| **v0.28.1** | 2026-09-21 | plik PDF ze składem komitetów **usunięty** (decyzja organizatora: strona `/dokumenty/komitety/` zostaje bez zmian, plik do pobrania i dokument w bibliotece Wagtaila znikają); `apps.cms.attachments.retire_documents` – funkcja współdzielona przez `seed_legacy_content` i nową komendę `manage.py retire_legacy_files` (zdejmuje plik na produkcji **bez** ponownego seedowania treści strony i bez nowej rewizji, `--dry-run` tylko liczy); `build_guardian_consent_pdf --document komitety` zostaje – składa wydruk na żądanie, ale wynik nie leży już w repozytorium ani nie jest nigdzie przypięty |
| **v0.28.0** | 2026-09-21 | **forum uczestników moderowane przez koordynatora** (prośba organizatora z 21.09.2026), za flagą `participant_forum` **domyślnie wyłączoną**: uczestnik dostaje `/forum/` (działy, wątek po 20 wpisów, nowy wątek, odpowiedź, zgłoszenie wpisu) i `/forum/mine/` – jedyne miejsce, w którym autor dowiaduje się o odrzuceniu i czyta uzasadnienie; koordynator `/coordinator/forum/` (kolejka wątków, wpisów i zgłoszeń, zbiorcze zatwierdzanie, spis wątków, wątek z każdym stanem, działy, ustawienia). Wypowiedź jest **zwykłym tekstem** (bez HTML-a i załączników, `linebreaksbr` + `urlize` z `rel="nofollow noopener noreferrer"`), podpisem jest **imię i inicjał nazwiska** – nigdy adres e-mail, szkoła ani kod `OLM-…` (klucz anonimowego oceniania). **Dopóki którykolwiek etap przyjmuje rozwiązania, obowiązuje moderacja wstępna niezależnie od ustawienia konkursu** (regulamin § 10 ust. 2 i § 17), a formularz pisania niesie ostrzeżenie z nazwą etapu. Własny wpis poprawialny przez 15 minut (poprawka opublikowanego wraca do kolejki), usunięcie miękkie (`HIDDEN`), limit `forum` 30/h; każda decyzja moderatora zostawia zdarzenie `forum.*` w audycie **bez kopii treści**. RODO: wiersz forum w rejestrze czynności (wersja **1.2**, warunkowy – wchodzi wyłącznie konkursom z włączoną flagą), wpisy w paczce `/account/export/`, anonimizacja konta zdejmuje podpis („Użytkownik usunięty”), treść zostaje częścią rozmowy. Bez powiadomień e-mail – sygnałem jest odznaka w menu panelu. Konkurs z domyślnymi przełącznikami nie zmienia się o ani jeden adres i ani jedną pozycję menu. Działy startowe jednym poleceniem: `manage.py seed_forum_categories --competition <slug>` zakłada dział „Ogólne” i po jednym dziale na każdy warsztat z tabeli harmonogramu strony „Warsztaty” (nazwa bez dopisku prowadzącego, termin i prowadzący w opisie) – idempotentne, rozpoznaje istniejące działy po slugu i nie nadpisuje redakcji koordynatora |
| **v0.27.4** | 2026-09-21 | uwagi organizatora z 21.09: **menu** w ustalonej kolejności – domek (strona główna), Komitety, Partnerzy, Harmonogram, Zadania, Wyniki, Warsztaty, Dokumenty, Kontakt, FAQ (`cms/context_processors.py`: `MENU_ORDER`; newsroom i archiwum zdjęte z menu, skład komitetów wyniesiony z listy „Dokumenty”, z której zniknęła też pozycja „Wszystkie dokumenty”); **aktualności** jako stały panel strony głównej z odnośnikiem do wszystkich; **linia czasu** przeniesiona z nagłówka na dół strony, nad stopkę (`.timeline-dock`); **stopka** bez „Dokumenty”, „Najczęstsze pytania”, „Statystyki” i „Rejestracja z kodem” (adresy działają dalej); **„Zgłoś problem”** – ten sam odnośnik do formularza w pasku konta (także dla niezalogowanych) i w stopce; **skład komitetów** z tytułami i afiliacjami (12 + 7 osób), PDF do pobrania składany z treści strony (`build_guardian_consent_pdf --document komitety`) |
| **v0.27.3** | 2026-09-21 | protokół etapu (eksport PDF) podpisuje **Komitet Sterujący** – przewodniczący, sekretarz i członek oraz zdanie o zgodności zestawienia (domknięcie v0.26.5: „Komitet Główny” nie występuje już w żadnym dokumencie); CI: shardy `pytest` układane po zmierzonym czasie (`backend/.test_durations`, `--splitting-algorithm least_duration`, odświeżanie: `docs/OPERACJE.md` § 10) |
| **v0.27.2** | 2026-09-21 | CI zielone i szybsze: zadanie `pytest` dostało środowisko obrazu (skompilowane katalogi tłumaczeń, `collectstatic`, klucz ≥ 50 znaków, poświadczenia S3/MinIO) – znika 12 stałych niepowodzeń i 4 błędy zależne od środowiska; `msgfmt` instaluje gettext; testy w 5 równoległych shardach (`pytest-split`) z jednym statusem zbiorczym „pytest (wynik zbiorczy)” – ok. 17 min zamiast ok. 30 (shardy jeszcze nierówne: brak pliku czasów). Bez zmian w aplikacji |
| **v0.27.1** | 2026-09-21 | regulamin – dwie poprawki brzmienia na polecenie organizatora: „Ministra właściwego ds. Edukacji” w ramce statusu i domknięty cudzysłów „ZOZ” w § 1 ust. 4 (strona, .docx, wyciąg tekstowy; PDF złożony z poprawionego .docx w Wordzie – 14 stron). W dokumencie Google obie zmiany stoją jako sugestie do zaakceptowania |
| **v0.27.0** | 2026-09-20 | przekazywanie przyjętych rozwiązań na skrzynkę organizatora: ekran `/coordinator/submission-forwarding/` (do pięciu adresów per konkurs, puste pole = wyłączone, audyt `competition.forwarding_updated` bez adresów), list z metryczką pracy i plikiem w załączniku wysyłany **po czystym skanie antywirusowym** z zadania Celery na kolejce `mail` (`apps/submissions/forwarding.py`), znacznik `SubmissionFile.forwarded_at` przeciw duplikatom, granica załącznika `SUBMISSION_FORWARD_MAX_ATTACHMENT_MB` (domyślnie 20 MB; powyżej list bez pliku i z odnośnikiem do panelu), limit koperty relaya podniesiony z 10 MB do 40 MiB, wiersz o tej drodze w rejestrze czynności przetwarzania (wersja 1.1) |
| **v0.26.5** | 2026-09-20 | dyplomy i zaświadczenia wystawia **Komitet Sterujący**: linia podpisu „Przewodniczący Komitetu Sterującego Olimpiady Kwantowej” (`SIGNATURE_LINE`, wartość początkowa nowych konkursów; migracja `tenancy.0007` podmienia nazwę komitetu w istniejących szablonach dokumentów). Dokumenty składają się przy pobraniu, więc już wystawione też dostają nowy podpis |
| **v0.26.4** | 2026-09-20 | uwagi organizatora z 20.09: **regulamin** w wersji z 20 września 2026 r. (eksport z Dokumentów Google: strona, PDF i .docx bez roboczych komentarzy; importer czyta tabele w `<th>` i sekcję „Status Olimpiady Kwantowej”; dokument bez metryki dostaje datę 20.09.2026; `TERMS_VERSION` = „z 20 września 2026” + migracja `accounts.0030` dla definicji zgody); **harmonogram i strona główna bez okna reklamacji**; komunikat o rejestracji „Zakończenie rejestracji: 28.02.2027” (migracja `cms.0025` podmienia tylko niezmienioną wartość domyślną); zakres Komitetu Merytorycznego + „rozpatrywanie odwołań”; instrukcja zgody opiekuna „Pobierz i wydrukuj formularz.”; komenda `replace_page_text` – poprawka jednego sformułowania na stronie prowadzonej w /cms/ (raport bez `--apply`, nowa rewizja + publikacja, odmowa przy szkicu) |
| **v0.26.3** | 2026-09-20 | wyszukiwarka szkół: lista podpowiedzi (szkół i miejscowości) rozwija się pod swoim polem – opakowania pól to `<div class="field">` zamiast `<p>`, bo parser HTML wyrzucał `<ul>` poza akapit i lista rozciągała się na całą szerokość strony |
| **v0.26.2** | 2026-09-19 | logo olimpiady na dyplomach i zaświadczeniach: znak z `static/img/logo-olimpiada-kwantowa@2x.png` w lewym górnym rogu każdego dokumentu (blok `logo` układu); logo szablonu graficznego ma pierwszeństwo, `show: false` je gasi, konkurs z własną marką dostaje swój logotyp (`competition_logo`) |
| **v0.26.1** | 2026-09-19 | poczta na `MAILERS` (Django 6.1) zamiast wycofywanych `EMAIL_*` – te same zmienne `.env` (`EMAIL_URL`, `EMAIL_TIMEOUT`), zero ostrzeżeń `RemovedInDjango70Warning`, testy kontrolne skrzynki (197 listów przed = 197 po, test po teście); `reportlab` 5.x (pin `>=5.0,<6`) – 12 rodzajów dokumentów bajt w bajt identycznych z 4.5.1; stopka dyplomu cofa się przed kodem QR (w domyślnym układzie kod zasłaniał końcówkę „Data wystawienia”) |
| **v0.26.0** | 2026-09-18 | aktualizacja frameworka: Django 5.1 → **6.1.1**, Wagtail 6.3 → **8.0**, DRF 3.15 → 3.18, celery 5.6, django-redis 7.0, drf-spectacular 0.30, django-environ 0.14, django-simple-captcha 0.7; `django.contrib.postgres` w `INSTALLED_APPS` (wymóg sprawdzenia `postgres.E005` dla indeksu wyszukiwania Wagtaila); ograniczenie `Django<6.1` w `django-celery-beat` 2.9.0 nadpisane w `[tool.uv] override-dependencies` (harmonogram sprawdzony: migracje, `DatabaseScheduler`, panel zadań, synchronizacja 8 wpisów); żadnej nowej migracji naszych aplikacji, budżety zapytań i złote testy bez zmian, 4623 testy; opis i wycofanie: `OPERACJE.md` § 9 |
| **v0.25.0** | 2026-09-18 | konkursy w subdomenach zakładane z panelu koordynatora (`/coordinator/competitions/new/` za flagą `competition_creation` i przełącznikiem `PLATFORM_SUBDOMAINS`, podgląd przed założeniem, twórca zostaje koordynatorem, certyfikat TLS na żądanie w Caddy za zgodą `/internal/tls-allowed`, nieznana subdomena = 404, usługa `apps/tenancy/provisioning.py`); uwagi organizatora z 18.09: karty partnerów i pas logotypów w równym rozmiarze, ZOZ ukryty (`LegacyPage.hidden`, seed nie publikuje strony wycofanej w /cms/), serwis tylko po polsku – przełącznik języka za opcją witryny `english_interface_enabled` (domyślnie wyłączona, polski niezależnie od przeglądarki, zapisane wybory zostają w bazie) |
| **v0.24.0** | 2026-09-18 | etap 2 (wydania E–K scalone): marka i dokumenty jako konfiguracja (`apps/tenancy/branding.py`, nadawca i podpisy listów z konkursu, `ConsentDefinition`, `DocumentTemplate` + `Certificate.template_version`, kalendarz/CAPTCHA/domena anonimowa, adres na stronie 500 z `ERROR_PAGE_CONTACT_EMAIL`), `/cms/` per konkurs (`cms:<slug>`, `scope_cms_access`), regiony jako drzewo per konkurs z 16 województwami i konfliktem interesów bez zmiany, typy placówek, profil rejestracji, słownik własny organizatora z importem CSV, wyszukiwarka dwóch słowników, import hurtowy z regionem/kategorią/placówką, edytor przebiegu (`PipelineStep`, `TransitionRule`, kategorie, komponenty etapu, punkty z rozmowy, drużyny, wagi i przesunięcie skali, remisy, role recenzenckie), kreator `/setup/` z tokenem, obrazy z CI do GHCR i `WEB_IMAGE`, profil compose dla operatora, Wagtail i18n z aliasami witryn, język listów per konkurs, wpisowe z rejestrem należności i webhookiem płatności, logistyka etapów stacjonarnych; 15 nowych flag konkursu domyślnie wyłączonych, Olimpiada Kwantowa bajt w bajt; E2E z drugim konkursem; 4508+ testów |
| **v0.23.0** | 2026-09-17 | etap 1, wydanie D (domknięcie): `NOT NULL` na kluczach `competition`, uczestnik per konkurs (`Participant.user` jako klucz obcy, `participant_for`), kod publiczny i numer dyplomu z prefiksami konkursu (`OLM-`/`OK` bez zmian dla Konkursu #1), jedna edycja bieżąca i unikalny rocznik per konkurs, kolumny konkursu w zgłoszeniach pomocy, audycie, kluczach API, webhookach, szablonach dyplomów i szablonach komentarzy, `create_competition` zakłada edycję, etapy i koordynatora, `check_memberships`, runbook drugiego konkursu; 3235 testów, 0 xfail |
| **v0.22.0** | 2026-09-17 | etap 1, wydanie C: odczyty w panelach zakresowane do konkursu (`for_competition`, `current_edition(competition)`), CMS per witryna Wagtaila, komunikaty z kolumną konkursu, strona „Ustawienia konkursu” za flagą `competition_settings_page`, 14 z 15 testów izolacji zielonych |
| **v0.21.0** | 2026-09-17 | etap 1, wydanie B: `accounts.Membership` i role per konkurs (za flagą `memberships_enforced`), nullowalne klucze obce `competition` z backfillem do Konkursu #1, `Caddyfile` generowany z `EXTRA_DOMAINS`, `pg_dump` przed migracjami w `deploy.sh`, testy izolacji i niezmienniczości |
| **v0.20.0** | 2026-09-17 | etap 1, wydanie A: model `tenancy.Competition` 1:1 z witryną Wagtaila, `CompetitionMiddleware` i `current_competition()`, Konkurs #1 utworzony z istniejącej witryny, `create_competition` |
| **v0.19.0** | 2026-09-17 | integracje (API, webhooki), testy online (`apps/quiz`), dyplomy 2.0 z pieczęcią PAdES, kopie zapasowe i monitoring, CI, 2FA za wyłączonym przełącznikiem, import grupowy uczniów, okręgi szkolne, dokumentacja i licencja |
| **v0.18.0** | 2026-09-17 | scalony zduplikowany `msgid` („wersja %(version)s”), który wywracał `msgfmt` przy budowaniu obrazu |
| **v0.17.1** | 2026-09-17 | wersja aplikacji w stopce i na `/status/` pochodzi z `APP_VERSION` (tag wdrożenia), a nie ze sztywnego „1.0” |
| **v0.17.0** | 2026-09-16 | przebudowa układu paneli, narzędzia RODO, zgłoszenia i pomoc (support desk), FAQ, ogłoszenia i strona statusu |
| **v0.16.0** | 2026-09-16 | drugi zestaw 15 funkcji paneli (koordynator, recenzent, uczestnik) |
| **v0.15.0** | 2026-09-16 | pobieranie prac i paczki ZIP, edytowalne skale punktacji, rozwiązania w JPEG oraz 15 funkcji paneli |
| **v0.14.0** | 2026-09-16 | ocenianie przed zamknięciem etapu („Zablokuj oddane prace do oceny”) |
| **v0.13.1** | 2026-09-16 | pasek linii czasu spoczywa jako cienka linia i rozwija się w dół po najechaniu; każdy warsztat jest osobnym wydarzeniem |
| **v0.13.0** | 2026-09-16 | pasek linii czasu w nagłówku i wydarzenia zarządzane przez koordynatora |
| **v0.12.0** | 2026-09-15 | recenzent poprawia własną recenzję, koordynator odbiera recenzje i zarządza wszystkimi kontami |
| **v0.11.1** | 2026-09-15 | województwo członka komitetu jest opcjonalne i nie warunkuje już przydziału |
| **v0.11.0** | 2026-09-15 | zaproszenia do komitetu e-mailem, z indywidualnym kodem dla każdego adresu |
| **v0.10.0** | 2026-09-15 | ręczny przydział recenzentów i korekty ocen przez koordynatora |
| **v0.9.4** | 2026-09-15 | polskie strony błędów: widok odmowy CSRF wyjaśniający przypadek nieaktualnego formularza (logowanie w innej karcie) z odnośnikiem ponowienia, plus 403/404/500 |
| **v0.9.3** | 2026-09-15 | tag Google w `<head>` na każdej stronie (z nonce, bez wyjątku w CSP) z Consent Mode v2: `analytics_storage` odmówione do czasu zgody |
| **v0.9.2** | 2026-09-15 | etap treningowy niesie wyłącznie przykładowe zadania organizatora (jeden wspólny PDF); generator PDF-ów wycofany |
| **v0.9.1** | 2026-09-15 | retencja danych zdarzeń Google Analytics ustalona na 14 miesięcy (decyzja organizatora) |
| **v0.9.0** | 2026-09-15 | Google Analytics 4 wyłącznie za wyraźną zgodą (pasek zgody, wycofanie ze stopki, anonimizacja IP, funkcje reklamowe wyłączone); polityka cookies 1.1 |
| **v0.8.6** | 2026-09-15 | plik weryfikacyjny Google Search Console serwowany spod własnego adresu |
| **v0.8.5** | 2026-09-15 | przykładowe zadania organizatora jako zadania treningowe 1–4, strona „sprawdź skrzynkę” po rejestracji, uporządkowana strona główna, TikTok i YouTube w odnośnikach społecznościowych |
| **v0.8.4** | 2026-09-13 | wyszukiwarka szkół przepisana na czysty JavaScript (bez zależności z CDN), łagodniejsza reguła szkoły spoza wykazu, oznaczenia pól wymaganych, odnośniki społecznościowe w ustawieniach serwisu |
| **v0.8.3** | 2026-09-12 | porządki w skrypcie testu e2e (długa asercja rozbita na dwie) |
| **v0.8.2** | 2026-09-12 | pozycje menu głównego (Zadania, Harmonogram, Warsztaty) przenoszą się do przyklejonego paska konta |
| **v0.8.1** | 2026-09-12 | logo w pasku konta, pasek przyklejony do góry okna (statyczny na telefonach), nawigacja serwisowa poniżej |
| **v0.8.0** | 2026-09-12 | aktywacja konta e-mailem (link 4 h, automatyczne czyszczenie nieaktywowanych kont, ręczna aktywacja przez koordynatora), telefon w profilu, edycja danych ze zmianą adresu, samodzielne usunięcie konta z anonimizacją; własna CAPTCHA, pułapka i minimalny czas wypełniania; etap treningowy |
| **v0.7.0** | 2026-09-12 | „Termin” stacjonarny pokazywany wyłącznie dla etapów z jawnymi dniami wydarzenia; seed treści nie odtwarza stron i aktualności skasowanych w `/cms/` |
| **v0.6.0** | 2026-09-10 | wersjonowane, linkowane zgody rejestracyjne (regulamin, RODO, zgoda opiekuna dla niepełnoletnich, publikacja nazwiska) z dowodem `ConsentRecord` i audytem; wzór zgody opiekuna do wydruku; `GET /api/auth/consents/` |
| **v0.5.2** | 2026-09-10 | koordynator steruje rejestracją uczestników (włącznik, godzina otwarcia i zamknięcia) jedną bramką dla formularza, API i logowania zewnętrznego |
| **v0.5.1** | 2026-09-10 | etapy nazwane Etap I/II/III, a nazewnictwo „okręg” zastąpione „województwem” w całym interfejsie |
| **v0.5.0** | 2026-09-08 | edycja terminów i zakładanie etapów w panelu (z blokadami domenowymi i audytem), zarządzanie zadaniami (treść PDF, formaty, limity), podgląd treści przed otwarciem etapu; harmonogram renderowany z bazy |
| **v0.4.1** | 2026-09-08 | logotypy partnerów, blok harmonogramu warsztatów, miejsce etapu (`Stage.location`) |
| **v0.4.0** | 2026-09-08 | logowanie przez Google i Facebooka (rejestracja przez adapter, ze zgodami RODO) oraz własna usługa poczty wychodzącej (Postfix + OpenDKIM, relay tylko wewnętrzny) z rekordami DNS wypisywanymi przez skrypt wdrożeniowy |
| **v0.3.3** | 2026-09-08 | reset hasła e-mailem dla wszystkich ról (limit prób, brak enumeracji kont, token jednorazowy 24 h, audyt), konfiguracja `EMAIL_URL`, mailpit w devie |
| **v0.3.2** | 2026-09-07 | oficjalny logotyp w nagłówku, favicon, ikona dotykowa i `og:image` składane z pliku organizatora |
| **v0.3.1** | 2026-09-07 | regulamin v1.0 z 2 września 2026 (model trzech etapów, PDF + DOCX), strona „Partnerzy” z poziomami partnerstwa |
| **v0.3.0** | 2026-09-07 | sekcja „Dokumenty”: strona indeksu, rozwijane menu bez JavaScriptu, wszystkie dokumenty organizatora pod `/dokumenty/`, trwałe przekierowania ze starych adresów |
| **v0.2.1** | 2026-09-07 | polityka RODO i standardy ochrony małoletnich przepisane 1:1 z PDF-ów organizatora jako HTML, uzupełniona strona komitetów |
| **v0.2.0** | 2026-09-06 | import treści starego serwisu: ustawienia marki, typ strony treści, strony informacyjne, polityki jako dokumenty, aktualności, sekcja kroków na stronie głównej, `seed_edition_kwantowa` |
| **v0.1.1** | 2026-09-06 | system projektowy: tokeny kolorów z wariantem ciemnym, samodzielnie serwowane kroje pisma, komponenty (karty, odznaki, tabele, linia czasu, odliczanie, segmenty punktów, strefa upuszczania), przebudowa szablonów wszystkich paneli |
| **v0.1.0** | 2026-09-05 | zamknięcie pierwszej fazy: API administracyjne Caddy'ego wyłącznie lokalnie, bezpiecznik produkcyjny dla klucza i poświadczeń S3, bezpieczne domyślne ciasteczka |

## Tagi zadań

Poza wydaniami repozytorium niesie tagi `task/T-01` … `task/T-10` (z wariantami `-fix`) — punkty
kontrolne kolejnych zadań z `docs/tasks/`. Nie są wydaniami i nie należy ich podstawiać jako
`APP_VERSION`.
