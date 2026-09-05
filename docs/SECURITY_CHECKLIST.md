# Checklista bezpieczeństwa (T-10)

Stan na zamknięcie T-10. Każda pozycja ma **odniesienie do kodu i do testu**, który ją pilnuje –
odhaczenie bez testu jest w tym dokumencie nieważne, bo nikt go nie odtworzy przy następnej zmianie.

Legenda statusu:

- ✔ – zamknięte, pokryte testem albo weryfikowalne poleceniem podanym w wierszu,
- ⚠ – świadomie przyjęte ryzyko albo dług; wiersz wskazuje pozycję w [`BACKLOG.md`](BACKLOG.md).

**Otwartych pozycji „high” nie ma.** Wszystkie findingi `high` z przeglądów T-02…T-09 są zamknięte
(tabela długu w `BACKLOG.md`); pozycje ⚠ poniżej są klasy `med`/`low`.

---

## 1. Wstrzyknięcia i dostęp do danych

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 1.1 | Brak surowego SQL sklejanego z danych | ✔ | `grep -rn "cursor()\|\.raw(\|RawSQL\|\.extra(" backend/apps backend/config` daje dokładnie dwa trafienia: `apps/core/views.py:10` (`SELECT 1` w healthchecku, bez parametrów) i `apps/grading/services.py:88` (`pg_advisory_xact_lock(%s, %s)` – placeholdery sterownika, nie f-string). Cała reszta dostępu do danych idzie przez ORM. |
| 1.2 | Filtrowanie querysetów per rola w jednym miejscu | ✔ | `for_user()` na managerach: `Submission.objects.for_user`, `StageEntry.objects.for_user`, `Review`/`Appeal` przez serwisy (`reviews_for_reviewer`, `appeals_queue`). Testy: `apps/competitions/tests/test_api.py::test_me_entries_zwraca_wylacznie_wlasne_wpisy`, `apps/grading/tests/test_review_api.py::test_reviewer_cannot_open_someone_elses_review`. |
| 1.3 | Brak IDOR – identyfikator właściciela nigdy z body | ✔ | `apps/submissions/services.py::_locked_entry` wyznacza `StageEntry` wyłącznie z `request.user`. Test: `apps/submissions/tests/test_upload_api.py` (przypadek IDOR na `entry_id`). |
| 1.4 | Migracje odwracalne | ✔ | Każda `RunPython` ma funkcję odwrotną: `accounts/0002_rbac_groups` (`delete_groups`), `cms/0002_initial_tree` (`remove_tree`), `cms/0003_coordinator_permissions` (`revoke`). Wyjątek świadomy: `appeals/0002_appeal_decision_committee_through` kopiuje M2M do tabeli `through` z `RunPython.noop` w tył – odwrócenie i tak kasuje tabelę docelową. Weryfikacja: `manage.py makemigrations --check` w CI. |

