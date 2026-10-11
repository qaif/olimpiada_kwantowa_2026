# AUTH-01b: Zmiana hasła w panelu konta

## 0. Cel i granice

Prośba organizatora (4.10.2026): „pozwólcie zmieniać hasło w panelu”. Do tej pory jedyną drogą do
nowego hasła był „Nie pamiętasz hasła?” (list z linkiem), także dla kogoś, kto hasło **zna** i chce
je tylko zmienić – np. po zalogowaniu się na szkolnym komputerze albo po wycieku z innego serwisu.

Zadanie dokłada ekran **„Zmień hasło”** (`/account/password/`) dostępny dla **każdego** zalogowanego
konta, niezależnie od roli: uczestnik, opiekun szkolny, opiekun drużyny narodowej, recenzent,
komisja odwoławcza, koordynator i superkoordynator. Konto jest jedno na platformę (jedno hasło na
wszystkie konkursy), więc ekran nie zależy od konkursu ani od roli.

Czego zadanie **nie** robi:
- nie zmienia resetu hasła (`/password-reset/`, `/reset/<uid>/<token>/`, szablony listu resetu) –
  równolegle zajmuje się nim osobne zadanie; tutaj wolno tylko **linkować** do tego przepływu,
- nie dodaje endpointu API (`/api/auth/…`) – prośba dotyczyła panelu; serwis jest gotowy, gdyby
  API było potrzebne,
- nie dodaje listy sesji/urządzeń – platforma jej nie ma (patrz § 3).

Kod mieszka w nowej aplikacji `apps.password_change` (serwis, formularz, widoki, list, szablony,
katalogi tłumaczeń); do części wspólnych dochodzą wyłącznie: wpis w `INSTALLED_APPS`, rozwinięcie
wzorców na końcu `apps/web/urls.py`, stawka limitu w `REST_FRAMEWORK`, sekcja „Hasło” na ekranie
edycji danych i odnośnik do ustawień konta w pasku konta.

## 1. Gdzie jest ekran

- **Edycja danych** (`/me/profile/` uczestnika i `/account/profile/` pozostałych ról – jeden szablon
  `web/account/profile.html`) dostaje sekcję „Hasło” z przyciskiem „Zmień hasło”.
- **Pasek konta**: adres e-mail zalogowanej osoby (dotąd sam tekst) staje się odnośnikiem do
  ustawień konta (`/account/profile/`, który uczestnika przekierowuje na `/me/profile/`). Dotąd
  recenzent, komisja i opiekunowie nie mieli w nawigacji **żadnej** drogi do ustawień konta.
  Odnośnik jest fragmentem aplikacji `web/_account_who.html`, więc nagłówek motywu (paczka IQO
  Quantum) dołącza go `{% include %}` bez dostępu do obiektów konta – kontekst paczki zostaje
  kuratorowany (`apps.themes.safe_context`).

## 2. Zmiana hasła (konto z hasłem)

Formularz: **aktualne hasło**, **nowe hasło**, **powtórz nowe hasło**.

1. Aktualne hasło jest wymagane zawsze – sama sesja nie wystarcza. Porzucona albo przejęta sesja
   (szkolny komputer) nie może zamienić się w trwałe przejęcie konta jednym POST-em.
2. Nowe hasło przechodzi przez `AUTH_PASSWORD_VALIDATORS` (min. 10 znaków, podobieństwo do danych
   konta, popularne, same cyfry) – z kontem jako `user`. Powtórzenie musi się zgadzać. Nowe hasło
   identyczne z aktualnym jest odrzucane (zmiana, która niczego nie zmienia, a wysyła list).
3. Sprawdzenie aktualnego hasła i walidatory stoją w **serwisie**
   (`apps.password_change.services.change_password`), a formularz tylko zbiera pola i przypina
   błędy do właściwych pól. Hasło liczymy raz (jeden `check_password` na próbę).
4. Po sukcesie, w jednej transakcji:
   - `set_password` + zapis,
   - **wszystkie tokeny API konta kasowane** (token DRF nie zawiera skrótu hasła, więc przeżyłby
     zmianę – tak samo robi reset hasła),
   - wpis audytu `password.changed` z `diff={"via": "account", "api_tokens_revoked": <liczba>}`
     – bez hasła, skrótu i adresu,
   - list „hasło zostało zmienione” (§ 4) – po commicie.
5. **Bieżąca sesja zostaje** (`update_session_auth_hash`, czyli także nowy klucz sesji), razem ze
   znacznikiem drugiego składnika – człowiek nie jest wylogowany z urządzenia, na którym zmienia
   hasło.
