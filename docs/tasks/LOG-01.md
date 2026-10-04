# LOG-01: Logistyka finału stacjonarnego dla delegacji (IQO)

## 0. Cel i granice (polecenie organizatora, 4.10.2026)

Finał olimpiady międzynarodowej (IQO) odbywa się na miejscu: kraje przyjeżdżają **delegacjami**
(uczniowie, opiekunowie drużyn, obserwatorzy i goście). Organizator potrzebuje od każdej delegacji
danych do wiz, przylotów, zakwaterowania, wyżywienia, identyfikatorów i bezpieczeństwa – i potrzebuje
ich **w terminie**, a po zawodach ma je **usunąć**.

Funkcja jest ogólna (każdy konkurs w trybie `DELEGATIONS`), pierwszym użytkownikiem jest `iqo`.

Czego zadanie **nie** robi:
- **płatności delegacji** (opłaty, faktury, wpłaty) – to zadanie PAY-01 prowadzone równolegle.
  Tu nie ma ani jednej kwoty; interfejs dla PAY-01 to wyłącznie odczyt członków delegacji
  (`apps.delegation_logistics.services.members_of`) – niczego od płatności nie potrzebujemy,
- nie zmienia istniejącej logistyki etapu (`apps.competitions.logistics`: miejsca, deklaracja przyjazdu
  uczestnika, obecność per etap) – ta zostaje dla konkursów krajowych; nowa aplikacja czyta z niej
  wyłącznie decyzję D21 (`collects_special_needs`),
- nie zmienia modeli delegacji (`apps.accounts.delegations`) – goście delegacji są nowym modelem
  w nowej aplikacji,
- Olimpiada Kwantowa (tryb `OPEN`, flaga wyłączona) nie widzi z tego niczego: ani adresu, ani pozycji
  menu, ani zapytania do bazy.

## 1. Bramki

Ekrany istnieją, gdy **oba** warunki są spełnione: flaga konkursu `onsite_logistics`
(`FEATURE_DEFAULTS`, § 1.5.2 – istnieje, domyślnie wyłączona) **i** tryb rejestracji
`DELEGATIONS`. Poza tym 404. Dane o zdrowiu (dieta, alergie, uwagi medyczne) dodatkowo wymagają
decyzji D21: `LogisticsSettings.collect_special_needs=True` (przełącznik „zbieraj potrzeby szczególne”
na ekranie „Miejsca zawodów”) – bez niej grupa „Zdrowie” nie istnieje na formularzu i nie przechodzi
przez serwis.

## 2. Role i dostęp (minimalizacja)

| Kto | Co widzi |
|---|---|
| opiekun drużyny (`DelegationLeader` w bieżącej edycji) | formularz logistyczny **członków swojej delegacji**, dodawanie/usuwanie gości, zdjęcia, listy zapraszające swojej delegacji |
| koordynator bez przydziału | ustawienia finału (terminy, daty, retencja), przydziały dostępu, liczby zbiorcze (kompletność per kraj) – **bez danych osobowych** |
| **oficer logistyki** (koordynator z przydziałem `OFFICER`) | wszystko: dane członków, paszporty, zdrowie, przyloty, pokoje, wyżywienie, koszulki, przypomnienia, eksporty, listy wizowe, identyfikatory |
| **obsługa rejestracji** (dowolne konto z przydziałem `CHECKIN`) | wyłącznie ekran skanowania: imię i nazwisko, kraj, rola, zdjęcie, stan odhaczenia |

Przydziały (`LogisticsAccess`) nadaje koordynator konkursu; przydział `OFFICER` – superkoordynator albo
istniejący oficer, a gdy konkurs nie ma jeszcze żadnego oficera, dowolny koordynator (pierwszy
przydział). Każde nadanie i odebranie – audyt. Role sprawdza **serwis**, nie tylko szablon.

## 3. Model

- `FinalEvent` (jeden na edycję): nazwa wydarzenia, miasto, miejsce, daty od–do, prefiks numeru listów,
  okres retencji po zakończeniu (domyślnie 30 dni), terminy pięciu grup pól, `purged_at`.
- `DelegationGuest` – brak w DEL-01, dokładany tu: delegacja, imię, nazwisko, e-mail (opcjonalny),
  rola (`OBSERVER` obserwator / `GUEST` gość). Bez konta.
