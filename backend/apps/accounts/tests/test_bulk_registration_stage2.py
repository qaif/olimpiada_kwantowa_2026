"""Import listy uczniów po etapie 2: region, kategoria i placówka jako kolumny pliku (§ 4.4 T26).

Przedmiotem są cztery zdania, a nie szczegóły wykonania:

- **Konkurs #1 nie widzi tej zmiany.** Zestaw kolumn, komunikaty, podgląd i koszyk są takie, jak
  przed etapem 2, a odczyt konfiguracji nie kosztuje ani jednego zapytania (§ 0.1, § 5.6). Plik
  przygotowany dla konkursu z flagami wczytuje się u niego dalej – nadmiarowe rubryki są po prostu
  pomijane, tak samo jak każda inna kolumna, której import nie zna;
- **kolumna wchodzi razem ze swoją flagą.** Instrukcja nad formularzem, wzorcowy wiersz nagłówka
  i parser czytają tę samą listę (``extra_columns``), więc nie ma rubryki, którą nauczyciel
  wypełni, a import zignoruje;
- **reguły nie są przepisane, tylko wywołane.** Region rozstrzyga ``_resolve_region``, kategorię
  ``Category.auto_for_grade``, a placówkę ``_resolve_institution`` – ten sam kod i te same odmowy,
  co przy ``/register/``. Import dokłada wyłącznie nazwę rubryki i wartość, bo błąd dotyczy wiersza
  w cudzym arkuszu;
- **import nadal nikogo nie zapisuje do etapu.** Podany ``stage`` uzupełnia kategorię na wpisach,
  które już są, i nie tworzy ani jednego nowego – konto bez zgód nie ma prawa startować.
"""

from __future__ import annotations

import io

import pytest
from django.utils import timezone

from apps.accounts.bulk_registration import (
    ACTION_CREATE,
    ACTION_SKIP,
    columns_for,
    extra_columns,
    header_line,
    import_students,
    preview_upload,
)
from apps.accounts.models import Participant, RegistrationProfile, Voivodeship
from apps.accounts.regions import ABROAD_CODE
from apps.accounts.services import (
    CUSTOM_DIRECTORY_FLAG,
    CUSTOM_REGIONS_FLAG,
    REGISTRATION_PROFILE_FLAG,
)
from apps.competitions.models import Category, StageEntry
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.schools.custom import CustomInstitution
from apps.schools.models import InstitutionType
from apps.schools.tests.factories import SchoolFactory

pytestmark = pytest.mark.django_db

CATEGORIES_FLAG = "categories"

#: Rocznik osoby pełnoletniej **względem dzisiaj** – literał przestałby cokolwiek znaczyć po sylwestrze.
ADULT_YEAR = timezone.localdate().year - 25

#: Dzisiejszy nagłówek importu nauczyciela, znak w znak.
BASE_HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"


class Upload(io.BytesIO):
    """Namiastka wgranego pliku: ``name`` i ``size``, czyli to, czego dotyka ``read_table``."""

    def __init__(self, data: bytes, name: str = "lista.csv"):
        super().__init__(data)
        self.name = name
        self.size = len(data)


