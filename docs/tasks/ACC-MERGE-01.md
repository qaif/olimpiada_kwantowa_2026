# ACC-MERGE-01: Scalanie zdublowanych kont uczestnika

## 0. Cel i granice

Prośba organizatora z 18.10.2026 (pkt 2 listy): „scalanie zdublowanych kont zamiast wyłącznie
usuwania”. Ekran „Zdublowane konta” (`docs/tasks/ACC-DUP-01.md`, `docs/tasks/ACC-DUP-02.md`) umiał
tylko usunąć kopię. To wystarcza, gdy kopia jest pusta; nie wystarcza, gdy **oba** konta tej samej
osoby mają aktywność – np. zgody i zaświadczenie na pierwszym, a pracę z etapu na drugim. Usunięcie
któregokolwiek z nich zabiera część historii (albo anonimizuje kopię z pracą, zostawiając osobie
dwa kody publiczne).

Scalenie przenosi aktywność konta **źródłowego** („kopia”) na konto **docelowe**, a potem usuwa
kopię tą samą drogą co dotąd (`apps.accounts.profile.delete_account_by_coordinator`, audyt
`account.deleted_by_coordinator` z `via=merge`).

Czego zadanie **nie** robi:
- nie przepina **dziennika zdarzeń** (`core.AuditLog`) – wpisy kopii zostają przy jej identyfikatorze,
  bo dziennik jest dowodem tego, co się działo, a nie stanem,
- nie zmienia **ogłoszonych tabel wyników** (zamrożone snapshoty `apps.results`/`apps.medals`
  z kodem kopii) – to ogłoszony dokument; nowe przeliczenie pokaże już kod docelowy,
- nie przenosi **poświadczeń** kopii (hasło, token API, logowanie Google/Facebook, 2FA, sesje) –
  przeniesienie logowania zewnętrznego dałoby dostęp do konta docelowego drogą, której nikt nie
  potwierdzał; osoba loguje się na konto docelowe jego danymi (albo resetem hasła),
- nie scala kont z **innymi rolami** ani kont z różnych konkursów (§ 2).

## 1. Reguły (`apps/accounts/merge.py`, słownik `RULES`)

Każda relacja (FK/O2O/M2M) do `Participant` i `User` w całym `backend/apps` (i w aplikacjach
zewnętrznych) ma regułę. Test `test_every_relation_is_classified` zatrzymuje CI, gdy pojawi się
nowa relacja bez reguły – nowa funkcja musi świadomie odpowiedzieć „co z nią przy scaleniu”.

Rodzaje reguł:

| Reguła | Znaczenie |
|---|---|
| **przepnij** | wszystkie wiersze kopii dostają konto/profil docelowy |
| **przepnij bez duplikatów** | jw.; ten sam fakt u docelowego (ten sam klucz) → wiersz kopii odpada |
| **zachowaj nowszy** | jeden wiersz na klucz: zostaje ten z późniejszym znacznikiem czasu |
| **zostaje docelowy** | wiersz docelowego zostaje; gdy go nie ma – przepinamy wiersz kopii |
| **konflikt blokuje** | ten sam klucz u obu kont → scalenie odmówione z komunikatem |
| **reguła własna** | opis niżej (§ 1.1) |
| **rola – odmowa** | wiersz kopii albo docelowego oznacza inną rolę → odmowa (§ 2) |
| **zostaje z kopią** | poświadczenia, ślady personelu w CMS – znikają razem z kopią |
| **nie ruszamy** | dziennik zdarzeń |

### 1.1 Profil uczestnika i jego relacje