- `DelegationMember` – „osoba w delegacji z punktu widzenia finału”: dokładnie jedno z
  `participant` (uczeń), `user` (opiekun), `guest`. Wiersze uczniów i opiekunów powstają same
  (synchronizacja przy wejściu na ekran), wiersze osób, które z delegacji wypadły, znikają.
  Grupy pól:
  1. **Tożsamość** (wiza, lista dla hotelu): imię i nazwisko jak w paszporcie, obywatelstwo (ISO),
     data urodzenia, numer paszportu, data ważności – **szyfrowane w bazie**,
  2. **Podróż**: przyjazd i wyjazd – data, godzina, środek (samolot/pociąg/autobus/samochód/inny),
     numer lotu/pociągu, lotnisko/dworzec,
  3. **Zakwaterowanie**: czy potrzebuje noclegu, płeć (do przydziału pokoi), preferencja
     współlokatora, uwagi (bez danych o zdrowiu),
  4. **Zdrowie** (tylko przy D21): dieta (lista zamknięta), uwagi do diety, alergie, uwagi medyczne –
     **szyfrowane**, zapis wyłącznie z **wyraźną zgodą** (art. 9 ust. 2 lit. a RODO) zaznaczoną przez
     opiekuna w imieniu osoby/rodzica; zgoda zapisana (kto, kiedy, wersja tekstu),
  5. **Pozostałe**: rozmiar koszulki, kontakt alarmowy (imię i telefon – **szyfrowane**), zdjęcie do
     identyfikatora (JPG/PNG po sygnaturze, do 5 MB, skan ClamAV, pokazywane dopiero po czystym skanie).
- `Room` (edycja): nazwa, budynek, pojemność, płeć pokoju (`F`/`M`/`X` – dowolna, tylko dorośli);
  `DelegationMember.room` – przydział.
- `Checkpoint` (edycja: „przyjazd”, „ceremonia otwarcia”, „dzień zawodów 1”…) i `CheckIn`
  (punkt, członek, chwila, kto) – jeden wpis na parę.
- `InvitationLetter` – rejestr listów: numer `PREFIKS/ROK/NNNN` (kolejny pod blokadą), delegacja,
  członek (list imienny) albo cała delegacja, kto i kiedy, wersja szablonu, **zaszyfrowana migawka
  treści** (dane osób z chwili wystawienia – PDF powstaje przy pobraniu z migawki, bez kopii PDF-a
  w storage).
- `LogisticsReminder` – ślad wysłanych przypomnień (delegacja, kto, kiedy, ile braków).

Szyfrowanie: Fernet z kluczem wyprowadzonym z `SECRET_KEY` z własną etykietą (wzorzec
`apps.ai_grading.crypto`, `twofactor`), z kluczami `SECRET_KEY_FALLBACKS` do odczytu – rotacja
klucza nie gubi danych, o ile stary klucz zostaje w fallbackach do końca retencji.

## 4. Przepływy

**Opiekun** (`/delegation/logistics/`): lista członków delegacji ze stanem pięciu grup (kompletna /
brak / zablokowana po terminie), formularz członka (wszystkie grupy na jednej stronie; grupa po
terminie tylko do odczytu), dodanie i usunięcie gościa, wgranie zdjęcia, pobranie listów wizowych
swojej delegacji. Członek z innej delegacji → 404.

**Oficer logistyki** (`/coordinator/logistics/…`):
- przegląd: kompletność per kraj, terminy, przyciski przypomnień,
- karta członka (pełne dane, historia zmian z audytu, edycja także po terminie),
- **tablica przylotów** (dzień → godzina; miejsce, środek, numer, kraj, osoby) i odlotów – do
  planowania odbiorów z lotniska; CSV,
- **lista pokoi**: pokoje, przydziały, wolne miejsca, nieprzydzieleni; reguły: pojemność, płeć pokoju,
  **niepełnoletni nigdy z dorosłym**, pokój „dowolna płeć” tylko dla dorosłych; CSV (rooming list),
- **wyżywienie**: liczby diet, lista alergii i uwag dla kuchni; CSV,
- **koszulki**: liczby per rozmiar i rola; CSV,
- **przypomnienia o brakach**: e-mail do opiekunów delegacji (kolejka `queue_mail`, język opiekuna
  `language_for`) z listą braków i terminami,
