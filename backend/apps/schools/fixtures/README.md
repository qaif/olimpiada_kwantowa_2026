# Słownik placówek (SIO / RSPO)

Plik `szkoly-srednie-sio-2025.json` jest **danymi referencyjnymi**, a nie treścią redakcyjną:
powstaje maszynowo z rejestru ministerialnego, leży w repozytorium i jest wgrywany do bazy przy
każdym wdrożeniu (`manage.py seed_schools`, krok 6/7 w `scripts/deploy.sh`).

## Źródło

- zbiór: **Wykaz szkół i placówek oświatowych wg stanu bazy SIO na 30.09.2025**
  ([dane.gov.pl, zbiór 839](https://dane.gov.pl/pl/dataset/839), zasób 1254769),
- plik: `Wykaz_szkół_i_placówek_oświatowych_30.09.2025_.xlsx` (18 MB, arkusz `Arkusz1`,
  55 741 wierszy), rok szkolny **2025/2026**,
- licencja: dane publiczne udostępniane przez Ministerstwo Edukacji Narodowej.

## Reguła doboru wierszy (`szkoly-srednie-sio-2025.json`)

Do słownika trafiają szkoły ponadpodstawowe **dla młodzieży**, czyli wiersze, w których:

- `Typ podmiotu` należy do listy: liceum ogólnokształcące, technikum, branżowa szkoła I i II
  stopnia, liceum sztuk plastycznych, ogólnokształcąca szkoła muzyczna II stopnia,
  ogólnokształcąca szkoła baletowa, szkoła specjalna przysposabiająca do pracy,
  Bednarska Szkoła Realna,
- `Kategoria uczniów` **nie jest** „Dorośli” (kategoria „Bez kategorii” zostaje – tak w wykazie
  bywają oznaczone szkoły artystyczne i specjalne).

Z wykazu 2025/2026 daje to **8 118 szkół**. Nazwy są przepisane dosłownie, wersalikami, tak jak
stoją w rejestrze – zamiana na zapis mieszany wymagałaby słownika wyjątków (patronowie, skróty,
liczebniki rzymskie) i zapisywałaby uczestnikom nazwy, których szkoły nie używają.

## Rodzaj placówki

Od etapu 2 słownik nie jest już z definicji listą szkół ponadpodstawowych: kolumna
`School.institution_type` (`InstitutionType` w `apps/schools/models.py`) mówi, z którego wykazu
wiersz pochodzi — `SECONDARY`, `PRIMARY`, `UNIVERSITY`. Wartości `FOREIGN`, `NONE` i `OTHER`
**nie mają wierszy w tej tabeli**: to sytuacje uczestnika, a nie pozycje rejestru.

| Rodzaj | Źródło | Reguła doboru | Plik |
|---|---|---|---|
| `SECONDARY` | wykaz SIO (niżej) | `sio.SECONDARY_KINDS` | `szkoly-srednie-sio-2025.json` |
| `PRIMARY` | **ten sam** wykaz SIO | `sio.PRIMARY_KINDS` | `szkoly-podstawowe-sio-2025.json` |
| `UNIVERSITY` | wykaz POL-on | osobny skrypt budujący | `uczelnie-polon-2025.json` |

Rodzaj stoi przy **każdym wierszu** pliku, a nie w nazwie pliku ani w opcji komendy, bo to od
niego zależy, co `seed_schools` wygasi: wygaszanie („wiersz nieobecny w pliku dostaje
`is_active=False`”) zawęża się do **rodzajów obecnych w pliku**. Bez tego zawężenia wgranie wykazu
uczelni wygasiłoby wszystkie szkoły ponadpodstawowe, a najbliższe wdrożenie wygasiłoby uczelnie.

`szkoly-srednie-sio-2025.json` tego pola **nie ma i nie musi mieć**: brak pola znaczy `SECONDARY`.
Plik powstał przed etapem 2, zawiera wyłącznie takie wiersze i ma się dać porównać z nowym wykazem
linijka po linijce — dopisanie do niego 8 118 razy tej samej wartości nic by nie wyjaśniło.
Pliki generowane od nowa (także przyszłe wydania wykazu szkół ponadpodstawowych) rodzaj już mają,
bo wpisuje go `sio.school_from_row`.

## Miejscowość: co jest w pliku, a co powstaje przy wgrywaniu

W pliku stoi `city` **dokładnie tak, jak w wykazie**, razem z jego osobliwością: pięć największych
miast jest w nim rozbitych na dzielnice (`Wrocław-Krzyki`, `Kraków-Nowa Huta`…), a Warszawa
figuruje **wyłącznie** pod nazwami dzielnic (`Śródmieście`, `Wola`, `Mokotów`… – napisu „Warszawa”
nie ma tu ani razu). Plik tego nie poprawia, bo jest wierną kopią źródła i ma się dać porównać
z nowym wykazem linijka po linijce.

Gminę (`School.city_parent`, po niej chodzi krok „Miejscowość” w rejestracji) wylicza dopiero
`seed_schools` – regułą z `apps/schools/normalise.py`, tą samą, której używa `School.save()`
i backfill w migracji `schools.0003`. Na wykazie 2025/2026 dotyczy to **903 wierszy**.

## Odświeżenie (raz na rok szkolny)

```bash
# 1. pobierz nowy wykaz z dane.gov.pl
# 2. wygeneruj fixture (openpyxl jest w backend/.venv, Django nie jest potrzebne)
backend/.venv/Scripts/python.exe scripts/build_school_fixture.py Wykaz_szkol.xlsx
# 3. sprawdź diff i zacommituj plik
# 4. wdrożenie samo uruchomi: manage.py seed_schools
```

`seed_schools` robi **upsert po numerze RSPO** i **nigdy nie kasuje wierszy**: szkoła, której nie
ma w nowym wykazie, dostaje `is_active=False` — w obrębie rodzajów placówek obecnych w pliku.
Powód jest twardy – `Participant.school_ref` wskazuje na te wiersze z `on_delete=PROTECT`,
a historia zgłoszeń nie może zniknąć razem z aktualizacją słownika.

Skrypt buduje dziś wykaz szkół **ponadpodstawowych**; regułę doboru szkół podstawowych
(`sio.PRIMARY_KINDS`) ma już `apps/schools/sio.py` i sięga się po nią przez
`read_schools(path, institution_type="PRIMARY")`. Przełącznik wiersza poleceń dla tej reguły
dokłada zadanie, które przynosi sam plik `szkoly-podstawowe-sio-2025.json` — bez pliku byłaby to
opcja bez zastosowania.
