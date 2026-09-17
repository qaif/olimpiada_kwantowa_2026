"""Sprawdzanie odpowiedzi – testy czystych funkcji, bez bazy i bez Django.

Ten plik jest najgęściej obsadzony testami w całej aplikacji i to nie jest przypadek: ``grading``
odpowiada na jedyne pytanie, którego zła odpowiedź jest **nieodwracalna po ogłoszeniu wyników**.
Reszta funkcji da się poprawić i przeliczyć; źle policzony punkt, którego nikt nie zauważył, wchodzi
do protokołu.

Testy nie potrzebują bazy (brak ``django_db``), więc biegną w kilkadziesiąt milisekund – można je
uruchamiać przy każdej zmianie klucza w ``validate_question_settings``.
"""

from decimal import Decimal

import pytest

from apps.quiz import grading
from apps.quiz.models import NegativeFloor, QuestionKind


def test_stale_rodzajow_pytan_zgadzaja_sie_z_modelem():
    """``grading`` powtarza kody rodzajów jako napisy – literówka znaczyłaby „nieznany rodzaj”.

    Moduł celowo nie importuje Django (żeby dało się go wołać i testować bez bazy), więc kody
    rodzajów są w nim powtórzone. Ten test jest ceną tego wyboru: pilnuje, żeby obie listy mówiły
    to samo. Bez niego zmiana nazwy rodzaju w modelu objawiłaby się dopiero wyjątkiem w środku
    przeliczania wyników.
    """
    assert grading.SINGLE_CHOICE == QuestionKind.SINGLE_CHOICE.value
    assert grading.MULTIPLE_CHOICE == QuestionKind.MULTIPLE_CHOICE.value
    assert grading.SHORT_TEXT == QuestionKind.SHORT_TEXT.value
    assert grading.NUMERIC == QuestionKind.NUMERIC.value
    assert grading.FLOOR_QUESTION == NegativeFloor.QUESTION.value
    assert grading.FLOOR_QUIZ == NegativeFloor.QUIZ.value


# --- normalizacja tekstu --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Splątanie", "splatanie"),
        ("SPLĄTANIE", "splatanie"),
        ("  splątanie  ", "splatanie"),
        ("splątanie   kwantowe", "splatanie kwantowe"),
        ("splatanie", "splatanie"),
        ("Łódź", "lodz"),
    ],
)
def test_normalizacja_domyslna_sklada_wielkosc_liter_spacje_i_diakrytyki(given, expected):
    assert grading.normalise_text(given) == expected


def test_normalizacja_bez_diakrytykow_zachowuje_ogonki_ale_sklada_wielkosc_liter():
    assert grading.normalise_text("Splątanie", fold_diacritics=False) == "splątanie"


def test_normalizacja_bez_wielkosci_liter_zachowuje_ja_ale_sklada_ogonki():
    """Sam ``fold_diacritics`` bez ``fold_case`` idzie inną gałęzią niż ``core.text.fold``."""
    assert grading.normalise_text("Splątanie", fold_case=False) == "Splatanie"
    assert grading.normalise_text("ŁÓDŹ", fold_case=False) == "LODZ"


def test_normalizacja_bez_zadnej_flagi_tylko_przycina_biale_znaki():
    bez_skladania = dict(fold_case=False, fold_diacritics=False, fold_whitespace=False)
    assert grading.normalise_text("  Splątanie  ", **bez_skladania) == "Splątanie"


def test_normalizacja_ujednolica_zapis_unicode_przed_porownaniem():
    """„ą” złożone z dwóch znaków to ten sam napis, co „ą” jednoznakowe – po ``NFKC``.

    Przeglądarki i systemy (zwłaszcza macOS) przysyłają obie postacie. Bez tego kroku odpowiedź
    poprawna co do litery bywałaby uznana za błędną zależnie od tego, na czym uczestnik pisał.
    """
    złożone = "ą"  # „a” + ogonek łączący
    assert grading.normalise_text("ą", fold_diacritics=False, fold_case=False) == grading.normalise_text(
        złożone, fold_diacritics=False, fold_case=False
    )


# --- liczby ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("3.14", Decimal("3.14")),
        ("3,14", Decimal("3.14")),
        ("  3,14  ", Decimal("3.14")),
        ("1 000,5", Decimal("1000.5")),
        ("1 000,5", Decimal("1000.5")),
        ("1,5e-3", Decimal("0.0015")),
        ("-2", Decimal("-2")),
        (0, Decimal("0")),
    ],
)
def test_parse_number_przyjmuje_postacie_w_jakich_uczestnik_pisze_liczby(given, expected):
    assert grading.parse_number(given) == expected


