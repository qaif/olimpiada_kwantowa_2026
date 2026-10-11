# Testy: jak uruchamiać i jak dopisywać

Krótki przewodnik po suicie `pytest` w `backend/` (ok. 6150 testów, pytest-django, Postgres).
Liczby czasów w tym dokumencie zmierzono 25.09.2026 na stacji deweloperskiej (32 rdzenie, Docker
Desktop) i w CI – przy innej maszynie będą inne, ale proporcje zostają.

## 1. Przebiegi

Wszystkie polecenia uruchamia się w kontenerze `web` (stos dev: `docker compose -f docker-compose.yml
-f docker-compose.dev.yml up -d`). Obraz musi mieć ekstrę `dev` z `backend/pyproject.toml`
(m.in. `pytest-xdist`) – po zmianie zależności: `docker compose build web`.

| Przebieg | Polecenie | Czas (25.09.2026) |
|---|---|---|
| szybka pętla (bez testów migracji i transakcyjnych) | `pytest -q -n auto -m "not slow"` | 2,5–3,5 min |
| pełny, równolegle | `pytest -q -n auto` | 4,5–5 min |
| pełny, jeden proces | `pytest -q` | ok. 20 min (przed 25.09.2026: 40 min) |
| jedna aplikacja / plik / test | `pytest -q apps/grading` · `pytest -q apps/grading/tests/test_x.py::test_y` | sekundy |
| ponownie tylko czerwone z poprzedniego przebiegu | `pytest -q --lf` (najpierw one, potem reszta: `--ff`) | zależnie od liczby |

Więcej workerów niż ok. 16 nie przyspiesza: każdy zakłada własną bazę (ok. 50–70 s przy wielu
naraz), a przy kilkunastu wąskim gardłem jest już jeden Postgres.

`-n auto` daje tyle workerów, ile rdzeni; każdy ma własną bazę (`test_<nazwa>_gw0`, …). `conftest.py`
zamienia `-n` na `--dist loadgroup`, więc nie trzeba go podawać.

**Własna baza.** Dwa przebiegi naraz na tej samej bazie testowej się wywracają. Osobna nazwa:
`docker compose exec -T -e DATABASE_URL=postgres://USER:HASŁO@db:5432/olimpiada_moje web pytest -q`.

**`--reuse-db`** zostawia bazę testową między sesjami i oszczędza jej zakładanie (ok. 30 s na worker).
Po zmianie migracji albo przerwanym przebiegu (Ctrl-C w środku testu transakcyjnego) użyj
`--create-db`. Baza po testach transakcyjnych wraca do stanu po migracjach sama (niżej, § 3).

## 2. Markery

| Marker | Znaczenie | Nadawany |
|---|---|---|
| `slow` | test drogi z natury; szybka pętla go pomija, CI uruchamia | automatycznie każdemu testowi transakcyjnemu i migracji (`conftest.py`); ręcznie wolno |
| `migrations` | test przewija migracje w transakcji fikstury modułu | ręcznie (`pytestmark = MIGRATION_TESTS` albo dekorator) |
| `clamav` | test rozmawia z prawdziwym clamd; bez clamd się pomija | ręcznie |

`--strict-markers` jest włączone: nowy marker wymaga wpisu w `backend/pyproject.toml`.

## 3. Co suita robi sama (i czego nie trzeba już pamiętać)

- **Katalogi tłumaczeń.** `.mo` kompiluje się na starcie sesji, gdy brakuje go albo jest starszy od
  `.po` (potrzebny `msgfmt`, jest w obrazie i w CI). Bez `msgfmt` sesja startuje z ostrzeżeniem.
- **Pliki statyczne.** Żaden test nie wymaga `collectstatic` – testy, które pobierają `/static/…`,
  włączają findery WhiteNoise same.
- **ClamAV.** Każdy test rozmawia z podstawionym clamd na poziomie gniazda (`_fake_clamd`
  w `conftest.py`: odpowiada `OK`, na EICAR – `FOUND`). Prawdziwy skaner tylko z markerem `clamav`.
- **SDK dostawców AI.** Testy `apps/ai_grading` nie wymagają pakietów `anthropic`/`openai`/
  `google-genai` (dostawca „widzi” SDK przez podmieniony `find_spec`); testy, które składają obiekty
  SDK, pomijają się bez pakietu. W CI pakiety są zawsze.