| Model | Reguła |
|---|---|
| `competitions.StageEntry` (wpisy do etapów; z nimi prace `Submission` + pliki, recenzje, oceny, odwołania, AI, przebiegi notatników, testy online `QuizAttempt`, rozmowy `InterviewBooking`/`InterviewScore`, formularz przyjazdu, obecność `AttendanceRecord`, dyplomy i zaświadczenia `results.Certificate`, korekty medali `MedalOverride`) | **własna**: etap tylko u kopii → przepnij; etap u obu – wpis **pusty** (bez prac, testu, rozmowy, formularza, obecności, dyplomu, korekty medalu, sesji nadzoru, punktów, decyzji komitetu, statusu innego niż „zarejestrowany”) odpada, a zostaje wpis z aktywnością; **aktywność w tym samym etapie u obu → konflikt** |
| `proctoring.ProctoringSession` (z dziećmi: zgody, zdarzenia, wiadomości, incydenty, nagrania) | konflikt blokuje (klucz: etap) |
| `competitions.TeamMember` | **własna**: przepnij; docelowe w drużynie tej samej edycji → konflikt |
| `tenancy.ParticipantFee` (z zamówieniami `payments.Order`) | **własna**, klucz cennik: tylko u kopii → przepnij; u obu – należność „nietknięta” (do zapłaty, bez zamówień i zdarzeń płatności) odpada, zostaje rozliczana; obie z ruchem pieniędzy → konflikt |
| `accounts.ConsentRecord` | przepnij (rejestr zdarzeń – cała historia obu kont) |
| `student_status.StudentStatusCertificate` (wersje) | **własna**, per edycja: tylko u kopii → przepnij; u obu → wersje kopii dopisane **po** wersjach docelowego (numer + maksimum docelowego); bieżące zostaje lepsze: zaakceptowane > oczekujące > odrzucone, przy równym stanie nowsze; przegrane oczekujące → „zastąpione nowszym plikiem” |
| `cms.WorkshopAttendance` (obecność na warsztatach) | przepnij bez duplikatów (klucz warsztat) |
| `appeals.Appeal.filed_by` | przepnij |
| `chat.Conversation` (rozmowa z organizatorem) | **własna**: tylko u kopii → przepnij; u obu → wiadomości kopii przechodzą do rozmowy docelowego, rozmowa kopii znika |
| `chat.Conversation` (rozmowa uczestnik–uczestnik, `participant_low/high`) | **własna**: przepnij z zachowaniem kolejności pary; obie kopie rozmawiały z tą samą osobą albo ze sobą → konflikt |
| `chat.Conversation.started_by`, `chat.Message.sender` | przepnij |
| `chat.ConversationMember` | przepnij bez duplikatów (klucz rozmowa) |
| `chat.ChatBlock` (blokujący/blokowany) | **własna**: przepnij bez duplikatów; blokada „sam siebie” odpada |
| `chat.ChatKey` (klucz szyfrowania) | **własna**: tylko u kopii → przepnij; u obu, a kopia ma rozmowy szyfrowane → konflikt (po scaleniu nie dałoby się ich odczytać); inaczej zostaje docelowy |
| `chat.ChatProfile` | zostaje docelowy |
| `alumni.AlumniProfile` | konflikt blokuje |
| `alumni.AlumniConsentEvent` | przepnij |
| `alumni.Mentorship` (mentor/podopieczny) | **własna**: przepnij; para „sam ze sobą” albo druga otwarta relacja tej samej pary → konflikt |
| `delegation_logistics.DelegationMember` (z odprawą, listami) | konflikt blokuje (klucz delegacja) |
| `time_windows.ParticipantWindow` | zachowaj nowszy (klucz plan, `set_at`) |
| `time_windows.ParticipantTimezone`, `problem_translations.StudentLanguage` | zachowaj nowszy (`set_at`) |
| `payments.BillingProfile` | zachowaj nowszy (`updated_at`) |

**Pola samego profilu** (docelowy zostaje, kopia go uzupełnia):
- zgody RODO i regulamin (`gdpr_consent_at`, `terms_accepted_at`) – późniejsza z dwóch dat
  (dowody `ConsentRecord` przechodzą w całości),
- zgoda opiekuna – jest, jeśli ma ją którekolwiek konto (zgoda dotyczy osoby, nie konta); razem z nią
  adres opiekuna z tego samego konta,
- zgoda na publikację nazwiska – przy różnicy z konta z nowszą deklaracją zgód (późniejsze
  `gdpr_consent_at`),
- puste u docelowego: telefon, klasa, adres opiekuna szkolnego, data urodzenia (tylko gdy zgadza się
  z rocznikiem docelowego),