@pytest.mark.parametrize("given", ["", "   ", "trzy", "3,,14", None, "nan", "inf", "-Infinity"])
def test_parse_number_odrzuca_to_co_liczba_nie_jest(given):
    """``nan`` i ``inf`` konstruują się jako ``Decimal`` bez błędu – i psułyby porównanie z tolerancją."""
    assert grading.parse_number(given) is None


# --- jednokrotny wybór ------------------------------------------------------------------------


def test_jednokrotny_wybor_trafiony_wariant_daje_pelna_odpowiedz():
    score = grading.grade_single_choice({"options": [7]}, {7})
    assert score.is_correct is True
    assert score.ratio == Decimal("1")


def test_jednokrotny_wybor_bez_zaznaczenia_to_brak_odpowiedzi_a_nie_blad():
    """Rozróżnienie decyduje o punktach ujemnych: brak odpowiedzi nigdy nie jest karany."""
    assert grading.grade_single_choice({"options": []}, {7}).is_correct is None
    assert grading.grade_single_choice({}, {7}).is_correct is None


def test_jednokrotny_wybor_dwa_zaznaczenia_to_odpowiedz_bledna():
    """Przyciski radiowe tego nie pozwolą – ale spreparowane żądanie tak, a to musi kosztować."""
    score = grading.grade_single_choice({"options": [7, 8]}, {7})
    assert score.is_correct is False


def test_jednokrotny_wybor_ignoruje_smieci_w_payloadzie():
    """Dane przychodzą od uczestnika; nieliczbowe wartości mają zniknąć, a nie wywrócić ocenę."""
    score = grading.grade_single_choice({"options": ["7", None, "abc"]}, {7})
    assert score.is_correct is True


# --- wielokrotny wybór ------------------------------------------------------------------------


def test_wielokrotny_wybor_wszystko_albo_nic_wymaga_dokladnego_zestawu():
    correct, wszystkie = {1, 2}, {1, 2, 3, 4}
    pelna = grading.grade_multiple_choice({"options": [1, 2]}, correct, wszystkie, grading.ALL_OR_NOTHING)
    czesciowa = grading.grade_multiple_choice({"options": [1]}, correct, wszystkie, grading.ALL_OR_NOTHING)
    assert pelna.is_correct is True
    assert czesciowa.is_correct is False
    assert czesciowa.ratio == Decimal("0")


def test_wielokrotny_wybor_proporcjonalny_liczy_trafienia_minus_pomylki():
    correct, wszystkie = {1, 2, 3, 4}, {1, 2, 3, 4, 5, 6}
    # trzy trafienia, zero pomyłek → 3/4
    score = grading.grade_multiple_choice({"options": [1, 2, 3]}, correct, wszystkie, grading.PROPORTIONAL)
    assert score.ratio == Decimal("3") / Decimal("4")
    # dwa trafienia, jedna pomyłka → 1/4
    score = grading.grade_multiple_choice({"options": [1, 2, 5]}, correct, wszystkie, grading.PROPORTIONAL)
    assert score.ratio == Decimal("1") / Decimal("4")


def test_wielokrotny_wybor_proporcjonalny_zaznaczenie_wszystkiego_nie_daje_punktow():
    """Najczęstszy błąd ocen częściowych: bez odejmowania pomyłek „zaznacz wszystko” wygrywa test."""
    correct, wszystkie = {1, 2}, {1, 2, 3, 4}
    score = grading.grade_multiple_choice({"options": [1, 2, 3, 4]}, correct, wszystkie, grading.PROPORTIONAL)
    assert score.ratio == Decimal("0")
    # …i pozostaje odpowiedzią **błędną**, więc punkty ujemne za nią odejdą.
    assert score.is_correct is False


def test_wielokrotny_wybor_odrzuca_warianty_spoza_pytania():
    """Wariant z innego pytania nie może stać się „pomyłką” ani wpłynąć na wynik."""
    score = grading.grade_multiple_choice({"options": [1, 2, 999]}, {1, 2}, {1, 2, 3}, grading.PROPORTIONAL)
    assert score.is_correct is True


def test_wielokrotny_wybor_bez_zaznaczenia_to_brak_odpowiedzi():
    assert (
        grading.grade_multiple_choice({"options": []}, {1}, {1, 2}, grading.PROPORTIONAL).is_correct is None
    )


# --- krótka odpowiedź tekstowa ------------------------------------------------------------------


def test_tekst_uznaje_ktorykolwiek_z_wariantow_po_normalizacji():
    settings = {"accepted": ["splątanie", "entanglement"], **grading.DEFAULT_TEXT_FLAGS}
    assert grading.grade_short_text({"text": "SPLATANIE"}, settings).is_correct is True
    assert grading.grade_short_text({"text": " Entanglement "}, settings).is_correct is True
    assert grading.grade_short_text({"text": "dekoherencja"}, settings).is_correct is False


