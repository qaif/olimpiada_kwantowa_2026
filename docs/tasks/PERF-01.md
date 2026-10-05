# PERF-01: Test obciążenia i plan pojemności przed etapem międzynarodowym IQO

## 0. Cel i granice (polecenie organizatora, 4.10.2026)

Przed etapem międzynarodowym IQO (iqo-official.org) trzeba wiedzieć, ilu uczniów naraz wytrzyma
obecna instalacja (jeden VPS Contabo, 6 vCPU / 11 GB, z ukradzionym przez hiperwizor czasem CPU
12–37 % – pomiar z 22.09.2026), gdzie są wąskie gardła i co zmienić przed dniem zawodów.

Zadanie robi trzy rzeczy:

1. **narzędzie** – generator ruchu scenariusza dnia zawodów i komenda danych testowych,
2. **pomiar lokalny i tanie poprawki** wąskich gardeł znalezionych pomiarem (z testami),
3. **plan pojemności** – ekstrapolacja na serwer produkcyjny, rekomendacja i bezpieczna procedura
   ewentualnego testu na serwerze (wyłącznie za osobną zgodą właściciela serwisu).

Czego zadanie **nie** robi:

- **nie wysyła żadnego ruchu na produkcję ani na żaden zewnętrzny host.** Generator ma
  bezpiecznik hosta (lokalne nazwy; produkcja odrzucana zawsze, także z flagą), stos testu ma sieć
  bez wyjścia na świat,
- nie dociąga nowych narzędzi (k6, locust): generator to Python ze standardową biblioteką i `httpx`,
  który już jest w obrazie aplikacji (zależność przechodnia `anthropic`/`openai`),
- nie przenosi usług na inne hosty i nie zmienia dostawcy – to są rekomendacje (§ 6) do decyzji
  organizatora.

## 1. Narzędzie

| Plik | Rola |
|---|---|
| `scripts/loadtest/docker-compose.loadtest.yml` | osobny projekt compose `olimpiada-loadtest`: `db` (baza `olimpiada_loadtest`, `pg_stat_statements`), `redis`, `minio`, `clamav` (bez freshclam), `web` (gunicorn jak w produkcji, `DEBUG=0`, `config.settings.production`), `worker`, `proxy` (Caddy), `loadgen`; limity CPU (`LOADTEST_*_CPUS`) |
| `scripts/loadtest/Caddyfile` | kształt bloku domeny z `deploy/Caddyfile` (kompresja, limit ciała, `/static/` z dysku), HTTP zamiast TLS, adres klienta z nagłówka generatora |
| `scripts/loadtest/run.sh` | `up` / `seed` / `run <nazwa> [arg. generatora]` / `down`; próbki `docker stats` co 5 s, `pg_stat_statements`, liczba połączeń z bazą, błędy z logu `web`; `LOADTEST_SHARDS` procesów generatora |
| `scripts/loadtest/loadgen.py` | generator scenariusza (§ 2) i raport (`summary.md`/`.csv`, `timeline.csv`, `requests.csv`) |
| `scripts/loadtest/profile_endpoints.py` | koszt CPU i liczba zapytań gorących adresów w procesie (bez sieci) |
| `backend/apps/competitions/management/commands/loadtest_seed.py` | dane testu: edycja, etap pisemny otwarty **teraz** z PDF-ami treści, test online (20 pytań), wcześniejszy etap z ogłoszoną tabelą (N wierszy), aktualności, N uczniów z wpisami, rozmowa każdego z organizatorem, koordynatorzy; manifest JSON dla generatora |

**Bezpieczniki `loadtest_seed`** (oba naraz): `DEBUG` albo jawne `--i-know-this-is-not-prod`
**oraz** `loadtest` w nazwie bazy (`DATABASES["default"]["NAME"]`). Druga bariera jest właściwa:
produkcja (`olimpiada`) i baza deweloperska jej nie spełnią żadną flagą. Konta uczniów powstają
hurtem (`bulk_create`) z jednym skrótem hasła – 3000 kont w kilka sekund zamiast kwadransa PBKDF2.

