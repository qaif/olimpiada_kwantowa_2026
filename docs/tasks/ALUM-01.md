# ALUM-01: Sieć absolwentów i mentoring (obie olimpiady)

## 0. Cel i granice

Nowa, **osobna** funkcja „Absolwenci”: byli uczestnicy (laureaci, finaliści… – kryterium ustawia
organizator konkursu) mogą **dobrowolnie** dołączyć do sieci absolwentów, pokazać, co dziś robią,
zgłosić się na mentora i dostawać od organizatora zaproszenia na warsztaty, webinary albo do
jury. Obecni uczestnicy proszą mentora o pomoc, a rozmowa toczy się w istniejących
**Wiadomościach** (`apps.chat`, CZ-01) – nie budujemy drugiego komunikatora.

Funkcja jest za flagą konkursu **`alumni`** (domyślnie wyłączona): to nowa czynność przetwarzania
danych osobowych na podstawie zgody, więc jej włączenie jest decyzją organizatora (wpis w rejestrze
czynności zmienia się razem z flagą), a nie skutkiem wdrożenia. Przy wyłączonej fladze wszystkie
adresy dają 404, menu nie zmienia się o bajt, rejestr czynności jest taki jak dziś.

Czego zadanie **nie** robi:
- nie zmienia modeli czatu ani jego reguł dla zwykłych rozmów – dokłada w `apps.chat.services`
  jeden **punkt rozszerzenia** (polityka rozmowy, § 5.3), z którego korzysta mentoring,
- nie zależy od kodu webinarów (LiveKit, budowane równolegle) – zaproszenie niesie **adres**
  wydarzenia, a funkcja `apps.alumni.invitations.send_invitation(...)` jest haczykiem, który moduł
  webinarów może wołać później,
- nie wylicza medali: platforma (gałąź `main`) nie ma dziś modelu medalu. Osiągnięcia mają
  rejestr źródeł (`apps.alumni.achievements.register_source`), do którego moduł medali dopisze
  się jedną linią w `AppConfig.ready()`,
- nie obejmuje wpisów drużynowych (wpis bez `participant`) – drużyna nie jest osobą, która może
  wyrazić zgodę.

## 1. Kto jest absolwentem

Absolwent = uczestnik konkursu (rola `participant` + profil `Participant` **tego** konkursu), który:

1. ma **osiągnięcie** w **zakończonej** edycji na poziomie co najmniej progu z ustawień konkursu
   (`AlumniSettings.eligibility`: `LAUREATE` ≥ `FINALIST` ≥ `QUALIFIED` ≥ `ANY`; domyślnie
   `FINALIST`). `ANY` = „każdy, kto wystartował” – to jest wariant „dołącza każdy po swojej
   ostatniej edycji”,
2. jest **dziś pełnoletni** (`apps.chat.services.is_adult` – ta sama, konserwatywna reguła co
   w czacie). Uzasadnienie: zgoda na nową czynność przetwarzania (publiczna ściana, kontakt
   mentorski z niepełnoletnimi) od osoby małoletniej wymagałaby osobnej zgody opiekuna i osobnego
   obiegu; laureat, który ma dziś 17 lat, dołącza po urodzinach. Dzięki temu **mentor jest zawsze
   pełnoletni**, a pytanie z polecenia („dorosły mentor z małoletnim”) ma jedną stronę do
   rozstrzygnięcia,
3. ma aktywne, niezanonimizowane konto.

**Edycja zakończona** = nie jest bieżąca (`is_current=False`) **albo** wszystkie jej etapy
nietreningowe mają ogłoszone wyniki. Dzięki temu laureaci tegorocznego finału mogą dołączyć zaraz
po ogłoszeniu wyników, zanim organizator założy następną edycję.

**Osiągnięcia wyłącznie z wyników opublikowanych** (`Stage.results_published_at` ustawione,
etap nietreningowy, wpis niezdyskwalifikowany) i z wystawionych dyplomów:

