"""Moduł marki: który napis wychodzi na zewnątrz – dzisiejszy literał czy wzorzec z konkursem.

Testy są **jednostkowe i bez bazy**: ``apps.tenancy.branding`` nie zadaje ani jednego zapytania,
a konkurs w pamięci (``Competition(...)`` bez ``save``) opisuje świat testu dokładniej niż wiersz
z migracji. Testy niezmienności dla Konkursu #1 (tematy listów znak w znak) stoją osobno,
w ``test_invariants.py`` – tam, gdzie stoją od etapu 1.

Nazwa pliku z przyrostkiem ``_unit``, bo ``test_branding.py`` jest w tym samym katalogu plikiem
zadania testowego etapu 2 (T16, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.3).
"""

import pytest

from apps.tenancy import branding
from apps.tenancy.models import FEATURE_DEFAULTS, Competition

#: Konkurs #1 – tyle jego marki, ile widzi ten moduł. Bez bazy, bo bazy tu nie potrzeba.
KWANTOWA = dict(
    name="Olimpiada Kwantowa",
    short_name="",
    genitive_name="Olimpiady Kwantowej",
    locative_name="Olimpiadzie Kwantowej",
)

#: Konkurs drugi: ma nazwę skróconą i własną odmianę, więc widać, którego pola użył wzorzec.
DRUGI = dict(
    name="Olimpiada Matematyczna Juniorów",
    short_name="Olimpiada Juniorów",
    genitive_name="Olimpiady Juniorów",
    locative_name="Olimpiadzie Juniorów",
)


def competition_with(values: dict, *, branded: bool) -> Competition:
    """Konkurs w pamięci z flagą marki ustawioną wprost."""
    flags = {branding.BRANDING_FLAG: True} if branded else {}
    return Competition(feature_flags=flags, **values)


# --- odwrót: bez konkursu i bez flagi wychodzi dzisiejszy napis ---------------------------------


def test_subject_without_a_competition_is_todays_subject():
    """Zadanie Celery i komenda bez konkursu mają wysłać list z dzisiejszym tematem."""
    assert (
        branding.subject("Aktywuj konto – %(competition)s", "Aktywuj konto – Olimpiada Kwantowa")
        == "Aktywuj konto – Olimpiada Kwantowa"
    )


def test_subject_with_the_flag_off_is_todays_subject_verbatim():
    """Odwrotem jest **literał**, a nie wzorzec podstawiony nazwą Konkursu #1.

    Różnica jest cała w tym, co się stanie, gdy ktoś poprawi ``Competition.name`` w panelu:
    literał zostaje, podstawienie zmieniłoby temat listu bez wdrożenia i bez śladu w audycie.
    """
    competition = competition_with({**KWANTOWA, "name": "Nazwa poprawiona w panelu"}, branded=False)

    assert (
        branding.subject("Aktywuj konto – %(competition)s", "Aktywuj konto – Olimpiada Kwantowa", competition)
        == "Aktywuj konto – Olimpiada Kwantowa"
    )


def test_signature_and_calendar_name_without_a_competition_are_todays_literals():
    assert branding.signature() == "Olimpiada Kwantowa"
    assert branding.calendar_name() == "Olimpiada Kwantowa"


def test_signature_keeps_the_translated_literal_given_by_the_caller():
    """Wołający podaje własny odwrót (w czterech z pięciu miejsc przetłumaczalny) – i on obowiązuje."""
    assert branding.signature(fallback="Olimpiada Kwantowa (tłumaczona)") == "Olimpiada Kwantowa (tłumaczona)"


# --- flaga włączona: napis niesie markę konkursu ------------------------------------------------


def test_subject_with_the_flag_on_uses_the_short_name():
    competition = competition_with(DRUGI, branded=True)

    assert (
        branding.subject("Aktywuj konto – %(competition)s", "Aktywuj konto – Olimpiada Kwantowa", competition)
        == "Aktywuj konto – Olimpiada Juniorów"
    )