**Bezpiecznik generatora**: hosty lokalne (`proxy`, `web`, `localhost`, `127.0.0.1`, `*.localhost`,
`*.test`, `*.local`); inny host wyłącznie z `--allow-remote-host <ta sama nazwa>` i limitem uczniów
(`--remote-max-students`, domyślnie 200); domeny i adres produkcji (`olimpiadakwantowa.pl`,
`iqo-official.org`, `169.58.242.197`) – odmowa zawsze.

## 2. Scenariusz dnia zawodów

| Faza | Co robi wirtualny uczeń |
|---|---|
| `login` (okno przed T0) | strona główna (cache stron), `GET/POST /login/` (PBKDF2), `/me/` |
| `burst` (T0 + rozłożenie) | `/me/` i **wszystkie** PDF-y treści (3 × 600 KB) naraz |
| `steady` (do końca) | 40 %: test online – start, arkusz, autozapis co 20 s (odpowiedzi narastają); 60 %: etap pisemny – przeładowanie panelu co 2–5 min, wysyłka skanów 1–5 MB (zagęszczona pod koniec); 50 %: otwarty czat (odpytanie co 15 s, 204 bez zmian) |

Obok: koordynatorzy (pulpit, postęp etapu, wyniki testu, wyszukiwarka, eksport CSV uczestników co
kilka minut) i goście stron publicznych (proces Poissona: strona główna, aktualności, `/wyniki/`,
`/results/<id>/` z tabelą 3000 wierszy, harmonogram, link z newslettera z `utm_*`, paginacja).
Każdy wirtualny uczeń ma **własny adres IP** (`X-Loadtest-Client-IP` → `X-Real-IP` w Caddym);
`--students-per-ip 30` symuluje salę za jednym NAT-em.

Profile (`--profile`): `smoke`, `stage-open-300`, `stage-open-1000`, `stage-open-3000`,
`steady-1000`; każdy parametr da się nadpisać. Kryteria przerwania (`--abort-error-rate`,
`--abort-p95-ms`) w oknie 30 s.

## 3. Środowisko pomiaru i jego granice

Komputer deweloperski (Intel i9-14900K), Docker Desktop/WSL2. Stos testu: `web` 6 workerów × 4 wątki
(jak produkcja, `.env` z 22.09) z limitem **4 CPU**, `db` 2 CPU, `proxy` 1, `worker` 1. Generator:
6 procesów (`LOADTEST_SHARDS=6`) – jeden proces Pythona przy ok. 90 żądaniach/s sam stawał się
wąskim gardłem (100 % jednego rdzenia, serwer bezczynny) i mierzył własną kolejkę; od 6 procesów
żaden nie przekracza 15 % rdzenia.

Granice: HTTP bez TLS (koszt uzgodnień TLS na produkcji poza pomiarem); klient i serwer na jednej
maszynie (opóźnienia sieci ~0); rdzeń i9 jest wyraźnie szybszy od vCPU EPYC Contabo – stąd współczynnik
w ekstrapolacji (§ 5). Pomiar CPU na żądanie na tej maszynie ma rozrzut ±30 % między sesjami
(turbo/termika) – porównania A/B robione są **przeplatane w jednej sesji** (`LOADTEST_BACKEND_DIR`),
a decyzje opierają się na liczbie zapytań (deterministyczna) i przebiegach end-to-end.

## 4. Ustalenia i poprawki