| Poziom | Skąd |
|---|---|
| `LAUREATE` „laureat” | `QUALIFIED` w etapie finałowym (w finale awans = tytuł laureata, jak w `Anonymization.FULL`) albo dyplom `LAUREAT` |
| `FINALIST` „finalista” | wpis w etapie finałowym albo dyplom `FINALISTA` |
| `QUALIFIED` „awans do kolejnego etapu” | `QUALIFIED` w etapie niefinałowym |
| `ANY` „uczestnik” | jakikolwiek wpis w etapie z ogłoszonymi wynikami |

Etap finałowy edycji = etap rodzaju `FINAL`, a w edycji bez niego (przebieg z edytora, same
`ROUND`) – etap nietreningowy otwierany najpóźniej. Osiągnięcia **liczymy na żywo** (jedno
zapytanie na listę profili), a nie kopiujemy: wycofana publikacja wyników zdejmuje osiągnięcie
z profilu bez żadnego zadania porządkowego. Na profilu stoi jedno osiągnięcie na edycję
(najwyższe), podpisane `„<oznaczenie edycji>: <poziom>”`.

## 2. Zgoda i RODO

- **Osobna zgoda**, niezależna od zgód konkursowych (`ConsentRecord`): pole wyboru z pełną treścią
  (wersja `ALUMNI_CONSENT_VERSION`) na ekranie dołączania. Treść mówi: jakie dane (imię i inicjał
  nazwiska albo – za zgodą na publikację nazwiska – pełne; osiągnięcia z ogłoszonych wyników; pola
  profilu, które sam wypełnię), cel (sieć absolwentów, katalog dla zalogowanych, opcjonalnie
  publiczna ściana, mentoring, zaproszenia od organizatora, zagregowane statystyki), że zgodę
  można wycofać w każdej chwili jednym przyciskiem.
- Dowód: `AlumniConsentEvent` (uczestnik, `GRANTED`/`WITHDRAWN`, wersja, chwila). Wiersze nie
  niosą danych poza pseudonimowym profilem, więc zostają także po wycofaniu (rozliczalność).
- **Wycofanie** = natychmiastowe usunięcie `AlumniProfile` (z bio, linkami, uczelnią…),
  zakończenie otwartych relacji mentorskich (`ENDED`, powód `WITHDRAWN`) i ich rozmów (tylko do
  odczytu, § 5.3). Kolejne dołączenie zaczyna od pustego profilu.
- **Rejestr czynności**: `ALUMNI_ACTIVITY` (podstawa: art. 6 ust. 1 lit. a; okres: do wycofania
  zgody, wyłączenia funkcji albo usunięcia konta; odbiorcy: zalogowani uczestnicy konkursu,
  publicznie – tylko profile oznaczone jako publiczne, minimalny zestaw pól; koordynatorzy;
  dostawca poczty). Wiersz pojawia się w `activities_for(competition)` tylko przy włączonej fladze.
- **Eksport danych** (`apps/accounts/data_export.py`): sekcja `absolwenci` – profil, zdarzenia
  zgody, relacje mentorskie (z rolą, stanem, datami, własną notatką prośby), własne zgłoszenia.
- **Usunięcie/anonimizacja konta** (`apps/accounts/profile.py`): `apps.alumni.services.erase_for_user`
  – profil znika, relacje kończą się (powód `ACCOUNT_REMOVED`), notatki prośby i treść własnych
  zgłoszeń są czyszczone.
- **Retencja** (`apps/accounts/retention.py`): aktywna zgoda absolwenta (flaga włączona, profil
  istnieje) jest nową przeszkodą `BLOCKED_ALUMNI` („należy do sieci absolwentów”). Bez tego
  automat retencji zanonimizowałby konto 24 miesiące po ostatnim etapie i sieć absolwentów
  traciłaby członków co rok. Wycofanie zgody albo wyłączenie flagi przywraca zwykłą retencję
  (najbliższy przebieg nocny). Znany dług: przy wstrzymanej retencji zostaje cały profil
  uczestnika (szkoła, telefon), a nie tylko dane potrzebne sieci – opisane w OPERACJE.

