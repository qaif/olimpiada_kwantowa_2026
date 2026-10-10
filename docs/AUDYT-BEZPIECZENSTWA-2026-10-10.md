# Audyt bezpieczeństwa – 10.10.2026 (gałąź `feature/motywy`)

Audyt wyłącznie z odczytu kodu i konfiguracji w repozytorium. Nie łączono się z produkcją, nie
uruchamiano testów ani exploitów. Zakres: backend (Django + DRF + Wagtail), djcms, pliki compose,
Caddy, skrypty wdrożeniowe, motywy. Pozycje już opisane w `SECURITY_CHECKLIST.md` jako świadomy
dług (DNS rebinding webhooków, S3 na :9000, token DRF bez TTL itd.) pominięto, chyba że okazały się
nieaktualne.

Każde ustalenie wysokie i większość średnich zweryfikowano ręcznie w kodzie (oznaczenie ✔). Przy
pozycjach, których nie da się potwierdzić bez serwera, napisano, co to potwierdzi.

**Krótko:** krytycznych luk zdalnych (RCE, SQLi, SSTI, IDOR „podmień id”) nie ma. Platforma jest
w tych obszarach wyjątkowo szczelna. Realne luki są w czterech miejscach: (1) buforowanie `/status/`
dla zalogowanych, (2) dokumenty Wagtaila z ograniczoną widocznością dostępne anonimowo z bucketu,
(3) operacje koordynatora na **globalnym** koncie użytkownika (kilka dróg przejęcia konta, groźne od
chwili uruchomienia drugiego konkursu), (4) maszyna stanów oceniania/wyników/testu online. Do tego
kilka spraw infrastrukturalnych (cały `.env` w kontenerach aplikacji, slow-POST, brak lockfile).

---

## 1. WYSOKIE – naprawić przed następnym wdrożeniem

### W1. `/status/` buforuje stronę wyrenderowaną dla zalogowanego i oddaje ją wszystkim ✔
- **Gdzie:** `backend/apps/web/views/status.py:33` – `@method_decorator(cache_page(30))` na
  `StatusView`; szablon `web/status.html` dziedziczy po `base.html`, który wypisuje
  `request.user.email` (`templates/theme/header.html:89`), linki ról, komunikaty flash i
  `{{ csrf_token }}` w `hx-headers`.
- **Mechanizm:** `cache_page` jako dekorator widoku liczy klucz (`learn_cache_key`) z **nierenderowanej**
  `TemplateResponse`, zanim `SessionMiddleware`/`CsrfViewMiddleware` dołożą `Vary: Cookie`. Klucz zależy
  więc tylko od adresu i języka. Dodatkowo `Cache-Control: max-age=30` bez `private` – stronę zapisze
  też proxy po drodze. `PageCacheMiddleware` tego nie łapie (obsługuje wyłącznie anonimów i inne ścieżki).
- **Atak:** anonim odpytuje `GET /status/` co ~30 s i dostaje HTML wyrenderowany dla ostatniego
  zalogowanego, który wszedł na stronę (e-mail, rola, flash, token CSRF). Strona statusu jest linkowana
  właśnie przed terminami, czyli przy największym ruchu zalogowanych.
- **Naprawa:** zdjąć `cache_page` z `StatusView`; buforować **dane**, nie odpowiedź
  (`cache.get_or_set(f"status:snapshot:{competition_id}", build_snapshot, 30)`) i renderować na żywo.
  `StatusJsonView` może zostać przy `cache_page` (nie dziedziczy po `base.html`).
  Test: `force_login(A)` → `GET /status/`; potem klient anonimowy → `A.email not in content`.

### W2. Dokumenty Wagtaila z kolekcji „tylko zalogowani”/„z hasłem” do pobrania anonimowo z bucketu ✔
- **Gdzie:** Wagtail zapisuje dokument pod `documents/<oryginalna nazwa>` (`wagtail/documents/models.py:28`,
  brak własnego modelu dokumentu w projekcie); `production.py` → `default` storage = bucket `public-media`
  z anonimowym `s3:GetObject`; `file_overwrite=False` dokleja sufiks tylko przy kolizji.
- **Atak:** `/documents/<id>/<nazwa>` sprawdza ograniczenia kolekcji, ale ten sam plik leży pod
  `https://<domena>:9000/public-media/documents/<nazwa>` – adres bez żadnej kontroli, bezterminowy,
  działa też po odebraniu dostępu, a nazwy typu `regulamin.pdf` da się zgadnąć. Ograniczenie
  widoczności kolekcji nie chroni niczego.
- **Naprawa:** własny model dokumentu (`WAGTAILDOCS_DOCUMENT_MODEL`) z `storage=private_media`
  (serwowanie i tak idzie przez `serve_view` → `apps/cms/views.py`). Minimum: `upload_to` z prefiksem
  `uuid4` (nieodgadywalny klucz) i przenoszenie obiektu do prywatnego storage w chwili nałożenia
  ograniczenia na kolekcję. Dopisać pozycję do checklisty § 2/§ 9.

### W3. Koordynator przejmuje konto, które ma rolę także w innym konkursie ✔
- **Gdzie:** `backend/apps/web/views/coordinator_accounts.py:285` (`users_for_competition` – ukrywa
  tylko konta należące **wyłącznie** do innego konkursu); `apps/accounts/profile.py:699`
  (`_assert_not_coordinator` chroni tylko koordynatorów/superusera); `update_account_by_coordinator`
  (`profile.py:790`) zmienia `email` i `is_active` **globalnie, bez potwierdzenia**;
  `delete_account_by_coordinator` (`:888`) anonimizuje profile we wszystkich konkursach;
  `CoordinatorTwoFactorResetView` (`:774`) i `CoordinatorPasswordResetView` (`:812`) idą tą samą drogą.
- **Atak:** nauczyciel jest opiekunem w konkursie B i recenzentem w A (decyzja D4 przewiduje wspólne
  konto). Koordynator B (złośliwy albo z przejętą sesją): zmienia e-mail na swój → „Nie pamiętasz
  hasła?” → loguje się jako recenzent A (anonimowe prace, oceny). Może też zdjąć 2FA, zablokować konto
  w A, usunąć je kaskadą. To samo dotyczy kont „niczyich” (bez `Membership`, sprzed backfillu) – widzi
  je każdy koordynator.