def enable(competition, *flags) -> None:
    """Włącza flagi zapisem do ``feature_flags`` – tak jak na produkcji, a nie podmianą metody."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])


def csv_upload(*rows: str, header: str = BASE_HEADER) -> Upload:
    return Upload("\n".join([header, *rows]).encode("utf-8"))


def student(email: str = "kasia@example.test", *, grade: int = 2, extra: str = "") -> str:
    """Wiersz ucznia w dzisiejszym układzie kolumn, z dopisanym blokiem kolumn konkursu.

    Dwa puste pola na końcu bloku podstawowego to telefon i adres opiekuna prawnego – obie kolumny
    są nieobowiązkowe, ale **miejsce** w wierszu zajmują, bo dopasowanie idzie po nagłówku.
    """
    return f"Kasia;Nowak;{email};{ADULT_YEAR};{grade};;;{extra}"


def preview(competition, *rows: str, header: str) -> list:
    return preview_upload(
        csv_upload(*rows, header=header), with_supervisor=False, competition=competition
    ).rows


def profile_for(competition, **fields) -> RegistrationProfile:
    return RegistrationProfile.objects.create(competition=competition, **fields)


def category(competition, code: str, **fields) -> Category:
    data = {"name": code.capitalize()}
    data.update(fields)
    return Category.objects.create(competition=competition, code=code, **data)


# --- zestaw kolumn -------------------------------------------------------------------------------


def test_konkurs_bez_flag_nie_dostaje_ani_jednej_dodatkowej_kolumny(competition, django_assert_num_queries):
    """Najtwardsze zdanie tego zadania: dla Konkursu #1 nagłówek i instrukcja są dzisiejsze.

    Zapytań zero, bo zestaw kolumn składa się przy **każdym** wejściu na ekran importu, a flagi
    Konkursu #1 są domyślne (§ 5.6).
    """
    with django_assert_num_queries(0):
        assert extra_columns(competition) == ()
        assert header_line(with_supervisor=False, competition=competition) == BASE_HEADER
        assert header_line(with_supervisor=False) == BASE_HEADER


@pytest.mark.parametrize(
    ("flags", "label"),
    [
        ((CUSTOM_REGIONS_FLAG,), "region"),
        ((CATEGORIES_FLAG,), "kategoria"),
        ((REGISTRATION_PROFILE_FLAG,), "typ placówki"),
        ((REGISTRATION_PROFILE_FLAG,), "placówka"),
        ((REGISTRATION_PROFILE_FLAG,), "kraj"),
    ],
)
def test_kazda_kolumna_wchodzi_razem_ze_swoja_flaga(competition, flags, label):
    assert label not in header_line(with_supervisor=False, competition=competition)

    enable(competition, *flags)

    labels = [column.label for column in columns_for(with_supervisor=False, competition=competition)]
    assert label in labels


def test_kolumna_slownika_organizatora_wymaga_flagi_i_decyzji_konkursu(competition):
    """Dwa warunki, tak samo jak przy ``/register/``: flaga platformy i pole profilu."""
    enable(competition, CUSTOM_DIRECTORY_FLAG)
    labels = [column.label for column in columns_for(with_supervisor=False, competition=competition)]
    assert "placówka (identyfikator)" not in labels

    profile_for(competition, allow_custom_directory=True)

    labels = [column.label for column in columns_for(with_supervisor=False, competition=competition)]
    assert "placówka (identyfikator)" in labels


def test_plik_z_cudza_kolumna_wczytuje_sie_jak_dawniej(competition):
    """Konkurs bez flag dostaje plik przygotowany dla konkursu z flagami i ma go wczytać.

    Nadmiarowa rubryka jest pomijana, a nie odrzucana: nauczyciel prowadzący klasy w dwóch
    olimpiadach ma jeden arkusz i nie ma powodu kazać mu kasować kolumn.
    """
    rows = preview(
        competition,
        student(extra="mazowieckie;podstawowa"),
        header=f"{BASE_HEADER};region;kategoria",
    )

    assert [row.action for row in rows] == [ACTION_CREATE]
    assert rows[0].errors == []
    assert rows[0].region_code == ""
    assert rows[0].category is None


def test_koszyk_pliku_bez_kolumn_konkursu_jest_taki_jak_dawniej(competition):
    """Podgląd Konkursu #1 nie zmienia się nawet w polu ukrytym – koszyk ma dzisiejsze klucze."""
    rows = preview(competition, student(), header=BASE_HEADER)

    assert set(rows[0].payload()) == {"n", "f", "l", "e", "b", "g", "p", "gu", "s"}


# --- region --------------------------------------------------------------------------------------


def test_region_z_pliku_trafia_do_profilu_i_do_kolumny_district(competition):
    """Region jest źródłem prawdy, ``district`` jego kopią – jedno miejsce zapisu (§ 1.4.2)."""
    enable(competition, CUSTOM_REGIONS_FLAG)
    rows = preview(competition, student(extra="pomorskie"), header=f"{BASE_HEADER};region")

    import_students(rows, school_name="XIV LO")

    saved = Participant.objects.get(user__email="kasia@example.test")
    assert saved.region is not None
    assert saved.region.code == Voivodeship.POMORSKIE
    assert saved.district == Voivodeship.POMORSKIE


def test_nieznany_region_jest_bledem_wiersza_z_nazwa_kolumny_i_wartoscia(competition):
    """„Nieznany region” bez wskazania rubryki i wartości nie mówi nauczycielowi nic."""
    enable(competition, CUSTOM_REGIONS_FLAG)
    rows = preview(competition, student(extra="atlantyda"), header=f"{BASE_HEADER};region")

    assert rows[0].action == ACTION_SKIP
    assert "kolumna „region” („atlantyda”)" in rows[0].errors[0]