def test_tekst_z_wylaczona_normalizacja_wielkosci_liter_rozroznia_symbole():
    """Przykład z życia: „Co” to kobalt, „CO” to tlenek węgla – tu wielkość liter rozstrzyga."""
    settings = {
        "accepted": ["Co"],
        "fold_case": False,
        "fold_whitespace": True,
        "fold_diacritics": True,
    }
    assert grading.grade_short_text({"text": "Co"}, settings).is_correct is True
    assert grading.grade_short_text({"text": "CO"}, settings).is_correct is False


def test_tekst_pusty_to_brak_odpowiedzi():
    settings = {"accepted": ["splątanie"], **grading.DEFAULT_TEXT_FLAGS}
    assert grading.grade_short_text({"text": "   "}, settings).is_correct is None
    assert grading.grade_short_text({}, settings).is_correct is None


# --- odpowiedź liczbowa --------------------------------------------------------------------------


def test_liczba_bez_tolerancji_wymaga_rownosci():
    settings = {"answer": "9.81", "tolerance_abs": "0", "tolerance_rel": "0"}
    assert grading.grade_numeric({"value": "9.81"}, settings).is_correct is True
    assert grading.grade_numeric({"value": "9,81"}, settings).is_correct is True
    assert grading.grade_numeric({"value": "9.8"}, settings).is_correct is False


def test_liczba_tolerancja_bezwzgledna_dziala_w_obie_strony_i_domyka_sie_na_granicy():
    settings = {"answer": "9.81", "tolerance_abs": "0.02", "tolerance_rel": "0"}
    assert grading.grade_numeric({"value": "9.83"}, settings).is_correct is True
    assert grading.grade_numeric({"value": "9.79"}, settings).is_correct is True
    assert grading.grade_numeric({"value": "9.84"}, settings).is_correct is False


def test_liczba_tolerancja_wzgledna_skaluje_sie_z_wartoscia():
    settings = {"answer": "1000", "tolerance_abs": "0", "tolerance_rel": "0.01"}
    assert grading.grade_numeric({"value": "1009"}, settings).is_correct is True
    assert grading.grade_numeric({"value": "1011"}, settings).is_correct is False


def test_liczba_obie_tolerancje_dzialaja_alternatywnie():
    """Wystarczy zmieścić się w jednej – to jest cała reguła, bo opisują dwie różne rzeczy."""
    settings = {"answer": "100", "tolerance_abs": "5", "tolerance_rel": "0.01"}
    assert grading.grade_numeric({"value": "104"}, settings).is_correct is True  # tylko bezwzględna
    settings = {"answer": "100", "tolerance_abs": "0.5", "tolerance_rel": "0.1"}
    assert grading.grade_numeric({"value": "108"}, settings).is_correct is True  # tylko względna


def test_liczba_zero_z_tolerancja_wzgledna_ma_margines_z_bezwzglednej():
    """Przy odpowiedzi 0 tolerancja względna daje zero – bez alternatywy pytanie byłoby bez marginesu."""
    settings = {"answer": "0", "tolerance_abs": "0.001", "tolerance_rel": "0.1"}
    assert grading.grade_numeric({"value": "0.0005"}, settings).is_correct is True


def test_liczba_nieczytelna_odpowiedz_to_brak_odpowiedzi_a_nie_blad():
    settings = {"answer": "9.81", "tolerance_abs": "0", "tolerance_rel": "0"}
    assert grading.grade_numeric({"value": "mniej więcej dziesięć"}, settings).is_correct is None
    assert grading.grade_numeric({"value": ""}, settings).is_correct is None


# --- punkty, kary i podłogi -----------------------------------------------------------------------


def _pelna():
    return grading.QuestionScore(grading.ZERO, True, Decimal("1"))


def _bledna():
    return grading.QuestionScore(grading.ZERO, False, grading.ZERO)


def _brak():
    return grading.QuestionScore(grading.ZERO, None)


def test_punkty_za_pelna_odpowiedz_to_waga_pytania():
    score = grading.award_points(
        _pelna(), points=Decimal("3"), negative_points=Decimal("1"), floor=grading.FLOOR_QUESTION
    )
    assert score.points == Decimal("3.00")


def test_punkty_za_odpowiedz_czesciowa_sa_ulamkiem_wagi_i_nie_niosa_kary():
    """Częściowa wiedza nie może kosztować: kara zostaje dla odpowiedzi, w której nie trafiono nic."""
    czesciowa = grading.QuestionScore(grading.ZERO, False, Decimal("1") / Decimal("3"))
    score = grading.award_points(
        czesciowa, points=Decimal("3"), negative_points=Decimal("3"), floor=grading.FLOOR_QUIZ
    )
    assert score.points == Decimal("1.00")