## 2. Upload rozwiązań

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 2.1 | Typ pliku z magic bytes, nie z nazwy ani `Content-Type` | ✔ | `apps/submissions/validators.py::_validate_pdf` (nagłówek `%PDF-`), `_validate_notebook` (`nbformat.validate`), `_validate_python`. Testy: `apps/submissions/tests/test_validators.py`, `test_upload_api.py` (plik `.pdf` z nagłówkiem ZIP → `INVALID_FILE_TYPE`). |
| 2.2 | Limit rozmiaru per zadanie i limit strumienia clamd | ✔ | `Problem.max_file_mb` (1–100 MB, `CheckConstraint`), `CLAMAV_STREAM_MAX_BYTES`. Testy: `test_validators.py`, `test_tasks.py`. |
| 2.3 | Klucz obiektu bez nazwy pliku od użytkownika | ✔ | `apps/submissions/storage.py::build_object_key` – `{edycja}/{etap}/{kod}/{uuid}/{sha256}.{ext}`, `sha256` walidowany regexem, `ext` z krótkiej listy. Oryginalna nazwa jest wyłącznie metadaną (`sanitize_original_name`). Test: `apps/submissions/tests/test_storage.py`. |
| 2.4 | Skan antywirusowy przed udostępnieniem recenzentowi | ✔ | `apps/submissions/tasks.py::scan_submission_file` (ClamAV przez `INSTREAM`); pobranie przez inną rolę niż właściciel wymaga `av_status=CLEAN` (`apps/submissions/api.py::SubmissionDownloadView`). Testy: `test_tasks.py`, `test_review_api.py::test_reviewer_cannot_download_file_before_clean_scan`. Scenariusz E2E czeka na **prawdziwy** werdykt ClamAV. |
| 2.5 | Prywatny bucket, dostęp wyłącznie presigned URL | ✔ | `minio-init`: `mc anonymous set none local/submissions`; `deploy/minio/policy-submissions.json`. Backend: `S3SubmissionStorage` + `S3_PRESIGNED_TTL_SECONDS` (domyślnie 600 s). Test: `apps/submissions/tests/test_download_api.py`. |
| 2.6 | Nazwa pliku dla recenzenta bez danych osobowych | ✔ | `apps/submissions/api.py::anonymous_download_name` (`OLM-XXXXXX-z1-v1.pdf`), także w `ResponseContentDisposition` presigned URL-a. Test: `apps/submissions/tests/test_download_privacy.py`. |
| 2.7 | Polyglot PDF (`%PDF-` + ZIP/JS w środku) | ⚠ `low` | Walidacja sprawdza nagłówek, nie strukturę. Łagodzone: ClamAV, prywatny bucket, brak renderowania pliku po stronie serwera. Pozycja w `BACKLOG.md` (dług T-04). |
| 2.8 | Osierocone obiekty w S3 po rollbacku transakcji | ⚠ `low` | `storage.put()` poprzedza commit; rollback zostawia obiekt bez wiersza. Plan: zadanie sprzątające / lifecycle policy MinIO – `BACKLOG.md` (dług T-04). |

## 3. RBAC – testy negatywne per endpoint

Każdy wiersz to test, który sprawdza **odmowę**, a nie zgodę.

| Endpoint / ekran | Kto dostaje odmowę | Test |
|---|---|---|
| `/me/`, `/me/stages/…/upload/` | anonim → 302, recenzent/koordynator → 403 | `apps/web/tests/test_access.py::test_anonymous_is_redirected_to_login`, `::test_reviewer_gets_403_outside_review_panel` |
| `/review/`, `/review/<id>/` | uczestnik → 403; recenzent bez przydziału → 404 | `apps/web/tests/test_access.py::test_participant_gets_403_outside_own_panel`, `apps/grading/tests/test_review_api.py::test_reviewer_cannot_open_someone_elses_review` |
| `/coordinator/…` | uczestnik i recenzent → 403; superuser bez grupy → 403 | `apps/web/tests/test_access.py`, `apps/accounts/tests/test_permissions.py::test_superuser_nie_jest_automatycznie_koordynatorem` |
| `/appeals/`, `/appeals/<id>/decide/` | recenzent bez roli `appeals` → 403; autor recenzji rundy 1 → sprawy nie widzi (404) | `apps/appeals/tests/test_appeal_api.py::test_reviewer_without_appeals_role_cannot_use_committee_endpoints`, `::test_round_one_reviewer_cannot_decide_and_does_not_see_appeal` |
| `POST /api/…/submissions/` | uczestnik bez `StageEntry` → 403 `NOT_REGISTERED`; cudzy `entry` → 403 | `apps/submissions/tests/test_upload_api.py` |
| `GET /api/submissions/<id>/download/` | cudze zgłoszenie → 404; plik przed `CLEAN` dla nie-właściciela → 403; komisja bez reklamacji → 404 | `apps/submissions/tests/test_download_api.py`, `apps/appeals/tests/test_appeal_api.py::test_committee_cannot_download_submission_without_appeal` |
| `POST /api/auth/committee/<id>/approve/` | uczestnik → 403, recenzent → 403 | `apps/accounts/tests/test_permissions.py::test_kryterium_5_uczestnik_wywolujacy_approve_dostaje_403`, `::test_kryterium_5_recenzent_nie_moze_zatwierdzac_innych` |
| endpointy recenzenta | recenzent `PENDING` / `SUSPENDED` → 403 | `apps/accounts/tests/test_permissions.py::test_kryterium_6_widok_is_active_reviewer_daje_403_dla_pending_i_200_dla_active` |
| `POST /api/competitions/stages/<id>/register/` | etap okręgowy / finał → 403 `STAGE_NOT_OPEN_FOR_REGISTRATION` | `apps/competitions/tests/test_api.py::test_rejestracja_do_etapu_okregowego_przez_api_daje_403` |
| `/cms/` (Wagtail) | uczestnik i recenzent → 302/403 | `apps/cms/tests/test_access.py` |
| `GET /api/competitions/problems/<id>/statement/` | przed `opens_at` → 404 (także anonimowo) | `apps/cms/tests/test_security.py::test_statement_is_served_by_the_view_only_after_opens_at` |

