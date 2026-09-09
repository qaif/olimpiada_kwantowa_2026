# Backlog wykonawczy (wynik węzła Architect)

Kolejność wynika z zależności. Każdy task kończy się dopiero po spełnieniu DoD z PROJEKT.md 3.7.
Status: `todo` / `in_progress` / `done` / `escalated`.

| ID | Tytuł | Zależy od | Status |
|---|---|---|---|
| T-01 | Scaffold: projekt Django, Dockerfile multi-stage, entrypoint, healthz, compose `web`+`db`+`redis` healthy, pytest+ruff skonfigurowane | – | done |
| T-02 | Konta i RBAC: custom User, grupy participant/reviewer/appeals/coordinator, otwarta rejestracja uczestnika, rejestracja komitetu z kodem zaproszenia (ACTIVE lub PENDING), zatwierdzanie przez koordynatora | T-01 | done |
| T-03 | Modele domeny: Edition, Stage (terminy, grace, okno reklamacji), ScoringScale (0/2/5/6 param.), QualificationRule, Problem, Participant, CommitteeMember, StageEntry; admin; seed_demo | T-02 | done |
| T-04 | Upload rozwiązań: Submission/SubmissionFile, storage MinIO, walidacja magic bytes + rozmiar + ipynb, deadline po stronie serwera z select_for_update, zadanie Celery skanu ClamAV, blokada etapu przez beat | T-03 | done |
| T-05 | Ocenianie: przydział 2 recenzentów (ślepy, bez konfliktu okręgu), Review z adnotacjami, walidacja score wobec skali, konsensus → FinalGrade, rozjazd → MODERATION → trzeci recenzent/koordynator | T-04 | done |
| T-06 | Reklamacje: okno czasowe, jedna reklamacja na zadanie, komisja odwoławcza bez autorów rundy 1, AppealDecision → FinalGrade(APPEAL), AuditLog | T-05 | done |
| T-07 | Wyniki i kwalifikacja: przeliczenie progów (MIN_POINTS/TOP_N/TOP_N_PER_DISTRICT/HYBRID), StageEntry.status, ResultsPublication snapshot zanonimizowany, publiczna tabela | T-06 | done |
| T-08 | Panel recenzenta UI (HTMX): lista przydziałów, podgląd PDF (pdf.js) z adnotacjami, formularz oceny; panel uczestnika: upload, statusy, wyniki własne, reklamacja | T-07 | done |
| T-09 | Część informacyjna (Wagtail): newsroom, strona bieżących zadań, archiwum edycji, tabela wyników publiczna | T-07 | done |
| T-10 | E2E: scenariusz rejestracja → upload → zamknięcie → 2 oceny → rozjazd → moderacja → reklamacja → publikacja; README, .env.example, security checklist | T-08, T-09 | done |

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
| T-02 | `district` członka komitetu jest samodeklarowany; reguła konfliktu interesów (T-05) na nim polega | zamknięte w T-05 (`InvitationCode.district`, `district_verified`, `verify-district`) |
| T-02 | Token DRF bez TTL i rotacji; jeden token na konto | T-08 (UI używa sesji); rotacja tokenu przy loginie + TTL 30 dni w osobnym tasku po T-10 |
| T-02 | Enumeracja kont przez `EMAIL_TAKEN` na rejestracji | Zaakceptowane (UX), limit 10/h/IP; do rozważenia flow z e-mailem potwierdzającym |
| T-02 | Brak testu wyścigu na `redeem_invitation` i testu 429 na `register` | 429 na `register` zamknięte (`apps/web/tests/test_throttle.py`); wyścig `redeem_invitation` (`transaction=True`) – po T-10 |
| T-02 | `allocate_public_code` TOCTOU (exists → create) | zamknięte w T-03 (retry na IntegrityError) |
| T-02 | Zmienne `plain_code` widoczne w tracebacku przy DEBUG | zamknięte w T-03 (`@sensitive_variables`) |
| T-03 | `statement_pdf` chroniony tylko przez pominięcie URL w serializerze; plik na storage bez kontroli dostępu | zamknięte: `GET /api/competitions/problems/{id}/statement/` (404 przed `opens_at`), test |
| T-03 | Brak `MEDIA_ROOT`/`MEDIA_URL` (pliki lądują w /app) | zamknięte w T-04 (`MEDIA_ROOT`/`MEDIA_URL` w settings) |
| T-03 | Admin tworzy Stage z pominięciem `create_stage` → brak ScoringScale/QualificationRule | zamknięte: `ensure_stage_defaults` w `save_related` StageAdmin/EditionAdmin, test admina |
| T-03 | `@sensitive_variables` pomija `password` w register_*/_create_user | zamknięte |
| T-03 | `create_participant_with_public_code` rozpoznaje kolizję po substringu komunikatu; brak testów | zamknięte (`_violates_constraint` + 3 testy) |
| T-03 | low: `register_for_stage` nie wymaga bieżącej edycji; `for_user` koordynatora w `/me/entries/`; `allowed_values()` akceptuje bool; `seed_demo` vs inna bieżąca edycja; 4 zapytania w `editions/current/` | zamknięte poza liczbą zapytań w `editions/current/` (low, do T-09 przy cache) |
| T-04 | Osierocone obiekty w S3 przy rollbacku transakcji po `storage.put()` | zadanie sprzątające / lifecycle policy w MinIO (po T-10) |
| T-04 | Polyglot PDF (`%PDF-` + ZIP/JS w środku) przechodzi walidację; łagodzone przez ClamAV i prywatny bucket | rozważyć lekkie parsowanie struktury PDF lub bezpieczny podgląd dla recenzenta (T-08) |
| T-04 | Brak twardego limitu bajtów dla ścieżki `.ipynb` niezależnego od `max_file_mb` | **niezrobione w T-10**: osobny limit 8 MB – łagodzone limitem 2 MB na outputy i `max_file_mb` (≤ 100 MB) |
| T-04 | Klient S3 cache'owany `lru_cache` po wartościach sekretów | singleton czytający settings wewnątrz (po T-10) |
| T-05 | **high**: download dla recenzenta wysyła `Content-Disposition` z `original_name` (nazwa od uczestnika, może zawierać nazwisko) | zamknięte (`anonymous_download_name`, także `ResponseContentDisposition` w presigned URL) |
| T-05 | ~~med~~ zamknięte: N+1 w `GET reviews/` (`latest_file` omija prefetch); `_settle_round_one` zakłada 2 recenzje (per_submission=1 = ślepy zaułek); brak blokady przy równoległym `assign_reviewers` (500 na IntegrityError); `NOT_ENOUGH_REVIEWERS` wywraca cały etap; `AuditLog.ip` z REMOTE_ADDR = adres Caddy; osierocona recenzja rundy 2 po rozstrzygnięciu przez koordynatora + `save_draft` bez sprawdzenia stanu; tautologiczny test ELIM dla niezweryfikowanego recenzenta | zamknięte w iteracji poprawkowej T-05 (`TRUSTED_PROXY_IPS`, `pg_advisory_xact_lock`, `skipped`, `CANCELLED`) |
| T-05 | low: `for_user` recenzenta bez sprawdzenia grupy; `verify-district` na PENDING/SUSPENDED; PROTECT na `Review.reviewer` vs RODO (anonimizacja konta zamiast usuwania); ścieżka trzeciego recenzenta przez `resolve` bez `review.submitted` w audycie; adnotacje przechowywane dosłownie (escapowanie w T-08) | anonimizacja konta komitetu: osobny task po T-10; reszta zamknięta |
| T-06 | **high**: `me/submissions/` ujawnia `FinalGrade.rationale`, które dla THIRD_REVIEW jest kopią `comment_internal` | zamknięte |
| T-06 | med: `for_user` – `~Q(reviews__reviewer=member)` reużywa JOIN i koreluje po wierszu recenzji (no-op); `finalize_unappealed` blokuje wszystkie wiersze etapu w jednej transakcji + pełna lista id w audycie | zamknięte |
| T-06 | low: beat skanuje wszystkie historyczne etapy; rozjazd definicji konfliktu (dowolna runda vs CONFLICTING_ROUNDS); `download_url` zawsze mimo av_status; komisja widzi też rundę 2; ciche obcięcie do 20000 znaków; M2M `committee` bez PROTECT; nieosiągalny `IN_REVIEW` | zamknięte (through+PROTECT, IN_REVIEW usunięty, walidacja długości) |
| T-07 | **high**: tryb FULL publikacji nie jest ograniczony do laureatów finału ani do zgody opiekuna | zamknięte (FULL tylko FINAL + laureat + zgoda/pełnoletność) |
| T-07 | med: TOP_N przy zerach wpuszcza wszystkich; ponowna kwalifikacja nie cofa wpisów w następnym etapie; brak warunku zamknięcia okna reklamacji przy publikacji; `me/results/` miesza zamrożony total z live score; FINAL bez FinalGrade = ciche 0; INITIALS_SCHOOL quasi-identyfikuje + zbędny `district` | zamknięte (k=3, district tylko CODE, `APPEAL_WINDOW_OPEN`, `differs_from_published`) |
| T-07 | low: adnotacje w `me/results/` bez jawnego serializera; N+1 w `apply_qualification`; brak blokady etapu przy `publish`; CASCADE na `ResultsPublication.stage`; kryterium 6 spec vs 200 [] | zamknięte |
| T-07 | `next_stage_conflicts` z publikacji nie ma odbiorcy w UI; `entry_totals` JSON urośnie przy dużych eliminacjach; DISQUALIFIED nie sprząta wpisu w następnym etapie | **niezrobione w T-10**: konflikty w panelu koordynatora i osobna tabela `entry_totals` – po T-10 |
| T-08 | **high**: `/login/`, `/register/`, `/register/committee/`, upload przez UI omijają throttling (`login` 10/min, `register` 10/h, `upload` 30/h działają tylko na API) | zamknięte (`ThrottledFormMixin`, stawki z `api_settings`) |
| T-08 | med: CSP bez nonce psuje `/api/docs/` (Swagger inline script); `script-src` z całymi originami CDN (dodać `'strict-dynamic'`); etykieta „(UTC)” przy czasie w Europe/Warsaw; `MESSAGE_STORAGE` cookie dla kodu zaproszenia | zamknięte |
| T-08 | low: `AppealDecideView` poza `appeals_queue` (oracle konfliktu); N+1 `participant` w `appeals_queue`; `_appealable()` w widoku zamiast serwisu; CSP za WhiteNoise | zamknięte |
| T-09 | **high**: CSP `img-src`/`media-src` bez originu publicznego bucketu – obrazy Wagtaila blokowane w produkcji | zamknięte |
| T-09 | med: dokumenty Wagtaila serwowane przez redirect (prywatność kolekcji pozorna) → `WAGTAILDOCS_SERVE_METHOD="serve_view"`; wspólne poświadczenia MinIO dla obu bucketów → infra gotowa (`S3_PUBLIC_*`/`S3_PRIVATE_*`, minio-init z politykami), backend ma ich użyć; `EmbedBlock` bez `WAGTAILEMBEDS_FINDERS` i bez `frame-src`; N+1 na `/wyniki/` | zamknięte (backend używa `S3_PUBLIC_*`/`S3_PRIVATE_*`, `serve_view`, YT/Vimeo) |
| T-09 | low: brak prefetch dokumentów archiwum; migracja `0004` nie przenosi plików; polityka CSP po prefiksie ścieżki; idempotencja drzewa CMS; testowy `private_media` w tym samym katalogu; kolizje slugów z trasami aplikacji; `unsafe-eval` w panelu do weryfikacji | zamknięte poza `unsafe-eval` (do ręcznej weryfikacji w panelu) |
| T-08 | ~~429 przy uploadzie HTMX nie trafia do DOM~~ zamknięte w T-10 (`HX-Retarget`/`HX-Reswap: beforeend` + `htmx:beforeSwap` w `static/js/app.js`, 3 testy); ~~reset licznika przy zmianie hasła~~ zamknięte przy resecie hasła (`throttle.reset_for_identity` w `PasswordResetConfirmView`); licznik per konto bez IP | po T-10 |
| T-09 | rotacja kluczy serwisowych MinIO (minio-init tworzy konto tylko raz) – procedura ręczna opisana w README 6.2, automatyzacja po T-10; `frame-src` panelu `https:`; N+1 `result_links()` w indeksie archiwum; dokumenty Wagtaila w prywatnym buckecie dla materiałów wrażliwych | po T-10 |

