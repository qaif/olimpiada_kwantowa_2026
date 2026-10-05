# OPS-03: Monitoring z zewnątrz – GitHub Actions co 10 minut

## 0. Cel i granice (polecenie organizatora, 5.10.2026)

Dowiedzieć się, że **cały serwer** przestał działać – z miejsca, które nie umiera razem z nim, za
zero złotych i bez zakładania nowych kont.

Stan przed zadaniem: produkcja to **jeden VPS** (169.58.242.197) z `olimpiadakwantowa.pl`,
`iqo-official.org` i `live.olimpiadakwantowa.pl` (LiveKit). Wszystko, co dziś alarmuje, stoi na tej
samej maszynie:

| Mechanizm | Co widzi | Czego nie zobaczy |
|---|---|---|
| watchdog aplikacyjny (`apps/core/alerts.py`, worker Celery) | dysk, kolejka, 5xx, kopie, połączenia z bazą | śmierci hosta – list wysyła worker z tego hosta |
| Uptime Kuma (`deploy/monitoring/README.md`, profil `monitoring`) | strony, S3, Jitsi, pocztę | śmierci hosta – stoi na tym samym hoście |

`deploy/monitoring/README.md` od początku mówi „co najmniej jeden monitor musi stać gdzie
indziej” – i nikt go nie założył, bo każda usługa zewnętrzna to nowe konto. To zadanie zamyka tę lukę
narzędziem, które już mamy: **GitHub Actions** w repozytorium (publicznym – minuty są darmowe).

Czego zadanie **nie** robi:
- nie zakłada kont w żadnej usłudze (UptimeRobot itp. – tylko instrukcja „jako druga opinia”, § 6),
- nie dokłada sekretów repozytorium: wyłącznie wbudowany `GITHUB_TOKEN`,
- nie zastępuje watchdoga ani Kumy (nie widzi dysku, kolejki, poczty, S3),
- nie łączy się z serwerem inaczej niż przez publiczne HTTPS – żadnego SSH, żadnych przepustek.

## 1. Co jest sprawdzane (`scripts/uptime_external.py`)

Dla każdej witryny z listy (domyślnie `https://olimpiadakwantowa.pl`, `https://iqo-official.org`):

| Sprawdzenie | Awaria (`fail`) | Ostrzeżenie (`warn`) |
|---|---|---|
| `GET /` | brak odpowiedzi, timeout 20 s, kod ≠ 200 (po przekierowaniach) | odpowiedź wolniejsza niż 5 s |
| `GET /healthz/` | kod ≠ 200 albo `"status"` ≠ `"ok"` | wolniej niż 5 s |
| `GET /status.json` → `status` | brak JSON-a, `"status"` ≠ `"ok"` (`degraded`, `maintenance`) | wolniej niż 5 s |
| `GET /status.json` → `backup_restore_check` | `failed` albo `stale` | `unknown` lub brak klucza |
| TLS (port 443) | uzgodnienie nieudane (także wygasły/niepasujący certyfikat), < 7 dni ważności | < 14 dni ważności |

Dla LiveKit (domyślnie `https://live.olimpiadakwantowa.pl`): `GET /` musi oddać 200 i treść `OK`
(tak odpowiada serwer LiveKit na korzeń), plus TLS jak wyżej.

Progi czasu odpowiedzi są celowo luźne: VPS traci 12–37 % CPU na kradzież hiperwizora, ogon p95 jest
hostem, nie aplikacją – ostrzeżenie o 1,5 s budziłoby co godzinę. Czas jest **ostrzeżeniem**,
nigdy awarią; awarią jest dopiero timeout.

Listę witryn można zmienić bez zmiany kodu: zmienne repozytorium `UPTIME_SITES` i `UPTIME_LIVE`
(adresy rozdzielone spacją; pusta = domyślne).

## 2. „Dwa razy z rzędu” – potwierdzenie w tym samym przebiegu

Pojedyncza zgubiona odpowiedź nie może nikogo budzić. Awaria jest **potwierdzona**, gdy to samo
sprawdzenie nie przejdzie w dwóch kolejnych próbach odległych o 120 s. Druga próba odbywa się w tym
samym przebiegu workflow i tylko wtedy, gdy pierwsza coś znalazła.

Dlaczego nie „dwa kolejne przebiegi crona”: pamięć między przebiegami wymagałaby albo uprawnienia
`actions: read` (odczyt poprzedniego przebiegu), albo cache'u/artefaktu jako stanu, albo
zgłoszenia założonego już po pierwszej porażce (a założenie zgłoszenia to list do obserwujących).
Próba po 2 minutach daje ten sam filtr dla przypadkowych błędów, bez żadnego stanu poza samym
zgłoszeniem i z szybszym alarmem (≈ 12 min zamiast ≈ 20 min od awarii).