- **Naprawa:** jeden strażnik `active_elsewhere(user, competition)` (`Membership`, `Participant`,
  `CommitteeMember`, `SchoolSupervisor`, `DelegationLeader` w innym konkursie), wołany przez edycję,
  blokadę, reset hasła, reset 2FA, usunięcie i eksport. Dla takich kont: odmowa (`ACCOUNT_SHARED`)
  i zamiast tego operacja „wypisz z tego konkursu” (tylko profil + członkostwo). Zmiana e-maila przez
  koordynatora – wyłącznie drogą z potwierdzeniem (`request_email_change`). Testy negatywne.

### W4. Koordynator wyłącza `memberships_enforced` z panelu – role z globalnych grup, potem awaria wdrożenia ✔
- **Gdzie:** `backend/apps/web/competition_forms.py:78` – flaga w `EDITABLE_FLAGS`; zapis
  w `coordinator_competition.py:68`; brak odmowy w `Competition.clean`.
- **Atak** (≥ 2 aktywne konkursy): koordynator A wysyła `POST /coordinator/competition/` bez
  `flag_memberships_enforced`. Od tej chwili w A o roli rozstrzyga grupa Django, więc każdy
  koordynator/recenzent/komisja **dowolnego** konkursu ma tę rolę w A. Przy następnym wdrożeniu
  `migrate` zatrzymuje się na `tenancy.E001` (`apps/tenancy/checks.py`), `entrypoint.sh` ma `set -eu`
  – kontener `web` nie wstaje dla wszystkich konkursów.
- **Dokumentacja:** `SECURITY_CHECKLIST.md` 3.3.10 twierdzi, że ten stan jest „niemożliwy drogą panelu”
  – nieprawda; test `test_flipping_memberships_enforced_is_allowed_and_audited` sprawdza tylko jeden konkurs.
- **Naprawa:** z panelu wyłącznie **włączanie**; w `clean()` odmowa wyłączenia, gdy istnieje inny
  aktywny konkurs (ta sama reguła co `_refuse_next_to_unscoped_roles`). Poprawić 3.3.10.

### W5. `/cms/account/` (Wagtail) zmienia e-mail i hasło konta bez potwierdzenia, limitu i audytu ✔
- **Gdzie:** `backend/config/settings/base.py:1019` wyłącza tylko `WAGTAIL_PASSWORD_RESET_ENABLED`;
  `WAGTAIL_EMAIL_MANAGEMENT_ENABLED` i `WAGTAIL_PASSWORD_MANAGEMENT_ENABLED` zostają domyślne (`True`).
- **Atak:** każdy z `wagtailadmin.access_admin` (koordynator, redaktor). Przejęta sesja (porzucony
  komputer, XSS w `/cms/` – tam CSP ma z założenia `unsafe-inline`) → wpis własnego adresu na
  `/cms/account/` → natychmiastowa zmiana → reset hasła → trwałe przejęcie konta koordynatora. Ta
  droga omija całą logikę `request_email_change`/`confirm_email_change` (potwierdzenie na nowym
  adresie, list na stary, audyt `account.email_changed`, czyszczenie `allauth.EmailAddress` – stary
  adres zostaje „zweryfikowany”, więc kto przejmie starą skrzynkę jako konto Google, wejdzie przez
  automatyczne łączenie). Zmiana hasła w Wagtailu: bez limitu prób na stare hasło, bez audytu,
  token DRF zostaje.
- **Naprawa:** `WAGTAIL_EMAIL_MANAGEMENT_ENABLED = False`, `WAGTAIL_PASSWORD_MANAGEMENT_ENABLED = False`
  w `base.py`. Test: `GET /cms/account/` bez pola `name_email-email`; `POST` z tym polem nie zmienia `User.email`.

### W6. Kontenery aplikacji dostają cały `.env`: root MinIO, hasło kopii, klucze magazynu kopii ✔
- **Gdzie:** `docker-compose.yml:53` – `x-app-base: env_file: .env` (web, worker, beat).
  Aplikacja nie czyta `BACKUP_PASSPHRASE`, `BACKUP_ACCESS_KEY/SECRET_KEY`, `BACKUP_DRIVE_CLIENT_SECRET`,
  `DJCMS_DB_PASSWORD`, `DJCMS_SECRET_KEY`, `MAINTENANCE_BYPASS_TOKEN`, a `MINIO_ROOT_*` używa tylko
  jako fallbacku – mimo to wszystko jest w `/proc/self/environ` procesu `web`. Usługa `djcms` ma już
  jawną listę `environment:` – to wzorzec do skopiowania.
- **Skutek:** `web` parsuje PDF/XLSX/obrazy/notebooki od użytkowników. Jedno RCE albo odczyt
  środowiska = konto root MinIO (upublicznienie/kasacja `submissions`, obejście rozdziału kont z 5.5),
  hasło szyfrowania kopii i klucze z prawem `DeleteObject` do magazynu poza serwerem. Pełny scenariusz
  „ransomware z usunięciem kopii”.
- **Naprawa:** jawna lista `environment:` w `x-app-base`; zdjąć wymóg `MINIO_ROOT_*` w `production.py`,
  gdy ustawione są oba konta `S3_PUBLIC_*`/`S3_PRIVATE_*`; kopie poza serwer kluczem **bez**
  `DeleteObject`, retencja regułą lifecycle/object-lock po stronie magazynu; opcjonalnie `.env.ops`
  czytany tylko przez skrypty hosta.

---

## 2. ŚREDNIE

### Logika domenowa (oceny, wyniki, test, konta)

**S1. Anulowany przydział (`Review.CANCELLED`) nadal daje recenzentowi dostęp do pliku, cudzych ocen i notatek ✔**
`apps/submissions/models.py:112` (`Q(reviews__reviewer=reviewer)` bez statusu), `grading/services.py:1913`
(`reviews_for_reviewer`), `grading/api.py:88`, `grading/comparison.py:96` i `dispute_context`
(`services.py:2206`). Po `POST /api/grading/reviews/<id>/unassign/` (np. konflikt interesów) recenzent
dalej pobiera plik (`/api/submissions/<sid>/download/`), otwiera `/review/<id>/`, `/compare/` (punkty
i komentarze innych) i `/dispute/`. `build_reviewer_zip` i `can_read_notes` już wykluczają CANCELLED –
reguła istnieje, nie jest stosowana wszędzie. **Naprawa:** w `for_user` podzapytanie
`Exists(Review.objects.filter(submission=OuterRef("pk"), reviewer=r).exclude(status=CANCELLED))`;
odmowa w `comparison_context`/`dispute_context`; `ReviewDetailView` bez pliku dla wycofanych.

