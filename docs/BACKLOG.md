# Backlog wykonawczy (wynik węzła Architect)

Kolejność wynika z zależności. Każdy task kończy się dopiero po spełnieniu DoD z PROJEKT.md 3.7.
Status: `todo` / `in_progress` / `done` / `escalated`.

| ID | Tytuł | Zależy od | Status |
|---|---|---|---|
| T-01 | Scaffold: projekt Django, Dockerfile multi-stage, entrypoint, healthz, compose `web`+`db`+`redis` healthy, pytest+ruff skonfigurowane | – | done |
| T-02 | Konta i RBAC: custom User, grupy participant/reviewer/appeals/coordinator, otwarta rejestracja uczestnika, rejestracja komitetu z kodem zaproszenia (ACTIVE lub PENDING), zatwierdzanie przez koordynatora | T-01 | done |
| T-03 | Modele domeny: Edition, Stage (terminy, grace, okno reklamacji), ScoringScale (0/2/5/6 param.), QualificationRule, Problem, Participant, CommitteeMember, StageEntry; admin; seed_demo | T-02 | done |
| T-04 | Upload rozwiązań: Submission/SubmissionFile, storage MinIO, walidacja magic bytes + rozmiar + ipynb, deadline po stronie serwera z select_for_update, zadanie Celery skanu ClamAV, blokada etapu przez beat | T-03 | in_progress |
| T-05 | Ocenianie: przydział 2 recenzentów (ślepy, bez konfliktu okręgu), Review z adnotacjami, walidacja score wobec skali, konsensus → FinalGrade, rozjazd → MODERATION → trzeci recenzent/koordynator | T-04 | todo |
| T-06 | Reklamacje: okno czasowe, jedna reklamacja na zadanie, komisja odwoławcza bez autorów rundy 1, AppealDecision → FinalGrade(APPEAL), AuditLog | T-05 | todo |
| T-07 | Wyniki i kwalifikacja: przeliczenie progów (MIN_POINTS/TOP_N/TOP_N_PER_DISTRICT/HYBRID), StageEntry.status, ResultsPublication snapshot zanonimizowany, publiczna tabela | T-06 | todo |
| T-08 | Panel recenzenta UI (HTMX): lista przydziałów, podgląd PDF (pdf.js) z adnotacjami, formularz oceny; panel uczestnika: upload, statusy, wyniki własne, reklamacja | T-07 | todo |
| T-09 | Część informacyjna (Wagtail): newsroom, strona bieżących zadań, archiwum edycji, tabela wyników publiczna | T-07 | todo |
| T-10 | E2E: scenariusz rejestracja → upload → zamknięcie → 2 oceny → rozjazd → moderacja → reklamacja → publikacja; README, .env.example, security checklist | T-08, T-09 | todo |

## Kryteria akceptacji per task

### T-01
- `docker compose up --build -d` → `web`, `db`, `redis` w stanie `healthy`.
- `GET /healthz/` zwraca 200 i `{"status":"ok","db":true,"redis":true}`.
- `pytest` w kontenerze `web` przechodzi (test healthz), `ruff check` czysty, `makemigrations --check` czysty.
- Obraz: użytkownik nie-root, brak sekretów, `.env.example` kompletny.

### T-02
- `POST /api/auth/register/participant/` tworzy użytkownika w grupie `participant` i profil Participant z `public_code`.
- `POST /api/auth/register/committee/` z ważnym kodem tworzy CommitteeMember ze statusem z kodu; z nieważnym/zużytym kodem → 400 `INVALID_INVITATION`.
- Koordynator może zatwierdzić PENDING → ACTIVE; recenzent PENDING nie ma dostępu do endpointów recenzenta (403).
- Logowanie sesyjne + token API; uczestnik nie ma dostępu do endpointów komitetu.

### T-03
- Modele z unikalnościami z PROJEKT.md 2.2; `ScoringScale.values` domyślne 0/2/5/6; walidacja `min_points`/`top_n` wg `mode`.
- `manage.py seed_demo` tworzy edycję z 3 etapami, 3 zadaniami, 5 uczestnikami, 3 recenzentami, kodem zaproszenia.
- Admin zarejestrowany dla wszystkich modeli.

