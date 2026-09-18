"""Odczyt wykazu SIO (.xlsx) i reguły doboru placówek (ponadpodstawowe, podstawowe).

Moduł jest **czysto tekstowy**: nie importuje Django, nie dotyka bazy i nie wie o modelach.
Woła go skrypt ``scripts/build_school_fixture.py`` (raz na rok szkolny, ręcznie) – a mieszka
tutaj, a nie w samym skrypcie, z jednego powodu: reguła doboru wierszy jest decyzją
merytoryczną („kto jest adresatem olimpiady”), więc ma być przetestowana razem z resztą
aplikacji, a nie tylko przy okazji uruchomienia narzędzia.

Nazwy placówek są przepisywane **dosłownie**, wersalikami, tak jak stoją w rejestrze. Zamiana na
zapis mieszany wymagałaby słownika wyjątków (patronowie, skróty „im.”/„nr”, liczebniki rzymskie)
i przy pierwszej pomyłce zapisałaby uczestnikowi w wynikach nazwę, której jego szkoła nie używa.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from apps.core.text import fold

#: Domyślny arkusz w skoroszycie z dane.gov.pl.
DEFAULT_SHEET = "Arkusz1"

#: „Typ podmiotu” z wykazu → wartość ``apps.schools.models.SchoolKind``. Klucze są jedynym
#: filtrem typów: podmiot spoza tej mapy do słownika nie trafia.
SECONDARY_KINDS: dict[str, str] = {
    "Liceum ogólnokształcące": "LO",
    "Technikum": "TECHNIKUM",
    "Branżowa szkoła I stopnia": "BRANZOWA_1",
    "Branżowa szkoła II stopnia": "BRANZOWA_2",
    "Liceum sztuk plastycznych": "ARTYSTYCZNA",
    "Ogólnokształcąca szkoła muzyczna II stopnia": "ARTYSTYCZNA",
    "Ogólnokształcąca szkoła baletowa": "ARTYSTYCZNA",
    "Szkoła specjalna przysposabiająca do pracy": "SPECJALNA",
    "Bednarska Szkoła Realna": "INNA",
}

#: To samo dla szkół podstawowych – **inna reguła doboru z tego samego wykazu**, a nie inny plik
#: źródłowy. Wartości po prawej są nadal ``SchoolKind``, bo ta oś rozróżnia typ szkoły wewnątrz
#: wykazu i etap 2 jej nie zmienia: szkoła podstawowa nie jest ani liceum, ani technikum, więc
#: trafia na ``INNA`` i staje za nimi w porządku podpowiedzi (``KIND_ORDER``). Tym, co odróżnia
#: te wiersze od ponadpodstawowych, jest ``School.institution_type``, a nie ``kind``.
PRIMARY_KINDS: dict[str, str] = {
    "Szkoła podstawowa": "INNA",
    "Ogólnokształcąca szkoła muzyczna I stopnia": "ARTYSTYCZNA",
}

#: Rodzaj placówki wyprodukowany domyślnie – dzisiejszy wykaz zawiera wyłącznie takie wiersze.
DEFAULT_INSTITUTION_TYPE = "SECONDARY"

#: Rodzaj placówki → reguła doboru wierszy. Klucze są napisami, a nie ``InstitutionType``, bo ten
#: moduł nie importuje Django (patrz docstring); zgodność z wykazem wartości pilnuje test
#: ``test_institution_types.py``. Rodzaju spoza tej mapy nie da się zbudować z arkusza SIO –
#: uczelnie idą z POL-onu (osobny plik), a ``FOREIGN``/``NONE``/``OTHER`` nie mają wierszy wcale.
INSTITUTION_KINDS: dict[str, dict[str, str]] = {
    DEFAULT_INSTITUTION_TYPE: SECONDARY_KINDS,
    "PRIMARY": PRIMARY_KINDS,
}

#: Kategoria uczniów, która wyklucza wiersz. Szkoły dla dorosłych mają własny rytm i wiek
#: uczestników; „Bez kategorii” zostaje, bo tak w wykazie bywają oznaczone szkoły artystyczne
#: i specjalne, które jak najbardziej uczą młodzież.
EXCLUDED_STUDENT_CATEGORY = "Dorośli"

#: Kolumny, na których ten moduł stoi. Brak którejkolwiek = wykaz zmienił kształt; cichy fixture
#: bez połowy danych byłby gorszy niż wywrócony import.
REQUIRED_COLUMNS = (
    "Wojewodztwo",
    "Miejscowość",
    "RSPO",
    "Typ podmiotu",
    "Nazwa placówki",
    "Ulica",
    "Numer domu",
    "Numer lokalu",
    "Kod pocztowy",
    "Publiczność",
    "Kategoria uczniów",
)


class SioFormatError(RuntimeError):
    """Wykaz nie ma kształtu, jakiego oczekuje ten moduł (brak kolumn, brak arkusza)."""


def _clean(value) -> str:
    """Komórka arkusza jako przycięty tekst. Puste komórki bywają ``None``, bywają pustym stringiem."""
    return "" if value is None else str(value).strip()


def street_address(street: str, house: str, flat: str) -> str:
    """„ul. Komuny Paryskiej 6/3”. Pusty string, gdy wykaz nie podaje ani ulicy, ani numeru."""
    if house and flat:
        number = f"{house}/{flat}"
    else:
        number = house or flat
    return " ".join(part for part in (street.strip(), number.strip()) if part).strip()


def kinds_for(institution_type: str) -> dict[str, str]:
    """Mapa „Typ podmiotu” → ``SchoolKind`` dla danego rodzaju placówki. Nieznany rodzaj jest błędem."""
    try:
        return INSTITUTION_KINDS[institution_type]
    except KeyError as exc:
        raise SioFormatError(
            f"Wykazu SIO nie da się odczytać jako {institution_type!r} "
            f"(znane rodzaje: {', '.join(sorted(INSTITUTION_KINDS))})"
        ) from exc


def school_from_row(row: dict[str, str], institution_type: str = DEFAULT_INSTITUTION_TYPE) -> dict | None:
    """Wiersz wykazu → wiersz słownika, albo ``None``, gdy nie przechodzi reguły doboru.

    Reguła (domyślnie: szkoły ponadpodstawowe dla młodzieży): typ podmiotu z ``SECONDARY_KINDS``
    **i** kategoria uczniów inna niż „Dorośli”. Wiersz bez nazwy albo bez miejscowości odpada –
    w podpowiedzi nie dałoby się go od niczego odróżnić.

    ``institution_type`` przełącza **wyłącznie** regułę doboru (``INSTITUTION_KINDS``) i trafia do
    wyniku jako pole. Rodzaj jest zapisany przy **każdym wierszu**, a nie domyślany z nazwy pliku,
    bo od niego zależy, które wiersze ``seed_schools`` wygasi: plik, który nie mówi o sobie, czym
    jest, wygasiłby przy wgraniu cały pozostały wykaz.
    """
    kind = kinds_for(institution_type).get(row.get("Typ podmiotu", ""))
    if kind is None or row.get("Kategoria uczniów", "") == EXCLUDED_STUDENT_CATEGORY:
        return None
    try:
        rspo = int(row.get("RSPO") or "")
    except ValueError:
        return None
    name = row.get("Nazwa placówki", "")
    city = row.get("Miejscowość", "")
    if not name or not city:
        return None
    return {
        "rspo": rspo,
        "name": name[:255],
        "kind": kind,
        "institution_type": institution_type,
        # Wykaz podaje województwo wersalikami („DOLNOŚLĄSKIE”); reszta systemu posługuje się
        # slugiem ASCII. Czy slug jest jednym z szesnastu dopuszczalnych, sprawdza już
        # ``seed_schools`` – to on zna ``Voivodeship``, a ten moduł nie importuje Django.
        "voivodeship": fold(row.get("Wojewodztwo", "")),
        "city": city[:120],
        "postal_code": row.get("Kod pocztowy", "")[:12],
        "address": street_address(
            row.get("Ulica", ""), row.get("Numer domu", ""), row.get("Numer lokalu", "")
        )[:255],
        "is_public": row.get("Publiczność", "").lower().startswith("publiczn"),
    }


def read_schools(
    path: Path, sheet: str = DEFAULT_SHEET, institution_type: str = DEFAULT_INSTITUTION_TYPE
) -> Iterator[dict]:
    """Wiersze słownika wyprodukowane z arkusza. Generator – wykaz ma kilkadziesiąt tysięcy wierszy.

    ``read_only=True`` jest warunkiem wykonalności, a nie optymalizacją: pełny model openpyxl
    trzyma cały arkusz w pamięci jako obiekty komórek, co dla 18 MB pliku znaczy kilka gigabajtów.

    ``institution_type`` wybiera regułę doboru (``INSTITUTION_KINDS``) – jeden arkusz ministerstwa
    daje więc tyle plików, ile rodzajów placówek, i **żaden z nich nie miesza rodzajów**.
    """
    from openpyxl import load_workbook

    kinds_for(institution_type)  # nieznany rodzaj ma się wywrócić przed otwarciem 18 MB pliku
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise SioFormatError(f"Skoroszyt nie ma arkusza {sheet!r} (są: {workbook.sheetnames})")
        stream = workbook[sheet].iter_rows(values_only=True)
        header = [_clean(cell) for cell in next(stream, ())]
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            raise SioFormatError(f"Arkusz nie ma kolumn: {', '.join(missing)}")
        columns = {name: index for index, name in enumerate(header)}
        seen: set[int] = set()
        for values in stream:
            row = {name: _clean(values[index]) for name, index in columns.items() if index < len(values)}
            school = school_from_row(row, institution_type)
            if school is None:
                continue
            # RSPO jest kluczem rejestru, ale eksport bywa wydany z powtórzeniami (np. gdy szkoła
            # zmieniła organ prowadzący w trakcie roku). Pierwsze wystąpienie wygrywa.
            if school["rspo"] in seen:
                continue
            seen.add(school["rspo"])
            yield school
    finally:
        workbook.close()


def write_fixture(rows, output: Path) -> int:
    """Zapisuje listę JSON – **jeden obiekt na linię**, żeby diff w git był czytelny.

    ``json.dump`` z wcięciami rozdmuchałby plik do kilkunastu megabajtów i pokazywał każdą zmianę
    jako ścianę linii; zapis w jednej linii nie pokazywałby jej wcale. Format pośredni sprawia,
    że dopisanie szkoły w kolejnym wykazie to jedna linia diffa.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("[\n")
        for row in rows:
            handle.write("" if count == 0 else ",\n")
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            count += 1
        handle.write("\n]\n")
    return count