**S2. Wycofane ogłoszenie wyników nadal widoczne ✔**
Wycofanie = wyczyszczenie `Stage.results_published_at` (`apps/cms/live_data.py:137`), rekord
`ResultsPublication` zostaje jako ślad. Tymczasem `apps/results/api.py:44` (`GET /api/public/results/<stage_id>/`,
AllowAny), `web/views/public.py:428` (`/results/<id>/`), `web/views/statistics.py:36`, `results/feedback.py:238`
(feedback uczestnika + eksport RODO) i `ai_grading/services.py:1535,1597` sprawdzają tylko istnienie
rekordu. Anonim przechodzi po `stage_id` i widzi tabelę wycofaną np. z powodu złego trybu anonimizacji.
**Naprawa:** jeden predykat `ResultsPublication.objects.live()` = `stage__results_published_at__isnull=False`
we wszystkich ścieżkach; unieważnienie cache statystyk przy wycofaniu; testy 404.

**S3. Test online ignoruje ręczne „Zamknij etap” ✔**
`apps/quiz/models.py:246` (`Quiz.window`) i `services.py:414,522` nie znają `stage.closed_at` (ani jedno
odwołanie w `apps/quiz/`). Po `POST /coordinator/stages/<id>/close/` (np. wyciek pytań) uczestnik dalej
zaczyna i kończy podejście, które wchodzi do `stage_scores`; upload plików w tej samej sytuacji
odmawia (`submissions/services.py:94`). `closes_at` nie jest ograniczone do `deadline_at`, a publikacja
wyników nie blokuje nowych podejść. **Naprawa:** `closed_at is None` w `is_open`/`accepts_answers_at`
pod blokadą; `close_stage_now` domyka trwające podejścia; walidacja `closes_at ≤ deadline_at`; odmowa
startu po `ResultsPublication`.

**S4. Recenzent rundy 1 „zgaduje” ocenę drugiego recenzenta i omija moderację**
`grading/serializers.py:33` wystawia `submission_status`; `revise_review` (`services.py:1457`) jest
dozwolone w stanie MODERATION bez limitu poprawek. Po `submit` recenzent widzi MODERATION vs
GRADED_PROVISIONAL (= czy zgadza się z drugą oceną) i w ≤ 3 próbach `revise` na skali 0/2/5/6 trafia
w konsensus, co anuluje trzeciego recenzenta. Testy `test_review_revision.py` traktują to jako
zamierzone. **Naprawa:** neutralny status dla rundy 1 (`REVIEW_SUBMITTED`); blokada `revise` w MODERATION
albo przy aktywnym przydziale rundy 2 (dalej tylko koordynator); limit poprawek.

**S5. Zablokowane konto odblokowuje się samo linkiem aktywacyjnym ✔**
`apps/accounts/activation.py:545` (`resend_activation` wysyła link każdemu z `email_verified_at IS NULL`),
`:526` (`activate_with_token` sprawdza tylko `email_verified_at`), `:485` (`mark_activated` ustawia
`is_active=True`). Blokada przez koordynatora to wyłącznie `is_active=False` – nieodróżnialna od
„czeka na aktywację”. Dotyczy kont zablokowanych przed `accounts.0010` (migracja świadomie zostawia
`NULL`, a kosiarka omija konta z dokumentacją zawodów – czyli akurat uczestników zablokowanych np. za
ściąganie) i kont z `/admin/`/`createsuperuser`. **Naprawa:** osobne `blocked_at` ustawiane przy
blokadzie z panelu/admina; `mark_activated`, `resend_activation`, `accept_invitation` odmawiają.
Skala na prod: `User.objects.filter(is_active=False, email_verified_at__isnull=True, last_login__isnull=False).count()`.

**S6. Link aktywacyjny omija przyjęcie zaproszenia (hasło, regulamin, RODO, zgoda opiekuna)**
Ta sama ścieżka co S5 dla kont zaproszonych (delegacje, import): `activate_with_token` ustawia
`is_active` bez `ConsentRecord`; potem logowanie Google (`AUTO_CONNECT`, `_check_existing` sprawdza
tylko `is_active`) i uczestnik jest w panelu bez zgód, a `register_for_stage` zgód nie sprawdza.
Nie jest to przejęcie przez osobę trzecią – to luka w regule „nikt nie zostaje uczestnikiem bez zgód”.
**Naprawa:** dla kont z `invited_at` i bez używalnego hasła `resend` wysyła ponownie **zaproszenie**;
odmowa w `activate_with_token`; opcjonalnie bramka zgód w `register_for_stage`.

**S7. Reset 2FA kont chronionych przez dowolnego koordynatora**
`coordinator_accounts.py:774` i `twofactor.py:434` (`reset_by_coordinator`) bez `is_protected` i bez
`pk == request.user.pk`. Zasięg: superuser bez `Membership` (konto „niczyje”), drugi koordynator tego
samego konkursu, superkoordynator. Razem z wyciekiem hasła = przejęcie konta operatora. To samo
`CoordinatorPasswordResetView` (`:812`) wysyła list resetu na konto chronione. **Naprawa:** bramka
`is_protected`/własne konto w widoku i serwisie.

**S8. Eksport RODO przez koordynatora wydaje dane z innych konkursów**
`coordinator_accounts.py:750` → `data_export.py::export_payload` (`:370`): wpisy forum ze wszystkich
konkursów (`:458`), **treść wiadomości czatu** także z organizatorem B (`:498`), profile komitetu/opiekuna;
działa też dla kont chronionych. **Naprawa:** `build_export_zip(user, competition=request.competition)`
na ścieżce koordynatora; odmowa dla `is_protected`.