## 3. Alarm: jedno zgłoszenie z etykietą `awaria`

Stanem jest **otwarte zgłoszenie** z etykietą `awaria` i znacznikiem `<!-- uptime-external -->`
w treści (zgłoszenia `awaria` założone ręcznie przez ludzi są pomijane). Lista potwierdzonych awarii
jest zapisana w treści zgłoszenia jako `<!-- uptime-state: [...] -->`.

| Stan przed | Wynik przebiegu | Akcja |
|---|---|---|
| brak zgłoszenia | potwierdzone awarie | **nowe zgłoszenie** (GitHub wysyła list obserwującym repozytorium) |
| brak zgłoszenia | brak potwierdzonych | nic |
| otwarte | potwierdzone, **inny** zestaw niż zapisany | komentarz „zmiana” + aktualizacja treści |
| otwarte | potwierdzone, ten sam zestaw | nic (bez komentarza co 10 minut) |
| otwarte | wszystko w porządku w pierwszej próbie | komentarz „wróciło” + **zamknięcie** |
| otwarte | porażka tylko w jednej z prób | nic (nie zamykamy w trakcie migotania) |

Etykietę `awaria` workflow zakłada sam, jeśli jej nie ma. Przebieg kończy się na zielono także przy
awarii serwisu – alarmem jest zgłoszenie, nie czerwony krzyżyk (ten trafia wyłącznie do osoby,
która ostatnio zmieniła crona, i to co 10 minut). Czerwony przebieg znaczy: **zepsuł się sam
monitoring** (np. `gh` odmówił założenia zgłoszenia).

## 4. Workflow `.github/workflows/uptime.yml`

- `schedule: "4-59/10 * * * *"` (co 10 minut, przesunięte z pełnych godzin, gdzie kolejka crona
  GitHuba jest najbardziej zatkana) + `workflow_dispatch`,
- `permissions: contents: read, issues: write` – nic więcej,
- jedyna akcja: `actions/checkout` przypięty pełnym SHA (v4.4.0), `persist-credentials: false`,
  sparse checkout samego skryptu; Python i `gh` są na obrazie runnera,
- `concurrency: uptime` – przebieg ręczny i z crona nie założą dwóch zgłoszeń naraz,
- zmienne kontekstu (`vars.*`) idą do skryptu przez `env`, nie przez `${{ }}` w treści polecenia,
- uruchamia się wyłącznie w `qaif/olimpiada_kwantowa_2026` (fork nie odpytuje produkcji z crona).

### 4.1. Ograniczenia crona GitHuba (trzeba je znać)

- **Opóźnienia**: przebiegi z `schedule` startują z poślizgiem od kilku do kilkudziesięciu minut
  w godzinach szczytu, a pojedyncze bywają pominięte. To monitoring „w ciągu kwadransa–pół
  godziny”, nie „w ciągu minuty”.
- **Wyłączenie po 60 dniach ciszy**: w repozytorium publicznym GitHub wyłącza zaplanowane
  workflow, gdy przez 60 dni nie było w nim żadnej aktywności (commitów). Wyłącza **po cichu**
  (jeden list do osoby z prawem zapisu). Włączenie: *Actions → Uptime → Enable workflow* albo
  `gh workflow enable uptime.yml`. Stąd § 6: druga, niezależna opinia.
- Cron chodzi wyłącznie z gałęzi domyślnej (`main`) – workflow zaczyna działać po scaleniu.

## 5. Testy

`scripts/tests/test_uptime_external.py` – wyłącznie biblioteka standardowa (`unittest`), bez sieci:
ocena odpowiedzi (kody, JSON, `OK`, progi czasu), obliczenie ważności TLS na stałych datach,
potwierdzenie w dwóch próbach (fałszywy `fetch` i zegar), decyzje o zgłoszeniu (otwarcie,
deduplikacja, zmiana zestawu, zamknięcie, migotanie), rozpoznanie własnego zgłoszenia, odporność
treści zgłoszenia na znaczniki Markdown/wzmianki, oraz zgodność składni z Pythonem 3.10
(`ast.parse(feature_version=(3, 10))`). W CI: job `uptime-script` w `ci.yml`.

## 6. Druga opinia: darmowy pinger zewnętrzny (opcjonalnie, ręcznie)

GitHub Actions to też jedna firma, a wyłączenie crona po 60 dniach jest ciche. Zalecane: monitor
w UptimeRobot (plan darmowy, interwał 5 min) – **konto zakłada człowiek**, nie agent. Instrukcja:
`deploy/monitoring/README.md` § 5, `docs/OPERACJE.md` § 46.4.