- **listy zapraszające** do wiz (PDF, imienny albo na delegację), rejestr z numerami,
- **identyfikatory** PDF (imię, kraj, rola, zdjęcie, QR) – dla delegacji albo wszystkich,
- eksport pełny (CSV, z danymi paszportowymi – audyt).

**Obsługa** (`/coordinator/logistics/checkin/`): wybór punktu kontroli, wyszukiwarka nazwiska,
skan QR aparatem telefonu (QR niesie **wyłącznie** adres z losowym tokenem identyfikatora – żadnych
danych osobowych); strona osoby z przyciskiem „Odhacz” / „Cofnij”. Token unieważnia „Wydaj nowy
identyfikator”.

## 5. Terminy i blokada

Pięć terminów (po jednym na grupę) w `FinalEvent`. Po terminie opiekun widzi grupę tylko do odczytu,
serwis odrzuca zapis grupy (`LOGISTICS_GROUP_LOCKED`). Oficer edytuje po terminie. Każdy zapis →
audyt `logistics.member_updated` z **nazwami** zmienionych pól (bez wartości).

## 6. RODO

- rejestr czynności: nowa czynność **warunkowa** „Logistyka finału – delegacje” (wchodzi do rejestru
  konkursu z flagą i trybem delegacji); podstawy: art. 6 ust. 1 lit. b (udział w zawodach),
  lit. c/f (wizy, hotel – obowiązek meldunkowy / interes organizatora), art. 9 ust. 2 lit. a
  (zdrowie – wyraźna zgoda), art. 8 / zgoda rodzica dla dzieci,
- retencja: dane członków, zdjęcia, migawki listów i odhaczenia usuwane automatycznie (zadanie
  dobowe) po `ends_on + retencja` (domyślnie 30 dni); rejestr listów zostaje bez danych osób,
- eksport danych konta: sekcja „logistyka finału” (własne dane osoby, odszyfrowane),
- usunięcie/anonimizacja konta: wiersze członka znikają razem z plikiem zdjęcia; osoba znika
  z migawek listów,
- dostęp wyłącznie przez role z § 2; otwarcie karty członka, eksport, wystawienie i pobranie listu
  oraz wydruk identyfikatorów – audyt.

## 7. Testy

Bramki (OPEN/flaga → 404, Olimpiada Kwantowa bez menu), izolacja (opiekun kraju A → 404 na członku
kraju B; obcy konkurs → 404; koordynator bez przydziału → 403 na danych osobowych), terminy
i blokada, szyfrowanie (w bazie nie ma jawnego numeru paszportu), D21 i zgoda na dane o zdrowiu,
reguły pokoi (niepełnoletni + dorosły, płeć, pojemność), numeracja listów, QR bez danych osobowych,
odhaczenie, przypomnienia w języku opiekuna, retencja, usunięcie konta, eksport danych konta.

## 8. Realizacja (4.10.2026) – doprecyzowania i odstępstwa

Gdzie co jest: `apps/delegation_logistics/` – `models.py` (modele i bramka `enabled`), `crypto.py`
(pole szyfrowane), `access.py` (role), `services.py` (członkowie, formularz, goście, zdjęcia),
`rooming.py`, `reports.py` (zestawienia, przypomnienia, CSV), `letters.py`, `badges.py`, `privacy.py`
(retencja, usunięcie konta, eksport), `register.py` (wiersz rejestru), `views.py`/`urls.py`, szablony
i katalogi tłumaczeń w aplikacji.

Odstępstwa (z powodem):
1. **Oficer logistyki to przydział** (`LogisticsAccess`), a nie nowa rola w `CompetitionRole`: rola
   wymagałaby zmian w `accounts` (grupa RBAC, migracja, nawigacja ról), a przydział jest zawężeniem
   roli koordynatora – odwołany koordynator traci wgląd bez sprzątania przydziałów.
2. **Ekran obsługi pod `/coordinator/logistics/checkin/`** – bez nowego przedrostka w kontrakcie tras
   CMS-a (`djcms_contract`); dostęp rozstrzyga przydział, nie przedrostek.
3. **Dane o zdrowiu za decyzją D21** (`collects_special_needs`) – ta sama decyzja organizatora, co
   w logistyce etapu; bez niej sekcji nie ma. Dieta „bez ograniczeń” nie wymaga zgody (to nie jest dana
   z art. 9), każda inna – tak (zdrowie albo wyznanie).