**S9. Uczeń delegacji przepisuje się do innego kraju ✔**
`profile.py:125` (`_participant_values`) przyjmuje `district` z `/me/profile/` (`web/forms.py:1055`)
i `PATCH /api/auth/me/`. Uczeń delegacji DE wysyła `district=fr`: kraj w wynikach, liczniki regionów
i reguła konfliktu interesów (etap DISTRICT) liczą FR, a `delegation` zostaje DE. **Naprawa:** przy
`delegation_id`/`former_delegation_id` ignorować/odrzucać `district`; zdjąć pole z formularza i API.

**S10. Tryb publikacji `INITIALS_SCHOOL` pomija zgody**
`results/services.py:1500`: `_may_show_full_name` obejmuje tylko tryby imienne; dla inicjałów ze szkołą
jedyną bramką jest `MIN_SCHOOL_GROUP = 3`. „J.K., XIV LO” z punktami identyfikuje 13-latka bez zgody
rodzica. **Naprawa:** te same warunki zgód co w trybach imiennych (bez zgody → kod). Decyzja organizatora/IOD.

**S11. Samoobsługowa zmiana e-maila i wyłączenie 2FA bez hasła; brak unieważnienia sesji**
`web/views/account.py:201` + `profile.py:266`, `web/views/twofactor.py:209`. Przejęta sesja → nowy
adres napastnika → potwierdzenie na **jego** skrzynce → reset hasła. Stary adres dostaje list po fakcie,
bez linku anulowania; `confirm_email_change` nie kasuje tokena DRF ani innych sesji. **Naprawa:**
bieżące hasło w `EmailChangeForm` i przy wyłączaniu 2FA (konta bez hasła: przepisanie adresu, jak przy
usuwaniu konta); list na stary adres z linkiem anulowania; e-mail w `get_session_auth_hash` + usunięcie
tokena DRF po zmianie.

**S12. `POST /supervisor/students/<pk>/resend/` bez limitu ✔**
`web/views/supervisor.py:500` (bez `ThrottledFormMixin`), `bulk_registration.py:1429` (bez karencji).
Przy otwartej rejestracji opiekunów: import 500 dowolnych adresów, potem nieograniczony resend –
wysyłacz listów z domeny organizatora na cudze skrzynki, w trakcie blokady Microsoft. **Naprawa:**
scope throttle + karencja 1 h na `invitation_sent_at`.

**S13 (warunkowe). Host bez aktywnego konkursu → `request.competition=None` → role z globalnych grup**
`tenancy/resolution.py:~157` (witryna nieaktywnego konkursu/alias bez tłumaczeń daje `Resolution(None)`;
`platform_subdomain_miss` daje 404 tylko dla subdomen platformy, nie dla `EXTRA_DOMAINS`);
`memberships_enforced(None)=False` (`accounts/services.py:673`); `participant_ids(None)` i
`users_for_competition(None)` zwracają konta wszystkich konkursów. Koordynator B logowany pod domeną
zdezaktywowanego konkursu A działa na kontach wszystkich (zmiana e-maila → reset hasła → przejęcie,
także konta małoletniego). Docstring `provisioning.py:540` („pod adresem nieaktywnego konkursu nikt nie
dostaje paneli”) jest nieprawdziwy. **Do potwierdzenia:** czy taka domena jest (albo będzie) kierowana
przez Caddy do `web`. **Naprawa:** 404 w middleware dla hosta z nieaktywnym konkursem; `RoleRequiredMixin`/
`IsCompetitionCoordinator` → 404 bez konkursu zamiast odwrotu do grup; `users_for_competition(None)` → `none()`.

### Pliki i zasoby

**S14. Bomba ZIP w imporcie XLSX opiekuna kładzie kontener `web` ✔**
`bulk_registration.py:462-512`: limit 2 MB tylko na rozmiar **skompresowany**; `openpyxl` nawet w
`read_only` ładuje cały `sharedStrings.xml` do pamięci, a `MAX_ROWS` działa po wczytaniu. Rejestracja
opiekuna otwarta, import nie wymaga `verified`. 2 MB `.xlsx` (deflate ~1000:1) = ~2 GB XML → OOM
(`mem_limit: 2g`, 4 procesy), powtarzalne. **Naprawa:** przed `load_workbook` przejść `ZipFile.infolist()`
i odrzucić, gdy suma `file_size` > ~20 MB albo stosunek > ~100; przerwać `iter_rows` po `MAX_ROWS+1`;
wymagać zweryfikowanego opiekuna lub throttle. Ten sam wzorzec sprawdzić w `apps/schools/sio.py`.

**S15. Pełny `/tmp` (tmpfs 256 MB) blokuje wgrywanie prac – i może to wywołać uczestnik**
`docker-compose.yml:61` (`/tmp:size=256m` wspólne dla web/worker/beat); `base.py:1361` (upload > 2 MB
→ `/tmp`); paczki ZIP w tym samym `/tmp` (`accounts/data_export.py:721`, `submissions/packaging.py:147`,
`results/certificates.py:1097`). Uczestnik wgrywa ~13 wersji po 20 MB (limit 30/h, `DEFAULT_MAX_FILE_MB=20`),
potem `/account/export/` buduje ZIP ze wszystkich wersji > 256 MB → ENOSPC; nieudany eksport nie zapala
licznika, a sprawdzenie i zapis nie są atomowe. W tym czasie każdy cudzy upload > 2 MB daje 500 – tuż
przed terminem. Bez złej woli: paczka etapu/recenzenta > 256 MB nigdy nie powstanie. **Naprawa:** osobny
dyskowy katalog na paczki (`dir=` w `TemporaryFile`), limit sumy `size_bytes` przed budową, docelowo
paczki w Celery → S3 → presigned URL; w eksporcie `cache.add()` przed budową i tylko najnowsza wersja pliku.

### Infrastruktura