### T-04
- POST z PDF przed deadline → 201, `Submission(version=k+1)`, `SubmissionFile(av_status=PENDING)`, zakolejkowane `scan_submission_file`.
- Po `deadline_at + grace_seconds` → 403 `DEADLINE_PASSED`.
- Plik `.pdf` z nagłówkiem zip → 400 `INVALID_FILE_TYPE`; > `max_file_mb` → 400 `FILE_TOO_LARGE`.
- Uczestnik bez StageEntry → 403; `entry` wyznaczany z `request.user`, nie z body (test IDOR).
- Równoległe uploady nie łamią unikalności wersji.
- `close_stage` ustawia `LOCKED` dla najnowszej wersji każdego (entry, problem).

### T-05
- `assign_reviewers(stage)` przydziela 2 recenzentów ACTIVE, różnych, nie z okręgu uczestnika (etap DISTRICT).
- Recenzent widzi tylko przydzielone Submission i tylko `public_code` uczestnika.
- `score` spoza skali → 400 `SCORE_NOT_IN_SCALE`.
- Dwie zgodne oceny SUBMITTED → `FinalGrade(CONSENSUS)`, status GRADED_PROVISIONAL; różne → MODERATION; trzecia ocena/koordynator → FinalGrade(THIRD_REVIEW|MODERATION).

### T-06
- Reklamacja poza oknem → 403 `APPEAL_WINDOW_CLOSED`; druga na to samo zadanie → 409.
- Członek komisji będący autorem Review rundy 1 nie może decydować (403).
- Decyzja z `new_score` → FinalGrade(APPEAL), AuditLog z diffem.

### T-07
- Dla każdego `mode` test kwalifikacji; przeliczenie tylko gdy wszystkie Submission FINAL.
- Snapshot nie zawiera imion/nazwisk przy `anonymization=CODE`; `FULL` tylko dla `publish_full_name=True`.
- Publiczny endpoint wyników działa bez logowania i zwraca snapshot, nie dane live.

### T-08 / T-09 / T-10
- Ścieżki UI renderują się (status 200, obecność kluczowych elementów) dla każdej roli.
- Wagtail: strona news, lista zadań bieżącego etapu, archiwum po edycjach.
- E2E Playwright zielone na czystym compose.

## Dług techniczny z przeglądów Critica (med/low, do domknięcia w kolejnych taskach)

| Źródło | Finding | Plan |
|---|---|---|
| T-02 | `district` członka komitetu jest samodeklarowany; reguła konfliktu interesów (T-05) na nim polega | T-05: `InvitationCode.district` ustalany przez koordynatora nadpisuje payload; pole w profilu oznaczone jako zweryfikowane |
| T-02 | Token DRF bez TTL i rotacji; jeden token na konto | T-08 (UI używa sesji); rotacja tokenu przy loginie + TTL 30 dni w osobnym tasku po T-10 |
| T-02 | Enumeracja kont przez `EMAIL_TAKEN` na rejestracji | Zaakceptowane (UX), limit 10/h/IP; do rozważenia flow z e-mailem potwierdzającym |
| T-02 | Brak testu wyścigu na `redeem_invitation` i testu 429 na `register` | T-10 (testy współbieżne z `transaction=True`) |
| T-02 | `allocate_public_code` TOCTOU (exists → create) | zamknięte w T-03 (retry na IntegrityError) |
| T-02 | Zmienne `plain_code` widoczne w tracebacku przy DEBUG | zamknięte w T-03 (`@sensitive_variables`) |
| T-03 | `statement_pdf` chroniony tylko przez pominięcie URL w serializerze; plik na storage bez kontroli dostępu | po T-04: serwować przez widok z `stage.has_opened()` / presigned URL; test że zasób jest nieosiągalny przed otwarciem |
| T-03 | Brak `MEDIA_ROOT`/`MEDIA_URL` (pliki lądują w /app) | po T-04: `MEDIA_ROOT=/var/app/media` (wolumen), `MEDIA_URL=/media/`, lub storage S3 |
| T-03 | Admin tworzy Stage z pominięciem `create_stage` → brak ScoringScale/QualificationRule | po T-04: `min_num=1` na inline'ach + `save_related` dopinający domyślne obiekty; test admina |
| T-03 | `@sensitive_variables` pomija `password` w register_*/_create_user | po T-04: bezargumentowy `@sensitive_variables()` |
| T-03 | `create_participant_with_public_code` rozpoznaje kolizję po substringu komunikatu; brak testów | po T-04: `constraint_name` z psycopg diag + 3 testy |
| T-03 | low: `register_for_stage` nie wymaga bieżącej edycji; `for_user` koordynatora w `/me/entries/`; `allowed_values()` akceptuje bool; `seed_demo` vs inna bieżąca edycja; 4 zapytania w `editions/current/` | po T-04 (jedna seria drobnych poprawek) |