## T-10 – co powstało

| Artefakt | Ścieżka |
|---|---|
| Scenariusz E2E (Playwright, Python) | `e2e/test_full_cycle.py`, `e2e/conftest.py`, `e2e/timeline.py`, `e2e/requirements.txt` |
| Reset środowiska + przebieg E2E | `scripts/e2e.sh` |
| Instrukcja uruchomienia i procedury operacyjne | `README.md` |
| Checklista bezpieczeństwa | `docs/SECURITY_CHECKLIST.md` |
| Raport pokrycia | `docs/COVERAGE.md` |
| Konfiguracja skanu sekretów | `.gitleaks.toml` |
| Przesuwanie osi czasu etapu (tylko `E2E_MODE=1`) | `backend/apps/competitions/management/commands/e2e_timeline.py` + testy |
| 429 z HTMX widoczny w DOM | `backend/apps/web/throttle.py`, `backend/static/js/app.js` + testy |

Otwarte pozycje po T-10 (żadna nie jest `high`): limit bajtów dla `.ipynb`, licznik logowań per
konto niezależny od IP, rotacja kluczy MinIO bez ręcznej procedury, `next_stage_conflicts` w UI,
`entry_totals` jako osobna tabela, anonimizacja konta członka komitetu, TTL i rotacja tokenu DRF,
sprzątanie osieroconych obiektów w S3, weryfikacja `unsafe-eval` w polityce CSP panelu.
| końcowy | Brak powiadomień e‑mail (decyzja reklamacji, wyniki) i generowania PDF wyników – obiecane w pierwotnym projekcie, nie zamówione w T-01..T-10 | osobny task po T-10 (kolejka `mail`, mailpit gotowe). Konfiguracja poczty (`EMAIL_URL`, `DEFAULT_FROM_EMAIL`), transport (usługa `mail`: Postfix + DKIM, README § 4.1) i pierwszy odbiorca – reset hasła – już są |
| reset hasła | **Otwarte.** Wysyłka listu jest synchroniczna w żądaniu `POST /password-reset/` (`EMAIL_TIMEOUT=10` ogranicza tylko czas zajęcia workera). Własny relay skraca połączenie do sieci compose (~0,1 s), ale nie usuwa zależności: gdy `mail` nie odpowiada, POST czeka do timeoutu | przenieść na kolejkę `mail` razem z resztą powiadomień; trasa `apps.core.tasks.send_mail_task` jest już w `CELERY_TASK_ROUTES` i kolejka `mail` jest już konsumowana przez workera (`-Q default,scan,mail`) – brakuje samego zadania i podmiany wywołania w widoku resetu |
| poczta | Rekordy DNS (SPF, DKIM, DMARC) i PTR dla `olimpiadakwantowa.pl` nie są jeszcze dodane – listy dochodzą, ale bez uwierzytelnienia (spam) | wartości gotowe do wklejenia: `/opt/olimpiada/mail-dns.txt` na serwerze (generuje `scripts/deploy.sh`, krok 7/7), tabela w `README.md` § 4.2; PTR w panelu Contabo |
| poczta | Brak monitoringu kolejki Postfiksa i reputacji adresu wyjściowego (bounce'y nikt nie czyta – `DEFAULT_FROM_EMAIL` to `noreply@`) | po dodaniu DNS: raporty DMARC na `rua`, okresowo `docker compose exec mail postqueue -p`; rozważyć skrzynkę na bounce'y zamiast `noreply@` |
| rozmowy | Etap w formie rozmowy kwalifikacyjnej (`StageFormat.INTERVIEW`) nie ma ścieżki oceniania: nie ma zadań, więc nie powstają `Submission`, `Review` ani `FinalGrade`, a `compute_stage_results` daje dla niego same zera. Punkty z rozmowy wpisuje koordynator **poza systemem** | TODO po T-10: ocena rozmowy jako osobny obiekt przy `InterviewBooking` (punkty + uzasadnienie komisji) wchodzący do `compute_stage_results` tak samo jak `FinalGrade`; do tego czasu kwalifikacja z etapu rozmowy jest ustawiana ręcznie w `/admin/` |
| końcowy | Brak nagłówka `Permissions-Policy`; test flag ciasteczek w `production.py`; `PHASE_OFFSETS` zduplikowane w e2e i komendzie; `EMBED_FRAME_SOURCES` vs `WAGTAILEMBEDS_FINDERS` bez testu równości; `seed_demo` z progiem 0 pkt | drobne, po T-10 |
| końcowy | zamknięte: admin API Caddy tylko localhost; bezpiecznik `SECRET_KEY`/S3 w `production.py`; flagi Secure w `.env.example` domyślnie bezpieczne | – |
| OAuth | **Świadomie przyjęte.** Rejestracji hasłem nie poprzedza weryfikacja adresu e-mail, więc przy łączeniu konta z Google (auto-connect po zweryfikowanym adresie) allauth czyści hasło konta, którego adresu nikt u nas nie potwierdził (`wipe_password`) – checklista § 3.2.8. Skutek uboczny: uczestnik, który miał hasło i raz zalogował się Google'em, musi je ustawić na nowo | usunąć przyczynę, nie objaw: weryfikacja adresu przy rejestracji hasłem (jednorazowy link, ta sama infrastruktura co reset hasła). Wtedy `EmailAddress.verified=True` powstaje przy rejestracji i czyszczenie hasła przestaje zachodzić |
| prod 2026-09-09 | **Incydent:** po dobie pracy `web` trzymał 92 bezczynne połączenia do Postgresa (`CONN_MAX_AGE=60` pod ASGI – połączenie przypięte do wątku żądania, który ginie bez `close_old_connections`), Postgres odpowiadał „too many clients already” i każda strona dawała 500 | zamknięte: `CONN_MAX_AGE=0` (`DB_CONN_MAX_AGE` w env). Do zrobienia: pula psycopg (`OPTIONS["pool"]`, wymaga `psycopg[pool]`) i alert na liczbę połączeń w `/healthz/` |
| OAuth | Nie ma ekranu „połączone konta” – uczestnik nie widzi, że jego konto jest powiązane z Google/Facebookiem, i nie może tego rozłączyć (`socialaccount_connections` celowo nie jest zamontowane) | prosty widok w panelu uczestnika: lista powiązań + rozłączenie z warunkiem „konto musi mieć hasło albo inne powiązanie”; wpis w audycie |