def test_pusta_kolumna_region_zostawia_dzisiejsza_regule(competition):
    """Brak wskazania = województwo szkoły z rejestru, czyli linia sprzed etapu 2."""
    enable(competition, CUSTOM_REGIONS_FLAG)
    school = SchoolFactory(voivodeship=Voivodeship.MALOPOLSKIE)
    rows = preview(competition, student(extra=""), header=f"{BASE_HEADER};region")

    import_students(rows, school_name=school.name, school_ref=school)

    saved = Participant.objects.get(user__email="kasia@example.test")
    assert saved.district == Voivodeship.MALOPOLSKIE
    assert saved.region is not None
    assert saved.region.code == Voivodeship.MALOPOLSKIE


# --- kategoria -----------------------------------------------------------------------------------


def test_kod_kategorii_z_pliku_wygrywa_z_regula_klas(competition):
    enable(competition, CATEGORIES_FLAG)
    category(competition, "podstawowa", grade_min=1, grade_max=1)
    wskazana = category(competition, "ponadpodstawowa", position=1)

    rows = preview(competition, student(grade=1, extra="ponadpodstawowa"), header=f"{BASE_HEADER};kategoria")

    assert rows[0].category == wskazana


def test_brak_kodu_liczy_kategorie_z_klasy(competition):
    """Reguła jest jedna i jest w ``Category.auto_for_grade`` – import jej nie powtarza."""
    enable(competition, CATEGORIES_FLAG)
    mlodsza = category(competition, "mlodsza", grade_min=1, grade_max=2)
    category(competition, "starsza", grade_min=3, grade_max=5, position=1)

    rows = preview(competition, student(grade=2), header=BASE_HEADER)

    assert rows[0].category == mlodsza


def test_konkurs_z_kategoria_z_wyboru_nie_wpisuje_jej_za_uczestnika(competition):
    """Profil czyta się tą samą drogą, co przy ``/register/`` – więc razem z jego flagą."""
    enable(competition, CATEGORIES_FLAG, REGISTRATION_PROFILE_FLAG)
    category(competition, "mlodsza", grade_min=1, grade_max=5)
    profile_for(competition, participant_picks_category=True)

    rows = preview(competition, student(grade=2), header=BASE_HEADER)

    assert rows[0].category is None


def test_nieznana_kategoria_jest_bledem_wiersza(competition):
    enable(competition, CATEGORIES_FLAG)
    rows = preview(competition, student(extra="zmyslona"), header=f"{BASE_HEADER};kategoria")

    assert rows[0].action == ACTION_SKIP
    assert "kolumna „kategoria” („zmyslona”)" in rows[0].errors[0]


def test_kategoria_lada_na_istniejacym_wpisie_do_etapu(competition):
    """Podany etap uzupełnia kategorię tam, gdzie wpis **już jest**."""
    enable(competition, CATEGORIES_FLAG)
    starsza = category(competition, "starsza", grade_min=1, grade_max=5)
    entry = StageEntryFactory(competition=competition)
    email = entry.participant.user.email
    rows = preview(competition, student(email), header=BASE_HEADER)

    import_students(rows, school_name="XIV LO", stage=entry.stage)

    entry.refresh_from_db()
    assert entry.category == starsza


def test_import_nadal_nikogo_nie_zapisuje_do_etapu(competition):
    """Konto bez zgód nie ma prawa startować – podany etap nie tworzy ani jednego wpisu."""
    enable(competition, CATEGORIES_FLAG)
    category(competition, "starsza", grade_min=1, grade_max=5)
    entry = StageEntryFactory(competition=competition)
    rows = preview(competition, student(), header=BASE_HEADER)

    import_students(rows, school_name="XIV LO", stage=entry.stage)

    assert StageEntry.objects.count() == 1


# --- placówka ------------------------------------------------------------------------------------


def with_types(competition, *types) -> RegistrationProfile:
    enable(competition, REGISTRATION_PROFILE_FLAG)
    return profile_for(competition, allowed_institution_types=list(types), allow_foreign=True)


def test_placowka_spoza_polski_wymaga_kraju(competition):
    with_types(competition, InstitutionType.SECONDARY, InstitutionType.FOREIGN)
    header = f"{BASE_HEADER};typ placówki;placówka;kraj"

    rows = preview(competition, student(extra="FOREIGN;Gymnasium Berlin;"), header=header)

    assert rows[0].action == ACTION_SKIP
    assert "kolumna „kraj”" in rows[0].errors[0]


