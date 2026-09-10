"""Reguła doboru wierszy z wykazu SIO – na syntetycznym arkuszu zbudowanym w teście.

Arkusz jest budowany, a nie wczytywany z repozytorium, bo testowana jest **decyzja**
(„kto trafia do słownika”), a nie zawartość konkretnego pliku ministerstwa. Kilkanaście wierszy
pokrywa każdy powód odrzucenia; prawdziwy wykaz ma 55 tysięcy i nie dałby się przeczytać.
"""

from openpyxl import Workbook

from apps.schools.sio import (
    DEFAULT_SHEET,
    REQUIRED_COLUMNS,
    SioFormatError,
    read_schools,
    school_from_row,
    street_address,
    write_fixture,
)

#: Nagłówek testowego arkusza. Kolumna „Adres www” jest tu po to, żeby test nie zakładał, że
#: wymagane kolumny stoją obok siebie ani że arkusz nie ma kolumn nadmiarowych.
HEADER = (*REQUIRED_COLUMNS, "Adres www")


def row(**overrides) -> dict:
    values = {
        "Wojewodztwo": "MAŁOPOLSKIE",
        "Miejscowość": "Kraków",
        "RSPO": 12345,
        "Typ podmiotu": "Liceum ogólnokształcące",
        "Nazwa placówki": "II LICEUM OGÓLNOKSZTAŁCĄCE IM. KRÓLA JANA III SOBIESKIEGO W KRAKOWIE",
        "Ulica": "ul. Sobieskiego",
        "Numer domu": "9",
        "Numer lokalu": "",
        "Kod pocztowy": "31-136",
        "Publiczność": "publiczna",
        "Kategoria uczniów": "Dzieci lub młodzież",
        "Adres www": "www.example.test",
    }
    values.update(overrides)
    return values


def workbook_with(rows, tmp_path, sheet: str = DEFAULT_SHEET):
    book = Workbook()
    sheet_obj = book.active
    sheet_obj.title = sheet
    sheet_obj.append(list(HEADER))
    for entry in rows:
        sheet_obj.append([entry.get(name, "") for name in HEADER])
    path = tmp_path / "wykaz.xlsx"
    book.save(path)
    return path


def test_secondary_school_for_youth_is_selected():
    school = school_from_row(row())

    assert school["rspo"] == 12345
    assert school["kind"] == "LO"
    # Województwo z wersalikowej nazwy sprowadzone do sluga, którym posługuje się reszta systemu.
    assert school["voivodeship"] == "malopolskie"
    assert school["city"] == "Kraków"
    assert school["is_public"] is True
    # Nazwa dosłownie z rejestru – bez „upiększania” wersalików.
    assert school["name"].startswith("II LICEUM OGÓLNOKSZTAŁCĄCE")


def test_adult_school_is_rejected_even_when_its_kind_matches():
    assert school_from_row(row(**{"Kategoria uczniów": "Dorośli"})) is None


def test_row_without_a_category_is_kept():
    """„Bez kategorii” to w wykazie m.in. szkoły artystyczne – odrzuca się wyłącznie „Dorośli”."""
    assert school_from_row(row(**{"Kategoria uczniów": "Bez kategorii"})) is not None


def test_primary_school_and_other_institutions_are_rejected():
    for kind in ("Szkoła podstawowa", "Przedszkole", "Szkoła policealna", "Bursa"):
        assert school_from_row(row(**{"Typ podmiotu": kind})) is None, kind


def test_art_kinds_collapse_to_one_value():
    for kind in (
        "Liceum sztuk plastycznych",
        "Ogólnokształcąca szkoła muzyczna II stopnia",
        "Ogólnokształcąca szkoła baletowa",
    ):
        assert school_from_row(row(**{"Typ podmiotu": kind}))["kind"] == "ARTYSTYCZNA", kind


def test_non_public_school_is_marked():
    assert school_from_row(row(**{"Publiczność": "niepubliczna"}))["is_public"] is False


def test_row_without_a_name_or_a_city_is_rejected():
    assert school_from_row(row(**{"Nazwa placówki": ""})) is None
    assert school_from_row(row(**{"Miejscowość": ""})) is None


def test_address_joins_street_with_house_and_flat():
    assert street_address("ul. Polna", "6", "3") == "ul. Polna 6/3"
    assert street_address("ul. Polna", "6", "") == "ul. Polna 6"
    assert street_address("", "", "") == ""


def test_reading_a_workbook_skips_rejected_rows_and_duplicate_rspo(tmp_path):
    path = workbook_with(
        [
            row(),
            row(**{"RSPO": 12345, "Nazwa placówki": "DUPLIKAT TEGO SAMEGO RSPO"}),
            row(**{"RSPO": 999, "Typ podmiotu": "Technikum", "Kategoria uczniów": "Dorośli"}),
            row(**{"RSPO": 777, "Typ podmiotu": "Technikum"}),
            row(**{"RSPO": 555, "Typ podmiotu": "Szkoła podstawowa"}),
        ],
        tmp_path,
    )

    schools = list(read_schools(path))

    assert [school["rspo"] for school in schools] == [12345, 777]


def test_reading_a_workbook_without_required_columns_fails_loudly(tmp_path):
    book = Workbook()
    book.active.title = DEFAULT_SHEET
    book.active.append(["Wojewodztwo", "RSPO"])
    path = tmp_path / "krotki.xlsx"
    book.save(path)

    try:
        list(read_schools(path))
    except SioFormatError as exc:
        assert "Nazwa placówki" in str(exc)
    else:  # pragma: no cover - obrona przed cichym przepuszczeniem błędu
        raise AssertionError("Oczekiwano SioFormatError")


def test_fixture_is_written_one_object_per_line(tmp_path):
    output = tmp_path / "szkoly.json"

    count = write_fixture(read_schools(workbook_with([row()], tmp_path)), output)

    assert count == 1
    content = output.read_text(encoding="utf-8")
    # Nawiasy listy w osobnych liniach, obiekt w jednej – tak wygląda czytelny diff w git.
    assert content.startswith("[\n{")
    assert content.endswith("}\n]\n")
    assert "\r" not in content