**S16. Slow-POST blokuje cały serwis ✔**
`deploy/Caddyfile` bez `servers { timeouts { read_body … } }` i `reverse_proxy` bez `request_buffers`
(także w blokach z `scripts/render_caddyfile.sh:452,503`); gunicorn gthread 4×4 wątki, `--timeout` nie
przerywa wątku czekającego na gnieździe; `CsrfViewMiddleware` czyta `request.POST`. ~16 połączeń
`POST` z `Content-Length: 25 MB` i bajtem co kilka sekund = serwis stoi (np. w ostatniej godzinie przed
terminem). djcms (3×4) tak samo. **Naprawa:** `request_buffers 26MB` w `reverse_proxy` do web/djcms;
`servers { timeouts { read_header 15s read_body 300s } }`; opcjonalnie limit połączeń per IP (nftables).

**S17. Kopie i odtwarzanie przestają działać po kroku z dokumentacji ✔ (sprawdzone w bashu)**
`scripts/backup.sh:84`, `backup_verify.sh:52`, `restore.sh:81` robią `. ./.env` pod `set -euo pipefail`.
README (`:318`), `OPERACJE.md:929` i `.env.example:171` każą wpisać wartości ze spacją bez cudzysłowu
(`EXTRA_DOMAINS=a.pl www.a.pl`, `CERT_SIGN_REASON=Dokument wystawiony przez …`) → `command not found`,
kod 127, od tej nocy brak kopii, `restore.sh` pada w czasie awarii. Do tego `.env` jest **wykonywany**
jako kod roota z crona (`$(…)` w wartości = wykonanie polecenia). **Naprawa:** czytać `.env` przez
`env_get` (wzorzec z `maintenance.sh`/`render_caddyfile.sh`) albo `docker compose config --format json`;
przypadek z wartością ze spacją w `backup_offsite_test.sh`.

**S18. `/admin/` i `/cms/` z całego internetu, koordynator = superuser, 2FA wyłączone**
`bootstrap_coordinator.py:45` (`is_superuser=True`), `TWO_FACTOR_ENABLED=0` (`base.py:648`), Caddy bez
reguły IP. Limit 10/min/IP na `/login/` **plus** osobne 10/min/IP na `/api/auth/login/` (inny cache),
brak licznika per konto, udane logowanie zeruje kubełek. Phishing/credential stuffing na jedno konto
= pełne dane osobowe niepełnoletnich + konfiguracja. **Naprawa:** `TWO_FACTOR_ENABLED=1`,
`TWO_FACTOR_REQUIRED_ROLES=coordinator,super_coordinator`, w `is_required_for` dodać `or user.is_superuser`
(dziś superuser bez grupy nie jest objęty); koordynator bez `is_superuser`; w Caddy `@admin path /admin/* /cms/*`
+ `remote_ip` listy organizatora lub `basic_auth`; API logowania na wspólne kubełki z `apps/web/throttle`.

**S19. Podrzucanie ciasteczek z sąsiednich subdomen (średnie przy `PLATFORM_SUBDOMAINS=1`, niskie bez)**
`base.py:948-951` dodaje `CSRF_TRUSTED_ORIGINS https://*.<domena>` (obejmuje `meet.`, `monitor.`, `dj.`,
`s3.`); ciasteczka bez prefiksu `__Host-`; `SameSite=Lax` nie chroni między subdomenami. XSS na
dowolnej subdomenie (najpewniej cudzy kod: Jitsi, Uptime Kuma) ustawia `csrftoken` z `Domain=.<domena>`
→ CSRF przechodzi; bez wildcardu zostaje podmiana `sessionid` (ofiara zalogowana na konto napastnika
wgrywa tam pracę). **Naprawa:** `SESSION_COOKIE_NAME="__Host-sessionid"`, `CSRF_COOKIE_NAME="__Host-csrftoken"`
(warunki spełnione: Secure, Path=/, bez Domain); zamiast wildcardu `Origin` z listy hostów aktywnych konkursów.

**S20. Izolacja djcms ma dziury: dostaje ciasteczka sesji web i jest „zaufany” dla web i Postfiksa ✔**
`scripts/render_caddyfile.sh:252-274`: przy `reverse_proxy djcms:8000` zdejmowany jest tylko `X-Djcms-Mode`,
`Cookie` idzie w całości – od DJ-02 djcms stoi na tym samym hoście i `/`, więc dostaje `sessionid`
i `csrftoken` aplikacji głównej każdego zalogowanego (także superadmina) przy każdej odsłonie publicznej.
`docker-compose.yml:38` `TRUSTED_PROXY_IPS=172.30.1.0/24,172.30.2.0/24` obejmuje djcms, monitor, minio,
clamav, mail, a `deploy/jitsi/docker-compose.jitsi.yml:75,187` dołącza `jitsi-web` do `edge`; Postfix
`mynetworks` = ta sama lista. RCE w djcms (Pillow/easy-thumbnails na obrazach redaktorów) albo w jitsi-web
(root, bez `cap_drop`) = zbieranie sesji adminów web, podrobione `X-Real-IP` (obejście limitów, fałszywy
audyt), phishing z prawdziwym DKIM. Unieważnia regułę 11 („djcms bez sekretów web”). **Naprawa:**
w Caddy filtr ciasteczek do djcms (zostawić wyłącznie `djcms_*`); stały `ipv4_address` dla `proxy`
i `TRUSTED_PROXY_IPS` = ten adres/32 (jak już dla djcms); osobna sieć `mail` (web/worker/beat) i
`mynetworks` tylko na nią; Jitsi w osobnej sieci tylko z `proxy`.

**S21. Brak pliku blokady zależności backendu, dev-zależności i uvicorn w obrazie produkcyjnym**
`backend/Dockerfile:7-9`: `uv pip install -r pyproject.toml --extra dev`, brak `backend/uv.lock`;
`djcms/Dockerfile:34`: `uv sync --frozen --extra dev`; builder `ghcr.io/astral-sh/uv:0.4` (pływający,
stary). Każdy build bierze najnowsze wersje w zakresach, zależności przechodnie bez granic – skompromitowane
wydanie na PyPI wchodzi na prod przy następnym buildzie; pytest/factory-boy/ruff/mypy w runtime.
**Naprawa:** `uv lock` + `uv sync --frozen --no-dev`; dev osobnym targetem; usunąć `uvicorn[standard]`
(WSGI); obrazy bazowe po `@sha256:`; `pip-audit`/`osv-scanner` w CI na obrazie z GHCR.

