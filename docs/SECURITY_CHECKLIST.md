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

### 3.1 Reset hasła (`/password-reset/`)

Jeden przepływ dla wszystkich ról – model konta jest jeden (`accounts.User`), loginem zawsze jest
adres e-mail. Wszystko poniżej pilnują testy z `apps/web/tests/test_password_reset.py`.

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 3.1.1 | Brak enumeracji kont: adres istniejący i nieistniejący dają tę samą odpowiedź | ✔ | `django.contrib.auth.views.PasswordResetView` zawsze przekierowuje na `/password-reset/sent/`; strona mówi warunkowo („jeśli konto istnieje”). Testy: `::test_unknown_address_looks_exactly_like_a_known_one`, `::test_confirmation_page_speaks_conditionally`, `::test_inactive_account_gets_no_message`. |
| 3.1.2 | Token jednorazowy, ważny 24 h | ✔ | `PASSWORD_RESET_TIMEOUT = 24*3600`; `PasswordResetTokenGenerator` miesza do skrótu hash hasła, więc po zmianie link przestaje działać. Testy: `::test_token_is_single_use`, `::test_made_up_token_shows_the_invalid_link_page`, `::test_reset_timeout_is_a_day`. |
| 3.1.3 | Limit żądań na formularzu wysyłki | ✔ | Scope `password_reset` = `5/hour`, konsumowany przez **każdy** POST (nie tylko nieudany) – bez tego formularz jest wysyłaczem listów na cudze skrzynki. Testy: `::test_sixth_request_within_the_window_is_throttled`, `::test_changing_the_target_address_does_not_dodge_the_limit`. |
| 3.1.4 | Nowe hasło przechodzi przez `AUTH_PASSWORD_VALIDATORS` (min. 10 znaków) | ✔ | `SetPasswordForm` w `PasswordResetConfirmView`. Test: `::test_short_password_is_rejected_by_the_validators`. |
| 3.1.5 | Reset nie loguje automatycznie | ✔ | `post_reset_login = False` – dostęp do cudzej skrzynki pocztowej nie zamienia się jednym kliknięciem w sesję w panelu. Test: `::test_reset_does_not_log_the_user_in`. |
| 3.1.6 | Zmiana hasła w audycie | ✔ | `apps.core.models.audit(user, "password.reset", user, {"via": "email"})` – w `diff` nie ma ani adresu e-mail, ani tokenu. Testy: `::test_successful_reset_is_recorded_in_the_audit_log`, `::test_requesting_a_link_alone_is_not_audited_as_a_password_change`. |
| 3.1.7 | Reset zeruje licznik blokady logowania | ✔ | `apps.web.throttle.reset_for_identity("login", request, user.email)` – inaczej link z listu działa, a logowanie zaraz po nim odbija się o 429. Test: `::test_successful_reset_clears_the_login_lockout`. |
| 3.1.8 | List bez danych osobowych i bez tokenu w temacie | ✔ | `templates/registration/password_reset_*` – poza adresem odbiorcy (i tak w nagłówku `To:`) nie ma imienia, szkoły ani roli; temat to stałe „Reset hasła – `<nazwa serwisu>`”. Testy: `::test_message_carries_a_link_but_no_password_and_no_token_in_the_subject`, `::test_message_has_a_plain_text_and_an_html_part_without_remote_resources`. |
| 3.1.9 | Wersja HTML listu bez zasobów zdalnych | ✔ | Style inline, zero `<img>` i zero adresów CDN – obrazek w liście to potwierdzenie odczytu i wyciek adresu IP czytelnika. Test: `::test_message_has_a_plain_text_and_an_html_part_without_remote_resources`. |
| 3.1.10 | Link `https` za proxy | ✔ | Protokół z `request.is_secure()` + `SECURE_PROXY_SSL_HEADER` (`config/settings/production.py`). Test: `::test_link_uses_https_when_the_request_came_through_the_proxy`. |
| 3.1.11 | Poświadczenia SMTP wyłącznie w `EMAIL_URL` (env) | ✔ | `config/settings/base.py` (`env.email_url`); produkcja loguje ostrzeżenie, gdy `EMAIL_URL` wskazuje `localhost:25` (brak MTA w kontenerze). Wariant domyślny (`smtp://mail:587`, własny Postfix w sieci compose) żadnych poświadczeń nie ma – patrz 10. Konfiguracja: `README.md` § 4.1. |
| 3.1.12 | Wysyłka listu jest synchroniczna w żądaniu | ⚠ `low` | `EMAIL_TIMEOUT=10` ogranicza czas zajęcia workera, ale niedostępny SMTP nadal spowalnia POST `/password-reset/`. Przeniesienie na kolejkę `mail` (trasa jest już w `CELERY_TASK_ROUTES`): `BACKLOG.md`. |

