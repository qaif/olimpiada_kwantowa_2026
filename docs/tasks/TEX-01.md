# TEX-01 — edytor LaTeX (webTEX) do pisania rozwiązań

## Cel

Uczestnik pisze rozwiązanie zadania w przeglądarce, w edytorze LaTeX z podglądem PDF (webTEX,
katalog `webtex/`, Node.js), i jednym przyciskiem na karcie zadania składa skompilowany PDF jako
zwykłe rozwiązanie – tą samą ścieżką co upload (`create_submission`: deadline, skan AV, wersje,
anonimizacja, potwierdzenie mailem). Platforma nie ufa edytorowi w niczym, co dotyczy zawodów:
bramki (rejestracja, okno etapu, nadzór, rola kadry) sprawdza zawsze Django, w chwili otwarcia
edytora i w chwili złożenia.

## Decyzje

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | webTEX żyje w repozytorium (`webtex/`) i jest usługą compose `webtex` za Caddy pod `tex.<SITE_DOMAIN>` (`WEBTEX_HOST`). | jedno wdrożenie i jedno CI; MIT jest zgodny z AGPL |
| D2 | Otwieranie w **nowej karcie** (jak laboratorium QC-02), bez iframe. | CSP platformy (`frame-src`, `frame-ancestors`, `form-action`) zostaje bez zmian |
| D3 | Tożsamość przekazuje jednorazowy token **JWT HS256** (TTL 120 s, `jti` jednorazowe) we **fragmencie** URL. | fragment nie trafia do logów serwera ani `Referer`; wzór: `apps/competitions/jitsi_jwt.py` |
| D4 | Składany jest **tylko PDF**. Platforma sama **pobiera** PDF z webTEX (serwer–serwer) i woła `create_submission`. | żadnych zmian w formatach, skanie i ocenianiu; zero zaufania do edytora przy składaniu |
| D5 | Praca **indywidualna** domyślnie. Koordynator może włączyć udostępnianie linków **per etap**; wyłączenie natychmiast odcina współpracowników. | ochrona przed współpracą na zawodach; elastyczność dla etapów treningowych/drużynowych |
| D6 | Funkcja za flagą konkursu `latex_editor` (domyślnie wyłączona). Wyłączona = adresów nie ma (404), karta zadania co do bajtu jak dziś. | konwencja `FEATURE_DEFAULTS` |
| D7 | Konta kadry (`has_staff_role`) nie otwierają edytora jako uczestnik. | jak w QC-02 |

## Kontrakt Django ↔ webTEX

Jeden sekret `WEBTEX_SHARED_KEY` (≥ 32 znaki, różny od innych sekretów). Z niego dwa klucze
z separacją domen (obie strony liczą identycznie):

```
sso_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-sso-v1")   # 32 bajty
api_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-api-v1")   # 32 bajty
```

### 1. Token SSO (Django → przeglądarka → webTEX)

JWT HS256 podpisany `sso_key`. Nagłówek `{"alg":"HS256","typ":"JWT"}`. Claimy:

| claim | typ | znaczenie |
|---|---|---|
| `iss` | str | `"olimpiada"` (stała `WEBTEX_ISSUER`) |
| `aud` | str | `"webtex"` |
| `iat`, `exp` | int | `exp = iat + 120`; webTEX dopuszcza 30 s rozjazdu zegara |
| `jti` | str | losowe ≥ 16 bajtów base64url; webTEX odrzuca powtórkę do `exp` |
| `sub` | str | id użytkownika platformy (`str(user.pk)`) – stabilny identyfikator |
| `name` | str | nazwa wyświetlana (imię i nazwisko) – widoczna tylko współpracownikom |
| `lang` | str | `"pl"` / `"en"` |
| `project` | str | klucz projektu, nieprzezroczysty dla webTEX: `"{competition.slug}/s{stage.pk}/p{problem.number}"`. Projekt jest unikalny dla pary (`sub`, `project`). |
| `scope` | str | zakres polityki udostępniania: `"{competition.slug}/s{stage.pk}"` |
| `share` | bool | czy w tym zakresie wolno udostępniać linki (D5) |
| `title` | str | tytuł do szablonu, np. `"Zadanie 3. Splątanie"` |
| `code` | str | kod publiczny uczestnika (`public_code`) – do nagłówka PDF **zamiast** nazwiska |
| `return_url` | str | absolutny URL karty zadań na platformie (link „Wróć do olimpiady”) |

Adres otwarcia: `{WEBTEX_URL}/sso#token=<jwt>`. Strona `/sso` webTEX czyta fragment, czyści go
(`history.replaceState`), wysyła `POST /api/integration/sso {"token": …}`, a webTEX:

1. weryfikuje podpis, `aud`, `iss`, `exp`, jednorazowość `jti`;
2. znajduje/zakłada użytkownika zewnętrznego (`external_id = "{iss}:{sub}"`, bez hasła i e-maila);
   aktualizuje `name`;
3. loguje (ciasteczko sesji webTEX);
4. znajduje/zakłada projekt (`sub`, `project`) z szablonem rozwiązania (tytuł, `code`, polski,
   `amsmath`, `braket`, `physics`, przykład `quantikz` w komentarzu); aktualizuje `scope`,
   `share`, `return_url`;