| # | Ustalenie | Dowód | Poprawka (ta gałąź) |
|---|---|---|---|
| U1 | `/me/` czytał **ten sam** wiersz `accounts_participant` 9× na wejście, odpytanie czatu 2× (`ParticipantRequiredMixin.participant` to właściwość – każde sięgnięcie to zapytanie; do tego procesor ról, czat, okna czasowe) | `pg_stat_statements`: najdroższe zapytanie przebiegu (18,5 tys. wywołań na 8,9 tys. żądań) | pamięć profilu na czas żądania (`apps.accounts.request_memo`, zasięg otwiera `PreferencesMiddleware`, czyszczona sygnałami zapisu/skasowania profilu, `None` niezapamiętywane); `/me/` 38 → 30 zapytań |
| U2 | autozapis testu (co 20 s) zapisywał każdą odpowiedź osobnym `update_or_create`: 95 instrukcji przy 20 odpowiedziach | profil: 95 zapytań, ~30 ms CPU | hurtem i tylko zmienione: 15 zapytań, ok. −55 % CPU; werdykt zerowany jak dotąd |
| U3 | link z `utm_*`/`fbclid` omijał cache stron – pełny render (~22 ms CPU zamiast ~3) | profil, `X-Page-Cache: BYPASS` | parametry kampanii pomijane w kluczu; nieznany parametr dalej omija cache |
| U4 | `/results/<id>/` (tabela 3000 wierszy, 1,1 MB) poza allow-listą, `/wyniki/` (1,3 MB) ponad limitem 512 KiB – obie po ~160 ms CPU, nigdy z cache'u | profil | `/results/` na allow-liście, limit 4 MiB, wpis > 128 KiB skompresowany `zlib` (10–20× mniej w Redisie), unieważnienie też przy wycofaniu publikacji; format wpisu w kluczu (stary kod nie odczyta wpisu skompresowanego – wykryte pomiarem A/B na wspólnym Redisie) |
| U5 | limit wysyłek `upload` (30/h) liczony **per IP**: sala za jednym NAT-em (delegacja, pracownia) wyczerpywała go po 10 uczniach × 3 zadania | `apps/web/throttle.py`; API liczy per konto od zawsze | `upload` w `PER_USER_SCOPES` |
| U6 | rotacja workera (`--max-requests 2000`) pod obciążeniem **zrywa żądania w toku**: wychodzący worker `gthread` kończy pętlę, a wątki z żądaniami (S3 przez `s3transfer` → `ThreadPoolExecutor`) dostają `RuntimeError: cannot schedule new futures after interpreter shutdown` → 500/502 na PDF-ach i wysyłkach | 267 takich wyjątków w 10 min przebiegu 3000 uczniów; 502 skorelowane co do sekundy z „Autorestarting worker” | `WEB_MAX_REQUESTS`/`WEB_MAX_REQUESTS_JITTER` w `docker-compose.yml` (domyślnie bez zmian); zalecenie na dzień zawodów § 6 |
| U7 | 6 workerów = 1,4–1,6 GB RSS przy `mem_limit: 2g` (rotacja trzyma chwilę stary i nowy proces) | `docker stats` | `WEB_MEM_LIMIT` (domyślnie 2g), zalecenie 3g |
| U8 | treść zadania strumieniowana kawałkami 4 KiB (150 zapisów na 600 KB) | profil | 64 KiB |
| U11 | odpytanie otwartego wątku czatu co 15 s to przy 3000 uczniach ~100 żądań/s – ponad połowa ruchu stanu ustalonego | przebiegi 3000 | `CHAT_POLL_SECONDS` (domyślnie 15; na dzień zawodów 45 – p50 stanu ustalonego przy 3000: 16 → 8 s) |
| U9 | logowanie to PBKDF2 1,5 mln iteracji: ~0,15 s CPU lokalnie, na vCPU Contabo ~0,3–0,5 s | pomiar `make_password` | bez zmiany kodu (bezpieczeństwo) – organizacyjnie: logowanie 15–30 min przed T0 (§ 6) |
| U10 | limit nieudanych logowań `login` 10/min **per IP** – sala za NAT-em dzieli budżet pomyłek | `apps/web/throttle.py` | bez zmiany (bezpieczeństwo) – procedura § 6: adresy sal z góry, w razie potrzeby podniesienie stawki na czas etapu |