## 3. Profil absolwenta (wszystkie pola opcjonalne)

| Pole | Uwagi |
|---|---|
| podpis | `Imię N.` (`display_author`); **pełne imię i nazwisko** tylko gdy absolwent zaznaczy `show_full_name` **i** ma zgodę konkursową na publikację nazwiska (`Participant.publish_full_name`) – ta sama reguła co w tabelach wyników |
| osiągnięcia | wyliczone (§ 1), nieedytowalne |
| uczelnia, kierunek, miasto, kraj | tekst ≤ 120 znaków; kraj – kod ISO z listy |
| bio | ≤ 600 znaków, wyświetlane escapowane z `linebreaksbr`, bez linków |
| zainteresowania | lista zamknięta (`Interest`): obliczenia kwantowe, fizyka kwantowa, fizyka, informatyka, matematyka, inżynieria, chemia, badania naukowe, przemysł, edukacja |
| LinkedIn, GitHub | tylko `https://`, host `linkedin.com`/`www.linkedin.com` (ścieżka `/in/…`) i `github.com`; renderowane z `rel="nofollow noopener noreferrer ugc"` |
| mentor | `mentor_available`, `mentor_topics` (podzbiór `Interest`), `mentor_capacity` 1–10 (domyślnie 2) |
| widoczność | `listed` (katalog dla zalogowanych, domyślnie tak), `public` (publiczna ściana, domyślnie **nie**), `invitations` (listy od organizatora, domyślnie tak) |

Koordynator może **ukryć** profil (np. obraźliwe bio) – znika z katalogu i ściany, audyt.

## 4. Ściana publiczna i katalog

- **Ściana** `/alumni/` – bez logowania, tylko gdy `AlumniSettings.public_wall` i profil `public`,
  nieukryty. Pola: podpis, osiągnięcia, uczelnia, kierunek. Nic więcej (bez bio, linków, miasta).
  Strona nie trafia do pełnostronicowego cache'u (allow-lista `page_cache`).
- **Katalog** `/me/alumni/directory/` – dla zalogowanych z rolą uczestnika **tego** konkursu
  (absolwenci też ją mają). Profile `listed`, nieukryte. Wyszukiwanie: tekst (imię, uczelnia,
  kierunek), zainteresowanie, „tylko mentorzy z wolnym miejscem”. Paginacja 20. **Nigdy**: e-mail,
  kod publiczny, szkoła, data urodzenia.
- Identyfikator w adresach: nieprzewidywalny `AlumniProfile.token`, nie `pk` uczestnika.

## 5. Mentoring

### 5.1 Przebieg
1. Uczestnik (mentee) wybiera mentora z katalogu (`mentor_available`, wolne miejsce), temat
   i krótką notatkę (≤ 500 znaków, widzi ją mentor i koordynator) → `Mentorship(REQUESTED)`.
   Limit: najwyżej **3** otwarte prośby/relacje na mentee; jedna otwarta para mentor–mentee.
2. Mentor akceptuje albo odrzuca (`ACCEPTED`/`DECLINED`). Akceptacja sprawdza pojemność
   (`mentor_capacity` aktywnych relacji) i **otwiera kanał rozmowy** (§ 5.2).
3. Każda strona może zakończyć relację (`ENDED`), mentee – anulować prośbę (`CANCELLED`).
   Koordynator może zakończyć każdą relację (notatka wymagana).
4. Każda strona może **zgłosić problem** z relacją (`MentorshipFlag`, powód ≤ 500) – list do
   koordynatorów od razu (bezpieczeństwo, a nie kolejka do odwiedzenia). Wiadomości w rozmowie
   zgłasza się jak w czacie („Zgłoś” – kolejka moderacji Wiadomości).

