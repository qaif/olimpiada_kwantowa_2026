"""Import CSV wykazu organizatora: nagłówek, wiersze, upsert, wygaszanie i audyt (§ 1.3.3).

Plik pilnuje reguł, które przy imporcie prowadzonym raz na sezon widać dopiero wtedy, gdy są
złamane:

- **plik z Excela ma się otworzyć** – z BOM-em i bez, średnikami i przecinkami, bo tyle wychodzi
  z arkusza prowadzonego przez człowieka (reguła przepisana z ``bulk_registration``),
- **błąd jednego wiersza nie przerywa importu** i ma numer linii, po którym da się go poprawić,
- **upsert, nigdy ``DELETE``** – wiersz nieobecny w pliku jest co najwyżej wygaszany, bo może być
  wskazany przez profil uczestnika sprzed roku,
- **podgląd niczego nie zapisuje** i liczy dokładnie to samo, co zapis,
- **audyt niesie same liczniki** – lista placówek organizatora jest listą jego kontrahentów.
"""

import pytest

from apps.accounts.models import Region
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.schools.custom import (
    AUDIT_IMPORTED,
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    CustomInstitution,
    RowError,
    import_custom_institutions,
    normalise_row,
)
from apps.schools.models import InstitutionType, School

pytestmark = pytest.mark.django_db

HEADER = "nazwa;identyfikator;rodzaj;kraj;region;miejscowosc;kod_pocztowy;adres"


def csv_bytes(*lines: str, header: str = HEADER, bom: bool = False) -> bytes:
    """Plik CSV jako bajty – dokładnie taki, jaki przychodzi z formularza."""
    text = "\r\n".join((header, *lines)) + "\r\n"
    return text.encode("utf-8-sig" if bom else "utf-8")


def rows_of(competition):
    return {row.name: row for row in CustomInstitution.objects.for_competition(competition)}


# --- odczyt pliku --------------------------------------------------------------------------------


def test_a_file_with_a_bom_and_semicolons_is_read(competition):
    """„CSV UTF-8” z polskiego Excela – BOM i średniki."""
    report = import_custom_institutions(
        competition, csv_bytes("Uniwersytet Testowy;;UNIVERSITY;;;Kraków;;", bom=True)
    )
    assert report.created == 1
    assert set(rows_of(competition)) == {"Uniwersytet Testowy"}


def test_a_file_with_commas_is_read(competition):
    """Arkusz z Google Docs – przecinki i ten sam nagłówek."""
    header = HEADER.replace(";", ",")
    report = import_custom_institutions(
        competition, csv_bytes("Uniwersytet Testowy,,UNIVERSITY,,,Kraków,,", header=header)
    )
    assert report.created == 1


def test_an_unreadable_encoding_is_refused_with_a_sentence(competition):
    with pytest.raises(DomainError) as error:
        # Bajty, których nie ma ani UTF-8, ani cp1250 – czyli plik, który nie jest tekstem.
        import_custom_institutions(competition, b"nazwa\n\x81\x83\x88")
    assert error.value.machine_code == "IMPORT_ENCODING"


def test_a_file_without_the_required_column_is_refused_whole(competition):
    """Brak kolumny obowiązkowej jest błędem **pliku**, a nie każdego wiersza z osobna."""
    with pytest.raises(DomainError) as error:
        import_custom_institutions(competition, csv_bytes("Kraków", header="miejscowosc"))
    assert error.value.machine_code == "IMPORT_HEADER"
    assert "nazwa" in str(error.value.detail)


def test_an_unknown_column_is_ignored_not_refused(competition):
    """Arkusz koordynatora bywa jego roboczym arkuszem i ma prawo mieć kolumnę „uwagi”."""
    report = import_custom_institutions(
        competition, csv_bytes("Ośrodek;do sprawdzenia", header="nazwa;uwagi")
    )
    assert report.created == 1