- **Baza po teście transakcyjnym.** `flush` czyści wszystko, także wiersze z migracji (Konkurs #1,
  drzewo stron). Suita odtwarza je z migawki zrobionej na starcie sesji – kolejny test w tym samym
  procesie zastaje świat taki, jak po migracjach, niezależnie od kolejności.
- **Pamięci podręczne procesu** (GA4, przełącznik opiekunów) są czyszczone przed każdym testem –
  pod xdist kolejność testów zmienia się z każdym przebiegiem.

## 4. Testy migracji

Wzorzec (`apps/core/tests/migration_helpers.py`): fikstura **modułu** otwiera transakcję,
przewija bazę raz i trzyma ją przez wszystkie testy modułu; każdy test biegnie w punkcie zapisu.
Na końcu modułu transakcja jest wycofywana – Postgres ma transakcyjny DDL, więc baza wraca do czoła
migracji bez `migrate` i bez `flush`. Wcześniej każdy test przewijał, wracał do czoła i czyścił
bazę sam (ok. 45 s na test).

```python
from apps.core.tests.migration_helpers import MIGRATION_TESTS, migrate_to, rewound_database

pytestmark = MIGRATION_TESTS
BEFORE = ("app", "0007_przed")
AFTER = ("app", "0008_migracja_danych")


@pytest.fixture(scope="module")
def rewound(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db  # db.apps – modele historyczne, db.competition – Konkurs #1 sprzed przewinięcia


def test_backfill(rewound):
    ...  # wiersze „sprzed wdrożenia”
    migrate_to(AFTER)
    ...  # asercje
```

- Moduł, w którym obok są zwykłe testy: fikstura o zasięgu **testu** (`@pytest.fixture` bez
  `scope`) i `@pytest.mark.migrations` przy testach migracji. Testy migracji idą w module na koniec
  (`conftest.py` je przestawia), a zwykły test na przewiniętej bazie kończy się błędem z nazwą modułu.
- Wiersze na przewiniętym schemacie zakładaj modelem historycznym (`db.apps.get_model`) albo
  `applied_state_model` – żywy model zna kolumny, których w tej chwili w tabeli nie ma.
- Przewinięty moduł trzyma ok. 3000 blokad Postgresa. Produkcja, dev i CI chodzą z
  `max_locks_per_transaction = 256`; na Postgresie z domyślnym 64 przy wielu workerach może paść
  „out of shared memory” – wtedy mniej workerów (`-n 4`) albo wyższy limit.

## 5. CI

`.github/workflows/ci.yml`: `ruff`, `makemigrations --check` i `msgfmt --check` idą jako osobne,
równoległe zadania; `pytest` w **pięciu shardach** (`pytest (shard i/5)`, każdy z własnym Postgresem
i `-n` = liczba rdzeni runnera), a wymaganym statusem jest `pytest (wynik zbiorczy)`. Podział na shardy:
`pytest-split --splitting-algorithm duration_based_chunks` według `backend/.test_durations`
(ciągłe kawałki zbioru – moduł z drogą fiksturą nie rozjeżdża się na kilka shardów).

**Bazy testowe w shardzie** (od 8.10.2026): krok „Bazy testowe” migruje **raz**
(`backend/ci_test_db.py`) i klonuje bazę dla każdego workera (`CREATE DATABASE … TEMPLATE`, nazwy
`test_olimpiada_gw0` …), a `pytest` idzie z `--reuse-db`. Wcześniej każdy worker migrował swoją bazę
od zera, cztery naraz – 1,5–2,5 min przed pierwszym testem.

**Grupy xdist** (od 8.10.2026): `conftest.py` zamienia `-n` na `--dist loadgroup` i – to była
brakująca połowa – przekazuje tę decyzję workerom (`pytest_configure_node`). Bez tego worker nie
doklejał grupy do identyfikatora testu, harmonogram działał jak `load`, a testy jednego modułu
migracji trafiały do różnych workerów i **każdy test** przewijał bazę od nowa (w CI 1,5–3 min na
test). Lokalne `pytest -n …` korzysta z tej samej poprawki.