Mentee = uczestnik z rolą w konkursie, który ma wpis w edycji bieżącej albo nie ma jeszcze żadnego
wpisu (świeżo zarejestrowany). Mentor nie może być swoim mentee. Mentoring działa tylko przy
`AlumniSettings.mentoring_enabled` i włączonych Wiadomościach (`ChatSettings.enabled`).

### 5.2 Kanał rozmowy (decyzja)

Rozmowa mentorska to **zwykła rozmowa P2P czatu** (`Conversation.kind=PEER`, jedna na parę –
więz czatu) zakładana przez serwis mentoringu przy akceptacji, **bez** warunku `discoverable`
i bez dziennego limitu nowych rozmów (obie strony wyraziły wolę: prośba + akceptacja). Rozmowa
jest zawsze **jawna** (nigdy szyfrowana), bo musi dać się moderować. Jeżeli para miała już
rozmowę, mentoring jej używa (`conversation_preexisting=True`).

Reguły pisania wyznacza **polityka rozmowy** liczona przy każdej wiadomości (wiek może się zmienić):

| mentee | `peer_mode` czatu | `age_policy` czatu | kanał | tryb wiadomości |
|---|---|---|---|---|
| pełnoletni | ≠ `OFF` | dowolna | `PEER` – zasady czatu | jak w czacie (z wymuszeniem `PRE` w trakcie etapu) |
| małoletni | ≠ `OFF` | `ANY` | `PEER` z podłogą | co najmniej `POST` (organizator przegląda każdą wiadomość; `NONE` → `POST`) |
| małoletni | ≠ `OFF` | `SAME_GROUP` | `SUPERVISED` – kanał moderowany przez organizatora | **zawsze `PRE`**; reguła grupy wiekowej czatu nie zamyka tej rozmowy, bo każdą wiadomość czyta i przepuszcza organizator |
| dowolny | `OFF` | – | `SUPERVISED` | zawsze `PRE` |

Uzasadnienie: polecenie mówi „dorosły mentor z małoletnim przechodzi przez moderację czatu
konkursu; jeśli polityka wieku tego zabrania – tylko kanałem moderowanym przez organizatora”.
`SAME_GROUP` zabrania rozmowy dorosły–małoletni **bez świadków**; kanał, w którym każda wiadomość
czeka na akceptację koordynatora, ma świadka przed doręczeniem, więc jest jedyną dopuszczalną
formą. Przy `ANY` organizator świadomie dopuścił rozmowy międzygrupowe – ale mentor jest osobą,
którą platforma **poleca** dziecku, więc podnosimy podłogę do `POST` (organizator widzi treść).
Wiadomości `SUPERVISED`/`POST` trafiają do **istniejącej** kolejki moderacji Wiadomości
(`/coordinator/chat/moderation/`) – zero nowego ekranu moderacji. Nadawca widzi nad formularzem
zdanie, że organizator czyta wiadomości (to samo, co w trybach PRE/POST czatu) i notkę
„Rozmowa mentorska”.

Relacja zakończona (każdy powód) zamyka rozmowę **tylko do odczytu**, o ile powstała dla
mentoringu; rozmowa sprzed mentoringu wraca do zwykłych reguł czatu. Wyłączenie flagi albo
mentoringu wstrzymuje pisanie we wszystkich rozmowach mentorskich (tylko do odczytu).

### 5.3 Punkt rozszerzenia w czacie (jedyna zmiana `apps.chat`)

`apps.chat.services`:
- `PeerPolicy` (dataclass): `mode` (wymuszony tryb), `at_least` (podłoga), `skip_age_policy`,
  `refusal` (rozmowa tylko do odczytu, zdanie dla użytkownika), `notice` (notka nad wątkiem),
