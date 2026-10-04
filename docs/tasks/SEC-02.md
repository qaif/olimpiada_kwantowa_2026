# SEC-02: Skanowanie zależności i obrazów kontenerów w CI

## 0. Cel i granice (polecenie organizatora, 5.10.2026)

Znana podatność w zależności (pakiet Pythona, pakiet systemowy obrazu, obraz usługi, biblioteka JS)
ma wychodzić **przy zgłoszeniu zmiany albo w ciągu tygodnia** – a nie przy przeglądzie
bezpieczeństwa raz w roku. Wynik ma być tani w utrzymaniu: czerwono tylko wtedy, gdy jest co zrobić.

Stan przed zadaniem:

| Luka | Skutek |
|---|---|
| brak skanu zależności Pythona | podatna wersja Django/Pillow/lxml wchodzi do obrazu bez słowa |
| brak skanu obrazów `web` i `djcms` | CVE w pakietach Debiana obrazu bazowego niewidoczne |
| obrazy usług (Caddy, Postgres, MinIO, ClamAV, Postfix, LiveKit, Jitsi…) nigdy nieskanowane | stary tag z krytyczną podatnością stoi na produkcji latami |
| brak Dependabota | aktualizacje zależą od tego, czy ktoś pamięta |
| akcje GitHuba na tagach (`@v4`) | przestawiony tag = cudzy kod w CI z tokenem do GHCR (incydent `trivy-action`, marzec 2026) |
| zwendorowany JS bez porównania z upstreamem | livekit-client/KaTeX/htmx starzeją się niezauważone |

Czego zadanie **nie** robi:
- nie aktualizuje niczego automatycznie (Dependabot otwiera PR-y, skany piszą raporty),
- nie zmienia obrazów ani zależności (bramki mogą po włączeniu ujawnić podatności – naprawa to
  osobna zmiana),
- nie podpisuje obrazów (cosign – osobna pozycja), nie robi SBOM-ów do publikacji,
- nie skanuje kodu (SAST/CodeQL) ani sekretów (`.gitleaks.toml` – osobno).

## 1. Zależności Pythona – job CI `pip-audit`

- Zbiory: **backend** – wynik `uv pip compile backend/pyproject.toml --extra dev` dla Pythona 3.14
  i platformy obrazu (backend nie ma pliku blokady; obraz instaluje „najnowsze w zakresie”, więc
  sprawdzamy dokładnie to, co zbudowałoby się teraz); **djcms** – `uv export --frozen` z `djcms/uv.lock`;
  **notebook-lab** – narzędzia budowy JupyterLite (QC-01, `apps/notebooks/labbuild/requirements.txt`,
  przypięte ze skrótami). Koła Pyodide w samym laboratorium (wykonywane w przeglądarce ucznia) są poza
  zakresem – ich wersje wyznacza wydanie Pyodide (QC-01).
- `pip-audit --no-deps --disable-pip --vulnerability-service osv -f json`, wersja przypięta
  (`PIP_AUDIT_VERSION` w `ci.yml`).
- Bramka `scripts/security/pip_audit_gate.py`: **czerwono** przy podatności z wydaną poprawką;
  bez poprawki – ostrzeżenie (adnotacja + podsumowanie joba); brak raportu = błąd (nie „zero”).
- Wyjątki: `.security/pip-audit-ignore.toml` – `id` (albo alias), `package`, `reason` (≥ 20 znaków),
  `expires` (data, najdalej 180 dni). Wygasły, bez powodu albo z dalszym terminem → czerwono.
  Wyjątek, który niczego nie dotyczy → ostrzeżenie „usuń”.

## 2. Obrazy `web` i `djcms` – job CI `image-scan`

- Po jobie `image` (`needs`), macierz `web`/`djcms`; obraz budowany z cache'u warstw (`type=gha`,
  djcms we własnym zakresie `scope=djcms`), `load: true`, bez publikacji.
- `aquasecurity/trivy-action` przypięty SHA, Trivy w wersji przypiętej (`TRIVY_VERSION`), baza
  podatności w cache'u Actions (klucz wg daty, wbudowany w akcję).
- Bramka: CRITICAL/HIGH z poprawką (`ignore-unfixed`) → czerwono.
- Raport SARIF (CRITICAL/HIGH, także bez poprawki) → GitHub code scanning (repozytorium publiczne,
  `security-events: write` tylko w tym jobie; pomijane dla PR z forka) + artefakt na 30 dni.
- Wyjątki: `.security/trivyignore.yaml` – natywny format Trivy z `expired_at`; `statement`
  i termin ≤ 180 dni wymusza `scripts/security/policy_check.py`.

## 3. Obrazy usług – workflow `security-scan.yml` (co tydzień)

- Poniedziałek 4:17 UTC + ręcznie (`workflow_dispatch`).
- Lista: `image:` z plików compose produkcyjnych (`docker-compose.yml`, `docker-compose.operator.yml`,
  `docker-compose.djcms.yml`, `deploy/livekit/…`, `deploy/jitsi/…`) z domyślnymi wartościami
  `${VAR:-…}`; bez naszych `olimpiada/*` (skanuje je CI) i bez narzędzi dewelopera (Playwright).
  Dodatkowo nasz opublikowany `ghcr.io/<właściciel>/olimpiada-web:main` – starzeje się jak każdy inny.