- delegacja: różne delegacje u obu → konflikt; u docelowego brak → bierzemy z kopii.

Dane osobowe konta (imię, nazwisko, szkoła, kod publiczny) zostają z konta docelowego.

### 1.2 Konto (`User`)

| Model | Reguła |
|---|---|
| `accounts.Membership` | rola uczestnika tego konkursu – znika z kopią (docelowe ma swoją); każda inna → odmowa |
| `accounts.CommitteeMember`, `SchoolSupervisor`, `DelegationLeader`, `delegation_logistics.LogisticsAccess`, `DelegationMember.user` (opiekun), `proctoring.ProctorAssignment`, `translation_review.TranslatorGrant`, `webinars.Webinar.co_moderators` | rola – odmowa |
| `forum.ForumThread/ForumPost.author`, `ForumReport.reporter`, `ForumDecisionNotice` | przepnij |
| `forum.ForumSubscription` | przepnij bez duplikatów (klucz wątek) |
| `forum.ForumNotificationSettings`, `chat.ChatNotificationSettings`, `webinars.WebinarNotificationSettings`, `accounts.UserPreference` | zachowaj nowszy (`updated_at`) |
| `chat.MessageReport.reporter` | przepnij bez duplikatów (klucz wiadomość) |
| `translation_review.TranslationVote` | przepnij bez duplikatów (klucz propozycja) |
| `support.SupportTicket`, `translation_review.TranslationReport`, `webinars.WebinarAttendee` i **każdy** inny klucz `SET_NULL`/`PROTECT` do konta w aplikacjach projektu (autor, zgłaszający, „kto zrobił”) | przepnij (u konta czysto uczestnickiego te kolumny „personelu” są puste) |
| `authtoken.Token`, `accounts.TwoFactorDevice`, `staff_mfa.*`, `account.EmailAddress`, `socialaccount.SocialAccount` | zostaje z kopią (poświadczenia) |
| `notifications.Notification` (UX-PANEL-01) | znika z kopią – powiadomienie to drogowskaz do aktywności, która przechodzi na konto docelowe własną regułą; przepięcie zderzałoby się z więzem „jedno nieprzeczytane powiadomienie na klucz” (rozmowa, wątek) i dublowało zdarzenia, które docelowe już zna; powiadomienia i tak żyją 90 dni |
| `admin.*`, `wagtail*`, strony i dokumenty `cms` (właściciel, blokada, wgrał) | zostaje z kopią (personel CMS) |
| `core.AuditLog.actor` | nie ruszamy |

## 2. Kiedy scalenie jest odmówione

Serwis odmawia (`DomainError`), zanim cokolwiek zapisze:
- konta z różnych konkursów, to samo konto dwa razy, konto zanonimizowane,
- konto chronione (koordynator, personel, superużytkownik) – po **obu** stronach,
- konto z inną rolą (§ 1.2) albo z rolą/profilem w innym konkursie (`active_elsewhere`) – po obu
  stronach: usunięcie kopii jest platformowe, a dopisanie aktywności do konta wspólnego zmieniałoby
  dane, które przetwarza też drugi organizator,
- wykonawca jest jednym z kont,
- konflikt z § 1 – komunikat wymienia każdy konflikt z nazwą etapu/edycji/cennika i radą
  („zostaw oba konta albo usuń kopię”).

## 3. Przebieg (`merge_participants(keep, drop, *, actor, request=None, take_email=False)`)

Jedna transakcja: blokada obu wierszy `User` i obu `Participant` (`select_for_update`, kolejność
po kluczu), **ponowne** wyliczenie planu na zablokowanych wierszach (to, co koordynator widział
w podglądzie, mogło się zmienić), odmowa przy konflikcie, przepięcia, pola profilu, adres e-mail,
usunięcie kopii (`delete_account_by_coordinator(..., via="merge")` w kontekście konkursu), audyt.
Błąd w dowolnym kroku cofa wszystko.