- `register_peer_policy(fn)` / `peer_policy(conversation)` – pierwsza niepusta odpowiedź z rejestru,
- `conversation_mode_and_stage(conversation, now, row)` – `mode_and_stage` konkursu z nałożoną
  polityką; używają jej `send_participant_message`, `_delivery_refusal` (akceptacja w kolejce)
  i widok wątku,
- `peer_write_refusal` sprawdza `refusal` polityki i pomija regułę wieku przy `skip_age_policy`,
- `ensure_peer_conversation(a, b)` – rozmowa P2P bez warunków katalogu (dla serwisów, które same
  sprawdziły zgodę obu stron); `started_by=None`, więc nie liczy się do dziennego limitu.

Rozmowy bez polityki zachowują się **dokładnie** jak dotąd (testy czatu bez zmian).

### 5.4 Nadzór koordynatora
`/coordinator/alumni/mentoring/`: lista relacji (prośby, aktywne, zakończone) z pełnymi danymi obu
stron (rola koordynatora), kanałem (`PEER`/`SUPERVISED`), znacznikiem „mentee małoletni”, liczbą
otwartych zgłoszeń wiadomości w rozmowie i zgłoszeń relacji; akcje: zakończ (notatka wymagana),
zamknij zgłoszenie. Koordynator **nie** dostaje widoku „czytaj rozmowę” – treść widzi wyłącznie
przez kolejkę moderacji czatu (obietnica CZ-01 § 5).

## 6. Zaproszenia

`/coordinator/alumni/invitations/`: rodzaj (`WORKSHOP`, `WEBINAR`, `JURY`, `OTHER`), tytuł (temat),
treść (≤ 4000, zwykły tekst), opcjonalny adres wydarzenia (`https://`), filtry: edycje, minimalny
poziom osiągnięcia, zainteresowania (dowolne z), „tylko mentorzy”. Podgląd liczby odbiorców,
potem „Wyślij”. Odbiorcy: profile aktywne, nieukryte, `invitations=True`, konto doręczalne
(`DELIVERABLE`, nie po anonimizacji). Listy przez `queue_mail` w **języku odbiorcy**
(`language_for`) – ramka (powitanie, stopka, wypis) tłumaczona, treść koordynatora bez zmian.
Każdy list ma odnośnik wypisu z podpisanym tokenem i nagłówki `List-Unsubscribe` /
`List-Unsubscribe-Post` (wzorzec `apps.forum.notifications`); `GET` pokazuje stronę z przyciskiem,
wypisuje dopiero `POST`. W bazie zostaje zaproszenie i **liczba** odbiorców (nie lista osób), audyt.

## 7. „Gdzie są teraz” (statystyki)

`/coordinator/alumni/stats/`: liczba absolwentów w sieci, mentorów, oraz rozkłady: kraj, uczelnia
(normalizacja: przycięcie, `casefold`), kierunek, zainteresowania, poziom osiągnięcia.
**k-anonimowość ≥ 5**: komórka < 5 trafia do „inne”; jeśli „inne” jest niezerowe i < 5 – pokazujemy
„< 5”; przy mniej niż 5 absolwentach w ogóle – brak rozkładów. Tylko jeden wymiar na tabelę
(krzyżowanie wymiarów rozbija grupy). Źródło: wyłącznie profile ze zgodą.

## 8. Model danych (`apps/alumni`)

- `AlumniSettings` (OneToOne `Competition`): `eligibility`, `mentoring_enabled` (domyślnie nie),
  `public_wall` (domyślnie nie), `updated_at`. Brak wiersza = domyślne.
- `AlumniProfile` (OneToOne `Participant`): pola § 3, `token`, `consent_version`, `joined_at`,
  `updated_at`, `hidden_at`, `hidden_by`. `objects = competition_scoped_manager("participant__competition")`.