def test_placowka_spoza_polski_zapisuje_nazwe_kraj_i_region_poza_polska(competition):
    """Nazwa kopiuje się do ``school`` – żeby statystyki nie musiały wiedzieć, skąd wiersz (§ 1.3.2)."""
    with_types(competition, InstitutionType.SECONDARY, InstitutionType.FOREIGN)
    enable(competition, CUSTOM_REGIONS_FLAG)
    header = f"{BASE_HEADER};typ placówki;placówka;kraj"
    rows = preview(competition, student(extra="FOREIGN;Gymnasium Berlin;DE"), header=header)

    import_students(rows, school_name="XIV LO")

    saved = Participant.objects.select_related("region").get(user__email="kasia@example.test")
    assert saved.school == "Gymnasium Berlin"
    assert saved.institution_name == "Gymnasium Berlin"
    assert saved.country == "DE"
    assert saved.district == ABROAD_CODE
    assert saved.region.code == ABROAD_CODE


def test_rodzaj_placowki_spoza_dopuszczonych_jest_bledem_wiersza(competition):
    with_types(competition, InstitutionType.SECONDARY)
    header = f"{BASE_HEADER};typ placówki;placówka;kraj"

    rows = preview(competition, student(extra="UNIVERSITY;Politechnika;"), header=header)

    assert rows[0].action == ACTION_SKIP
    assert "kolumna „typ placówki” („UNIVERSITY”)" in rows[0].errors[0]


def test_wiersz_bez_wlasnej_placowki_zostaje_przy_szkole_calego_pliku(competition):
    """Szkoła jest jedna dla listy i tak zostaje – blok „placówka” dotyczy wyjątków, nie reguły."""
    with_types(competition, InstitutionType.SECONDARY, InstitutionType.FOREIGN)
    school = SchoolFactory(voivodeship=Voivodeship.SLASKIE)
    header = f"{BASE_HEADER};typ placówki;placówka;kraj"
    rows = preview(competition, student(extra=";;"), header=header)

    import_students(rows, school_name=school.name, school_ref=school)

    saved = Participant.objects.get(user__email="kasia@example.test")
    assert saved.school == school.name
    assert saved.school_ref_id == school.pk
    assert saved.district == Voivodeship.SLASKIE


def test_placowka_ze_slownika_organizatora_trafia_do_profilu_razem_z_regionem(competition):
    enable(competition, CUSTOM_DIRECTORY_FLAG, CUSTOM_REGIONS_FLAG)
    profile_for(
        competition,
        allow_custom_directory=True,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.UNIVERSITY],
    )
    enable(competition, REGISTRATION_PROFILE_FLAG)
    own = CustomInstitution.objects.create(
        competition=competition,
        name="Uniwersytet Partnerski",
        institution_type=InstitutionType.UNIVERSITY,
        region_code=Voivodeship.POMORSKIE,
    )
    header = f"{BASE_HEADER};typ placówki;placówka (identyfikator)"
    rows = preview(competition, student(extra=f"UNIVERSITY;{own.pk}"), header=header)

    import_students(rows, school_name="XIV LO")

    saved = Participant.objects.select_related("region").get(user__email="kasia@example.test")
    assert saved.custom_institution_ref_id == own.pk
    assert saved.school == own.name
    assert saved.school_ref_id is None
    assert saved.region.code == Voivodeship.POMORSKIE


def test_cudza_placowka_ze_slownika_jest_nie_do_odroznienia_od_nieistniejacej(competition, other_competition):
    """Lista placówek organizatora jest listą jego kontrahentów – nawet „ten numer istnieje” jest
    o niej zdaniem (§ 1.3.3)."""
    enable(competition, CUSTOM_DIRECTORY_FLAG, REGISTRATION_PROFILE_FLAG)
    profile_for(competition, allow_custom_directory=True)
    cudza = CustomInstitution.objects.create(
        competition=other_competition, name="Cudzy ośrodek", institution_type=InstitutionType.SECONDARY
    )
    header = f"{BASE_HEADER};placówka (identyfikator)"

    rows = preview(competition, student(extra=str(cudza.pk)), header=header)

    assert rows[0].action == ACTION_SKIP
    assert "kolumna „placówka (identyfikator)”" in rows[0].errors[0]


# --- audyt ---------------------------------------------------------------------------------------


def test_audyt_niesie_dalej_same_liczby(competition):
    """Kształt wpisu się nie zmienia: audyt czytają osoby bez wglądu w listy klasowe."""
    enable(competition, CUSTOM_REGIONS_FLAG, CATEGORIES_FLAG)
    rows = preview(competition, student(extra="pomorskie;"), header=f"{BASE_HEADER};region;kategoria")

    import_students(rows, school_name="XIV LO")

    entry = AuditLog.objects.get(action="accounts.students_imported")
    assert entry.diff == {"created": 1, "linked": 0, "skipped": 0}