### 3.2 Logowanie przez dostawcę zewnętrznego (`/accounts/…`, Google i Facebook)

Funkcja jest opcjonalna: bez kluczy w środowisku żaden dostawca nie jest skonfigurowany i cała
sekcja sprowadza się do adresów, które nikogo nigdzie nie wpuszczają. Wszystko poniżej pilnują
testy z `apps/web/tests/test_social_login.py`. Konfiguracja: `README.md` § 4.4.

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 3.2.1 | Brak drugiej ścieżki logowania i rejestracji hasłem | ✔ | `allauth.account.urls` **nie** jest montowane – `apps/web/social_urls.py` bierze z allauth wyłącznie uścisk dłoni OAuth i dwa widoki komunikatów; `AccountAdapter.is_open_for_signup` zwraca `False`. Gdyby istniały, byłaby to ścieżka poza `apps.web.throttle` i bez zgody RODO. Testy: `::test_allauth_local_account_views_are_not_mounted` (404 dla `/accounts/login/`, `/accounts/signup/`, `/accounts/`), `::test_our_login_form_is_the_only_password_login`. |
| 3.2.2 | Żadne konto nie powstaje przed zgodą RODO | ✔ | `SOCIALACCOUNT_AUTO_SIGNUP = False` → allauth odsyła na nasz formularz (`/rejestracja/dokoncz/`), a login czeka w sesji; konto zakłada `apps.accounts.services.register_social_participant`, które zaczyna od `_require_gdpr_consent`. `SocialAccountAdapter.save_user` **rzuca wyjątkiem** – bezpiecznik na wypadek włączenia auto-rejestracji. Testy: `::test_new_google_user_is_sent_to_our_signup_form_without_creating_an_account`, `::test_signup_without_gdpr_consent_creates_nothing`. |
| 3.2.3 | Adres e-mail konta pochodzi od dostawcy, nie z formularza | ✔ | `SocialParticipantSignupForm` nie ma pola `email`; serwis bierze adres z `SocialLogin`. Pole edytowalne pozwalałoby założyć konto na cudzy adres i przejąć je resetem hasła. Test: `::test_signup_form_shows_the_provider_email_and_prefills_the_name`. |
| 3.2.4 | Konto społecznościowe nie ma użytecznego hasła | ✔ | `set_unusable_password()`; hasło ustawia się dopiero przez „Nie pamiętasz hasła?”, czyli po potwierdzeniu dostępu do skrzynki i przez `AUTH_PASSWORD_VALIDATORS`. Test: `::test_signup_with_consent_creates_participant_linked_to_the_provider`. |
| 3.2.5 | Automatyczne łączenie z istniejącym kontem **tylko** dla zweryfikowanego adresu z Google | ✔ | `SOCIALACCOUNT_EMAIL_AUTHENTICATION = False` globalnie, `EMAIL_AUTHENTICATION: True` wyłącznie w `SOCIALACCOUNT_PROVIDERS["google"]`; adres jest „zweryfikowany” wtedy i tylko wtedy, gdy Google poda `email_verified` (`VERIFIED_EMAIL: False` – nie ufamy konfiguracji, tylko odpowiedzi dostawcy). Testy: `::test_verified_google_email_connects_to_the_existing_account`, `::test_unverified_google_email_does_not_take_over_an_existing_account`. |
| 3.2.6 | Facebook nigdy nie przejmuje istniejącego konta | ✔ | `VERIFIED_EMAIL: False` **i** `EMAIL_AUTHENTICATION: False` – Facebook nie potwierdza, że adres należy do logującej się osoby. Adres zajęty kończy się stroną „konto istnieje – zaloguj się hasłem albo zresetuj”, a nie połączeniem kont. Testy: `::test_facebook_never_connects_to_an_existing_account_by_email`, `::test_facebook_can_still_create_a_brand_new_account`. |
| 3.2.7 | Konto `is_active=False` nie loguje się przez OAuth | ✔ | Odmowa w `SocialAccountAdapter.pre_social_login`, czyli **przed** powiązaniem konta – własna kontrola allauth (`pre_login`) jest dopiero po `_accept_login`, więc wyłączone konto zdążyłoby zmienić stan. Testy: `::test_inactive_account_cannot_log_in_with_a_linked_provider`, `::test_inactive_account_is_not_connected_by_a_verified_email`. |
| 3.2.8 | Auto-connect czyści hasło konta z niepotwierdzonym adresem | ⚠ świadome (allauth `wipe_password`) | Rejestracji hasłem nie poprzedza weryfikacja adresu, więc ktoś mógł założyć konto na cudzy adres i czekać na właściciela. Po zalogowaniu Google'em hasło napastnika przestaje działać; właściciel ustawia własne przez reset. Cena: uczestnik, który miał hasło i raz zalogował się Google'em, musi je ustawić na nowo. Fakt trafia do audytu. Usunięcie przyczyny (weryfikacja adresu przy rejestracji hasłem) – `BACKLOG.md`. Test: `::test_auto_connect_wipes_the_unverified_accounts_password`. |
| 3.2.9 | Zmiana sposobu logowania w audycie | ✔ | `login.social_connect` (auto-connect, z flagą `password_wiped`) i `account.social_signup` (nowe konto). W `diff` nie ma adresu e-mail ani nazwiska – wyłącznie identyfikator dostawcy. Testy: `::test_signup_is_audited_without_personal_data`, `::test_verified_google_email_connects_to_the_existing_account`. |
| 3.2.10 | Konta komitetu nie powstają przez OAuth | ✔ | Formularz dokończenia rejestracji tworzy wyłącznie profil `Participant` w grupie `participant`; rejestracja recenzenta zostaje na kodzie zaproszenia (5.3). Istniejący członek komitetu może się zalogować Google'em i trafia do swojego panelu. Test: `::test_committee_member_lands_in_the_review_panel`. |
| 3.2.11 | `state` + PKCE, tokeny niezapisywane | ✔ | Parametr `state` trzymany w sesji (allauth `statekit`), `OAUTH_PKCE_ENABLED: True` dla Google – przechwycony kod autoryzacyjny jest bez `code_verifier` bezużyteczny. `SOCIALACCOUNT_STORE_TOKENS = False`: nic nie robimy w imieniu użytkownika, więc token byłby tylko sekretem do wycieku. Odrzucony `state` kończy się stroną błędu bez szczegółów technicznych. |
| 3.2.12 | Uścisk dłoni rusza wyłącznie POST-em z CSRF | ✔ | `SOCIALACCOUNT_LOGIN_ON_GET = False`; przyciski w `templates/web/_social_auth.html` to formularze POST. GET pokazuje wyłącznie stronę potwierdzenia – bez tego obca strona mogłaby zainicjować logowanie (login CSRF). Test: `::test_provider_login_does_nothing_on_get`. |
| 3.2.13 | Cel po zalogowaniu bez otwartego przekierowania | ✔ | `next` przechodzi przez `is_safe_url` allauth; bez `next` decyduje rola (`apps.web.views.public.default_panel_url` – ta sama funkcja, co przy logowaniu hasłem). Testy: `::test_next_parameter_wins_over_the_role_panel`, `::test_open_redirect_through_next_is_rejected`. |
| 3.2.14 | Limit prób na formularzu dokończenia rejestracji | ✔ | `SocialSignupView` ma `ThrottledFormMixin` ze scope'em `register` – tym samym, co rejestracja hasłem (8.2). Sam uścisk dłoni jest ograniczony pośrednio: bez konta u dostawcy nie da się go powtórzyć. |
| 3.2.15 | Brak `SocialApp` w bazie | ✔ | Klucze wchodzą przez `SOCIALACCOUNT_PROVIDERS[...]["APPS"]` ze zmiennych środowiskowych (`config/settings/base.py`), więc sekret nie leży w bazie, nie wychodzi w `pg_dump` i nie jest edytowalny z panelu admina. Pusta zmienna = dostawcy nie ma, a jego adresy zwracają 404 (`apps/web/social_urls.py::only_if_configured` – bez tego allauth kończy `SocialApp.DoesNotExist`, czyli 500 na publicznym adresie). Testy: `::test_login_page_has_no_provider_buttons_without_keys`, `::test_provider_urls_without_keys_are_404_not_500`. |
| 3.2.16 | Brak zasobów obcych na stronach logowania | ✔ | Logotypy dostawców są SVG w szablonie (`templates/web/_social_icon.html`), nie obrazkami z serwerów Google/Meta – żaden z nich nie widzi, kto ogląda stronę logowania. Zero JavaScriptu: CSP `script-src` bez zmian. Test: `::test_pages_of_the_social_flow_keep_the_nonce_only_script_policy`. |
| 3.2.17 | Brak adresu e-mail od dostawcy = brak logowania | ✔ | E-mail jest u nas loginem i jedyną drogą odzyskania konta. `SocialAccountAdapter.pre_social_login` odrzuca login bez adresu stroną z instrukcją. Test: `::test_provider_without_an_email_is_refused`. |
| 3.2.18 | Strony błędów bez szczegółów technicznych | ✔ | `templates/socialaccount/authentication_error.html` nie renderuje `auth_error` (kod błędu, wyjątek); `refused.html` mówi tylko tyle, ile użytkownik i tak wie o własnym koncie. |
| 3.2.19 | Zamontowane tylko dwa endpointy dostawcy | ✔ | `apps/web/social_urls.py::provider_paths` – rozpoczęcie logowania i adres powrotny. Moduły dostawców w allauth dokładają jeszcze `login/token/` (logowanie tokenem z SDK w przeglądarce, np. Google One Tap): endpoint przyjmujący poświadczenia, którego nie używamy i nie testujemy. Test: `::test_login_by_token_endpoint_is_not_mounted`. |

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
| 5.2 | Brak sekretów w obrazie | ✔ | `backend/Dockerfile` nie kopiuje `.env`; wszystkie wartości wchodzą przez `env_file`/`environment` w compose. Skan `gitleaks` (katalog roboczy **i** historia repozytorium) – **bez trafień**. |
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
| 6.7 | Nagłówki bezpieczeństwa na proxy | ✔ | `deploy/Caddyfile`: `Strict-Transport-Security max-age=31536000`, `X-Content-Type-Options nosniff`, `Referrer-Policy same-origin`; limit rozmiaru żądania `MAX_UPLOAD_MB`. Django dokłada `X-Frame-Options` (`XFrameOptionsMiddleware`) i `frame-ancestors 'none'` w CSP. ⚠ `low`: brak `Permissions-Policy` i `Permissions-Policy (COOP jest wysyłany)` – do rozważenia po T-10. |
| 6.8 | Brak `\|safe`/`mark_safe` na treściach od użytkowników | ✔ | `grep -rn "\|safe\|mark_safe" backend/templates` – brak trafień w szablonach `web/` i `cms/`; treści redakcyjne renderują się przez `\|richtext` i `{% include_block %}` (whitelist Wagtaila). |
| 6.9 | Odpowiedź 429 widoczna w interfejsie (także HTMX) | ✔ | `apps/web/throttle.py::throttled_response` (`HX-Retarget`/`HX-Reswap: beforeend`) + `static/js/app.js` (`htmx:beforeSwap`). Testy: `apps/web/tests/test_throttle.py::test_upload_429_is_retargeted_so_htmx_puts_it_in_the_dom`, `::test_upload_429_ignores_a_target_id_that_is_not_a_plain_identifier`. |
| 6.10 | `form-action` zawężone do `'self'` i ekranów zgody włączonych dostawców OAuth | ✔ świadome | `apps/web/middleware.py::form_action_sources` dokłada `https://accounts.google.com` / `https://www.facebook.com` **wyłącznie** dla dostawcy, który ma klucze w środowisku. Powód: przycisk logowania to POST na nasz adres, a odpowiedź jest przekierowaniem 302 na ekran zgody – przeglądarki nie są zgodne co do tego, czy `form-action` obowiązuje dla przekierowań po wysłaniu formularza. Instalacja bez OAuth zostaje przy `form-action 'self'`. Testy: `apps/web/tests/test_social_login.py::test_form_action_lists_only_the_enabled_providers`, `::test_form_action_stays_self_without_oauth`. |

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
| 8.1 | Limity na API | ✔ | `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`: `anon 60/min`, `login 10/min`, `register 10/hour`, `upload 30/hour`, `password_reset 5/hour`. |
| 8.2 | Te same limity na formularzach HTML | ✔ | `apps/web/throttle.py::ThrottledFormMixin` czyta stawki z `api_settings` – jedno źródło konfiguracji. Testy: `apps/web/tests/test_throttle.py` (m.in. `test_form_rate_reads_the_same_setting_as_the_api`, `test_changing_the_api_rate_moves_the_form_limit_too`). |
| 8.3 | Dwa kubełki: IP oraz (IP + e-mail); w cache wyłącznie skróty | ✔ | `throttle_keys`, `_digest`. Test: `test_registration_is_throttled_per_client_address`. |
| 8.4 | Logowanie liczy wyłącznie nieudane próby | ✔ | `LoginView.throttle_on_request = False`. Test: `test_successful_login_does_not_consume_the_limit`. |
| 8.5 | Licznik per konto niezależny od IP | ⚠ `med` | Udane logowanie zeruje też kubełek IP (jak `django-axes`), więc rozpylanie haseł z jednego adresu jest tańsze niż mówi stawka. Osobny, dłuższy licznik per konto: `BACKLOG.md` (dług T-08). |
| 8.6 | Enumeracja kont przez `EMAIL_TAKEN` | ⚠ `low`, zaakceptowane | Świadomy kompromis UX + limit 10/h/IP. `BACKLOG.md` (dług T-02). Reset hasła tej dziury **nie** ma – patrz 3.1.1. |
| 8.7 | Reset hasła: limit konsumowany przez każdy POST | ✔ | Scope `password_reset` = `5/hour`, `throttle_on_request = True` (domyślne). Formularz wysyła list na adres podany przez nadawcę żądania, więc liczenie dopiero „nieudanych” prób nie miałoby sensu. Patrz 3.1.3. |