- `scripts/security/third_party_images.py scan|report`: CRITICAL/HIGH, digest skanowanego tagu,
  liczba podatności z poprawką, szczegóły w `<details>`; pełne JSON-y w artefakcie (90 dni).
- **Tylko raport**: jedno zgłoszenie z etykietą `security-scan`, aktualizowane; komentarz
  (powiadomienie) wyłącznie przy zmianie wyników; zamykane, gdy czysto (`upsert_issue.sh`).

## 4. Dependabot – `.github/dependabot.yml`

| Ekosystem | Katalog | Uwagi |
|---|---|---|
| `pip` | `/backend` | `increase-if-necessary` – PR tylko, gdy wydanie wychodzi poza zakres w pyproject |
| `uv` | `/djcms` | zamiast `pip`: aktualizuje pyproject **i** `uv.lock` (CI: `uv sync --frozen`) |
| `docker` | `/backend`, `/djcms` | bez zmiany wersji Pythona (minor/major ignorowane) |
| `docker-compose` | `/`, `/deploy/livekit`, `/deploy/jitsi` | bez wersji głównych Postgresa i Redisa (migracja danych) |
| `github-actions` | `/` | podbija SHA razem z komentarzem `# vX.Y.Z` |

Wspólne: co tydzień (poniedziałek 6:00 Europe/Warsaw), minor+patch w jednym PR na ekosystem,
poprawki bezpieczeństwa grupowane osobno, `cooldown` 7 dni, limity 3–5 otwartych PR-ów, etykiety
`dependencies` + ekosystem. npm – brak `package.json` (JS: § 5); `e2e/requirements.txt` poza
Dependabotem (playwright musi odpowiadać obrazowi).

## 5. JavaScript spoza repozytorium

Rejestr `.security/vendor.toml`: zwendorowane (`livekit-client`, KaTeX), z CDN z SRI (htmx,
Alpine CSP, Swagger UI, pdf.js) i zewnętrzne bez wersji (gtag.js – z uzasadnieniem).

- `vendor_check.py check` (job `supply-chain`, offline): wersja odczytywalna z `VERSION`; **każdy**
  plik katalogu ma skrót w `VERSION`/`SHA384`/`SHA256SUMS` (KaTeX dostał `SHA256SUMS` dla krojów);
  wpisy `SHA256SUMS` sprawdzane w obie strony; każdy katalog `vendor/<x>` i każdy zewnętrzny
  `<script src>` w szablonach jest w rejestrze; wersja CDN wpisana wszędzie tak samo.
- `vendor_check.py upstream` (workflow `vendor-upstream.yml`, 1. dnia miesiąca): najnowsza wersja
  w npm (nowsza / nowa główna), znane podatności naszej wersji (OSV), suma paczki npm zapisana przy
  wendorowaniu, SRI plików na CDN. Jedno zgłoszenie `vendor-js`; zamykane, gdy wszystko aktualne.

## 6. Akcje GitHuba i uprawnienia

- Wszystkie `uses:` przypięte pełnym SHA z komentarzem wersji (także istniejące w `ci.yml`
  i `deploy.yml`); `policy_check.py` odrzuca tag/gałąź.
- `ci.yml`: domyślne `permissions: contents: read`; szersze tylko w `image` (GHCR) i `image-scan`
  (SARIF). Workflowy okresowe: `permissions: {}` na górze, w jobie `issues: write` (+ `packages: read`).
- Wersje skanerów przypięte w `env` (`TRIVY_VERSION` v0.74.0 – nie najnowsza z dnia wydania,
  `PIP_AUDIT_VERSION` 2.10.1).

## 7. Testy i weryfikacja

- `scripts/security/tests` (pytest, bez sieci i Django; job `supply-chain`): bramka pip-audit
  (blokuje / ostrzega / wyjątki: alias, nazwa PEP 503, wygasły, krótki powód, daleki termin, termin
  jako tekst; brak raportu = kod 2), vendor check (podmieniony plik, plik dodatkowy, nieopisany
  katalog, `SHA256SUMS` w obie strony, rozjazd wersji CDN, nieznany skrypt), przypięcie akcji,
  wyjątki Trivy, lista obrazów compose, raport Trivy.
- Ruff (konfiguracja backendu) obejmuje `scripts/security` w jobie `lint`.
- Lokalnie (5.10.2026): pip-audit i Trivy **nie** są zainstalowane na maszynie dewelopera – zgodnie
  z poleceniem nie instalowaliśmy ich; zbiory zależności sprawdzone zapytaniem do API OSV
  (150 pakietów backendu, 42 djcms, 8 notebook-lab: **zero znanych podatności**); `vendor_check.py upstream`
  uruchomiony (bez uwag bezpieczeństwa; nowsze wydania htmx, Alpine, Swagger UI, pdf.js 6.x).
  Wynik Trivy dla obrazów – dopiero w pierwszym przebiegu CI na PR.

## 8. Kroki operatora (jednorazowo, ustawienia repozytorium)

Opis w `docs/OPERACJE.md` § 47.7: etykiety, Dependabot alerts + security updates, ochrona gałęzi
(`pip-audit`, `łańcuch dostaw`, `trivy (obraz web)`, `trivy (obraz djcms)` jako wymagane).
