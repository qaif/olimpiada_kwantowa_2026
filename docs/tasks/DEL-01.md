# DEL-01: Rejestracja przez delegacje krajowe (IQO)

## 0. Cel (polecenie organizatora, 4.10.2026)

W olimpiadzie międzynarodowej (IQO) uczniowie **nie rejestrują się sami**. Kolejność:
1. Koordynator wysyła **zaproszenie** do opiekuna drużyny (team leader) z danego kraju.
2. Opiekun zakłada konto z zaproszenia i **rejestruje uczniów ze swojego kraju**.
3. **Z jednego kraju może być kilku opiekunów** (wspólnie prowadzą jedną delegację kraju).

Konkurs `kwantowa` bez żadnej zmiany (tryb rejestracji jak dziś, testy niezmienności).

## 1. Tryb rejestracji konkursu

- `Competition.registration_mode`: `OPEN` (dziś, domyślnie) | `DELEGATIONS`.
- W `DELEGATIONS`: publiczna samorejestracja uczestnika (`/register/`, API, social login,
  rejestracja szkolna/importy) **wyłączona** dla tego konkursu – strona rejestracji pokazuje
  wyjaśnienie („Students are registered by their national team leader; contact the organizer”)
  i adres kontaktowy konkursu. Logowanie istniejących kont działa normalnie.
- Przełączenie trybu: superkoordynator/koordynator w „Ustawieniach konkursu” (z ostrzeżeniem),
  audyt. Kraje: regiony `COUNTRY` konkursu (REG-01, `custom_regions`).

## 2. Model

- `Delegation`: konkurs, edycja, kraj (`accounts.Region` poziomu `COUNTRY`), `max_students`
  (domyślnie z ustawienia konkursu, np. 6; koordynator może zmienić per delegacja), status
  (`ACTIVE`/`CLOSED`), notatka koordynatora; unikalne (edycja, kraj).
- Rola `TEAM_LEADER` w `CompetitionRole` + `Membership`; powiązanie `DelegationLeader`
  (delegacja, użytkownik, kto zaprosił, kiedy przyjął) – **wielu opiekunów na delegację**,
  jeden użytkownik może prowadzić tylko jedną delegację w edycji (walidacja).
- `DelegationInvitation`: e-mail, delegacja (kraj), token (jednorazowy, ważny 14 dni, haszowany
  w bazie), status (`PENDING`/`ACCEPTED`/`REVOKED`/`EXPIRED`), kto wysłał, ponowne wysłanie.
  Reużyj wzorców z istniejących zaproszeń (`apps/accounts/bulk_registration.py`,
  `create_invitation`, `accept_invitation`, `queue_mail`) zamiast pisać od zera.
- Uczestnik zarejestrowany przez opiekuna: `Participant` z `region` = kraj delegacji, pole
  `delegation` (FK, null dla trybu OPEN), `registered_by` (opiekun).

## 3. Przepływy

**Koordynator** (`/coordinator/delegations/`):
- lista krajów/delegacji: kraj, opiekunowie (zaproszeni/przyjęci), liczba uczniów / limit, status;
- „Zaproś opiekuna” (e-mail + kraj; tworzy delegację, jeśli jej nie ma); kolejny opiekun dla tego
  samego kraju dołącza do istniejącej delegacji; ponów / cofnij zaproszenie; usuń opiekuna;
- zmiana limitu, zamknięcie delegacji (blokuje dalszą rejestrację); eksport CSV.

**Opiekun** (zaproszenie → `/delegation/accept/<token>/`):
- zakłada konto (imię, nazwisko, hasło, zgody: regulamin, RODO) albo – jeśli e-mail już ma konto –
  loguje się i przyjmuje zaproszenie; e-mail z zaproszenia musi się zgadzać z kontem;
- panel `/delegation/`: dane delegacji, współopiekunowie (widzi ich imiona i e-maile), lista
  uczniów, „Dodaj ucznia” (imię, nazwisko, e-mail, data urodzenia, szkoła/placówka, klasa jeśli
  konkurs pyta), edycja danych przed aktywacją ucznia, usunięcie ucznia przed startem etapu,
  limit `max_students` egzekwowany w serwisie (transakcja + blokada wiersza delegacji – dwóch
  opiekunów naraz nie przekroczy limitu);
- każdy uczeń dostaje e-mail z linkiem aktywacji konta (ustawia hasło, akceptuje zgody;
  niepełnoletni – zgoda opiekuna prawnego wg istniejącego mechanizmu zgód; opiekun drużyny może
  wgrać/potwierdzić zgodę, jeśli istniejący mechanizm na to pozwala – nie wymyślaj nowego prawa);
  status ucznia w panelu: zaproszony / aktywny.
- Uprawnienia: opiekun widzi i edytuje **wyłącznie** uczniów swojej delegacji (404 dla cudzych);
  nie widzi ocen, prac ani danych innych krajów.

## 4. Wymagania przekrojowe

- Wszystko per konkurs (`request.competition`, `for_competition`), audyt każdej akcji,
  throttling zaproszeń i dodawania uczniów, e-maile przez `queue_mail` w języku odbiorcy
  (`language_for`), wszystkie napisy przez gettext + tłumaczenia w 10 katalogach (`backend/locale`,
  styl i jakość jak istniejące; `msgfmt --check`), UI zgodny z `docs/UI.md`.
- RODO: rejestr czynności (nowa czynność „Delegacje”), eksport danych konta, usuwanie konta.
- Czat (`apps/chat`): opiekun drużyny **nie** jest uczestnikiem – nie dostaje dostępu do czatu
  (chyba że trywialnie jako „organizator”? – NIE; zostaw poza zakresem i zapisz w lukach).