6. Nieudana próba (złe aktualne hasło) zostawia wpis `password.change_failed`
   (`{"reason": "wrong_current", "consecutive": n, "session_ended": bool}`), bez wpisanego tekstu.
   Piąta pomyłka z rzędu w jednej sesji kończy sesję (§ 9).
7. Po sukcesie: przekierowanie na ekran edycji danych z komunikatem.

## 3. Pozostałe urządzenia

Platforma nie ma ekranu „zalogowane urządzenia”, więc opcja „wyloguj pozostałe urządzenia” nie
jest przełącznikiem – jest **zawsze** skutkiem zmiany: Django wiąże każdą sesję ze skrótem hasła
(`get_session_auth_hash`), a sesja z nieaktualnym skrótem jest przy następnym żądaniu czyszczona.
Tokeny API kasujemy jawnie (§ 2.4). Ekran mówi o tym przed wysłaniem formularza i w komunikacie
po zmianie. Wylogowanie innych urządzeń jest tu zamierzone: zmiana hasła bywa odpowiedzią na
„ktoś zna moje hasło”, a wtedy pozostawienie jego sesji czyniłoby zmianę bezużyteczną.

## 4. List bezpieczeństwa

Do właściciela konta (adres konta) idzie list „Hasło do konta zostało zmienione”:

- w języku żądania, w którym nastąpiła zmiana (to żądanie adresata, więc aktywny język jest jego –
  `apps.accounts.activation.recipient_language`), z nadawcą i marką konkursu żądania
  (`mail_competition`), kolejkowany po commicie **odporny na awarię brokera** (błąd kolejki jest
  logowany po kluczu konta, zmiana i sesja zostają),
- godzina zmiany w strefie ucznia (TZ-01, `participant_timezone`), a bez niej w strefie konkursu
  (`Competition.time_zone`), zapisana jednoznacznie: `(Asia/Tokyo, UTC+09:00)`, nie „CEST”,
- treść: co się stało i kiedy, że pozostałe urządzenia zostały wylogowane, a gdy to nie Ty –
  **link do „Nie pamiętasz hasła?”** pod hostem konkursu żądania (dla konkursu pod prefiksem
  ścieżki – z prefiksem) i prośba o kontakt z organizatorem,
- **bez** hasła, bez adresu IP, bez nazwy przeglądarki,
- temat zamrożony w `apps/tenancy/tests/test_invariants.py` jak każdy inny temat listu.

## 5. Konto bez hasła platformy

Konto bez używalnego hasła (`has_usable_password()` fałsz) to w praktyce konto założone logowaniem
Google/Facebook (`register_social_participant`) albo takie, któremu allauth wyczyścił hasło przy
łączeniu po adresie. Konta zaproszone (import listy klasowej, delegacje) ustawiają hasło pod linkiem
zaproszenia, zanim w ogóle mogą się zalogować.

**Decyzja: bez „ustaw hasło” w samej sesji.** Model bezpieczeństwa platformy (docstring
`register_social_participant`) mówi, że pierwsze hasło takiego konta ustawia się drogą, która
**potwierdza dostęp do skrzynki**. Sesja z Google'a nie jest takim dowodem dla hasła platformy:
przejęta sesja dostałaby trwałe poświadczenie. Ekran pokazuje więc wyjaśnienie i przycisk
„Wyślij mi link do ustawienia hasła”, który wysyła **zwykły list resetu hasła** (te same szablony,
ten sam token, ten sam ekran `/reset/<uid>/<token>/` z walidatorami i audytem `password.reset`)
– wyłącznie na adres **własnego** konta bez hasła.

