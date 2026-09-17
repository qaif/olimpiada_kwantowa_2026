# Słownik szkół ponadpodstawowych (SIO / RSPO)

Plik `szkoly-srednie-sio-2025.json` jest **danymi referencyjnymi**, a nie treścią redakcyjną:
powstaje maszynowo z rejestru ministerialnego, leży w repozytorium i jest wgrywany do bazy przy
każdym wdrożeniu (`manage.py seed_schools`, krok 6/7 w `scripts/deploy.sh`).

## Źródło

- zbiór: **Wykaz szkół i placówek oświatowych wg stanu bazy SIO na 30.09.2025**
  ([dane.gov.pl, zbiór 839](https://dane.gov.pl/pl/dataset/839), zasób 1254769),
- plik: `Wykaz_szkół_i_placówek_oświatowych_30.09.2025_.xlsx` (18 MB, arkusz `Arkusz1`,
  55 741 wierszy), rok szkolny **2025/2026**,
- licencja: dane publiczne udostępniane przez Ministerstwo Edukacji Narodowej.

## Reguła doboru wierszy

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
ma w nowym wykazie, dostaje `is_active=False`. Powód jest twardy – `Participant.school_ref`
wskazuje na te wiersze z `on_delete=PROTECT`, a historia zgłoszeń nie może zniknąć razem
z aktualizacją słownika.