**S22. djcms: import drzewa startowego w żądaniu anonima – kolejka chętnych wyłącza wszystkie witryny**
`djcms/apps/sites/middleware.py:91` woła `ensure_content_for_request`; `importer/starter.py:111-130`
bierze `pg_advisory_xact_lock` bez limitu czasu, znacznik porażki sprawdza tylko **przed** blokadą
i trzyma w LocMem (per proces), paczkę (32 MB, 120 s) pobiera w otwartej transakcji. Warunek: aktywny
konkurs bez stron (świeżo założony albo z paczką, która stale się nie importuje – slug kolidujący z trasą,
> 50 stron, padające API). Anonim trzyma ≥ 12 żądań na ten host: każde po kolei pobiera i waliduje
paczkę od nowa, wszystkie wątki djcms zajęte, strony **wszystkich** konkursów w trybie PRIMARY nie
odpowiadają; web generuje wielokrotnie ciężki `export`. **Naprawa:** `pg_try_advisory_xact_lock` → 503
bez czekania; ponowne sprawdzenie znacznika po blokadzie; znacznik w bazie (`CompetitionSite.starter_failed_at`);
`SET LOCAL lock_timeout`; liczba stron z listy `competitions` **przed** pobraniem; docelowo import tylko
przy wdrożeniu.

---

## 3. NISKIE (zebrane)

**Konta i panel**
- Kreator `/setup/` (`setup.py:325`) woła `bootstrap_coordinator` bez `--reset-password`: istniejące konto
  o podanym adresie dostaje superusera z niezmienionym hasłem i jest od razu logowane (`setup_views.py:158`).
  Odmowa adresu, który już ma konto.
- Sesja 2 tygodnie bez wygasania po bezczynności, także koordynator na szkolnym komputerze – krótszy
  `SESSION_COOKIE_AGE` lub wygaszanie po bezczynności dla ról uprzywilejowanych. To samo w djcms dla
  logowania hasłem (`EditorAccessMiddleware` przycina tylko sesje SSO).
- `_allowed_prefixes` (`twofactor.py:619`) ma `lru_cache` po `target`, a `reverse()` zależy od prefiksu
  konkursu – po włączeniu 2FA pętla przekierowań dla innych konkursów. Limit kodu 2FA tylko po IP.
- `accept_invitation` (`bulk_registration.py:1474`) bez blokady wiersza – dwa równoległe POST-y z tym samym
  tokenem (tylko posiadacz tokenu).
- Konta `is_staff` bez grupy i członkostwa są „niczyje” i nie są chronione (`is_protected`) – do
  sprawdzenia na prod: `User.objects.filter(is_staff=True, is_superuser=False).exclude(groups__name__in=COORDINATOR_GROUPS)`.
- `competition_forms.py:240`: `logo`/`favicon`/`site_logo`/`social_image` z `Image.objects.all()` – koordynator
  widzi i przypina obrazy z kolekcji innych konkursów.
- `Competition.from_email` (`tenancy/models.py:403`) bez kontroli domeny – podszycie pod platformę/inny
  konkurs przez wspólny przekaźnik.
- Eksport RODO zawiera `kwalifikacja_reczna` (`data_export.py:257`) przed publikacją wyników.

**Ocenianie i reklamacje**
- `set_review_score` (`grading/services.py:1589`) ocenia recenzję CANCELLED (wraca do konsensusu) i nie
  sprawdza ogłoszonych wyników.
- `override_final_grade` (`:1690`) na pracy APPEALED ustawia GRADED_PROVISIONAL; `decide_appeal` nie wymaga
  `status == APPEALED`.
- Forum: `edit_post` (`forum/services.py:447`) ignoruje `thread.is_locked`; `reply` (`:398`) ignoruje
  `category.is_open`; `report_post` (`:496`) bez unikalności pary wpis–zgłaszający (zaśmiecanie kolejki).

**Delegacje i opiekunowie**
- Import nauczyciela przy otwartej rejestracji „zajmuje” adres ucznia, `school_id` dowolny.
- Zgoda opiekuna prawnego: uczeń może podać alias `+tag` albo adres nauczyciela (`accounts/guardian.py`)
  – normalizacja aliasów, odrzucanie `supervisor_email`.
- `student_status/services.py:415`: `accept` nie odmawia akceptacji własnego zaświadczenia.

**Pliki, PDF, poczta**
- reportlab `Paragraph` bez escape w `integrations/exports.py:259,506` i `coordinator_fees.py:307`
  (`display_name`, `year_label`, tytuł `DocumentTemplate`): `<`/`&` → 500; `<img src="/ścieżka">` osadza
  lokalny plik z kontenera (URL-e zablokowane: `trustedHosts=None`). `html.escape(..., quote=False)` jak
  w `coordinator_fees.py:249`.
- Brak skanu AV dla mediów redakcyjnych (dokumenty Wagtaila `zip/doc/xls…`, obrazy, plakaty, szablony
  dyplomów, treści zadań); clamd bez `AlertEncrypted`/`AlertExceedsMax` – ZIP/PDF z hasłem wraca `OK`.
  Hook `before_save` Wagtaila → `scan_stream`; zamontować `clamd.conf`.
- Ilustracje pytań testu (`web/quiz/attempt.html:72`) to presigned URL na wewnętrzny `http://minio:9000`
  (nie działa); naiwna poprawka dałaby link ważny godzinę do rozesłania w trakcie testu – osobny widok
  z `FileResponse` + `no-store`, wzorem `ProblemStatementView`.
- `submissions/preview.py:91` (pypdf) w workerze współdzielonym z kolejkami `scan` i `mail`, soft limit
  30 min – złośliwy PDF zatrzymuje skan i pocztę. Osobne zadanie z `soft_time_limit≈30 s`.
- `Problem.max_file_mb` do 100 MB vs Caddy `MAX_UPLOAD_MB=25` → 413 zamiast komunikatu.
- Dokumenty Wagtaila z ograniczeniem LOGIN/GROUPS działają ponad konkursami (grupy globalne, rejestracja
  otwarta) – hook `before_serve_document` z `has_role`.

**Infrastruktura i operacje**
- `scripts/deploy.sh:214` – `find … -exec rm -rf` nie wyklucza `jitsi/` (tam `.env` z `JWT_APP_SECRET`
  i hasłami JICOFO/JVB): po zwykłym wdrożeniu projektem Jitsi nie da się zarządzać. Dodać `! -name jitsi`.