- `AlumniConsentEvent`: `participant`, `kind`, `version`, `created_at`.
- `Mentorship`: `competition`, `mentor` (`Participant`), `mentee` (`Participant`), `status`, `topic`,
  `note`, `conversation` (`chat.Conversation`, `SET_NULL`), `conversation_preexisting`,
  `created_at`, `responded_at`, `ended_at`, `ended_by`, `end_reason`, `coordinator_note`.
  Więzy: jedna otwarta (`REQUESTED`/`ACCEPTED`) para; mentor ≠ mentee.
- `MentorshipFlag`: `mentorship`, `reporter`, `reason`, `created_at`, `resolved_at`, `resolved_by`.
- `AlumniInvitation`: `competition`, `kind`, `title`, `body`, `url`, `filters` (JSON),
  `recipients`, `created_by`, `created_at`.

## 9. Ekrany i bezpieczeństwo

- Uczestnik: `/me/alumni/` (dołącz / profil / moje relacje), `/me/alumni/directory/`, akcje POST
  pod `/me/alumni/…`. Link „Absolwenci” w pasku konta (bez zapytań, flaga z pamięci).
- Publiczne: `/alumni/`, `/alumni/unsubscribe/<token>/`.
- Koordynator: `/coordinator/alumni/` (ustawienia + lista), `…/mentoring/`, `…/invitations/`,
  `…/stats/`; pozycja „Absolwenci” w sekcji „Uczestnicy i konta” (bez odznaki).
- Role sprawdza **serwis** (`DomainError` 403), widok tylko tłumaczy. Recenzent, komisja,
  opiekun – 403. Obiekt innego konkursu – 404 (`for_competition`). Flaga wyłączona – 404.
- POST-y: `ThrottledFormMixin`, nowy zakres `alumni` (`30/hour`, per konto). Audyt: dołączenie,
  wycofanie, ustawienia, ukrycie, zakończenie przez koordynatora, wysłanie zaproszenia,
  zamknięcie zgłoszenia – bez treści bio i notatek.
- Zero inline JS; napisy przez gettext (polskie `msgid`) + katalog `apps/alumni/locale` (10 języków).

## 10. Testy

`apps/alumni/tests/`: kwalifikowalność (poziomy, edycja zakończona, wyniki nieopublikowane,
małoletni), zgoda i wycofanie, podpis wg zgody na publikację nazwiska, walidacja linków, katalog
bez danych wrażliwych (asercja na HTML), ściana publiczna (tylko `public`, minimalne pola),
mentoring (limity, pojemność, akceptacja otwiera rozmowę, kanały z tabeli § 5.2, wiadomości
małoletniego w `SAME_GROUP` → `PENDING` i doręczenie po akceptacji, podłoga `POST`, zakończenie →
tylko do odczytu), uprawnienia (recenzent/opiekun 403, inny konkurs 404, flaga 404), zaproszenia
(filtry, język odbiorcy, wypis), statystyki (k ≥ 5), eksport, anonimizacja, retencja.
Czat: istniejące testy `apps/chat/tests` muszą przejść bez zmian.

## 11. Dokumentacja

OPERACJE § 38 (włączenie flagi, kroki), PODRĘCZNIK-ORGANIZATORA § 10l (Absolwenci i mentoring),
PODRĘCZNIK-UCZESTNIKA § 8c (sieć absolwentów, mentoring, prywatność), CHANGELOG `[Unreleased]`.

## 12. Poprawki po przeglądzie krytyka (04.10.2026)

Zmieniają reguły z §§ 1–7 tam, gdzie są z nimi sprzeczne (np. § 7: „< 5” zastąpione dokładaniem
najmniejszych grup do „inne”).