Scenariusz E2E dokłada dwie asercje negatywne na żywym systemie: panel recenzenta nie zawiera
nazwiska ani adresu e-mail uczestnika, a publiczna tabela wyników – nazwiska ani szkoły.

## 4. Czas, terminy, współbieżność

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 4.1 | Deadline egzekwowany po stronie serwera, w UTC | ✔ | `Stage.submission_deadline` (= `deadline_at + grace_seconds`), porównanie z `timezone.now()` w `apps/submissions/services.py`; `USE_TZ=True`. Zegar przeglądarki służy wyłącznie do odliczania (`static/js/app.js` koryguje o `data-server-now`). Testy: `apps/submissions/tests/test_upload_api.py` (403 `DEADLINE_PASSED`), `apps/competitions/tests/test_services.py`. |
| 4.2 | Okno reklamacji i publikacja po stronie serwera | ✔ | `Stage.is_appeal_window_open`, `apps/results/services.py::_assert_appeal_window_closed` (409 `APPEAL_WINDOW_OPEN`). Test: `apps/results/tests/test_publication.py::test_publishing_before_the_appeal_window_closes_is_rejected`. |
| 4.3 | Wersjonowanie uploadu odporne na wyścig | ✔ | `select_for_update` na `StageEntry` nadaje numer wersji. Test: `apps/submissions/tests/test_concurrency.py`. |
| 4.4 | Przydział recenzentów odporny na wyścig | ✔ | `pg_advisory_xact_lock` na etapie. Test: `apps/grading/tests/test_concurrency.py::test_two_parallel_assignments_do_not_double_assign_reviewers`. |
| 4.5 | Przesuwanie terminów z powłoki wymaga jawnego trybu | ✔ | `manage.py e2e_timeline` odmawia bez `E2E_MODE=1`; w produkcji zmienna nie jest ustawiana (jest tylko w `docker-compose.dev.yml`). Test: `apps/competitions/tests/test_e2e_timeline.py::test_bez_e2e_mode_komenda_odmawia`. |