## 9. Kontener i sieć

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 9.1 | Użytkownik nie-root, `read_only`, `no-new-privileges`, `cap_drop: ALL` | ✔ | `docker-compose.yml` (`x-app-base`), `backend/Dockerfile` (multi-stage, użytkownik aplikacyjny). Wyjątek: `mail` – master Postfiksa startuje jako root i sam zrzuca uprawnienia, więc `cap_drop: ALL` + siedem capabilities zamiast pełnego zrzutu (uzasadnienie w 10.5). |
| 9.2 | Usługi danych poza siecią `edge`, bez portów na hoście | ✔ | `docker-compose.yml`: `db`, `redis`, `clamav`, `minio` wyłącznie w `internal` (`internal: true`). `mail` jest w `edge` **tylko** po to, żeby doręczyć list do MX-a odbiorcy (`internal` nie ma wyjścia na świat) – bez `ports:`, więc z internetu nieosiągalny (10.1). Porty pomocnicze wystawia tylko `docker-compose.dev.yml`. |
| 9.3 | Healthcheck każdej usługi + `depends_on: service_healthy` | ✔ | `docker-compose.yml`. |
| 9.4 | Trusted proxy | ✔ | `TRUSTED_PROXY_IPS` domyślnie zawężone do podsieci compose (`172.30.1.0/24`, `172.30.2.0/24`); Caddy ustawia `X-Real-IP` i `X-Forwarded-Proto`. Patrz 7.9. |
| 9.5 | Publiczny host S3 pod osobną nazwą | ✔ | `deploy/Caddyfile`: `s3.{$SITE_DOMAIN}` → `minio:9000` (podpis SigV4 obejmuje host, więc podścieżka nie wchodzi w grę); bucket `submissions` bez dostępu anonimowego. |