def test_subject_substitutes_the_declined_forms():
    """Dopełniacz i miejscownik są **danymi**: „komitetu Olimpiady Juniorów”, a nie regułą w kodzie."""
    competition = competition_with(DRUGI, branded=True)

    assert (
        branding.subject("Zaproszenie do komitetu %(competition_genitive)s", "x", competition)
        == "Zaproszenie do komitetu Olimpiady Juniorów"
    )
    assert (
        branding.subject("Udział w %(competition_locative)s", "x", competition)
        == "Udział w Olimpiadzie Juniorów"
    )


def test_declined_forms_fall_back_to_the_nominative():
    """Puste pole odmiany znaczy „nie odmieniamy”, a nie „pusty napis w temacie listu”."""
    competition = competition_with(
        {"name": "Konkurs Wiedzy o Wszystkim", "short_name": "", "genitive_name": "", "locative_name": ""},
        branded=True,
    )

    assert branding.subject("Komitet %(competition_genitive)s", "x", competition) == (
        "Komitet Konkurs Wiedzy o Wszystkim"
    )


def test_subject_passes_the_callers_own_substitutions():
    competition = competition_with(DRUGI, branded=True)

    assert (
        branding.subject("Wyniki etapu %(stage)s – %(competition)s", "x", competition, stage="szkolnego")
        == "Wyniki etapu szkolnego – Olimpiada Juniorów"
    )


def test_signature_and_calendar_name_with_the_flag_on_are_the_competition_name():
    competition = competition_with(DRUGI, branded=True)

    assert branding.signature(competition) == "Olimpiada Juniorów"
    assert branding.calendar_name(competition) == "Olimpiada Juniorów"


def test_the_full_name_is_used_when_there_is_no_short_one():
    competition = competition_with(KWANTOWA, branded=True)

    assert branding.signature(competition) == "Olimpiada Kwantowa"


# --- flaga jest czytana w jednym miejscu ---------------------------------------------------------


def test_branding_is_read_through_one_flag():
    competition = competition_with(DRUGI, branded=False)

    assert branding.uses_competition_branding(None) is False
    assert branding.uses_competition_branding(competition) is False
    assert branding.uses_competition_branding(competition_with(DRUGI, branded=True)) is True


def test_the_branding_flag_is_in_the_catalogue_and_defaults_to_todays_behaviour():
    """Nazwa flagi jest jedna i jest w katalogu – inaczej ``has_feature`` podniosłoby ``KeyError``."""
    assert FEATURE_DEFAULTS[branding.BRANDING_FLAG] is False


# --- katalog flag etapu 2 -------------------------------------------------------------------------

#: Komplet flag etapu 2 z ``docs/UNIWERSALNY-ETAP-2.md`` § 0.6. Lista jest przepisana z dokumentu,
#: a nie wyliczona z katalogu: test ma porównywać kod z **umową**, a nie kod z samym sobą.
STAGE_TWO_FLAGS = (
    "per_competition_consents",
    "competition_branding_in_mail",
    "document_templates",
    "scoped_cms_permissions",
    "process_editor",
    "categories",
    "team_entries",
    "weighted_scoring",
    "reviewer_roles",
    "institution_types",
    "custom_school_directory",
    "custom_regions",
    "fees",
    "onsite_logistics",
    "content_translations",
)


@pytest.mark.parametrize("flag", STAGE_TWO_FLAGS)
def test_every_stage_two_flag_defaults_to_off(flag):
    """Konkurs #1 ma ``feature_flags`` bez ani jednego z tych wpisów, więc liczy się domyślna."""
    assert FEATURE_DEFAULTS[flag] is False
    assert Competition(feature_flags={}).has_feature(flag) is False


def test_a_flag_outside_the_catalogue_is_an_error_of_the_caller():
    """Literówka w nazwie flagi ma być słyszalna, a nie wyglądać jak funkcja wyłączona przez organizatora."""
    with pytest.raises(KeyError):
        Competition(feature_flags={}).has_feature("competition_branding_in_mails")