## 5. Sekrety i konfiguracja

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 5.1 | `.env` poza repozytorium | ✔ | `.gitignore` (pierwsza linia), `git ls-files \| grep -i env` → wyłącznie `.env.example`. |
| 5.2 | Brak sekretów w obrazie | ✔ | `backend/Dockerfile` nie kopiuje `.env`; wszystkie wartości wchodzą przez `env_file`/`environment` w compose. Skan `gitleaks` (katalog roboczy **i** historia 26 commitów) – **bez trafień**. |
| 5.2a | Allowlist skanu sekretów nie wycisza całych katalogów | ✔ | `.gitleaks.toml` wyklucza po ścieżce wyłącznie pliki spoza repozytorium (`.env`, `.venv/`, `__pycache__/`, artefakty E2E); hasła demonstracyjne są wyciszane **po wartości** (`targets = ["secret"]`), więc prawdziwy klucz wklejony do `apps/*/tests/` albo do `e2e/` dalej jest zgłaszany. Sprawdzone kontrolą pozytywną: sztuczny `ghp_…` w `e2e/conftest.py` i w `apps/accounts/tests/factories.py` → 2 trafienia. |
| 5.3 | Kod zaproszenia wyłącznie jako sha256 | ✔ | `apps/accounts/models.py::hash_invitation_code`; kod jawny pokazywany raz i nigdzie nie zapisywany (`@sensitive_variables("plain_code")`). Test: `apps/accounts/tests/test_registration.py::test_kod_zaproszenia_jest_trzymany_wylacznie_jako_sha256`. |
| 5.4 | Komunikaty z kodem zaproszenia nie idą do ciasteczka | ✔ | `MESSAGE_STORAGE = SessionStorage` (`config/settings/base.py`, z uzasadnieniem w komentarzu). |
| 5.5 | Rozdzielone poświadczenia MinIO per bucket | ✔ | `minio-init` tworzy dwa konta serwisowe z politykami `deploy/minio/policy-*.json`; backend czyta `S3_PUBLIC_*` (alias `default`, Wagtail) i `S3_PRIVATE_*` (`private_media` + rozwiązania). Fallback na `MINIO_ROOT_*` loguje ostrzeżenie. Test: `apps/submissions/tests/test_bucket_credentials.py`. |
| 5.6 | Rotacja kluczy serwisowych MinIO | ⚠ `med` | `minio-init` tworzy konta **tylko raz** (`mc admin user info \|\| add`), więc zmiana wartości w `.env` nie rotuje klucza. Procedura ręczna jest w README („Rotacja kluczy serwisowych MinIO”); automatyzacja – `BACKLOG.md` (dług T-09). |
| 5.7 | Token DRF bez TTL i rotacji | ⚠ `med` | Panel WWW używa sesji, token jest wyłącznie dla API. Rotacja przy logowaniu + TTL 30 dni – `BACKLOG.md` (dług T-02). |

## 6. Przeglądarka: CSRF, CSP, nagłówki

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 6.1 | CSRF na każdym formularzu i na żądaniach HTMX | ✔ | `CsrfViewMiddleware`; `base.html` ustawia `hx-headers` z tokenem dla całego drzewa; wylogowanie wyłącznie POST-em. Testy: cały pakiet `apps/web/tests` chodzi po formularzach przez `django.test.Client` z włączonym CSRF w widokach. |
| 6.2 | CSP bez `'unsafe-inline'`/`'unsafe-eval'` dla skryptów na stronach publicznych | ✔ | `apps/web/middleware.py::build_policy` – `'self'`, `nonce-…`, CDN-y, `'strict-dynamic'`. Testy: `apps/web/tests/test_public.py::test_csp_header_is_present_and_has_no_unsafe_inline_scripts`, `::test_script_src_has_strict_dynamic_with_cdn_fallback`, `apps/cms/tests/test_security.py::test_public_pages_keep_nonce_only_script_policy`. |
| 6.3 | Wyjątek CSP dla `/cms/` i `/admin/` (polityka bez nonce, z `'unsafe-inline'`) | ✔ świadomy | Panele bibliotek wstrzykują skrypty inline; obie ścieżki wymagają logowania i uprawnień, treści anonimów się w nich nie renderują. Rozpoznanie po `resolver_match`, prefiks ścieżki z `reverse()` jako ostatnie kryterium. Testy: `apps/cms/tests/test_security.py::test_admin_paths_get_the_relaxed_policy`, `::test_namespaced_admin_view_gets_the_admin_policy`. `'unsafe-eval'` w polityce panelu pozostaje do ręcznej weryfikacji – `BACKLOG.md` (dług T-09, `low`). |
| 6.4 | CSP obejmuje też pliki statyczne | ✔ | Middleware stoi **przed** WhiteNoise. Testy: `test_csp_middleware_sits_above_whitenoise`, `test_static_file_served_by_whitenoise_gets_the_csp_header`. |
| 6.5 | `frame-src` zawężone do dostawców osadzeń | ✔ | Ta sama lista, co `WAGTAILEMBEDS_FINDERS` (YouTube, Vimeo). Testy: `apps/cms/tests/test_security.py::test_public_policy_limits_frames_to_the_allowed_embed_providers`, `::test_embed_from_an_unlisted_provider_is_rejected`. |
| 6.6 | Skrypty z CDN pinowane i z SRI | ✔ | `templates/base.html` (HTMX, Alpine CSP build), `SWAGGER_UI_SRI` w ustawieniach. Test: `apps/web/tests/test_public.py::test_home_page_loads_pinned_cdn_scripts_with_sri`. |
| 6.7 | Nagłówki bezpieczeństwa na proxy | ✔ | `deploy/Caddyfile`: `Strict-Transport-Security max-age=31536000`, `X-Content-Type-Options nosniff`, `Referrer-Policy same-origin`; limit rozmiaru żądania `MAX_UPLOAD_MB`. Django dokłada `X-Frame-Options` (`XFrameOptionsMiddleware`) i `frame-ancestors 'none'` w CSP. ⚠ `low`: brak `Permissions-Policy` i `Cross-Origin-Opener-Policy` – do rozważenia po T-10. |
| 6.8 | Brak `\|safe`/`mark_safe` na treściach od użytkowników | ✔ | `grep -rn "\|safe\|mark_safe" backend/templates` – brak trafień w szablonach `web/` i `cms/`; treści redakcyjne renderują się przez `\|richtext` i `{% include_block %}` (whitelist Wagtaila). |
| 6.9 | Odpowiedź 429 widoczna w interfejsie (także HTMX) | ✔ | `apps/web/throttle.py::throttled_response` (`HX-Retarget`/`HX-Reswap: beforeend`) + `static/js/app.js` (`htmx:beforeSwap`). Testy: `apps/web/tests/test_throttle.py::test_upload_429_is_retargeted_so_htmx_puts_it_in_the_dom`, `::test_upload_429_ignores_a_target_id_that_is_not_a_plain_identifier`. |