| ID | Decyzja |
|---|---|
| H1 | Notatka prośby małoletniego: `Mentorship.note_status=PENDING`, mentor widzi sam temat do akceptacji koordynatora (`/coordinator/alumni/mentoring/`); notatka nigdy nie idzie listem. Opis i odnośniki profilu z `mentor_available` widoczne dla małoletniego widza dopiero po akceptacji koordynatora – skrót treści (`reviewed_hash`), każda zmiana wymaga ponownej akceptacji. Wzorce danych kontaktowych (`apps.alumni.safety.contact_hits`: telefon, e-mail, @nazwa, komunikatory) – automatyczne zgłoszenie przy notatce, znacznik w kolejce opisów. |
| M1 | Ukrycie profilu kończy trwające relacje mentora i odrzuca czekające prośby (`EndReason.HIDDEN`); akceptacja wymaga profilu nieukrytego, `mentor_available` i mentee wciąż z rolą uczestnika. |
| M2 | Data urodzenia mentee zapisana przy akceptacji; mentee jest małoletni według zapisanej **albo** bieżącej daty. Pełnoletność mentora potwierdzona przy dołączeniu (`adult_confirmed_at`) zostaje. Zmiana daty urodzenia osoby w otwartej relacji: audyt i automatyczne zgłoszenie (sygnały `post_init`/`post_save`, zero dodatkowych zapytań przy zwykłym zapisie profilu). |
| M3 | Dyplomy liczą się tylko przy wpisie z ogłoszonymi wynikami i bez dyskwalifikacji; katalog i ściana pokazują osiągnięcia wyłącznie z edycji zakończonych. |
| M4 | `/me/alumni/` i wycofanie zgody działają przy wyłączonej fladze (tylko dla osób z profilem); skutki wyłączenia flagi – OPERACJE § 38. |
| M5 | Wstrzymana retencja = minimalizacja (telefon, szkoła, region, klasa, adresy opiekunów, dzień urodzenia), tylko dla profili nieukrytych z aktywnym kontem; przeszkoda sprawdzana po reklamacjach i nieogłoszonych wynikach. |
| L1 | Walidatory łapią `ValueError` (`urlsplit`, port) i odrzucają `..`, `%2F`, `%5C`, `%2E` w ścieżce. |
| L2 | Akceptacja odmawia, gdy para ma rozmowę szyfrowaną, a relacja wymaga moderacji; zgłoszenie automatyczne do koordynatora (poza transakcją akceptacji), bez listu „rozmowa czeka”. |
| L3 | `combine_policies`: odmowa wygrywa, tryb i podłoga najostrzejsze, zdjęcie reguły wieku tylko za zgodą wszystkich polityk. |
| L4 | Polityka liczona raz i przekazywana do `peer_write_refusal`; zero zapytań w konkursie, który nigdy nie zapisał flagi; kanały listy liczone z jednym odczytem ustawień czatu. |
| L5 | Akceptacja blokuje profil mentora (`select_for_update`) przed liczeniem miejsc. |
| L6 | „Inne” wciąga najmniejsze grupy, aż osiągnie próg (bez komórek komplementarnych); liczebność zaokrąglona do 5; profile ukryte się nie liczą. |
| L7 | Dowód zgody z językiem i skrótem SHA-256 treści; po zmianie `ALUMNI_CONSENT_VERSION` profil śpi do potwierdzenia (`/me/alumni/renew/`). |
| L8 | Rejestr czynności: okres przechowywania z rozróżnieniem profilu, dowodu zgody i dokumentacji bezpieczeństwa mentoringu. |
| L9 | Pierwsze 5 wiadomości nowej pary dorosły–małoletni w premoderacji także przy zasadzie „bez ograniczeń”. |
| L10 | Zakończonej relacji nie wznawia się – nowa prośba (udokumentowane w OPERACJE i podręczniku). |
| L11 | Limity próśb: 5 dziennie na mentee, 2 tygodniowo do tego samego mentora (pętla „prośba → wycofanie”). |
| MED | Po scaleniu MED-01 (v0.43.0): medale i wyróżnienia z **zamrożonych** schematów etapów z ogłoszonymi wynikami są źródłem osiągnięć (`achievements.medal_source`, rejestrowane w `AlumniConfig.ready`): medal = poziom laureata z podpisem nagrody, wyróżnienie = poziom finalisty. Zastępuje zdanie z § 0 o braku medali. |
