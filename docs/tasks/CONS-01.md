# CONS-01: Uzupełnienie zgód po zalogowaniu

## 0. Cel i granice

AUTH-01a (#67) zamknęło drogi do stanu „konto aktywne bez wymaganych zgód” u źródła i zapisało
wprost, że aplikacja **nie ma ekranu uzupełniania zgód**. Stan ten i tak powstaje – i będzie
powstawał – bez niczyjego błędu:

- organizator **zmienia wersję dokumentu** (regulamin, polityka RODO, wzór zgody opiekuna): ze stałej
  w kodzie (`TERMS_VERSION`, Konkurs #1 bez flagi `per_competition_consents`) albo ekranem
  „Zgody konkursu” (`ConsentDefinition.version`, konkursy z flagą – IQO),
- koordynator **dokłada wymaganą zgodę** albo przestawia dobrowolną na wymaganą,
- uczestnik **zmienia datę urodzenia** w profilu i staje się osobą, od której wymagamy zgody opiekuna,
- profile sprzed wpisów dowodowych (`ConsentRecord`) albo z importów, które ich nie zakładały.

Zadanie dokłada **bramkę** dla zalogowanego uczestnika i ekran „Uzupełnij zgody”. Czego **nie** robi:

- nie zmienia modelu `ConsentRecord` ani `ConsentDefinition` (bez migracji we wspólnych aplikacjach),
- nie zmienia reguł zgody opiekuna online (`apps.accounts.guardian`) ani tego, co dziś wolno
  osobie niepełnoletniej bez potwierdzenia opiekuna (§ 4),
- nie dotyka ekranów personelu (koordynator, komisja, recenzja, nadzór, `/cms/`, `/admin/`),
- nie zbiera zgód dobrowolnych (publikacja nazwiska zostaje przełącznikiem w panelu).

## 1. Kiedy zgoda „brakuje”

Uczestnik (profil `Participant` w konkursie żądania) ma brak, gdy dla **wymaganego od niego**
rodzaju zgody (`consents.required_kinds` – zawsze wymagane plus `required_for_minor` przy
`is_minor`) **nie ma aktywnego** (`withdrawn_at IS NULL`) wpisu `ConsentRecord` z
`document_version` **równą bieżącej wersji** z zestawu konkursu (`consents.consent_set`).

- Wersja jest porównywana dokładnie (napis). Zmiana wersji = ponowna zgoda od każdego, kto złożył
  ją pod wcześniejszą. Poprawka literówki w treści **bez** zmiany wersji niczego nie wymusza.
- Zgoda opiekuna (`GUARDIAN`) jest spełniona każdym aktywnym wpisem bieżącej wersji: oświadczeniem
  ucznia z rejestracji / zaproszenia / tego ekranu albo potwierdzeniem online od opiekuna.
  Poprawka przy okazji: `guardian.consent_version()` brał wersję ze stałej, a nie z zestawu
  konkursu – potwierdzenie opiekuna w konkursie z flagą zapisywało wersję, której zestaw nie zna.
- Zgody dobrowolne nigdy nie blokują.

## 2. Bramka (`apps.consent_gate.middleware.ConsentGateMiddleware`)

`process_view`, w łańcuchu **za** 2FA, strefą czasową ucznia i komunikatami, **przed** bramką
nadzoru (PROC-01): najpierw tożsamość (2FA), potem zgody, potem nadzór etapu. Okna czasowe (TZ-01)
egzekwują widoki i serwisy – kolejność się nie zmienia.

**Obszar bramki** – pierwszy człon trasy (`resolver_match.route`, więc niezależnie od prefiksu
ścieżki konkursu): `me/` (panel, upload, test, czat, nadzór ucznia, dyplomy…), `forum/`,
`notebook-starter/`, `webinars/`, `warsztaty/`, `payments/` oraz API przestrzeni `submissions`,
`competitions`, `appeals`. Nowe ekrany uczestnika pod `/me/` wchodzą do bramki same.

**Przepuszczane w obszarze** (lista dozwolona, test kontraktu pilnuje, że nazwy istnieją): ekran
zgód i jego POST, `/me/profile/` (sprostowanie danych – art. 16 RODO; zmiana daty urodzenia zmienia
wymagane zgody), prośba do opiekuna `/me/guardian/`, autozapis testu (odpowiedzi nie mogą zginąć),
klient nadzoru ucznia (token, komunikaty, akcje – sesja nadzoru nie może się zerwać), wypis z listów
forum. **Poza obszarem** zostaje wszystko inne, w tym: wylogowanie, zmiana hasła, `/account/…`
(eksport danych, usunięcie konta, adres e-mail, język i kontrast, 2FA), zgłoszenia do organizatora,
strony publiczne (CMS, dokumenty, wyniki), `/zgoda/<token>/` (opiekun bywa przy tym samym komputerze),
pliki statyczne (WhiteNoise nie dochodzi do warstwy).

**Kto przechodzi bez pytania**: anonim, `is_staff`/`is_superuser`, żądanie bez konkursu, adres spoza
obszaru – **zero zapytań**. Pozostali: jeden odczyt cache'a (stan konta w konkursie); przy chybieniu
**jedno** zapytanie (profil + aktywne wpisy w `ArraySubquery`), wynik w cache'u na 5 min.
Zestaw zgód przy fladze `per_competition_consents` – osobny wpis cache'a na konkurs; bez flagi
stała, bez zapytania. Unieważnienie: sygnały `post_save`/`post_delete` na `ConsentRecord`,
`Participant` (każdy zapis zgód serwisem `record_consents` zapisuje też profil – `bulk_create`
sygnałów nie wysyła), `ConsentDefinition`; TTL jako siatka bezpieczeństwa (urodziny).

**Odpowiedź bramki**: GET przeglądarki – 302 na `/me/consents/complete/?next=<adres>`; HTMX – 403
z `HX-Redirect`; API/JSON – 403 `{"code": "CONSENTS_REQUIRED", "detail", "url"}`; pozostałe POST-y
przeglądarki – 302 na ekran (treść POST-u przepada; komunikat ekranu mówi, co zrobić).

API z nagłówkiem `Authorization` (token): konto liczone w kolejności DRF (`proctoring.middleware.
request_user`) – jedno zapytanie o token, wyłącznie na adresach przestrzeni z bramką.

Wyłącznik instalacji: `CONSENT_GATE_ENABLED` (`.env`, domyślnie wł.; w testach wył. – testy bramki
włączają ją same, tak jak cache stron).

## 3. Ekran „Uzupełnij zgody” (`/me/consents/complete/`, `web:consent-complete`)

- Szablon `consent_gate/complete.html` dziedziczy `base.html` – motyw konkursu (IQO Quantum) i język
  interfejsu jak każdy ekran panelu; napisy przez gettext, 10 katalogów w `apps/consent_gate/locale`.
- Pola wyłącznie dla **brakujących** zgód, z tą samą etykietą (treść + odnośnik do PDF/strony
  dokumentu, nazwa organizatora) co w rejestracji (`consents.label`). Przy zgodzie złożonej wcześniej
  pod inną wersją: „Dokument zmienił się: obowiązuje wersja X (zaakceptowano wersję Y)”.
- Zapis (`services.complete_consents`): transakcja z blokadą wiersza profilu, ponowne policzenie
  braków (podwójne kliknięcie nie mnoży wpisów), `ConsentRecord` na każdą brakującą zgodę –
  `document_version` bieżąca, `given_at`, `source=panel`, `ip_address=client_ip(request)` (ta sama
  reguła zaufania do proxy co w audycie i przy zgodzie opiekuna); projekcje na profilu
  (`terms_accepted_at`, `gdpr_consent_at`, `guardian_consent` – wyłącznie na `True`;
  `publish_full_name` nietknięte). **Jeden** wpis audytu `participant.consents_completed`:
  `{"source", "language", "consents": {RODZAJ: {"version", "text_sha256", "previous_version"}}}`
  – język interfejsu i skrót SHA-256 dokładnie tej treści, którą uczestnik widział (`plain_text`).
  `ConsentRecord` nie ma kolumn języka i skrótu; dokładanie ich byłoby migracją wspólnego modelu
  dla jednej drogi – audyt jest dowodem uzupełniającym, łączonym po profilu i czasie.
- Brak czegokolwiek do uzupełnienia → przekierowanie na `next` (sprawdzony
  `url_has_allowed_host_and_scheme`) albo panel. Konto bez profilu uczestnika – 403.
- Sekcja „Nie zgadzasz się?”: eksport danych, usunięcie konta, zgłoszenie do organizatora,
  wylogowanie.

## 4. Osoby niepełnoletnie

Dziś (kod, nie deklaracja): od małoletniego wymagamy oświadczenia `GUARDIAN` przy rejestracji
i przyjęciu zaproszenia; **potwierdzenie online od opiekuna** nie blokuje panelu, uploadu, testu ani
czatu – wymaga go wyłącznie zgoda na nadzór zdalny (`proctoring.services.give_consent`). Zostaje
identycznie: brak `GUARDIAN` (np. po zmianie wersji wzoru zgody) jest brakiem jak każdy inny i ekran
pokazuje pole oświadczenia; brak potwierdzenia online – sam stan (brak / oczekuje / potwierdzona)
i formularz „wyślij prośbę / wyślij ponownie” (`web:guardian-request`, istniejący przepływ, limit
`password_reset` 5/h wspólny z resetem hasła), bez blokady.

## 5. Koordynator

- Pulpit: kafelek „Uczestnicy z brakującymi zgodami” (liczba, cache 60 s) z odnośnikiem do CSV.
- `GET /coordinator/consents/missing.csv` (`web:coordinator-consent-gaps`): kod publiczny, imię,
  nazwisko, e-mail, brakujące zgody (rodzaj + wymagana wersja). Tylko dane, które koordynator już
  widzi na liście kont – bez nowych danych osobowych; wpis audytu `consent_gate.exported`.
  Liczone są konta aktywne, bez anonimizowanych (zaproszeni złożą zgody na ekranie zaproszenia).
- Ekran zmiany wersji (`consent_version_confirm.html`) mówi wprost, że zmiana wymusi ponowną zgodę.
- `manage.py consent_gate_report` – liczby per konkurs i rodzaj (bez danych osobowych), do
  sprawdzenia przed wdrożeniem i po zmianie wersji.

## 6. Testy (`apps/consent_gate/tests`)

Każdy rodzaj zgody (regulamin, RODO, opiekun dla małoletniego, dobrowolna nie blokuje); zmiana
wersji (stała i `ConsentDefinition`) wymusza zgodę; ścieżki małoletniego (oświadczenie, stan opiekuna,
wysyłka prośby z limitem, potwierdzenie online spełnia `GUARDIAN`); lista dozwolona (RODO, hasło,
wylogowanie, autozapis); personel i anonim bez zapytań; budżet zapytań uczestnika (0 przy trafieniu,
1 przy chybieniu); unieważnienie cache'a; API (sesja i token); HTMX; prefiks ścieżki; motyw IQO
i domyślny; angielski interfejs; CSV i kafelek koordynatora (izolacja konkursów); kolejność warstw.

## 7. Operator

`docs/OPERACJE.md` § 51.