4. **Członkowie synchronizowani przy wejściu na ekran** (`sync_members`), a nie sygnałami w DEL-01:
   uczeń wypisany i opiekun odwołany znikają razem z danymi przy najbliższym odczycie (i natychmiast przy
   usunięciu konta – hak w `delegation_services.erase_for_user`).
5. **List z migawki** (zaszyfrowany JSON w rejestrze), PDF generowany przy pobraniu – żadnej kopii PDF
   z paszportami w storage. Tekst odwrotu po angielsku.
6. **Retencja finału jest własna** (domyślnie 30 dni po ostatnim dniu) i niezależna od retencji danych
   uczestników edycji – numer paszportu służy jednemu wyjazdowi.
7. **Ekrany koordynatora po polsku bez gettext** (I18N-01 § 0, jak „Delegacje”); ekrany opiekuna,
   obsługi i list przypomnienia – gettext w 10 katalogach aplikacji.
8. **Nowe limity żądań** `onsite_logistics` (600/h) i `onsite_checkin` (3000/h), liczone per konto.

Znane luki:
- skaner w przeglądarce działa tam, gdzie jest `BarcodeDetector` (Chrome/Android); na iOS obsługa
  skanuje aparatem systemowym (kod QR to zwykły adres) albo szuka po nazwisku,
- przypomnienia są wysyłane przyciskiem (bez harmonogramu automatycznego),
- zgoda rodzica na dane o zdrowiu niepełnoletniego jest oświadczeniem opiekuna drużyny (pole wyboru
  z datą i autorem), a nie osobnym przepływem z podpisem rodzica,
- brak importu przylotów z pliku i walidacji numeru paszportu (MRZ); podgląd zdjęcia nie trafia do audytu,
- płatności delegacji (PAY-01) – poza zakresem; interfejsem jest `services.members_of(delegation)`,
- tłumaczenia maszynowe (do przeglądu native speakerów).

### 8.1. Poprawki po przeglądzie (4.10.2026)

- **H1** migracja listu wizowego: `tenancy.0015_document_kind_visa_invitation` po `0014_merge_20261004_1935`;
  dokumentacja w `OPERACJE.md` § 31, podręcznik § 10d.
- **H2** pokoje: zmiana płci, daty urodzenia albo „bez noclegu” u osoby z pokojem **zdejmuje przydział**
  (audyt `logistics.room_unassigned`, komunikat) – to osoba przestała pasować; zmiana pierwszego dnia
  finału **oznacza** naruszenia (ekran, CSV, komunikat z liczbą), ale nikogo nie przenosi – jedna data
  mogłaby opróżnić wiele pokoi naraz, a decyzja należy do oficera.
- **L6** niepełnoletni bez płci binarnej mieszka w pokoju jednoosobowym (dowolny pusty pokój).
- **M1** po retencji: synchronizacja nic nie odtwarza, zapis danych i zdjęć odmawiany wszystkim; dane
  paszportowe i o zdrowiu wyłącznie przy `FinalEvent.ends_on` (bez zastępczego terminu retencji).
- **M2** dieta szyfrowana (migracja `0002` szyfruje istniejące wartości), walidacja wyboru w serwisie.
- **M3** zdjęcie: limit 40 Mpx sprawdzany w nagłówku przy wgraniu, przekodowanie po czystym skanie do
  JPEG ≤ 600×800 bez EXIF; identyfikatory PDF per kraj albo osoba.
- **M4** każde usunięcie członka (wypisanie, gość, konto, retencja) czyści jego wiersz w migawkach listów.
- **L1–L12**: obsługę rejestracji nadaje oficer/superkoordynator (nie sobie), goście blokowani terminem
  dokumentu podróży, `update_fields` (pole nieodszyfrowane nie jest nadpisywane), bez D21 brak eksportu
  wyżywienia, przypomnienia tylko oficer (terminy ze strefą), wyszukiwania ograniczone do bieżącej edycji,
  blokada doradcza numeracji (konkurs, rok), skan porzucony po ponowieniach → błąd, synchronizacja raz na
  żądanie i wyszukiwarka obsługi bez odszyfrowywania, obecność i listy w eksporcie danych konta, usunięte
  martwe `remove_photo`.