---

## 10. Poczta wychodząca (usługa `mail`)

Własny Postfix zamiast zewnętrznego dostawcy: relay stoi w sieci compose i doręcza listy wprost do
serwerów MX odbiorców. Konfiguracja i weryfikacja: `README.md` § 4.1–4.3.

| # | Pozycja | Status | Gdzie / czym sprawdzone |
|---|---|---|---|
| 10.1 | Relay nieosiągalny spoza sieci compose | ✔ | `docker-compose.yml`, usługa `mail` bez `ports:` – Docker nie mapuje 587 na host. Sprawdzenie na serwerze: `ss -lntp \| grep -E ':(25\|587)'` nie zwraca nic, `nc -vz <publiczne-IP> 587` → `Connection refused`. |
| 10.2 | Brak open relaya: obcy nadawca odrzucony | ✔ | `ALLOWED_SENDER_DOMAINS=${SITE_DOMAIN}` → `smtpd_recipient_restrictions = … check_sender_access lmdb:/etc/postfix/allowed_senders, reject`. Sprawdzenie z kontenera `web`: `MAIL FROM:<spam@evil.example>` + `RCPT TO:<ktos@obca.domena>` → `554 5.7.1 … Recipient address rejected: Access denied`; ten sam `RCPT` po `MAIL FROM:<noreply@<domena>>` → `250 2.1.5 Ok`. |
| 10.3 | Brak open relaya: obcy klient odrzucony | ✔ | `POSTFIX_mynetworks = 127.0.0.0/8` + podsieci compose → `smtpd_client_restrictions = permit_mynetworks,permit_sasl_authenticated,reject`. Poza tymi podsieciami połączenie kończy się odmową jeszcze przed `MAIL FROM` (a z internetu nie ma jak go nawiązać – 10.1). |
| 10.4 | Podpis DKIM na każdym wychodzącym liście | ✔ | `DKIM_AUTOGENERATE=true`, `DKIM_SELECTOR=olimpiada`, klucz RSA-2048 na wolumenie `mail_dkim` (`/etc/opendkim/keys/<domena>.private`, `chmod 400`, `opendkim:opendkim`). W logu na każdą wiadomość: `opendkim[…]: <id>: DKIM-Signature field added (s=olimpiada, d=<domena>)`. Klucz **nie** jest w repozytorium ani w obrazie – powstaje przy pierwszym starcie usługi. |
| 10.5 | Minimalne capabilities kontenera | ✔ | `cap_drop: [ALL]` + `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`, `SYS_CHROOT`, `KILL` (master zrzuca uprawnienia do `postfix`/`opendkim`, pilnuje właściciela kolejki i wchodzi do chroota `/var/spool/postfix`), `security_opt: no-new-privileges:true`. `NET_BIND_SERVICE` **nie** jest potrzebne – obraz nasłuchuje na 587, nie na 25. |
| 10.6 | TLS do serwera odbiorcy | ⚠ `low` | `smtp_tls_security_level=may` (oportunistyczne STARTTLS): szyfrowanie, gdy odbiorca je ogłosi, bez weryfikacji certyfikatu (`Untrusted TLS connection established … TLSv1.3` w logu). Świadome: `encrypt`/`verify` odcięłoby odbiorców z niepoprawnym TLS-em, a listy resetu hasła nie mogą przepadać. Poufność treści opiera się na tym, że list nie zawiera hasła – tylko jednorazowy token 24 h (3.1.2, 3.1.8). |
| 10.7 | SPF / DKIM / DMARC / PTR w DNS | ⚠ – **do zrobienia po stronie operatora strefy** | Rekordy wypisuje `scripts/deploy.sh` (krok 7/7) i zapisuje do `<REMOTE_DIR>/mail-dns.txt`; tabela w `README.md` § 4.2. Do czasu ich dodania listy dochodzą, ale bez uwierzytelnienia – trafiają do spamu, a część odbiorców je odrzuci. PTR (`<IP>` → `mail.<domena>`) ustawia się w panelu dostawcy serwera, nie w strefie. |
| 10.8 | Rozmiar wiadomości ograniczony | ✔ | `POSTFIX_message_size_limit=10485760` (obraz domyślnie nie ma limitu); aplikacja wysyła wyłącznie krótkie listy transakcyjne. |
| 10.9 | Klucz DKIM przeżywa restart | ✔ | Wolumen `mail_dkim:/etc/opendkim/keys`; przy kolejnym starcie w logu `Key for domain <domena> already exists … Will not overwrite.` Inaczej każdy `up -d --force-recreate` unieważniałby rekord TXT w DNS-ie. |
| 10.10 | Poczta nie blokuje startu aplikacji | ✔ | `web`/`worker`/`beat` **nie** mają `depends_on` na `mail`; awaria relaya psuje reset hasła, ale nie serwis. `EMAIL_TIMEOUT=10` ogranicza czas zajęcia workera (3.1.12). |

---

## Weryfikacja jednym przebiegiem

```bash
docker compose exec -T web pytest -q            # testy jednostkowe i integracyjne
cd backend && .venv/Scripts/ruff.exe check .    # lint
./scripts/e2e.sh                                # scenariusz E2E na czystym środowisku
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest detect -s /repo -v
```

Poczta (na serwerze produkcyjnym, `docker-compose.yml` bez nakładki `dev`):

```bash
nc -vz "$(hostname -I | awk '{print $1}')" 587        # musi odmówić – relay nie jest publikowany
docker compose logs mail --tail 30                    # DKIM-Signature field added + status=sent
docker compose exec mail postqueue -p                 # pusta kolejka = nic nie utknęło
```