**Po przeglądzie (PR #78):** M1 – unieważnienie cache'u z sygnałów także w `on_commit` (gość
między podbiciem wersji a `COMMIT` zapisywał starą tabelę pod nowym kluczem); M2 – parametry
śledzące zdejmowane z `QUERY_STRING`/`request.GET` przed widokiem (inaczej pierwszy gość z `utm_*`
wpisywał swoją wartość w pole `next` strony serwowanej wszystkim); L1 – cache czyszczony przy zapisie
`MedalScheme` (ogłoszenie/cofnięcie medali) i konkursu (przełączniki); L2 – pamięć profilu oddaje
kopie; L3 – generator rozwiązuje nazwę hosta i odrzuca adres produkcji w każdym zapisie
(`scripts/loadtest/test_loadgen_guard.py`); L4 – rozpakowanie wpisu z limitem (wpis ponad limit
albo uszkodzony = chybienie).

Odrzucone (zmierzone, nieopłacalne albo za drogie na tę gałąź): sesje w Redisie (`cached_db`,
−1 zapytanie na żądanie – zmiana bezpieczeństwa sesji), `--keep-alive 0` w gunicornie (przy
częstych rotacjach dawał **więcej** 502, nie mniej), cache PDF-ów treści w pamięci procesu albo
przekierowanie na podpisany URL MinIO (−40 % CPU tego adresu, ale PROJEKT.md § 1.4 świadomie serwuje
treść przez aplikację; rekomendacja w § 6).

## 5. Wyniki

Uzupełnione w `docs/OPERACJE.md` § 42.3–42.4 (liczby, tabela A/B, ekstrapolacja) – jedno źródło
liczb, żeby nie rozjechały się dwie kopie.

## 6. Rekomendacja

W `docs/OPERACJE.md` § 42.5 (konfiguracja na dzień zawodów) i OPERACJE § 42.7 (serwer: zostać / większy VPS
/ dedykowane vCPU / wydzielenie LiveKit i notatników).

## 7. Testy

- `apps/accounts/tests/test_request_memo.py` – brak pamięci poza żądaniem, trafienie w żądaniu,
  klucz per konkurs, `None` niezapamiętywane, czyszczenie przy zapisie i skasowaniu, zamknięcie
  zasięgu po odpowiedzi, `/me/` czyta profil **raz**,
- `apps/quiz/tests/test_autosave_batch.py` – stała liczba zapytań (2 vs 12 odpowiedzi), brak zapisu
  niezmienionych, zmiana jednej odpowiedzi, zerowanie werdyktu, zdublowany klucz pytania,
- `apps/web/tests/test_perf01_hot_paths.py` – budżety: odpytanie czatu (≤ 12), odstęp odpytania
  z `CHAT_POLL_SECONDS` (z dolną granicą 5 s), PDF (≤ 8, kawałki 64 KiB), autozapis przez widok (2 = 10 odpowiedzi); `utm_*`, `/results/` w cache'u i po wycofaniu,
  kompresja bajt w bajt, format wpisu w kluczu, limit wysyłek per konto,
- `apps/competitions/tests/test_loadtest_seed.py` – oba bezpieczniki i kształt danych (idempotencja),
- `apps/web/tests/test_perf01_review.py` – M1 (`django_db(transaction=True)`), M2, L1, L4;
  `scripts/loadtest/test_loadgen_guard.py` (`unittest` w obrazie aplikacji) – L3.

Uruchomione celowane (zmienione aplikacje i ich ekrany): `apps/quiz`, `apps/proctoring`,
`apps/password_change`, `apps/time_windows`, `apps/alumni`, testy WWW uczestnika/profilu/konta/
czatu/testu/cache'u/limitów/delegacji/zaświadczeń, `apps/tenancy/tests/test_invariants.py`
(budżety zapytań złotej fikstury – progi są sufitami, spadek je spełnia). Pełny zestaw – CI.