**Odświeżenie `.test_durations`** – gdy shardy wyraźnie się rozjadą (najdłuższy ≥ 1,5 × najkrótszy).
Każdy shard CI mierzy czasy swoich testów (`-p _durations_plugin`, bez kosztu baz workerów) i wystawia
je jako artefakt `test-durations-<n>` (14 dni). Z przebiegu, w którym przeszły wszystkie shardy:

```bash
python scripts/refresh_test_durations.py <run-id>     # gh run list --workflow ci.yml
git add backend/.test_durations                       # i commit jak każdy inny plik
```

Pomiar z CI, a nie ze stacji deweloperskiej, bo proporcje są inne (testy CMS-u z seedami są na
runnerze ok. 4× droższe względem reszty niż na 32 rdzeniach). Lokalny pomiar nadal działa
(`python -m pytest -q --create-db -p _durations_plugin`, także z `-n`), ale nadaje się do podziału
w CI tylko orientacyjnie. Nowy test, którego w pliku nie ma, dostaje czas średni.

Plik z 8.10.2026 to **kalibracja przejściowa**: czasy z 25.09.2026 przeliczone współczynnikami
dopasowanymi do czasów shardów dwóch przebiegów CI (CMS × 3,7, moduły migracji × 3,2, reszta × 0,8;
testy dopisane po 25.09 – średnia swojego modułu). Do zastąpienia pierwszym pomiarem z CI.

**Pliki spoza `backend/` w testach** (notatniki kwantowe, QC-01): zgodność polityki CSP
z `deploy/Caddyfile` i konfiguracja `docker-compose.yml` są czytane ze ścieżek względem korzenia
repozytorium (CI ma pełny checkout). Poza CI test bez tych plików jest pomijany (obraz z samym
backendem), w CI (`CI=true`) – **pada**. Testy zgodności `qclab` z prawdziwym Qiskitem
(`apps/notebooks/tests/test_qclab.py::*_with_real_qiskit`) w CI są pominięte z powodem
`QISKIT-PARITY: … NIESPRAWDZONA` (widać go w `pytest -rs`): Qiskit nie jest zależnością projektu.
Przy zmianach w `backend/qclab` uruchom je w kontenerze z doinstalowanym `qiskit`.

## 6. Dopisywanie bez ruszania cudzych testów

Asercje mają sprawdzać **regułę per element**, a nie liczbę elementów, którą każda nowa funkcja
musiałaby poprawiać. Konkretnie:

- **Nowy temat listu** – brzmienie do `EXPECTED_*` i stała do `SUBJECT_CONSTANTS`
  w `apps/tenancy/tests/test_invariants.py`. Test sam znajdzie w kodzie każdą stałą z `SUBJECT`
  w nazwie bez wiersza i każdy temat `gettext` bez tłumaczenia w `locale/en/…/django.po`.
- **Nowa flaga konkursu z pozycją menu** – jeden wiersz w `FLAG_ITEMS`
  (`apps/web/tests/test_coordinator_nav_flags.py`). Lista flag etapu 2 jest zamrożona i się nie zmienia.
- **Nowa wersja rejestru czynności** – zmień `REGISTER_VERSION`; testy czynności warunkowych
  sprawdzają „wersja co najmniej ta, w której wszedł mój wiersz”, a nie konkretną wartość.
- **Nowy scope throttlingu** – tylko w `config/settings/base.py`; ustawienia testowe biorą zestaw stamtąd.
- **Budżet zapytań** – sufity ekranów stoją w jednej tabeli: `apps/core/tests/query_budgets.py`
  (`budget("…")`). Przekroczenie pokazuje w komunikacie wszystkie zapytania, a na górze powtórzone
  (tak wygląda zapytanie na wiersz). Podnoś wiersz tabeli z komentarzem „co i od kiedy”, po sprawdzeniu,
  że przyrost nie rośnie z danymi.
- **Pamięć podręczna procesu** (słownik modułu z TTL) – dopisz jej czyszczenie do
  `_clear_process_caches` w `conftest.py`, inaczej wynik testów zależy od kolejności pod xdist.

## 7. E2E przełączenia Wagtail ⇄ django CMS (DJ-02j)

