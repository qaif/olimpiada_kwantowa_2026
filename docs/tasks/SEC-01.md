# SEC-01: Logowanie dwuskładnikowe dla ról personelu

## 0. Cel i granice

Platforma trzyma dziś dane, których przejęcie jednym hasłem byłoby incydentem w rozumieniu RODO:
zaszyfrowane paszporty i dane o zdrowiu członków delegacji (LOG-01), płatności i faktury (PAY-01),
nagrania nadzoru zdalnego (PROC-01), dane osobowe małoletnich. Konta, które to widzą – personel –
mają mieć drugi składnik logowania **obowiązkowo**, z okresem przejściowym i z jasną drogą
odzyskania dostępu.

**Co już było (v0.19.0, 17.09.2026) i zostaje fundamentem** – `apps/accounts/twofactor.py`:

- TOTP (RFC 6238) we własnej implementacji (`hmac`/`struct`), sekret szyfrowany Fernetem z kluczem
  z `SECRET_KEY`, kod QR jako SVG z macierzy `reportlab` (zależność od dyplomów),
- 10 kodów zapasowych (skróty SHA-256, pokazywane raz), ochrona przed powtórzeniem (`last_counter`),
- `TwoFactorMiddleware` – sesja po samym haśle w „poczekalni” (`/login/2fa/`), osobne listy
  dozwolonych adresów dla kroku „podaj kod” i „skonfiguruj”,
- `TwoFactorTokenAuthentication` (DRF) – token sprzed potwierdzenia urządzenia nie działa,
  `POST /api/auth/login/` z polem `code`,
- ekrany `/account/2fa/`, `/account/2fa/codes/`, `/account/2fa/disable/`, reset z panelu
  koordynatora (`/coordinator/accounts/<pk>/2fa-reset/`), audyt `2fa.*`, limit `two_factor` 10/min,
- wyłącznik główny `TWO_FACTOR_ENABLED` (domyślnie **wyłączony** – decyzja organizatora)
  i `TWO_FACTOR_REQUIRED_ROLES` (globalne grupy Django, domyślnie puste).

**Czego brakowało** (zakres SEC-01): polityki per konkurs, okresu przejściowego z banerem,
zapamiętania urządzenia, ponownego potwierdzenia hasłem i kodem przy wyłączaniu, nowego kompletu
kodów, blokady konta po serii złych kodów, odporności jednorazowości na wyścig równoległych żądań,
powiadomień e-mail, zawężenia resetu cudzego drugiego składnika personelu do superkoordynatora
i ekranu „kto z personelu ma 2FA”.

Czego zadanie **nie** robi:
- nie dokłada zależności (WebAuthn/passkeys wymagałyby nowej biblioteki – poza zakresem),
- nie zmienia nic przy `TWO_FACTOR_ENABLED=0`: warstwa wymuszająca nie robi ani jednego zapytania,
  ekrany odpowiadają 404, menu i profil są bajt w bajt takie jak dziś,
- **nigdy nie wymusza 2FA na uczestniku** (rola `participant` jest odrzucana z każdej listy ról
  wymaganych; uczestnik, który włączył 2FA sam, podaje kod jak dotąd),
- nie zmienia ekranu „Zmiana hasła” (osobne zadanie, gałąź `feature/zmiana-hasla`) – profil dostaje
  wyłącznie mały fragment z odnośnikiem do ekranu 2FA.

## 1. Role personelu (klucze polityki)

| Klucz | Kto | Skąd wiadomo |
|---|---|---|
| `superkoordynator` | rola platformy | `apps.accounts.super_coordinator.is_super_coordinator` |
| `admin` | dostęp do `/admin/` | `is_staff` albo `is_superuser` |
| `coordinator` | koordynator konkursu (w tym oficer logistyki) | `roles_for(user, competition)` |
| `team_leader` | opiekun drużyny narodowej (DEL-01) | jw. |
| `logistics` | przydział w logistyce finału (oficer, obsługa rejestracji) | `LogisticsAccess` w konkursie |
| `reviewer` | komitet / recenzent | `roles_for` |
| `appeals` | komisja odwoławcza | `roles_for` |
| `supervisor` | opiekun szkolny | `roles_for` (tylko gdy organizator wybierze) |