5. odpowiada `{"projectId": …}`, a klient przechodzi do `/project/<id>`.

Błędy: `400` (zły token), `409` (token już użyty), `410` (wygasł) – strona `/sso` pokazuje
komunikat i link powrotny, jeśli go zna.

### 2. API serwer–serwer (Django → webTEX)

Adres wewnętrzny `WEBTEX_INTERNAL_URL` (np. `http://webtex:3000`), sieć wewnętrzna compose.
Każde żądanie podpisane `api_key`:

```
X-Webtex-Signature: t=<unix>,v1=<hex HMAC-SHA256(api_key, "{t}.{METHOD}.{path}.{raw body}")>
```

webTEX odrzuca (`401`) brak/niepoprawny podpis i `|now - t| > 300 s`. `path` bez query stringu.

| Metoda i ścieżka | Ciało (JSON) | Odpowiedź |
|---|---|---|
| `POST /api/integration/build` | `{"sub": "…", "project": "…", "label": "…"}` (`label` opcjonalne, patrz niżej) | `200 application/pdf` (świeża kompilacja, nagłówki `X-Webtex-Errors: <liczba błędów LaTeX>`, `X-Webtex-Build: <id>`); `404 {"error":"no_project"}` gdy uczestnik nie otwierał edytora; `422 {"error":"compile_failed","issues":[{"file","line","message"}]}` gdy nie powstał PDF (max 20 pozycji); `503 {"error":"compiler_unavailable"}` |
| `POST /api/integration/sharing` | `{"scope": "…", "enabled": bool}` | `200 {"projects": <ile zmieniono>}`; wyłączenie natychmiast rozłącza współpracowników i blokuje dołączanie |
| `GET /api/integration/health` | – | `200 {"ok": true, "backend": "local"}` (podpis wymagany) |

`label` w `/build` (Uzupełnienie B): opcjonalny napis (do 120 znaków) – podpis wersji, którą webTEX
zapisuje w historii projektu, gdy kompilacja da PDF. Platforma wysyła „Złożone jako rozwiązanie –
wersja N” (N = numer, który dostanie to złożenie). Pole jest dodane później i działa w obie strony:
bez `label` webTEX zapisuje złożenie bez podpisu (panel pokazuje „Złożone jako rozwiązanie” i czas),
a starszy webTEX nieznane pole pomija. `label` niebędący napisem → `400 {"error":"bad_request"}`.

## Zachowanie webTEX w trybie integracji (`WEBTEX_INTEGRATION=true`)

- Rejestracja i logowanie hasłem wyłączone (konta tylko z SSO); `/login` pokazuje „Otwórz edytor
  z karty zadania na platformie olimpiady” + `PLATFORM_URL`.
- Użytkownik zewnętrzny nie tworzy, nie importuje, nie kopiuje i nie usuwa projektów – projekty
  powstają wyłącznie z SSO (koniec obejścia D5 przez „projekt na boku”).
- Projekt ma pole `sharing_locked`. Gdy zablokowany: tylko właściciel ma dostęp (członkostwa
  zostają w bazie, ale `roleFor` ich nie uznaje), `POST /api/join/:token` → `403`, endpointy
  tokenów/członków → `403`, okno „Udostępnij” mówi „Udostępnianie wyłączone przez organizatora”.
- Pobieranie ZIP źródeł zostaje (to praca uczestnika).
- Nagłówek edytora: link „Wróć do olimpiady” (`return_url`).
- Kompilacja: `openin_any=p`, `openout_any=p` (TeX nie czyta plików spoza katalogu projektu),
  bez shell-escape; brak dostępu do sieci (sieć compose `internal: true`).

## Zachowanie platformy

- Flaga `latex_editor` w `FEATURE_DEFAULTS`; aplikacja `apps.webtex`.
- Ustawienia: `WEBTEX_URL` (publiczny), `WEBTEX_INTERNAL_URL`, `WEBTEX_SHARED_KEY`, `WEBTEX_ISSUER`;
  check ostrzegający o krótkim/powtórzonym kluczu (wzór `cms.W013`).
- Karta zadania (`_problem_card.html`, obok panelu notatnika): tag włączeniowy, zero zapytań przy
  wyłączonej fladze. Pokazuje „Napisz rozwiązanie w LaTeX” (nowa karta) i – gdy etap przyjmuje
  rozwiązania, a zadanie dopuszcza PDF – formularz „Złóż PDF z edytora” z obowiązkowym
  potwierdzeniem (jak `SubmissionUploadForm.confirmed`), HTMX podmienia kartę.
- Otwarcie: `GET /me/stages/<stage_id>/problems/<number>/latex/` – bramki jak przy uploadzie
  (uczestnik, wpis do etapu, okno, nadzór, nie-kadra) → `302` na `{WEBTEX_URL}/sso#token=…`.