## 7. Dane osobowe, RODO, logi

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 7.1 | Brak PII w logach aplikacji | ✔ | `grep -rn "logger\.\(info\|warning\|error\)" backend/apps --include=*.py \| grep -v tests` – wszystkie wywołania logują wyłącznie identyfikatory liczbowe, statusy i liczniki. Nigdzie e-maila, nazwiska ani nazwy pliku od użytkownika. |
| 7.2 | Brak PII w `AuditLog.diff` | ✔ | `publish_results` i `apply_qualification` zapisują wyłącznie liczniki; kolizje w następnym etapie idą jako pseudonimy. Test: `apps/results/tests/test_publication.py::test_republish_overwrites_snapshot_and_leaves_audit_without_personal_data`. |
| 7.3 | Snapshot wyników zanonimizowany | ✔ | `build_snapshot`; `CODE` nie niesie imion ani nazwisk. Test: `test_code_snapshot_has_no_personal_data`. |
| 7.4 | k-anonimowość przy `INITIALS_SCHOOL` | ✔ | Poniżej 3 uczestników danej szkoły wiersz spada do `public_code`; `district` wchodzi do snapshotu wyłącznie w trybie `CODE`. Testy: `test_initials_school_falls_back_to_the_code_below_the_k_anonymity_threshold`, `test_district_is_published_only_next_to_pseudonyms`. |
| 7.5 | `FULL` wyłącznie w finale, dla laureatów, za zgodą | ✔ | Testy: `test_full_anonymization_is_rejected_outside_the_final`, `test_full_name_only_for_laureates_of_the_final`, `test_minor_without_guardian_consent_stays_anonymous`, `test_adult_participant_does_not_need_a_guardian`. |
| 7.6 | Zgody zbierane i utrwalone | ✔ | `Participant.gdpr_consent_at`, `guardian_consent`, `publish_full_name`; rejestracja bez zgody → 400 `GDPR_CONSENT_REQUIRED`. Test: `apps/web/tests/test_public.py::test_registration_without_gdpr_consent_shows_domain_error`. |
| 7.7 | Ocenianie ślepe – recenzent nie widzi tożsamości | ✔ | Kontekst szablonów recenzenta niesie wyłącznie `public_code`; `comment_internal` i tożsamość recenzenta nie trafiają do uczestnika. Testy: `apps/web/tests/test_reviewer.py`, `apps/results/tests/…`; dodatkowo asercje w E2E. |
| 7.8 | Usunięcie/anonimizacja konta członka komitetu | ⚠ `low` | `Review.reviewer` i `AppealDecision.committee` mają `PROTECT` – konta nie da się skasować bez utraty śladu oceny. Docelowo anonimizacja konta zamiast usuwania: `BACKLOG.md` (dług T-05). |
| 7.9 | Adres IP w audycie tylko od zaufanego proxy | ✔ | `apps/core/models.py::client_ip` honoruje `X-Real-IP` wyłącznie z `TRUSTED_PROXY_IPS` (domyślnie pusto = sam `REMOTE_ADDR`). Test: `apps/core/tests/test_client_ip.py`. |