def test_the_header_is_matched_without_diacritics_and_case(competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek;Kraków", header="NAZWA;Miejscowość"))
    assert rows_of(competition)["Ośrodek"].city == "Kraków"


def test_a_file_over_the_size_limit_is_refused_before_parsing(competition):
    with pytest.raises(DomainError) as error:
        import_custom_institutions(competition, b"x" * (MAX_UPLOAD_BYTES + 1))
    assert error.value.machine_code == "IMPORT_TOO_LARGE"


def test_a_file_over_the_row_limit_is_refused(competition):
    lines = [f"Ośrodek numer {number}" for number in range(MAX_ROWS + 1)]
    with pytest.raises(DomainError) as error:
        import_custom_institutions(competition, csv_bytes(*lines, header="nazwa"))
    assert error.value.machine_code == "IMPORT_TOO_MANY_ROWS"


def test_empty_lines_are_skipped_without_an_error(competition):
    """Arkusz zapisany z Excela ma na końcu puste wiersze i nie jest to błąd człowieka."""
    report = import_custom_institutions(competition, csv_bytes("Ośrodek", ";;", "", header="nazwa"))
    assert report.created == 1
    assert report.errors == []


# --- normalizacja wiersza ------------------------------------------------------------------------


def test_the_type_can_be_written_as_a_code_or_as_a_label():
    assert normalise_row({"name": "Ośrodek", "institution_type": "UNIVERSITY"})["institution_type"] == (
        InstitutionType.UNIVERSITY
    )
    assert (
        normalise_row({"name": "Ośrodek", "institution_type": "uczelnia wyższa"})["institution_type"]
        == InstitutionType.UNIVERSITY
    )


def test_an_empty_type_means_other_not_a_guess():
    """Zgadywanie rodzaju z nazwy wpisywałoby do bazy coś, czego organizator nie podał."""
    assert normalise_row({"name": "Klub Fizyka"})["institution_type"] == InstitutionType.OTHER


def test_an_unknown_type_is_an_error_of_the_row():
    with pytest.raises(RowError):
        normalise_row({"name": "Ośrodek", "institution_type": "uczelnia prywatna"})


def test_a_row_without_a_name_is_an_error():
    with pytest.raises(RowError):
        normalise_row({"name": "LO"})


def test_the_country_is_folded_to_two_upper_case_letters():
    assert normalise_row({"name": "Ośrodek", "country": " de "})["country"] == "DE"
    with pytest.raises(RowError):
        normalise_row({"name": "Ośrodek", "country": "Niemcy"})


def test_a_broken_row_does_not_stop_the_import_and_carries_its_line(competition):
    """Plik z jedną literówką ma wejść w pozostałych wierszach – z listą linii do poprawienia."""
    report = import_custom_institutions(
        competition,
        csv_bytes(
            "Ośrodek Pierwszy;;UNIVERSITY;;;Kraków;;",
            "LO;;;;;;;",
            "Ośrodek Trzeci;;;;;Gdańsk;;",
        ),
    )
    assert report.created == 2
    assert [issue.line for issue in report.errors] == [3]
    assert report.skipped == 1


def test_the_same_institution_twice_in_one_file_is_an_error_of_the_second_row(competition):
    report = import_custom_institutions(
        competition, csv_bytes("Ośrodek;A;;;;Kraków;;", "Ośrodek inny;A;;;;Gdańsk;;")
    )
    assert report.created == 1
    assert [issue.line for issue in report.errors] == [3]


# --- upsert i wygaszanie -------------------------------------------------------------------------


def test_the_second_import_updates_by_the_external_id(competition):
    """Upsert po identyfikatorze organizatora – jak ``seed_schools`` po ``rspo``."""
    import_custom_institutions(competition, csv_bytes("Ośrodek Stary;A7;;;;Kraków;;"))
    report = import_custom_institutions(competition, csv_bytes("Ośrodek Nowy;A7;;;;Kraków;;"))
    assert (report.created, report.updated) == (0, 1)
    assert list(rows_of(competition)) == ["Ośrodek Nowy"]


def test_without_an_external_id_the_row_is_matched_by_name_and_city(competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek;;;;;Kraków;;"))
    report = import_custom_institutions(competition, csv_bytes("OŚRODEK;;;;;KRAKÓW;30-001;"))
    assert (report.created, report.updated) == (0, 1)
    assert CustomInstitution.objects.for_competition(competition).count() == 1


def test_a_row_identical_to_the_file_counts_as_unchanged(competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek;A7;;;;Kraków;;"))
    report = import_custom_institutions(competition, csv_bytes("Ośrodek;A7;;;;Kraków;;"))
    assert (report.created, report.updated, report.unchanged) == (0, 0, 1)


def test_a_row_present_again_comes_back_to_the_active_ones(competition):
    """Obecność w nowym pliku jest oświadczeniem organizatora, że placówka nadal jest w programie."""
    import_custom_institutions(competition, csv_bytes("Ośrodek;A7;;;;Kraków;;"))
    CustomInstitution.objects.filter(external_id="A7").update(is_active=False)
    import_custom_institutions(competition, csv_bytes("Ośrodek;A7;;;;Kraków;;"))
    assert CustomInstitution.objects.get(external_id="A7").is_active is True


def test_a_missing_row_is_kept_by_default(competition):
    """Plik bywa uzupełnieniem, a nie całym wykazem – wygaszanie jest kratką, nie domyślną."""
    import_custom_institutions(competition, csv_bytes("Ośrodek Stary;A1;;;;Kraków;;"))
    report = import_custom_institutions(competition, csv_bytes("Ośrodek Nowy;A2;;;;Gdańsk;;"))
    assert report.deactivated == 0
    assert CustomInstitution.objects.get(external_id="A1").is_active is True


def test_a_missing_row_is_deactivated_never_deleted(competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek Stary;A1;;;;Kraków;;"))
    report = import_custom_institutions(
        competition, csv_bytes("Ośrodek Nowy;A2;;;;Gdańsk;;"), deactivate_missing=True
    )
    assert report.deactivated == 1
    assert CustomInstitution.objects.get(external_id="A1").is_active is False
    assert CustomInstitution.objects.for_competition(competition).count() == 2


def test_the_import_never_writes_to_the_public_register(competition):
    """Czego nie wolno: dopisywać wierszy do ``School`` (decyzja D2 etapu 1)."""
    import_custom_institutions(competition, csv_bytes("Ośrodek;;;;;Kraków;;"))
    assert School.objects.count() == 0


def test_rows_land_in_the_competition_that_imported_them(competition, other_competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek A;;;;;Kraków;;"))
    import_custom_institutions(other_competition, csv_bytes("Ośrodek B;;;;;Kraków;;"))
    assert list(rows_of(competition)) == ["Ośrodek A"]
    assert list(rows_of(other_competition)) == ["Ośrodek B"]


def test_the_region_column_is_reduced_to_the_code_of_the_competition_region(competition):
    """W arkuszu stoi nazwa regionu, w bazie ma stać jego kod – wariantów jest kilka, kod jeden."""
    region = Region.objects.for_competition(competition).get(code="malopolskie")
    import_custom_institutions(competition, csv_bytes(f"Ośrodek;;;;{region.name};Kraków;;"))
    assert CustomInstitution.objects.get(name="Ośrodek").region_code == "malopolskie"


def test_an_unknown_region_code_is_kept_as_written(competition):
    """Wiersz opisany podziałem, którego konkurs jeszcze nie ma, wchodzi – napis zostaje."""
    import_custom_institutions(competition, csv_bytes("Ośrodek;;;;okreg-polnocny;Kraków;;"))
    assert CustomInstitution.objects.get(name="Ośrodek").region_code == "okreg-polnocny"


def test_the_source_label_is_kept_with_the_row(competition):
    import_custom_institutions(
        competition, csv_bytes("Ośrodek;;;;;Kraków;;"), source_label="uczelnie.csv, 2026-09-18"
    )
    assert CustomInstitution.objects.get(name="Ośrodek").source_label == "uczelnie.csv, 2026-09-18"


# --- podgląd i audyt -----------------------------------------------------------------------------


def test_the_dry_run_writes_nothing_and_counts_the_same(competition):
    report = import_custom_institutions(
        competition, csv_bytes("Ośrodek;A1;;;;Kraków;;", "LO;;;;;;;"), dry_run=True
    )
    assert (report.created, report.skipped, report.dry_run) == (1, 1, True)
    assert CustomInstitution.objects.count() == 0


def test_the_dry_run_leaves_no_trace_in_the_audit(competition):
    import_custom_institutions(competition, csv_bytes("Ośrodek;;;;;Kraków;;"), dry_run=True)
    assert not AuditLog.objects.filter(action=AUDIT_IMPORTED).exists()


def test_the_audit_entry_carries_counts_only(competition):
    """Nazwy placówek nie mają czego robić w tabeli, którą czyta operator platformy."""
    import_custom_institutions(competition, csv_bytes("Ośrodek Tajny;;;;;Kraków;;", "LO;;;;;;;"))
    entry = AuditLog.objects.get(action=AUDIT_IMPORTED)
    assert entry.diff == {
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "deactivated": 0,
        "skipped": 1,
    }
    assert "Tajny" not in str(entry.diff)
    assert entry.target_type == "tenancy.competition"