**Adres e-mail.** Domyślnie zostaje adres konta docelowego. Opcja „weź adres z kopii” jest
dostępna wyłącznie, gdy docelowe **nie potwierdziło** adresu (rejestracja z literówką). Adres jest
unikalny (bez wielkości liter), więc najpierw kopia dostaje adres zastępczy (`*@scalanie.invalid`),
potem docelowe – adres kopii; wpisy allauth starego adresu znikają (`_forget_allauth_addresses`),
stary adres trafia do `PreviousEmail` (gdy 2FA włączone). Gdy kopia miała adres potwierdzony,
docelowe dostaje datę potwierdzenia i – o ile nie jest zablokowane – staje się aktywne (inaczej
osoba nie zalogowałaby się na żaden z dwóch adresów). Konto docelowe z 2FA/personelu – jak przy
zwykłej zmianie adresu – tylko superkoordynator (`_assert_may_change_protected_email`).

**Audyt `account.merged`** (cel: konto docelowe): kody publiczne obu profili, identyfikator konta
kopii, liczby przepiętych/odrzuconych wierszy per model, nazwy uzupełnionych pól profilu (bez
wartości), czy wzięto adres kopii, wynik usunięcia kopii. Bez imion, nazwisk, adresów.

## 4. Ekran

1. Przy każdej grupie na `/coordinator/accounts/duplicates/` formularz GET **„Scal…”**: konto
   docelowe (domyślnie „do zachowania”, inaczej konto ostatnio logowane) i kopia; bez kont chronionych.
2. **Podgląd** `GET /coordinator/accounts/duplicates/merge/?target=<id>&source=<id>`
   (`web:coordinator-duplicates-merge`): oba konta obok siebie, co zostanie przepięte (model, liczba,
   reguła), co odpadnie, uzupełnione pola profilu, który adres e-mail zostaje (+ pole wyboru „weź
   adres z kopii”, gdy wolno), odmowy i konflikty (wtedy bez przycisku), odnośnik „zamień kierunek”.
3. **„Tak, scal konto <kod kopii> w <kod docelowego>”** – `POST` na ten sam adres (CSRF), wartość
   potwierdzenia niesie sam przycisk (`confirm=<id kopii>`), jak „Usuń” w wierszu. Po sukcesie powrót
   na listę z komunikatem; po odmowie – na podgląd z komunikatem.
4. Konto spoza profili uczestnika **tego** konkursu → 404; para, która nie jest już w jednej grupie
   duplikatów → komunikat i powrót na listę (serwis sam w sobie wymaga tylko tego samego konkursu).
5. Bez skryptu (CSP), wyłącznie koordynator (`CoordinatorRequiredMixin`), panel nie jest tłumaczony.

## 5. RODO

Scalenie nie zbiera nowych danych: zestawia dane, które koordynator widzi na liście kont i karcie
uczestnika, i **zmniejsza** ich ilość (jedna kopia mniej – art. 5 ust. 1 lit. c i d RODO). Mieści się
w czynności „obsługa kont uczestników”, więc rejestr czynności się nie zmienia. Dowody zgód
przechodzą w całości (nie znikają przy scaleniu), audyt nie zawiera danych osobowych.

## 6. Testy

`apps/accounts/tests/test_merge.py` (serwis, baza): klasyfikacja wszystkich relacji (bez bazy),
etap + praca, pusty wpis po stronie docelowego i kopii, konflikt aktywności w tym samym etapie,
zgody (dowody i projekcje, zgoda opiekuna), zaświadczenie (wersje, bieżące), obecność na
warsztatach (duplikat), forum (wątek, wpis, subskrypcja), czat (rozmowa z organizatorem u obu),
należności, odmowy (inna rola, inny konkurs, koordynator, własne konto), transakcyjność (błąd przy
usuwaniu kopii cofa przepięcia), adres e-mail (wolno/nie wolno, unikalność), audyt bez danych osobowych.
`apps/web/tests/test_coordinator_duplicates_merge.py` (ekran): uprawnienia (uczestnik 403), konto
z innego konkursu 404, formularz „Scal…” przy grupie, podgląd z konfliktem bez przycisku, POST bez
potwierdzenia odrzucony, POST scala i wraca na listę, opcja adresu.