- Sekrety w argv: `deploy.sh:818,842` (`COORDINATOR_PASSWORD` w `ssh … env …`, interpretowane przez zdalną
  powłokę), `backup.sh:250`/`restore.sh:378` (`MC_HOST_src=http://root:hasło@…`). Przez stdin/`printf %q`
  jak dla `DJCMS_ADMIN_*`.
- `scripts/pull_prod_data.sh` kopiuje pełną bazę (jawne `authtoken_token`, dane małoletnich) i oba
  buckety na laptop z `DEBUG=1` – pseudonimizacja w zrzucie, `TRUNCATE authtoken_token, django_session`,
  bez `submissions`.
- Tokeny w ścieżkach URL w access logu gunicorna (`zaproszenie/wideo/<key>/`, `activate/<token>/`,
  `reset/<uidb64>/<token>/`, `zaproszenie/<token>/`), 250 MB w `docker logs` – maskowanie w `--access-logformat`.
- `caddy:2.8` (Go 1.22, bez poprawek stdlib 2025), root, bez `cap_drop`/`no-new-privileges`/`read_only`,
  w sieci `internal`. `caddy:2.10@sha256:…`, `cap_drop: [ALL]` + `NET_BIND_SERVICE`, `read_only`.
- `/api/schema/` i `/api/docs/` publiczne (AllowAny) – `SERVE_PERMISSIONS: IsAuthenticated` albo tylko `/api/v1/`.
- `/captcha/refresh/` i `/captcha/image/<key>/` bez limitu (zapis do bazy + Pillow na każde żądanie).
- `apps/quiz/services.py:446` zapisuje `REMOTE_ADDR` (= Caddy) zamiast `client_ip()`.
- Blok `meet.` w Caddyfile bez `frame-ancestors` (clickjacking pokoju z kamerą).
- MinIO `RELEASE.2025-04-22` – obrazy społecznościowe nie są już wydawane; potrzebna decyzja o następcy.
- `django_language` bez `LANGUAGE_COOKIE_SECURE/HTTPONLY` (djcms ma).
- `ufw` nie chroni portów publikowanych przez Dockera (dziś tylko 80/443/9000, 10000/udp).
- djcms: redaktor konkursu może ustawić przekierowanie strony na dowolny `https://host` i stronę z nakładką
  `position:fixed` (nh3 przepuszcza `style`) w originie logowania web – cele tylko z hostów platformy,
  usunąć `style` z `ALLOWED_ATTRIBUTES["*"]`. `DJCMS_BUILD=1` w runtime po cichu podmienia `SECRET_KEY`
  na stałą (compose tego nie ustawia) – bezpiecznik w `wsgi.py`.
- Webhooki: `integrations/tasks.py:23` i `send_test_delivery` nie sprawdzają `endpoint.is_active`; klucze
  API bez `expires_at`; „podwójna zgoda” na PII to dwa checkboxy w jednym POST.
- `GET /coordinator/fees/register/<pk>/document/` wydaje rachunek (jedyny GET ze skutkiem ubocznym);
  `coordinator_workshops.py:201` `int(request.GET["page"])` → 500 przy `?page=x`.
- Zbędny `|safe` na `help_text` w `templates/web/coordinator/competition.html:50` (dziś stała z kodu).
- `/dyplomy/<code>/` bez zawężenia do konkursu i bez limitu (kod ~59 bitów, enumeracja niepraktyczna).

---

## 4. Sprawdzone i w porządku (żeby nikt tego nie robił drugi raz)

- **Wstrzyknięcia:** brak surowego SQL poza sparametryzowanym `pg_advisory_xact_lock`; brak `pickle`/`yaml.load`/
  `eval`/`subprocess` z wejściem; ORM wszędzie.
- **XSS/SSTI:** CSP z nonce + `strict-dynamic`, bez `unsafe-inline` na stronach publicznych; `|safe`/`mark_safe`
  tylko za sanitizerami (diff prac, zgody RODO, forum/czat przez `urlize(autoescape=True)`); RichText
  z białą listą, bez `RawHTMLBlock`; HTMX bez `hx-on`, `HX-Retarget` walidowany. Motywy (`apps/themes`):
  osobny `Engine`, kontekst z kopii danych bez modeli i żądania, lint + kontrola wyniku, ZIP z obroną
  przed zip-slip/symlink/bombą, SVG sanityzowane, wgrywa tylko superkoordynator – bypassu lintu nie znaleziono.
- **SSRF:** webhooki z trzema warstwami (walidacja, ponowne DNS + `is_global`, `allow_redirects=False`,
  `https` tylko); AI grading przez SDK ze stałym endpointem; oEmbed tylko YouTube/Vimeo; djcms importer bez URL od użytkownika.
- **Open redirect:** wszędzie `url_has_allowed_host_and_scheme`.
- **Uwierzytelnianie:** tylko dwa `login()` w kodzie, brak magic linków/impersonacji; sesja rotowana;
  wylogowanie tylko POST; `panel_login_redirect` przechwytuje każdą metodę; reset hasła z tokenem Django
  (24 h, skrót hasła), reset kasuje token DRF; brak wyliczania kont przez czas odpowiedzi; `/internal/*`
  bramka Host + token w czasie stałym; klucze API SHA-256 + `compare_digest`, zakresy i odwołanie działają;
  CAPTCHA obchodzona tylko przy `E2E_MODE`, a produkcja z `E2E_MODE` nie startuje; `/setup/` pod blokadą doradczą.
- **IDOR:** wyszukiwanie po pk konsekwentnie przez `for_competition`/`scope_to_competition`/`_account()`/
  `own_*`; brak `fields='__all__'`; sortowanie z białej listy; delegacje i opiekun bez IDOR, tokeny 256-bitowe
  jednorazowe; reklamacje z konfliktem interesów i oknem; quiz bez wycieku klucza odpowiedzi, `select_for_update`.