## 8. Ograniczanie żądań (throttling)

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 8.1 | Limity na API | ✔ | `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`: `anon 60/min`, `login 10/min`, `register 10/hour`, `upload 30/hour`. |
| 8.2 | Te same limity na formularzach HTML | ✔ | `apps/web/throttle.py::ThrottledFormMixin` czyta stawki z `api_settings` – jedno źródło konfiguracji. Testy: `apps/web/tests/test_throttle.py` (m.in. `test_form_rate_reads_the_same_setting_as_the_api`, `test_changing_the_api_rate_moves_the_form_limit_too`). |
| 8.3 | Dwa kubełki: IP oraz (IP + e-mail); w cache wyłącznie skróty | ✔ | `throttle_keys`, `_digest`. Test: `test_registration_is_throttled_per_client_address`. |
| 8.4 | Logowanie liczy wyłącznie nieudane próby | ✔ | `LoginView.throttle_on_request = False`. Test: `test_successful_login_does_not_consume_the_limit`. |
| 8.5 | Licznik per konto niezależny od IP | ⚠ `med` | Udane logowanie zeruje też kubełek IP (jak `django-axes`), więc rozpylanie haseł z jednego adresu jest tańsze niż mówi stawka. Osobny, dłuższy licznik per konto: `BACKLOG.md` (dług T-08). |
| 8.6 | Enumeracja kont przez `EMAIL_TAKEN` | ⚠ `low`, zaakceptowane | Świadomy kompromis UX + limit 10/h/IP. `BACKLOG.md` (dług T-02). |

## 9. Kontener i sieć

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 9.1 | Użytkownik nie-root, `read_only`, `no-new-privileges`, `cap_drop: ALL` | ✔ | `docker-compose.yml` (`x-app-base`), `backend/Dockerfile` (multi-stage, użytkownik aplikacyjny). |
| 9.2 | Usługi danych poza siecią `edge`, bez portów na hoście | ✔ | `docker-compose.yml`: `db`, `redis`, `clamav`, `minio` wyłącznie w `internal` (`internal: true`). Porty pomocnicze wystawia tylko `docker-compose.dev.yml`. |
| 9.3 | Healthcheck każdej usługi + `depends_on: service_healthy` | ✔ | `docker-compose.yml`. |
| 9.4 | Trusted proxy | ✔ | `TRUSTED_PROXY_IPS` domyślnie zawężone do podsieci compose (`172.30.1.0/24`, `172.30.2.0/24`); Caddy ustawia `X-Real-IP` i `X-Forwarded-Proto`. Patrz 7.9. |
| 9.5 | Publiczny host S3 pod osobną nazwą | ✔ | `deploy/Caddyfile`: `s3.{$SITE_DOMAIN}` → `minio:9000` (podpis SigV4 obejmuje host, więc podścieżka nie wchodzi w grę); bucket `submissions` bez dostępu anonimowego. |

---

## Weryfikacja jednym przebiegiem

```bash
docker compose exec -T web pytest -q            # testy jednostkowe i integracyjne
cd backend && .venv/Scripts/ruff.exe check .    # lint
./scripts/e2e.sh                                # scenariusz E2E na czystym środowisku
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect -s /repo -v
```