`scripts/tests/djcms_primary_e2e.sh` – obowiązkowy przed DoD DJ-02 (docs/tasks/DJ-02.md § 11).
Prawdziwy stos (web i djcms na gunicornie z `DEBUG=0`, Caddy z plikiem z `scripts/render_caddyfile.sh`
przy `DJCMS_ENABLED=1`, `PLATFORM_SUBDOMAINS=1`, `SITE_DOMAIN=olimpiada.test`) w **osobnym projekcie
compose** `olimpiada-e2e-djcms`: środowisko dev (`olimpiadaclade`) zostaje nietknięte (własne
wolumeny, podsieci 172.31.x zamiast 172.30.x, obrazy `…:e2e-djcms`, na hoście wyłącznie
`127.0.0.1:443`). Katalog roboczy `runs/djcms-e2e/install/` (poza gitem) udaje katalog instalacji
serwera, więc `scripts/djcms_switch.sh` i `scripts/djcms_cutover.sh` idą w nim **bez zmian**.
Nakładka compose: `docker-compose.e2e-djcms.yml` (tylko dla tego skryptu).

| Polecenie | Co robi | Czas (26.09.2026) |
|---|---|---|
| `scripts/tests/djcms_primary_e2e.sh` | czyste wolumeny, budowanie, świat, scenariusz A–E, pomiar, `down -v` | ok. 10 min |
| `… --keep` / `… --down` | jw., stos zostaje / tylko sprzątnięcie | |
| `… --reuse` | scenariusz na stosie z poprzedniego `--keep` (bez budowania i seedów) – **tylko przed przełączeniem** (po pełnym przebiegu `.env` ma `DJCMS_CUTOVER_DONE`) | |
| `… --up-only` | tylko stos i świat (do ręcznego klikania / debugowania) | ok. 3 min |
| `… --no-bench` | bez pomiaru | −4 min |

Wymagania: Docker, wolny port 443 na 127.0.0.1, Git Bash albo Linux (`openssl`, `gpg` – kopia
w kroku przełączenia). Obraz `web`: `docker compose build web`; gdy budowanie nie przechodzi (antywirus
przechwytujący HTTPS – `backend/Dockerfile` nie ma sekretu `extra_ca`), skrypt składa obraz z zależności
`olimpiada/web:dev` (`DJCMS_E2E_WEB_BASE`) i kodu z drzewa roboczego. `EXTRA_CA_FILE` z `.env` trafia
do budowania djcms i do `pip` kontenera klienta. Klient testów: kontener Playwrighta w sieci `edge`
stosu; `*.olimpiada.test` → adres `proxy` (requests – podmienione `getaddrinfo`, Chromium –
`--host-resolver-rules`), bez pliku hosts. Wyniki: `runs/djcms-e2e/artifacts/` (wyjście każdego kroku,
`bench-*.json`, zrzuty ekranu).

Świat: konkurs domyślny (seedy jak dev) pod `olimpiada.test`, `fizyczna` w subdomenie platformy,
`e2e-druga` pod prefiksem `/druga/` (domena własna `e2e-druga.localhost`, jak `scripts/e2e.sh`).

Scenariusz (każdy wiersz `ok`/`FAIL`, kod ≠ 0 przy którejkolwiek porażce):

- **A. PRIMARY=0** – `djcms_switch.sh status|check`, `e2e/check_djcms_primary.py --phase preview`
  (trzy konkursy × `/`, `/zadania/`, `/wyniki/`: Wagtail bez `X-Djcms-Mode`, `Vary: Cookie`;
  `djcms_view=dj` → djcms `preview` z noindex w nagłówku i meta; przycisk na `/djcms/preview/`
  ustawia/kasuje ciasteczko host-only; `robots.txt` w podglądzie `Disallow: /`, `sitemap.xml` 404;
  adresy aplikacji niezależnie od ciasteczka z web: `/login/`, `/me/`, `/coordinator/`, `/cms/`,
  `/api/…`, `/static/…`, `/documents/…`; `/internal/*` – pusta 404 z proxy; `dj.` → 302;
  logowanie uczestnika i koordynatora w przeglądarce, marka konkursu, wylogowanie, `/cms/` bez banera),
  `djcms_cutover.sh --check` i `--dry-run`.
