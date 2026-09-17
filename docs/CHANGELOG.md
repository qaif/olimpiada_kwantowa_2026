# Historia zmian

Jedna linia na wydanie — treść pochodzi z opisu commitu oznaczonego tagiem (`git log --tags`).
Numeracja jest `v<major>.<minor>.<patch>`, a tag wydania jest zarazem wartością `APP_VERSION`
wpisywaną przez `scripts/deploy.sh`, więc numer widoczny w stopce serwisu i na `/status/` odpowiada
dokładnie jednemu wierszowi tej tabeli.

Pełny opis każdej funkcji: [`../README.md`](../README.md). Stan prac i dług techniczny:
[`BACKLOG.md`](BACKLOG.md).

## Niewydane (po `v0.23.0`)

- Brak — katalog roboczy jest równy tagowi `v0.23.0`. Etap 1 systemu wielokonkursowego jest
  domknięty; etap 2 (marka i dokumenty jako konfiguracja, edytor procesu, rejestracja część 2)
  nie został jeszcze zlecony.

## Wydania

| Wersja | Data | Zmiana |
|---|---|---|
| **v0.23.0** | 2026-09-17 | etap 1, wydanie D (domknięcie): `NOT NULL` na kluczach `competition`, uczestnik per konkurs (`Participant.user` jako klucz obcy, `participant_for`), kod publiczny i numer dyplomu z prefiksami konkursu (`OLM-`/`OK` bez zmian dla Konkursu #1), jedna edycja bieżąca i unikalny rocznik per konkurs, kolumny konkursu w zgłoszeniach pomocy, audycie, kluczach API, webhookach, szablonach dyplomów i szablonach komentarzy, `create_competition` zakłada edycję, etapy i koordynatora, `check_memberships`, runbook drugiego konkursu; 3235 testów, 0 xfail |
| **v0.22.0** | 2026-09-17 | etap 1, wydanie C: odczyty w panelach zakresowane do konkursu (`for_competition`, `current_edition(competition)`), CMS per witryna Wagtaila, komunikaty z kolumną konkursu, strona „Ustawienia konkursu” za flagą `competition_settings_page`, 14 z 15 testów izolacji zielonych |
| **v0.21.0** | 2026-09-17 | etap 1, wydanie B: `accounts.Membership` i role per konkurs (za flagą `memberships_enforced`), nullowalne klucze obce `competition` z backfillem do Konkursu #1, `Caddyfile` generowany z `EXTRA_DOMAINS`, `pg_dump` przed migracjami w `deploy.sh`, testy izolacji i niezmienniczości |
| **v0.20.0** | 2026-09-17 | etap 1, wydanie A: model `tenancy.Competition` 1:1 z witryną Wagtaila, `CompetitionMiddleware` i `current_competition()`, Konkurs #1 utworzony z istniejącej witryny, `create_competition` |
| **v0.19.0** | 2026-09-17 | integracje (API, webhooki), testy online (`apps/quiz`), dyplomy 2.0 z pieczęcią PAdES, kopie zapasowe i monitoring, CI, 2FA za wyłączonym przełącznikiem, import grupowy uczniów, okręgi szkolne, dokumentacja i licencja |
| **v0.18.0** | 2026-09-17 | scalony zduplikowany `msgid` („wersja %(version)s”), który wywracał `msgfmt` przy budowaniu obrazu |
| **v0.17.1** | 2026-09-17 | wersja aplikacji w stopce i na `/status/` pochodzi z `APP_VERSION` (tag wdrożenia), a nie ze sztywnego „1.0” |
| **v0.17.0** | 2026-09-16 | przebudowa układu paneli, narzędzia RODO, zgłoszenia i pomoc (support desk), FAQ, ogłoszenia i strona statusu |
| **v0.16.0** | 2026-09-16 | drugi zestaw 15 funkcji paneli (koordynator, recenzent, uczestnik) |
| **v0.15.0** | 2026-09-16 | pobieranie prac i paczki ZIP, edytowalne skale punktacji, rozwiązania w JPEG oraz 15 funkcji paneli |
| **v0.14.0** | 2026-09-16 | ocenianie przed zamknięciem etapu („Zablokuj oddane prace do oceny”) |
| **v0.13.1** | 2026-09-16 | pasek linii czasu spoczywa jako cienka linia i rozwija się w dół po najechaniu; każdy warsztat jest osobnym wydarzeniem |
| **v0.13.0** | 2026-09-16 | pasek linii czasu w nagłówku i wydarzenia zarządzane przez koordynatora |
| **v0.12.0** | 2026-09-15 | recenzent poprawia własną recenzję, koordynator odbiera recenzje i zarządza wszystkimi kontami |
| **v0.11.1** | 2026-09-15 | województwo członka komitetu jest opcjonalne i nie warunkuje już przydziału |
| **v0.11.0** | 2026-09-15 | zaproszenia do komitetu e-mailem, z indywidualnym kodem dla każdego adresu |
| **v0.10.0** | 2026-09-15 | ręczny przydział recenzentów i korekty ocen przez koordynatora |
| **v0.9.4** | 2026-09-15 | polskie strony błędów: widok odmowy CSRF wyjaśniający przypadek nieaktualnego formularza (logowanie w innej karcie) z odnośnikiem ponowienia, plus 403/404/500 |
| **v0.9.3** | 2026-09-15 | tag Google w `<head>` na każdej stronie (z nonce, bez wyjątku w CSP) z Consent Mode v2: `analytics_storage` odmówione do czasu zgody |
| **v0.9.2** | 2026-09-15 | etap treningowy niesie wyłącznie przykładowe zadania organizatora (jeden wspólny PDF); generator PDF-ów wycofany |
| **v0.9.1** | 2026-09-15 | retencja danych zdarzeń Google Analytics ustalona na 14 miesięcy (decyzja organizatora) |
| **v0.9.0** | 2026-09-15 | Google Analytics 4 wyłącznie za wyraźną zgodą (pasek zgody, wycofanie ze stopki, anonimizacja IP, funkcje reklamowe wyłączone); polityka cookies 1.1 |
| **v0.8.6** | 2026-09-15 | plik weryfikacyjny Google Search Console serwowany spod własnego adresu |
| **v0.8.5** | 2026-09-15 | przykładowe zadania organizatora jako zadania treningowe 1–4, strona „sprawdź skrzynkę” po rejestracji, uporządkowana strona główna, TikTok i YouTube w odnośnikach społecznościowych |
| **v0.8.4** | 2026-09-13 | wyszukiwarka szkół przepisana na czysty JavaScript (bez zależności z CDN), łagodniejsza reguła szkoły spoza wykazu, oznaczenia pól wymaganych, odnośniki społecznościowe w ustawieniach serwisu |
| **v0.8.3** | 2026-09-12 | porządki w skrypcie testu e2e (długa asercja rozbita na dwie) |
| **v0.8.2** | 2026-09-12 | pozycje menu głównego (Zadania, Harmonogram, Warsztaty) przenoszą się do przyklejonego paska konta |
| **v0.8.1** | 2026-09-12 | logo w pasku konta, pasek przyklejony do góry okna (statyczny na telefonach), nawigacja serwisowa poniżej |
| **v0.8.0** | 2026-09-12 | aktywacja konta e-mailem (link 4 h, automatyczne czyszczenie nieaktywowanych kont, ręczna aktywacja przez koordynatora), telefon w profilu, edycja danych ze zmianą adresu, samodzielne usunięcie konta z anonimizacją; własna CAPTCHA, pułapka i minimalny czas wypełniania; etap treningowy |
| **v0.7.0** | 2026-09-12 | „Termin” stacjonarny pokazywany wyłącznie dla etapów z jawnymi dniami wydarzenia; seed treści nie odtwarza stron i aktualności skasowanych w `/cms/` |
| **v0.6.0** | 2026-09-10 | wersjonowane, linkowane zgody rejestracyjne (regulamin, RODO, zgoda opiekuna dla niepełnoletnich, publikacja nazwiska) z dowodem `ConsentRecord` i audytem; wzór zgody opiekuna do wydruku; `GET /api/auth/consents/` |
| **v0.5.2** | 2026-09-10 | koordynator steruje rejestracją uczestników (włącznik, godzina otwarcia i zamknięcia) jedną bramką dla formularza, API i logowania zewnętrznego |
| **v0.5.1** | 2026-09-10 | etapy nazwane Etap I/II/III, a nazewnictwo „okręg” zastąpione „województwem” w całym interfejsie |
| **v0.5.0** | 2026-09-08 | edycja terminów i zakładanie etapów w panelu (z blokadami domenowymi i audytem), zarządzanie zadaniami (treść PDF, formaty, limity), podgląd treści przed otwarciem etapu; harmonogram renderowany z bazy |
| **v0.4.1** | 2026-09-08 | logotypy partnerów, blok harmonogramu warsztatów, miejsce etapu (`Stage.location`) |
| **v0.4.0** | 2026-09-08 | logowanie przez Google i Facebooka (rejestracja przez adapter, ze zgodami RODO) oraz własna usługa poczty wychodzącej (Postfix + OpenDKIM, relay tylko wewnętrzny) z rekordami DNS wypisywanymi przez skrypt wdrożeniowy |
| **v0.3.3** | 2026-09-08 | reset hasła e-mailem dla wszystkich ról (limit prób, brak enumeracji kont, token jednorazowy 24 h, audyt), konfiguracja `EMAIL_URL`, mailpit w devie |
| **v0.3.2** | 2026-09-07 | oficjalny logotyp w nagłówku, favicon, ikona dotykowa i `og:image` składane z pliku organizatora |
| **v0.3.1** | 2026-09-07 | regulamin v1.0 z 2 września 2026 (model trzech etapów, PDF + DOCX), strona „Partnerzy” z poziomami partnerstwa |
| **v0.3.0** | 2026-09-07 | sekcja „Dokumenty”: strona indeksu, rozwijane menu bez JavaScriptu, wszystkie dokumenty organizatora pod `/dokumenty/`, trwałe przekierowania ze starych adresów |
| **v0.2.1** | 2026-09-07 | polityka RODO i standardy ochrony małoletnich przepisane 1:1 z PDF-ów organizatora jako HTML, uzupełniona strona komitetów |
| **v0.2.0** | 2026-09-06 | import treści starego serwisu: ustawienia marki, typ strony treści, strony informacyjne, polityki jako dokumenty, aktualności, sekcja kroków na stronie głównej, `seed_edition_kwantowa` |
| **v0.1.1** | 2026-09-06 | system projektowy: tokeny kolorów z wariantem ciemnym, samodzielnie serwowane kroje pisma, komponenty (karty, odznaki, tabele, linia czasu, odliczanie, segmenty punktów, strefa upuszczania), przebudowa szablonów wszystkich paneli |
| **v0.1.0** | 2026-09-05 | zamknięcie pierwszej fazy: API administracyjne Caddy'ego wyłącznie lokalnie, bezpiecznik produkcyjny dla klucza i poświadczeń S3, bezpieczne domyślne ciasteczka |

## Tagi zadań

Poza wydaniami repozytorium niesie tagi `task/T-01` … `task/T-10` (z wariantami `-fix`) — punkty
kontrolne kolejnych zadań z `docs/tasks/`. Nie są wydaniami i nie należy ich podstawiać jako
`APP_VERSION`.