`participant` nie jest kluczem polityki – nie da się go wybrać ani wpisać w ustawieniu.

## 2. Polityka

Wymagane role konta w konkursie żądania = **role platformy** ∪ **role konkursu**.

- **Platforma** – `TWO_FACTOR_REQUIRED_ROLES` (`.env`). Domyślnie zmienione z pustej listy na
  `superkoordynator,admin`: te dwie role widzą wszystkie konkursy naraz. Znaczenie ma wyłącznie przy
  `TWO_FACTOR_ENABLED=1`.
- **Konkurs** – model `staff_mfa.TwoFactorPolicy` (brak wiersza = tryb `auto`):
  - `auto` (domyślnie): jeśli konkurs ma włączoną **funkcję wrażliwą** – tryb delegacji, `fees`,
    `onsite_logistics`, `proctoring` (gdy flaga istnieje w katalogu) – wymagane od
    `coordinator`, `team_leader`, `logistics`; bez funkcji wrażliwej – nic ponad platformę,
  - `custom`: dokładnie role zaznaczone przez superkoordynatora (pusta lista = nic ponad platformę),
  - `grace_days` (puste = `TWO_FACTOR_GRACE_DAYS`, domyślnie 14), `allow_remember` (domyślnie tak).
- Zmienia ją **wyłącznie superkoordynator** (ekran `/coordinator/security/2fa/`) albo operator
  w `/admin/`. Koordynator widzi politykę i stan personelu, ale jej nie poluzuje – inaczej wymóg
  byłby zaleceniem. Zmiana w audycie (`2fa.policy_changed`, przed → po).

**Uzasadnienie domyślnych ról „wrażliwych”.** Koordynator widzi wszystko; oficer logistyki
(koordynator z przydziałem) i obsługa rejestracji widzą paszporty, zdrowie, zdjęcia; opiekun
drużyny wpisuje paszporty i dane zdrowotne swoich uczniów. Komitet i komisja odwoławcza widzą prace
(bez danych szczególnych) – organizator dokłada je trybem `custom`.

## 3. Okres przejściowy

- Pierwsze żądanie konta, od którego 2FA jest wymagane, a które go nie ma, zakłada wiersz
  `staff_mfa.TwoFactorGrace(user, required_since)`. Termin = `required_since + grace_days`.
- Do terminu: konto działa normalnie, na każdej stronie stoi baner z datą i odnośnikiem
  do `/account/2fa/` (baner w `base.html` przez znacznik `{% two_factor_grace_banner %}` – motyw
  go nie zasłania, bo stoi poza slotami).
- Po terminie: dotychczasowa poczekalnia „skonfiguruj” (cała sesja, nie tylko panele: sesja po
  samym haśle na koncie personelu nie może zmienić e-maila ani zabrać danych z `/account/export/`).
- Okres jest jednorazowy: wyłączenie 2FA albo reset przez superkoordynatora go nie odnawia
  (konto po resecie konfiguruje 2FA od razu po zalogowaniu hasłem – to jest samoobsługowe).
- Stan w sesji (`2fa_grace`, `2fa_exempt` z identyfikatorem konkursu): warstwa nie pyta bazy na każdym
  żądaniu; konkurs pod prefiksem ścieżki (wspólna sesja) liczy politykę osobno.

## 4. Logowanie, zapamiętanie urządzenia, odzyskiwanie

- Drugi krok bez zmian (`/login/2fa/`), z polem „Zapamiętaj to urządzenie na N dni”
  (`TWO_FACTOR_REMEMBER_DAYS`, domyślnie 7; 0 = brak pola; polityka konkursu może je wyłączyć).
  Ciasteczko `2fa_trust` – podpisane (`signing`, sól własna), `HttpOnly`, `Secure` jak sesja,
  `SameSite=Lax`; niesie konto, chwilę potwierdzenia urządzenia i skrót hasła
  (`get_session_auth_hash`) – zmiana hasła, wyłączenie albo reset 2FA unieważniają je od razu.
- **Wyłączenie** i **nowy komplet kodów** (`/account/2fa/codes/regenerate/`) wymagają hasła
  **i** bieżącego kodu (TOTP albo zapasowego). Limit `two_factor`, blokada z § 5.
