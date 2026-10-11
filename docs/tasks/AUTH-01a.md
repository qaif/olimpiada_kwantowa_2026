# AUTH-01a: Audyt i poprawki resetu hasła

## 0. Cel i granice

Pytanie organizatora: „sprawdź, czy użytkownicy mogą zresetować hasło”. Audyt całej drogi
„Nie pamiętasz hasła?” w świecie wielu konkursów (Olimpiada Kwantowa na `olimpiadakwantowa.pl`,
anglojęzyczne IQO na `iqo-official.org`, konkursy pod prefiksem ścieżki) i poprawki tego, co nie
działa. Bez nowych ekranów i bez nowych napisów interfejsu.

Czego zadanie **nie** robi: ekranu „zmień hasło” w panelu konta (osobne zadanie, gałąź
`feature/zmiana-hasla`), zmian w resecie zlecanym przez koordynatora (poza nadawcą listu),
zmian konfiguracji poczty na produkcji.

## 1. Wejścia do resetu (stan zastany)

| Wejście | Gdzie | Uwagi |
|---|---|---|
| link „Nie pamiętasz hasła?” na `/login/` | `templates/web/login.html` | |
| `/password-reset/` → `/password-reset/sent/` | `apps.web.views.public.PasswordResetView` | limit `password_reset` 5/h, CSRF, bez enumeracji |
| `/reset/<uidb64>/<token>/` → `/reset/done/` | `PasswordResetConfirmView` | token 24 h, jednorazowy, walidatory haseł, bez autologowania, kasuje token API, audyt |
| reset zlecony przez koordynatora | `CoordinatorPasswordResetView` | ten sam formularz i list |
| `/cms/password_reset/`, `/admin/` | wyłączone | jedna droga resetu |
| API | brak endpointu resetu | |

List idzie zadaniem `apps.core.tasks.send_mail_task` na kolejce `mail` (`CELERY_TASK_ROUTES`,
worker `-Q default,scan,mail`), po commicie.

## 2. Znalezione błędy i decyzje

1. **Nadawca listu nie był nadawcą konkursu.** Reset szedł zawsze od `DEFAULT_FROM_EMAIL`, a każdy
   inny list konta (aktywacja, zaproszenia) od `Competition.from_email`. Poprawka: pusty
   `from_email` formularza → `mail_from(konkurs żądania)` (`QueuedPasswordResetForm.send_mail`).
2. **Konta bez hasła platformy nie dostawały linku.** `PasswordResetForm.get_users` Django pomija
   konta z nieużywalnym hasłem, a nasze ekrany obiecują, że konto z Google/Facebooka (i konto,
   któremu allauth wyczyścił hasło przy łączeniu) ustawia hasło przez „Nie pamiętasz hasła?”.
   Poprawka: `get_users` bez warunku `has_usable_password()` – nadal wyłącznie konta aktywne.
3. **Konto nieuruchomione dostawało ciszę.** Zaproszony uczeń (import listy klasowej, zgłoszenie
   przez opiekuna drużyny) i konto z rejestracji przed aktywacją są nieaktywne, więc formularz
   nie wysyłał nic. **Decyzja:** zaproszony uczeń dostaje ponowione zaproszenie (zgody zbiera ekran
   zaproszenia); konto z rejestracji – patrz M1 w § 2a. Odpowiedź strony bez zmian.
4. **Link aktywacyjny uruchamiał konto zaproszone z pominięciem zgód.** `resend_activation`
   wysyłał zaproszonemu uczniowi zwykły link aktywacyjny, a ten aktywował konto bez zgód i bez
   hasła; po poprawce 2 dałoby się potem ustawić hasło resetem. Poprawka: `resend_activation`
   wysyła takiemu kontu zaproszenie, a `activate_with_token` odmawia konta z oczekującym
   zaproszeniem (także dla linków wydanych przed poprawką).
5. Konta zablokowane (`is_active=False` z potwierdzonym adresem) i zanonimizowane – nic, bez zmian.

### 2a. Poprawki po przeglądzie (5.10.2026)

- **H1** – koordynator nie aktywuje ręcznie konta z niezaakceptowanym zaproszeniem („Aktywuj ręcznie”
  i checkbox „Konto aktywne” odmawiają z prośbą o ponowienie zaproszenia). Konto bez hasła, którego
  profil z zaproszenia nie ma kompletu wymaganych zgód (`consents.required_kinds` kontra niewycofane
  `ConsentRecord`), nie dostaje linku resetu, a `/reset/…` odmawia takiego konta (także dla linku
  z AUTH-01b). Bramki zgód przy logowaniu **nie** dokładamy: aplikacja nie ma ekranu uzupełniania
  zgód, a jego budowa wykracza poza to zadanie – droga do takiego stanu jest zamknięta u źródła.
- **M1 (zmiana decyzji z § 2.3)** – konto z rejestracji przed aktywacją dostaje **link resetu**,
  nie link aktywacyjny. Zapis nowego hasła zastępuje hasło z rejestracji (mógł je wpisać ktokolwiek,
  kto zarejestrował cudzy adres) i dopiero wtedy aktywuje konto (`account.activated_by_password_reset`).
- **M2** – konto bez hasła dostaje link tylko z adresem potwierdzonym naszą aktywacją albo przez
  dostawcę (wpis allauth `EmailAddress(verified=True)` dla adresu konta); samo `email_verified_at`
  nie wystarcza (hurtowe wypełnienie migracją `accounts.0010`). Konto, któremu allauth wyczyścił
  hasło, loguje się Google'em i ustawia hasło z panelu (AUTH-01b).
- **M3** – `mail_from` pomija nadawcę konkursu spoza `ALLOWED_SENDER_DOMAINS` (domyślnie
  `SITE_DOMAIN`, ta sama zmienna co relay `mail`) i wraca do `DEFAULT_FROM_EMAIL`, ostrzegając raz.
- **L1–L4** – komunikat „Zaproszenie zostało wysłane ponownie.” i audyt `participant.invitation_resent`;
  link i nadawca zaproszenia z konkursu ucznia; kubełek limitu per adresat (bez IP) i odstęp 10 min
  między zaproszeniami z formularzy publicznych; reset koordynatora według tej samej reguły
  (`reset_eligible`), łącznie z widocznością przycisku.
- Link **resetu** aktywnego konta nadal prowadzi pod host żądania: konto jest jedno na instalację
  i loguje się pod każdym hostem, a nadawca listu jest nadawcą tego samego konkursu, co link.

## 3. Sprawdzone i działające (testy w `apps/web/tests/test_password_reset_tenancy.py`)

- link pod host żądania (domena IQO, domena OK, prefiks `/druga/`), przekierowanie
  `set-password` pod tym samym hostem i prefiksem, logowanie nowym hasłem,
- język listu = język interfejsu konkursu (IQO po angielsku), temat z nazwą serwisu z `/cms/`,
- token wygasły i zmyślony → „link nieważny”,
- strony resetu poza pamięcią stron (allow-lista `apps.web.page_cache`), CSRF wymagany,
- motyw IQO: strony resetu dostają tokeny i arkusz motywu, sloty – aplikacji (formularze),
- `PASSWORD_RESET_TIMEOUT` = 24 h – bez zmian.

## 4. Do sprawdzenia przez operatora na produkcji

Patrz `docs/OPERACJE.md` § 9.7.