- **Pliki:** rozwiązania – magic bytes, klucz z identyfikatorów, anonimowy `ResponseContentDisposition`
  w podpisie, bramka `CLEAN`; `ExtensionContentTypeS3Storage` działa; widok dokumentów Wagtaila z CSP
  `default-src 'none'`, `nosniff`, `attachment` poza PDF; Wagtail nie przyjmuje SVG, limit 128 Mpx;
  CSV/XLSX przez `_safe_text`; openpyxl z defusedxml; pdf.js 4.10.38 (po CVE-2024-4367); ClamAV
  niedostępny = odmowa.
- **Infrastruktura:** publikowane tylko 80/443/9000 i 10000/udp; brak `docker.sock`/`privileged`/
  `network_mode: host`; kontenery aplikacyjne nie-root, `read_only`, `cap_drop: ALL`; Redis z hasłem
  w osobnej sieci; nagłówki proxy nadpisywane (`X-Forwarded-Proto`, `X-Real-IP`, `X-Forwarded-For`),
  `USE_X_FORWARDED_HOST=False`; admin API Caddy na localhost; `DEBUG=0`; sekrety w repo i historii –
  tylko atrapy testowe; `deploy.sh` z `set -euo pipefail`, `.env` 600, kopie AES256 z hasłem przez fd;
  polityki MinIO: anonimowo tylko `GetObject` na `public-media`; Jitsi z JWT, bez gości; djcms – osobny
  `SECRET_KEY`, ciasteczka host-only, SSO z HMAC/nonce/TTL 60 s, strażnicy zasięgu redaktora działają.
- **Cache stron (`page_cache.py`):** klucz z konkursem, językiem, ścieżką; tylko anonimowi, bez
  `Set-Cookie`/`Vary`/`private`; losowe placeholdery CSRF i nonce. W porządku – problem jest wyłącznie
  w `cache_page` na `/status/` (W1).
- **Zależności** (lokalny venv, bez lockfile – patrz S21): django 6.1.1, DRF 3.18.1, wagtail 8.0,
  allauth 65.19.4, pillow 12.3.0, celery 5.6.3, gunicorn 23.0.0, requests 2.34.2, urllib3 2.8.0,
  h11 0.16.0, cryptography 50.0.1, pypdf 6.19.0, reportlab 5.0.1; djcms (uv.lock): django-cms 5.1.3,
  djangocms-text 1.0.1, filer 3.6.0, nh3 0.3.7. Wszystkie nowsze niż znane poprawki CVE (gunicorn
  CVE-2024-1135/6827, h11 CVE-2025-43859, requests CVE-2024-47081, urllib3 CVE-2025-50181/50182,
  DRF CVE-2024-21520). Wersje w obrazie z GHCR mogą się różnić – potrzebny `pip-audit` na obrazie.

## 5. Czego nie dało się sprawdzić bez serwera
- `sshd` (`PasswordAuthentication`, `PermitRootLogin`), faktyczne `PLATFORM_SUBDOMAINS`/`EXTRA_DOMAINS`/
  `REDIS_PASSWORD` w `/opt/olimpiada/.env`, istnienie `/opt/olimpiada/jitsi/.env`, wersje pakietów
  w obrazie z GHCR, liczba kont objętych S5 i kont `is_staff` bez ochrony, lista `CollectionViewRestriction`.

---

## 6. Plan napraw (proponowana kolejność)

**Pakiet 1 – dziś, małe zmiany, zero ryzyka regresji**
1. W1: `cache_page` z `StatusView` → cache danych. (1 plik + test)
2. W5: dwa ustawienia Wagtaila w `base.py` + test.
3. S7: `is_protected` w resecie 2FA i resecie hasła koordynatora.
4. S12: throttle + karencja na resend opiekuna.
5. `deploy.sh`: `! -name jitsi`; S17: `env_get` zamiast `. ./.env` w `backup*.sh`/`restore.sh`.
6. `|safe` w `competition.html:50`; `html.escape` w reportlab (`integrations/exports.py`, `coordinator_fees.py`).

**Pakiet 2 – przed uruchomieniem drugiego konkursu (IQO)**
7. W3: strażnik `active_elsewhere` + operacja „wypisz z konkursu”; zmiana e-maila przez koordynatora
   tylko z potwierdzeniem. S8: eksport RODO zawężony do konkursu.
8. W4: `memberships_enforced` tylko do włączenia z panelu; poprawka 3.3.10 w checkliście.
9. S13: 404 dla hosta z nieaktywnym konkursem; bez odwrotu do grup w mixinach ról.
10. S19/S20: `__Host-` ciasteczka, filtr `Cookie` do djcms, `TRUSTED_PROXY_IPS` i `mynetworks` na
    stały adres proxy, osobna sieć `mail`.

**Pakiet 3 – maszyna stanów i pliki (ten sezon)**
11. S1 (CANCELLED), S2 (wycofane wyniki – wspólny predykat), S3 (quiz vs `closed_at`), S4 (status dla
    rundy 1 + blokada `revise` w moderacji), S5/S6 (`blocked_at`, resend zaproszenia zamiast aktywacji),
    S9 (district delegacji), S11 (hasło przy zmianie e-maila/2FA, unieważnienie sesji).
12. W2: prywatny storage dokumentów Wagtaila (albo przynajmniej uuid w kluczu).
13. S14 (bomba XLSX), S15 (`/tmp` – osobny katalog + limit), skan AV mediów redakcyjnych, `clamd.conf`.
14. S10: decyzja organizatora/IOD o trybie `INITIALS_SCHOOL`.

**Pakiet 4 – infrastruktura**
15. W6: jawna lista `environment:` dla web/worker/beat; klucz kopii bez `DeleteObject`.
16. S16: `request_buffers` + `timeouts` w Caddy. Caddy 2.10 po skrócie, `cap_drop`.
17. S18: 2FA dla koordynatorów i superusera, koordynator bez `is_superuser`, `/admin/` i `/cms/` za
    listą IP lub `basic_auth`.
18. S21: `uv lock`, `--no-dev`, `pip-audit` w CI.
19. S22: djcms starter – `try_lock` + 503 + znacznik w bazie.
20. Reszta pozycji niskich według uznania (log tokenów, `pull_prod_data`, MinIO następca, schema API).

Po każdej naprawie: wpis w `SECURITY_CHECKLIST.md` z odniesieniem do testu – zgodnie z zasadą dokumentu.