- **Reset cudzego 2FA** (`/coordinator/accounts/<pk>/2fa-reset/`): konto personelu (dowolny klucz
  z § 1 poza `supervisor`) – wyłącznie superkoordynator; jeśli na platformie nie ma żadnego aktywnego
  superkoordynatora, wolno koordynatorowi (jak „pierwszy oficer” w LOG-01). Uczestnik/opiekun –
  koordynator, jak dotąd. Audyt `2fa.reset` + list do właściciela.

## 5. Bezpieczeństwo kodów

- **Jednorazowość odporna na wyścig**: przyjęcie kroku TOTP to warunkowy `UPDATE … WHERE
  last_counter < krok` (jeden wiersz albo zero), kod zapasowy zużywany pod `select_for_update`.
- **Blokada konta**: 5 złych kodów w 15 min → 15 min blokady (licznik w cache, per konto, niezależnie
  od adresu IP; limit `two_factor` per IP zostaje). W blokadzie nawet dobry kod jest odrzucany.
  Audyt `2fa.locked`, list do właściciela. API: `429 TWO_FACTOR_LOCKED`.
- Odpowiedzi ekranów 2FA: `Cache-Control: private, no-store` (sekret, kody zapasowe); pełnostronicowy
  cache ich nie dotyczy (zalogowani, adresy spoza allow-listy) – pilnuje tego test.

## 6. API

- `POST /api/auth/login/`: konto z urządzeniem – `code` jak dotąd (nowy token); konto wymagane bez
  urządzenia – **w okresie przejściowym** token jak dotąd, po nim `403 TWO_FACTOR_SETUP_REQUIRED`.
- `TwoFactorTokenAuthentication`: konto wymagane bez urządzenia po terminie → 401; token sprzed
  potwierdzenia urządzenia → 401 (bez zmian). Token personelu powstaje więc wyłącznie po kodzie albo
  w okresie przejściowym. Klucze integracji (`/api/v1/`, `apps.integrations`) to osobny mechanizm
  bez sesji użytkownika – poza zakresem.

## 7. Powiadomienia i audyt

Listy (kolejka `mail`, po commicie, w języku konta): włączenie, wyłączenie, nowy komplet kodów,
użycie kodu zapasowego (z liczbą pozostałych), reset przez organizatora, blokada po złych kodach.
Audyt: `2fa.enabled`, `2fa.disabled`, `2fa.codes_regenerated`, `2fa.verified` (metoda), `2fa.failed`,
`2fa.locked`, `2fa.remembered`, `2fa.reset`, `2fa.grace_started`, `2fa.policy_changed`.

## 8. Ekrany

- `/account/2fa/` (istniejący) – + formularz wyłączenia z hasłem i kodem, + „Nowe kody zapasowe”,
  + informacja, czy 2FA jest na tym koncie wymagane i do kiedy trwa okres przejściowy,
- `/account/2fa/codes/regenerate/` – hasło + kod → nowy komplet pokazany raz,
- `/login/2fa/` – + „Zapamiętaj to urządzenie”, komunikat o blokadzie,
- `/coordinator/security/2fa/` – polityka konkursu (edycja: superkoordynator) i lista personelu
  konkursu: rola, 2FA tak/nie, termin okresu przejściowego, przycisk resetu. Pozycja menu
  „Bezpieczeństwo logowania” w „Raportach” – wyłącznie przy `TWO_FACTOR_ENABLED=1`.

Ekrany konta – gettext (10 katalogów w `apps/staff_mfa/locale`), ekran koordynatora po polsku
(I18N-01 § 0).

## 9. Testy (celowane)

`apps/staff_mfa/tests/` + dotychczasowe testy 2FA w `apps/accounts/tests` i `apps/web/tests`:
konfiguracja, logowanie z/bez 2FA, kody zapasowe jednorazowe, powtórzenie kodu (także wyścig),
limit i blokada, polityka per rola i per konkurs (auto/custom, funkcje wrażliwe), okres
przejściowy i baner, zapamiętanie urządzenia (i jego unieważnienie), API (login, token), reset przez
superkoordynatora (i odmowa dla koordynatora), wyłącznik = zachowanie sprzed zmiany dla uczestnika,
render w motywie IQO, `no-store`, tłumaczenia.