Od AUTH-01a (PR #67) publiczny „Nie pamiętasz hasła?” też wysyła link kontom bez hasła, więc
przycisk jest **wygodą** zalogowanego: konto wskazuje sesja (bez wpisywania adresu – nie jest
wyszukiwarką kont), a limit liczy konto. **Własnej reguły nie ma:** o tym, komu wolno wysłać link,
rozstrzyga reguła resetu (`apps.accounts.password_reset.reset_eligible` i
`QueuedPasswordResetForm.get_users` – aktywne konto, adres potwierdzony naszą drogą i u dostawcy,
bez zaproszenia z brakującymi zgodami); przycisk tylko zawęża jej wynik do konta z sesji bez hasła,
a odmowę mówi wprost (pyta o własne konto). Limit: scope `password_reset` (5/h na konto). Audyt:
`password.set_link_sent`.

## 6. Drugi składnik (TOTP)

Kod 2FA **nie jest** wymagany ponownie na ekranie zmiany hasła – spójnie z polityką logowania:
`TwoFactorMiddleware` nie wpuszcza sesji czekającej na kod nigdzie poza ekranem kodu, więc do
`/account/password/` dochodzi wyłącznie sesja, która przeszła **cały** drugi składnik (konto
z urządzeniem albo z roli objętej `TWO_FACTOR_REQUIRED_ROLES`). Tą samą zasadą kieruje się
wyłączenie 2FA (`TwoFactorDisableView`; PR #71 dokłada tam hasło i kod); zmiana hasła pyta
o hasło aktualne. Zmiana hasła **nie** dotyka urządzenia 2FA ani kodów zapasowych, a znacznik
weryfikacji w sesji przeżywa zmianę klucza sesji.

## 7. Limit, CSRF, cache

- Limit POST-ów: scope `password_change`, **10/h na konto** (kubełek konta, nie adresu IP – jak
  `PER_USER_SCOPES`: cała pracownia za jednym NAT-em nie dzieli budżetu, a zgadujący z przejętej
  sesji nie zyska nowego budżetu zmianą adresu). Liczy się każdy POST. Odmowa: 429 z `Retry-After`.
- CSRF: zwykła ochrona Django (formularz HTML z `{% csrf_token %}`), bez wyjątków.
- Cache: `never_cache` (`Cache-Control: no-store…`). Pełnostronicowy cache gościa
  (`apps.web.page_cache`) i tak obejmuje wyłącznie listę dozwolonych ścieżek publicznych i nigdy
  zalogowanych – test pilnuje, że `/account/password/` na tej liście nie jest.
- Niezalogowany → przekierowanie do logowania (z `next`), także pod prefiksem ścieżki.

## 8. Testy (`apps/password_change/tests/`)

Każda rola dochodzi do ekranu i zmienia hasło; złe aktualne hasło (i audyt próby); błędy
walidatorów i niezgodne powtórzenie; nowe = stare; sesja zostaje, druga sesja jest wylogowana,
token API skasowany; list z właściwym hostem (domena konkursu, prefiks ścieżki) i językiem (polski,
angielski IQO), bez hasła; audyt bez sekretów; limit 429; gość → logowanie; `no-store` i brak na
liście page cache; konto bez hasła – brak formularza, link wysłany na własny adres, odmowa dla
konta z hasłem; 2FA – sesja bez kodu nie dochodzi, po zmianie znacznik zostaje; odnośnik w pasku
konta i sekcja na ekranie edycji danych; nagłówek motywu IQO z fragmentem przechodzi lint paczki.

## 9. Poprawki po przeglądzie (PR #70)

- **H1 – inne drogi do hasła i adresu.** Samo „wymagaj aktualnego hasła” na tym ekranie nie zamyka
  przejęcia konta z cudzej sesji, dopóki obok istnieją drogi bez tego wymogu:
  - `/cms/account/` Wagtaila: panele „Hasło” i pole e-mail wyłączone
    (`WAGTAIL_PASSWORD_MANAGEMENT_ENABLED = False`, `WAGTAIL_EMAIL_MANAGEMENT_ENABLED = False`),
  - `/admin/password_change/` (i `done/`) Django: przekierowanie na `/account/password/`,
  - `/account/email/`: **aktualne hasło** w formularzu i w serwisie (`request_email_change`);
    konto bez hasła najpierw ustawia hasło (§ 5). Limit `password_reset` liczony per konto, pomyłki
    w audycie `account.email_change_failed`,
  - `/account/2fa/disable/` – PR #71 (hasło i kod), poza tym zadaniem.
- **Wspólne potwierdzenie hasłem** – `apps.accounts.reauth.confirm_current_password`: jeden
  `check_password`, licznik kolejnych pomyłek w sesji wspólny dla obu ekranów (5 → `logout`,
  dalej przez logowanie z limitem prób i 2FA), audyt, a przy podniesieniu skrótu hasła
  (`PASSWORD_HASHERS`) – `update_session_auth_hash` **przed** jakąkolwiek inną odmową (L1).
- **M1** – list kolejkowany odpornie na awarię brokera; `update_session_auth_hash` przed kolejkowaniem.
- **L2** – sesji edytora django CMS (osobna baza, `DJCMS_SSO_SESSION_SECONDS`) zmiana hasła nie
  kończy; redaktor (SSO włączone + dostęp do `/cms/`) dostaje o tym zdanie na ekranie i w liście.
- **L3** – komunikat limitu per konto mówi „na tym koncie” (`PerAccountThrottleMixin` w
  `apps.web.throttle`). Dodatkowy kubełek per adres IP – świadomie pominięty: seria pomyłek i tak
  kończy sesję, a nowa sesja to logowanie z limitem per IP i per (IP, e-mail).
- **L6** – IQO 1.1.1 ma `min_app_version` 0.45.0; testy instalują prawdziwą paczkę z fikstury.
- **L8** – strefa w liście (§ 4).