def test_punkty_czesciowe_sa_zaokraglone_do_dwoch_miejsc_powtarzalnie():
    """Idempotencja ``grade_attempt`` wymaga, żeby ta sama odpowiedź dawała zawsze tę samą liczbę."""
    czesciowa = grading.QuestionScore(grading.ZERO, False, Decimal("2") / Decimal("3"))
    argumenty = dict(points=Decimal("1"), negative_points=grading.ZERO, floor=grading.FLOOR_QUESTION)
    pierwsze = grading.award_points(czesciowa, **argumenty)
    drugie = grading.award_points(czesciowa, **argumenty)
    assert pierwsze.points == drugie.points == Decimal("0.67")


def test_brak_odpowiedzi_nigdy_nie_jest_karany():
    for floor in (grading.FLOOR_QUESTION, grading.FLOOR_QUIZ):
        score = grading.award_points(_brak(), points=Decimal("3"), negative_points=Decimal("3"), floor=floor)
        assert score.points == grading.ZERO
        assert score.is_correct is None


def test_podloga_na_pytaniu_przycina_kare_do_zera():
    score = grading.award_points(
        _bledna(), points=Decimal("2"), negative_points=Decimal("1"), floor=grading.FLOOR_QUESTION
    )
    assert score.points == grading.ZERO


def test_podloga_na_tescie_pozwala_pytaniu_zejsc_ponizej_zera():
    score = grading.award_points(
        _bledna(), points=Decimal("2"), negative_points=Decimal("1"), floor=grading.FLOOR_QUIZ
    )
    assert score.points == Decimal("-1.00")


def test_suma_z_podloga_na_tescie_nie_schodzi_ponizej_zera():
    assert grading.total_score([Decimal("-1"), Decimal("-2")], floor=grading.FLOOR_QUIZ) == Decimal("0.00")
    assert grading.total_score([Decimal("5"), Decimal("-2")], floor=grading.FLOOR_QUIZ) == Decimal("3.00")


def test_suma_z_podloga_na_pytaniu_nie_przycina_niczego_powtornie():
    """Przy podłodze „za pytanie” składniki są już nieujemne, więc suma nie ma czego naprawiać."""
    assert grading.total_score([Decimal("0"), Decimal("2.5")], floor=grading.FLOOR_QUESTION) == Decimal(
        "2.50"
    )


# --- brama ustawień -------------------------------------------------------------------------------


def test_walidacja_ustawien_obcina_klucz_do_wybranego_rodzaju():
    """Pytanie, które zmieniło rodzaj, nie może zachować klucza po poprzednim."""
    wynik = grading.validate_question_settings(
        grading.SINGLE_CHOICE, {"answer": "42", "accepted": ["cokolwiek"]}
    )
    assert wynik == {}


def test_walidacja_ustawien_liczbowych_zapisuje_wartosci_jako_napisy():
    """JSONField serializowałby ``Decimal`` przez ``float`` – tolerancja 0,005 wracałaby zmieniona."""
    wynik = grading.validate_question_settings(
        grading.NUMERIC, {"answer": "9,81", "tolerance_abs": "0,005", "unit": "m/s²"}
    )
    assert wynik == {"answer": "9.81", "tolerance_abs": "0.005", "tolerance_rel": "0", "unit": "m/s²"}


def test_walidacja_ustawien_odrzuca_niepelny_klucz():
    with pytest.raises(ValueError, match="odpowiedź liczbową"):
        grading.validate_question_settings(grading.NUMERIC, {"answer": "nie liczba"})
    with pytest.raises(ValueError, match="uznawaną odpowiedź"):
        grading.validate_question_settings(grading.SHORT_TEXT, {"accepted": ["  "]})
    with pytest.raises(ValueError, match="Tolerancja"):
        grading.validate_question_settings(grading.NUMERIC, {"answer": "1", "tolerance_abs": "-1"})


def test_walidacja_ustawien_tekstowych_przepuszcza_flagi_i_przycina_puste_warianty():
    wynik = grading.validate_question_settings(
        grading.SHORT_TEXT, {"accepted": [" splątanie ", "", "entanglement"], "fold_case": False}
    )
    assert wynik["accepted"] == ["splątanie", "entanglement"]
    assert wynik["fold_case"] is False
    assert wynik["fold_diacritics"] is True


def test_score_question_odmawia_nieznanego_rodzaju():
    with pytest.raises(ValueError, match="Nieznany rodzaj"):
        grading.score_question(kind="COS_INNEGO", settings={}, payload={}, correct_ids=set(), all_ids=set())
