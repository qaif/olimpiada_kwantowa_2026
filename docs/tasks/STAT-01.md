# STAT-01: Statystyki szkół i opiekunów szkolnych

## 0. Cel i granice

Nowa funkcja „Statystyki szkół” dla obu olimpiad, za flagą konkursu `school_statistics`
(domyślnie **wyłączoną** – menu i panel Konkursu #1 zostają co do bajtu, § 2.1 etapu 2).

- **Opiekun szkolny** (`/supervisor/statistics/`): jego uczniowie w kolejnych edycjach i etapach,
  stan oddania prac, po **oficjalnej publikacji** – punkty i kwalifikacja, porównanie ze szkołą,
  województwem i całą olimpiadą, wykres postępu przez edycje, raport PDF szkoły dla dyrektora.
- **Koordynator** (`/coordinator/school-stats/`): ranking szkół wg udziału i (po publikacji)
  wyników, tabela województw, lista „szkoły z uczestnikami rok temu, w tej edycji bez nikogo”
  (akcja promocyjna), eksport CSV, raport PDF dowolnej szkoły.

Czego zadanie **nie** robi:
- nie zbiera **żadnych nowych danych osobowych** i nie dodaje modeli z danymi – wszystko liczy się
  z tego, co już jest (`StageEntry`, `Submission`, `ResultsPublication.entry_totals`,
  `Participant.school_ref`/`district`), a w pamięci podręcznej leżą wyłącznie agregaty,
- nie zmienia reguły widoczności uczniów u opiekuna (`apps.accounts.supervisors.students_of`:
  opiekun widzi tych, którzy **sami** wpisali jego adres) ani dzisiejszego pulpitu `/supervisor/`
  (poza jednym odnośnikiem za flagą),
- nie zmienia publicznych statystyk (`apps.results.statistics`, `/statystyki/`).

## 1. Grupa: szkoła teraz, delegacja później

Pojęcie **grupy** (`apps.school_stats.grouping.GroupAxis`) ma dwa wcielenia:

| Oś | Klucz grupy | Region porównawczy | Stan |
|---|---|---|---|
| `school` | `Participant.school_ref` (wykaz RSPO), a bez dowiązania – znormalizowana nazwa wpisana ręcznie | `Participant.district` (województwo) | zaimplementowana |
| `delegation` | delegacja IQO (`feature/delegacje`) | `Participant.country` | punkt zaczepienia, bez implementacji |

Serwisy liczące przyjmują oś, a nie zakładają szkoły – dołożenie delegacji to nowa oś i jedna
gałąź w `axis_for(competition)`, a nie przepisanie agregatów.

## 2. Źródła danych i reguły czasu

- Etapy: wszystkie etapy edycji **poza treningiem** (`Stage.is_training`). Wpisy drużynowe
  (bez `participant`) nie wchodzą – grupą jest szkoła ucznia, a nie skład.
- Udział: wpis do etapu (`StageEntry`). Oddanie: co najmniej jedno zgłoszenie poza
  `REJECTED_INFECTED`; „w terminie”: oddane i żadne z tych zgłoszeń nie ma `is_late`. Dla etapów
  bez prac pisemnych (test, rozmowa) oddanie jest „nie dotyczy”.
- Punkty: **wyłącznie** z zamrożonej mapy `ResultsPublication.entry_totals` – liczby ogłoszone,
  a nie bieżące. Etap bez publikacji nie ma ani punktów, ani kwalifikacji, ani średnich.
- Kwalifikacja: `StageEntry.status == QUALIFIED` – tylko w etapie z publikacją.
- Publikacja „tylko awansujący” (`qualified_only`): średnie się nie liczą (jak w publicznych
  statystykach – inaczej zdradzałyby wyniki, których organizator nie ogłosił); uczeń opiekuna
  spoza listy ma „brak na liście awansujących” **bez** punktów.
- Tryby anonimizacji i zgody na publikację nazwiska rządzą **tabelą publiczną**. Opiekun widzi
  nazwiska swoich uczniów z mocy dzisiejszej reguły (uczeń sam go wskazał); raport PDF i eksport
  CSV nie zawierają żadnego nazwiska ani kodu uczestnika – niezależnie od zgód.

## 3. k-anonimowość (próg 5)

`K_ANONYMITY = 5`.

- **Opiekun**: agregat grupy innej niż „moi uczniowie” (szkoła, województwo, wszyscy) jest
  widoczny, gdy grupa ma ≥ 5 wpisów **oraz** część grupy spoza uczniów opiekuna ma 0 albo ≥ 5
  wpisów. Drugi warunek zamyka atak różnicowy: opiekun zna punkty swoich uczniów, więc średnia
  szkoły z jednym „obcym” uczniem zdradzałaby jego wynik. „Moi uczniowie” nie podlegają progowi –
  opiekun i tak widzi ich pojedynczo.
- **Koordynator**: liczby udziału zawsze (to dane operacyjne, które i tak widzi na liście
  uczestników); średnia i liczba zakwalifikowanych szkoły/województwa – dopiero od 5 ocenionych
  wpisów. Ta sama reguła w CSV i w PDF.
- Ukryta wartość to „mniej niż 5” / pusta komórka, nigdy zero.

## 4. Kto widzi szkołę

- Agregaty **szkoły** (i raport PDF) dostaje wyłącznie opiekun z dowiązaniem do wykazu
  (`SchoolSupervisor.school_ref`) **i** z danymi zweryfikowanymi przez organizatora
  (`SchoolSupervisor.verified`). Rejestracja opiekuna jest otwarta – bez weryfikacji każdy mógłby
  podać się za nauczyciela dowolnej szkoły.
- Opiekun niezweryfikowany widzi swoich uczniów, województwo i całość; zamiast szkoły – zdanie,
  że porównanie ze szkołą wymaga weryfikacji.
- Koordynator widzi wszystkie szkoły swojego konkursu (izolacja: edycje i szkoły wyłącznie przez
  wpisy **tego** konkursu, edycja cudzego konkursu → 404).

## 5. Wydajność

- Agregat edycji (`edition_summary`) to **dwa zapytania**: wpisy etapów z dwoma `EXISTS` po
  zgłoszeniach (indeksy FK `StageEntry.stage_id`, `Submission.entry_id`, złączenia po PK) i
  publikacje etapów. Sumowanie w Pythonie po kilku tysiącach krotek.
- Pamięć podręczna per edycja: klucz zawiera odcisk publikacji (`stage_id`, `published_at`),
  więc ponowna publikacja unieważnia wpis sama. Czas życia: doba, gdy wszystkie etapy edycji mają
  publikację; 5 minut, dopóki edycja trwa (liczby udziału są wtedy żywe).
- Część opiekuna („moi uczniowie”) liczona na żywo – jedno zapytanie po wpisach jego uczniów.
- Nowych indeksów nie dodajemy: każde zapytanie idzie po istniejących indeksach kluczy obcych.

## 6. Ekrany

- `/supervisor/statistics/?edition=<id>` – wybór edycji, tabela uczniów × etapy (stan, termin,
  punkty i kwalifikacja po publikacji), tabela porównań na etap, wykres SVG średnich przez edycje,
  odnośnik do PDF. Bez JavaScriptu (CSP), wykres rysowany po stronie serwera.
- `/supervisor/statistics/report.pdf?edition=<id>` – raport szkoły (§ 4).
- `/coordinator/school-stats/?edition=<id>&sort=participants|results|name` – ranking szkół,
  województwa, szkoły „do odzyskania”.
- `/coordinator/school-stats/export.csv?edition=<id>` – ranking w CSV (`apps.core.exports`).
- `/coordinator/school-stats/schools/<school_id>/report.pdf?edition=<id>` – raport szkoły.
- Pozycja menu „Statystyki szkół” w „Raportach” i odnośnik na pulpicie opiekuna – oba za flagą.
- Flaga wyłączona → wszystkie adresy 404.

## 7. Audyt i RODO

- Pobranie CSV: `export.generated` (rodzaj, edycja, liczba wierszy). Pobranie PDF:
  `school_stats.report_downloaded` (szkoła, edycja) – bez danych osobowych.
- Rejestr czynności: warunkowy wiersz „Statystyki szkół i opiekunów” (za flagą) z podstawą
  art. 6 ust. 1 lit. f RODO dla wglądu opiekuna w wyniki uczniów, którzy go wskazali, oraz opisem
  k-anonimowości agregatów; wersja rejestru 1.12 (1.11 zajęły delegacje, DEL-01).

## 8. Testy (`apps/school_stats/tests/`)

- reguła progu i dopełnienia (opiekun), maskowanie średnich (koordynator),
- brak punktów i kwalifikacji przed publikacją; `qualified_only` bez średnich i bez punktów
  ucznia spoza listy; punkty z `entry_totals`, a nie z bieżącego `total_points`,
- opiekun widzi wyłącznie swoich uczniów; szkoła tylko po weryfikacji; izolacja konkursów,
- flaga wyłączona → 404; ról innych → 403/302,
- CSV i PDF (nagłówek, brak nazwisk), pamięć podręczna unieważniana publikacją,
- menu: flaga dokłada dokładnie „Statystyki szkół”.

## 9. Dokumentacja

OPERACJE (włączenie flagi), PODRĘCZNIK-ORGANIZATORA (ekran koordynatora, opiekun), CHANGELOG
`[Unreleased]`.

## 10. Poprawki po przeglądzie (4.10.2026) i odstępstwa

- **H1 – zagnieżdżenie.** Szkoła ⊂ województwo ⊂ całość: dla każdej pary pokazanych wierszy różnica
  (szerszy minus część wspólna z węższym, po odjęciu uczniów znanych czytelnikowi) musi mieć 0 albo
  ≥ 5 wpisów i ocenionych; inaczej ukrywamy wiersz **węższy** (`services.scoped_lines`). Dotyczy ekranu
  opiekuna, wykresu postępu i raportu PDF. U koordynatora: województwo minus **suma** pokazanych szkół
  województwa (`services.coordinator_visibility`) – w razie potrzeby ukrywamy najmniejszą szkołę.
  Część szkoły w danym województwie liczymy osobno (`StageStats.group_regions`), bo uczeń bywa
  z innego województwa niż jego szkoła.
- **M1 – konta po anonimizacji** nie tworzą grupy ani regionu (adres `@invalid.`, szkoła „—”):
  nie ma ich w rankingu, CSV, liczbach osób ani na liście szkół do odzyskania; w „całości” zostają.
- **M2 – profil z innego konkursu** nie jest profilem tego konkursu (`supervisor_profile`), a w
  statystykach jest niezweryfikowany.
- **M3 – odstępstwo: nowy model `FrozenMembership`** (aplikacja `school_stats`, migracja
  `0001_initial`). Przy publikacji wyników (odbiornik `post_save` `ResultsPublication`, bez zmian
  w `apps.results`) zamrażamy dla każdego wpisu: szkołę (klucz, nazwa, RSPO), województwo, awans,
  oddanie i termin. Agregaty etapów z publikacją liczą się z zamrożenia, więc późniejsza zmiana
  szkoły albo anonimizacja nie przesuwa ogłoszonych liczb (i nie daje „przed” i „po” do odjęcia).
  Etapy ogłoszone przed wdrożeniem albo przed zapaleniem flagi zamraża się leniwie przy pierwszym
  liczeniu agregatu – wtedy obowiązuje stan profilu z tej chwili. Zdjęcie publikacji kasuje
  zamrożenie. Liczby **osób** w szkołach (ranking, szkoły do odzyskania) są bieżące, bez kont usuniętych.
  Konto usunięte po publikacji liczy się dalej wyłącznie do zamrożonych agregatów.
- **M4** – koordynator (ekran, CSV, PDF): dopełnienie szkoły liczone wobec sumy uczniów wszystkich
  opiekunów tej szkoły w konkursie.
- **L1** – średnia dopiero od 5 wpisów ocenionych. **L2** – historia udziału w PDF pokazuje liczbę
  tylko, gdy jest bezpieczna wobec znanych uczniów. **L4** – eksport CSV szkół do odzyskania
  (z RSPO). **L5** – doba w pamięci podręcznej także dla edycji nie bieżącej, ochrona przed lawiną
  (`cache.add`), etapy historii PDF jednym zapytaniem.