- Złożenie: `POST /me/stages/<stage_id>/problems/<number>/latex/submit/` – throttle `upload`,
  `POST /api/integration/build` (timeout ~120 s, `label` „Złożone jako rozwiązanie – wersja N”), potem
  `create_submission(user, stage, number, ContentFile(pdf, name="rozwiazanie.pdf"), request=…)`.
  Błędy webTEX → czytelny komunikat na karcie (lista błędów LaTeX przy 422).
- Koordynator: przełącznik „Udostępnianie linków w edytorze LaTeX” per etap (domyślnie wyłączony),
  wpis w dzienniku zdarzeń, po zmianie zadanie Celery `POST /api/integration/sharing` z ponawianiem.
- Testy pytest bez sieci: podpis/token liczone w teście, webTEX mockowany.

## Uzupełnienie A — asystent AI w edytorze (TEX-02)

webTEX dostaje asystenta LLM (czat, poprawianie błędów kompilacji, przeredagowanie zaznaczenia)
z adapterami natywnych API dostawców (OpenAI, Anthropic, Google Gemini, Mistral, Cohere, Azure
OpenAI, AWS Bedrock) oraz serwerów lokalnych/proxy (Ollama, vLLM, LiteLLM i inne zgodne z OpenAI).
Klucze dostawców są wyłącznie po stronie serwera webTEX.

Na zawodach asystent AI to pomoc niedozwolona, więc w trybie integracji:

| # | Decyzja |
|---|---|
| D8 | Asystent jest **domyślnie wyłączony**. Koordynator może go włączyć **per etap** (np. etap treningowy), osobnym przełącznikiem obok udostępniania linków (D5). |
| D9 | Token SSO dostaje claim `ai` (bool). Brak claimu albo wartość inna niż JSON `true` = wyłączony. Projekt zapisuje `ai_locked = !ai`; każde SSO aktualizuje. |
| D10 | `POST /api/integration/ai` – ciało `{"scope": "…", "enabled": bool}`, podpis i odpowiedź jak `/api/integration/sharing`; wyłączenie działa natychmiast (serwer odrzuca kolejne żądania AI dla projektów w zakresie, otwarte okna dostają zdarzenie i chowają panel). Caddy blokuje tę ścieżkę z zewnątrz jak pozostałe ścieżki serwer–serwer. |
| D11 | Serwer webTEX odrzuca żądanie AI (`403 ai_disabled`) dla projektu z `ai_locked`, niezależnie od tego, co pokazuje klient. |

## Uzupełnienie B — historia wersji projektu

Uczestnik, który skasuje pół rozwiązania (albo współpracownik, gdy koordynator włączył udostępnianie),
odzyskuje je z historii, a przy odwołaniu można pokazać, jak szkic wyglądał w danej chwili.

| # | Decyzja |
|---|---|
| D12 | webTEX zapisuje **wersje** projektu sam: po ustaniu zmian (`HISTORY_IDLE_SEC`, 120 s), co `HISTORY_ACTIVE_SEC` (600 s) ciągłej pracy, po każdej udanej kompilacji uruchomionej przez użytkownika i po udanym `/build` (złożenie, z `label`). Wersja = stan Y.Doc (aktualizacja Yjs) + drzewo plików + odwołania do plików binarnych po sha256; identyczna treść zapisana raz. Spis w SQLite (`snapshots`), treść w `/data/projects/<id>/history/` – kopia nocna wolumenu ją obejmuje. |
| D13 | Przechowywanie: wszystko z 24 h, potem jedna na godzinę do 7 dni, potem jedna na dzień; nic starszego niż `HISTORY_KEEP_DAYS` (365; `0` = bez limitu); limit `HISTORY_MAX_MB` (100) na projekt – najpierw najstarsze automatyczne. Złożenia i wersje z nazwą nie podlegają przerzedzaniu ani limitowi rozmiaru. |
| D14 | Panel „Historia”: lista z autorami zmian (kto edytował od poprzedniej wersji, kto ją wywołał), różnice plik po pliku względem stanu bieżącego, `.zip` wersji, przywrócenie projektu albo pliku. Przywrócenie to **nowa zmiana** w dokumencie (minimalne edycje wierszy, współpracownicy dostają ją na żywo; historia Yjs nie jest przepisywana), poprzedzona zapisem bieżącego stanu – „Cofnij przywrócenie”. |
| D15 | Dostęp jak do projektu: właściciel i edytorzy przywracają i zapisują wersje z nazwą, czytelnicy tylko oglądają i pobierają; przy `sharing_locked` członkowie nie widzą historii (404, jak projektu). |

## Wdrożenie

- compose: usługa `webtex` (build `webtex/`), wolumen `webtex-data`, sieci: `tex` (z `proxy`,
  `internal: true` dla ruchu wychodzącego) i `internal` (z `web`/`worker`); bez portów na hoście.
- Caddy: blok `{$WEBTEX_HOST}` → `reverse_proxy webtex:3000`, nagłówki bezpieczeństwa, limit
  ciała jak `MAX_UPLOAD_MB`; `scripts/render_caddyfile.sh` pomija blok, gdy `WEBTEX_HOST` puste.
- `.env.example`: sekcja „Edytor LaTeX (TEX-01)”.
