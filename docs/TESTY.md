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
i `-n auto`), a wymaganym statusem jest `pytest (wynik zbiorczy)`. Podział na shardy:
`pytest-split --splitting-algorithm duration_based_chunks` według `backend/.test_durations`
(ciągłe kawałki zbioru – moduł z drogą fiksturą nie rozjeżdża się na kilka shardów).

**Odświeżenie `.test_durations`** – gdy shardy wyraźnie się rozjadą (najdłuższy ≥ 1,5 × najkrótszy):

```bash
docker compose exec -T web python -m pytest -q --create-db -p _durations_plugin
```

Wtyczka (`backend/_durations_plugin.py`) działa też z `-n auto` i nadpisuje plik; commituje się go
jak każdy inny. Nowy test, którego w pliku nie ma, dostaje czas średni.

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