- Dokumentacja: OPERACJE, PODRĘCZNIK-ORGANIZATORA (nowy rozdział), nowy krótki
  `docs/PODRECZNIK-OPIEKUNA-DRUZYNY.md` (po angielsku – IQO jest anglojęzyczny), CHANGELOG.

## 5. Testy

Tryb OPEN bez zmian (kwantowa: rejestracja działa, złote testy); DELEGATIONS: publiczna
rejestracja zablokowana na wszystkich drogach (HTML, API, social, import); zaproszenie
(ważność, jednorazowość, zgodność e-maila, cofnięcie, wygaśnięcie); **dwóch opiekunów jednego
kraju** widzi tę samą delegację i tych samych uczniów; limit pod współbieżnością; izolacja
(opiekun kraju A nie widzi uczniów kraju B → 404; opiekun innego konkursu → 404; recenzent → 403);
aktywacja ucznia; region ucznia = kraj delegacji; e-maile w języku odbiorcy; uprawnienia
koordynatora.


## 6. Realizacja (4.10.2026) – doprecyzowania i odstępstwa

**Doprecyzowanie organizatora (w trakcie):** tryb `OPEN` jest domyślny dla **każdego** konkursu –
istniejącego (migracja `tenancy.0013`) i nowego (`create_competition`, „Nowy konkurs”, `/setup/`;
szablony nie mają tego pola). `create_competition --registration open|delegations` (domyślnie `open`;
`delegations` wymaga `--regions countries`). Test współistnienia: konkurs `OPEN` obok konkursu
`DELEGATIONS` na jednej platformie.

Gdzie co jest:
- model: `tenancy.Competition.registration_mode` / `delegation_max_students` / `uses_delegations`;
  `apps/accounts/delegations.py` (modele), `apps/accounts/delegation_services.py` (wszystkie czynności);
  `Participant.delegation`, `Participant.registered_by`; `ConsentRecord.team_leader`; rola
  `CompetitionRole.TEAM_LEADER` (`team_leader`, grupa z migracji `accounts.0037`),
- bramka rejestracji: `current_registration_status` zwraca powód `delegations` (formularz, API, social,
  nawigacja, strona główna – jedno rozstrzygnięcie); import listy (`bulk_registration.ensure_import_allowed`);
  rejestracja opiekuna szkolnego (`registration_enabled_for_request` → 404),
- ekrany: `apps/web/views/coordinator_delegations.py`, `apps/web/views/delegation.py`,
  `apps/web/urls_delegations.py`, szablony `web/coordinator/delegation*.html`, `web/delegation/*.html`.

Odstępstwa od § 2–4 (z powodem):
1. **Status zaproszenia jest wyliczany** (`accepted_at`/`revoked_at`/`expires_at`), a nie zapisany
   w kolumnie – ta sama decyzja, co przy `InvitationCode.status()`: upływu czasu żaden zapis nie zauważy,
   a wyliczenie nie rozjedzie się z faktami. Etykiety są te same (PENDING/ACCEPTED/REVOKED/EXPIRED).
2. **„Ponów zaproszenie” wymienia token** – w bazie jest tylko skrót, więc ponowne wysłanie tego samego
   linku wymagałoby trzymania tokenu jawnie. Jedno otwarte zaproszenie na (delegacja, adres) – więz.
3. **Uczeń z adresem, który ma już konto, jest odmową** (`EMAIL_TAKEN`), a nie dowiązaniem: dopisanie
   działającego konta dawałoby opiekunowi wgląd w dane osoby bez jej zgody. Taki uczeń trafia do drużyny
   przez organizatora (luka niżej).
4. **Okno rejestracji edycji obowiązuje opiekunów** (dodanie ucznia), a zamknięta delegacja zamraża
   także poprawki i wypisanie – „zamknięta” znaczy „lista ostateczna”.
5. **Usunięcie ucznia** = usunięcie konta, które powstało ze zgłoszenia (`profile._erase_account`: bez
   śladu w zawodach kasowane, ze śladem – anonimizowane i odpinane od delegacji). Konto z innym profilem
   lub inną rolą – wyłącznie przez organizatora.
6. **Zgoda opiekuna prawnego**: opiekun drużyny może podać adres rodzica (jak import listy), ale jej
   nie potwierdza – istniejący mechanizm (`apps.accounts.guardian`) wymaga działania ucznia/rodzica.
7. **Przyjęcie zaproszenia przez nowe konto** zakłada konto od razu aktywne (link z listu potwierdza
   adres) i **nie loguje** automatycznie – jak przy zaproszeniu ucznia.
8. **Przełączenie trybu** jest polem ekranu „Ustawienia konkursu” (i `/admin/`); osobny wpis audytu
   `competition.registration_mode_changed`. Ekranu delegacji poza trybem `DELEGATIONS` nie ma (404).
9. Ekrany koordynatora są po polsku bez gettext (I18N-01 § 0); ekrany opiekuna, ekran zaproszenia
   i listy – przez gettext w 10 katalogach.

Znane luki:
- czat (`apps/chat`) – opiekun drużyny nie jest uczestnikiem i nie ma do niego dostępu (zgodnie z § 4),
- adres z istniejącym, **nieaktywnym** kontem (np. porzucona rejestracja) nie przyjmie zaproszenia, dopóki
  konto nie zostanie aktywowane albo usunięte przez organizatora,
- uczeń z istniejącym kontem nie może być dopisany do delegacji z panelu (pkt 3) – wymaga obsługi ręcznej,
- automatyczna retencja nie obejmuje osobno kont opiekunów drużyn (jak opiekunów szkolnych) – czyszczenie
  przez usunięcie konta,
- tłumaczenia nowych napisów są maszynowe (do przeglądu native speakerów).