- **B.** `djcms_cutover.sh --yes` (kopia + `backup_verify`, `cms_freeze on`, import, `verify_cutover`,
  `djcms_switch.sh on`) pod ruchem w tle (`--phase load`: GET `/` i `/login/` trzech konkursów, ≥ 200
  żądań, zero 5xx i zerwań). Gdy `verify_cutover` zatrzyma przełączenie – FAIL i ponowienie z `--skip`.
- **C. PRIMARY=1** – `--phase primary` (djcms `primary`, bez noindex, `canonical` na własny host
  i prefiks, `robots.txt` z `Sitemap:`, `sitemap.xml` z adresami konkursu, `/djcms/static/` immutable,
  `djcms_view=wagtail` → Wagtail, `/regulamin/` → 301, 404 z ramą, panele z web, `/cms/` z banerem
  zamrożenia); nowy konkurs `ekologiczna` po przełączeniu (pierwsze wejście = drzewo startowe djcms)
  i jego wyłączenie (→ 404).
- **D.** pomiar (`scripts/djcms_bench.py`, niżej).
- **E.** `djcms_cutover.sh --rollback --unfreeze` pod ruchem, `--phase wagtail`.

**Pomiar** `scripts/djcms_bench.py` (sama biblioteka standardowa): N żądań GET po liście adresów, stała
współbieżność (wątki z keep-alive), p50/p95/p99, req/s, kody i upstream (`X-Djcms-Mode`). Ręcznie
(z kontenera w sieci stosu E2E):

```bash
docker exec olimpiada-e2e-djcms-runner python /scripts/djcms_bench.py --connect proxy \
    --url https://olimpiada.test/ --url https://olimpiada.test/zadania/ --requests 300 --concurrency 8 \
    [--cookie djcms_view=wagtail]
docker exec olimpiada-e2e-djcms-runner python /scripts/djcms_bench.py --summary artifacts/djcms
```

**Wynik przebiegu 26.09.2026** (stacja deweloperska, 32 rdzenie; 615 s): A preview 96/97,
C primary 108/109, E wagtail 96/97, ruch w tle przy przełączeniu i wycofaniu ~10 tys. żądań –
0 × 5xx, 0 zerwań; `djcms_cutover.sh` pełny przebieg 79 s (z `--skip fizyczna`), wycofanie 18 s;
nowy konkurs widoczny w djcms po 23 s, wyłączony → 404 po 61 s (bufory 60 s w web). Czerwone
wyłącznie z powodu dwóch usterek poza testem: `dj.<domena>` bez certyfikatu przy
`PLATFORM_SUBDOMAINS=1` (nazwy dosłowne podpadają pod politykę on-demand bloku `*.`, a `ask` im
odmawia) i `verify_cutover` S16 dla konkursu z szablonu (`/faq/`, `/harmonogram/`, `/warsztaty/`
w `linked_paths`, choć strony nie ma ani w Wagtailu).

Pomiar (300 żądań na wiersz, `/`, `/zadania/`, `/wyniki/` domeny głównej, przez Caddy'ego, ms):

| współbieżność | djcms p50 / p95 | Wagtail bez bufora p50 / p95 | Wagtail z buforem p50 / p95 | req/s djcms / Wagtail bez bufora |
|---|---|---|---|---|
| 1 | 34,7 / 66,2 | 26,0 / 48,2 | 7,6 / 29,9 | 24 / 32 |
| 8 | 61,7 / 168,0 | 50,4 / 131,4 | 20,8 / 68,7 | 107 / 110 |
| 16 | 112,4 / 224,3 | 105,1 / 191,0 | 35,2 / 106,3 | 122 / 135 |

djcms jest ok. 1,2–1,4× wolniejszy od Wagtaila **bez** bufora (strona główna najdroższa: 59 vs
43 ms przy c1) – poniżej progu 2× z DJ-02 § 10.6, więc bufor pełnostronicowy djcms nie jest warunkiem
przełączenia. Wagtail z buforem stron jest 3–5× szybszy od obu. Liczby z maszyny deweloperskiej –
porównywać stosunki, nie wartości (VPS: 6 vCPU z kradzieżą CPU 12–37 %).
