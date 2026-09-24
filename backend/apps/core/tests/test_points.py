"""Punkty jako liczba (wydanie 0.35.0): czytanie od człowieka, wyświetlanie, JSON i CSV.

``apps.core.points`` jest jedynym miejscem, które wie, jak liczbę punktów przeczytać i pokazać – te
testy pilnują jego kontraktu, bo od niego zależy, czy ta sama ocena wygląda tak samo na ekranie,
w PDF-ie, w CSV i w API.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.template import Context, Template
from django.utils import translation

from apps.core.points import (
    PointsError,
    format_points,
    input_value,
    is_whole,
    jsonable_points,
    parse_points,
    points_csv,
    points_json,
    round_points,
    to_points,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, Decimal("5.00")),
        ("5", Decimal("5.00")),
        ("4,25", Decimal("4.25")),
        ("4.25", Decimal("4.25")),
        (" 4,5 ", Decimal("4.50")),
        ("1 000", Decimal("1000.00")),
        (4.25, Decimal("4.25")),
        (Decimal("4.250"), Decimal("4.25")),
        ("-1,5", Decimal("-1.50")),
        ("0", Decimal("0.00")),
    ],
)
def test_parse_points_przyjmuje_przecinek_kropke_i_liczby(raw, expected):
    assert parse_points(raw) == expected


@pytest.mark.parametrize("raw", ["4,255", 4.255, Decimal("0.001"), "1,001"])
def test_parse_points_odmawia_trzeciego_miejsca_zamiast_zaokraglac(raw):
    with pytest.raises(PointsError) as exc:
        parse_points(raw)
    assert exc.value.code == "SCORE_INVALID"
    assert "dwa miejsca" in str(exc.value)


@pytest.mark.parametrize("raw", [None, True, False, "", "abc", "4,2,5", "1e2", "4.", ",5", [], "NaN"])
def test_parse_points_odmawia_temu_co_nie_jest_liczba(raw):
    with pytest.raises(PointsError):
        parse_points(raw)


@pytest.mark.parametrize("raw", [1e30, "1" * 40, Decimal("1E+30"), "100000000"])
def test_parse_points_odmawia_liczb_poza_pojemnoscia_kolumny_zamiast_500(raw):
    with pytest.raises(PointsError) as exc:
        parse_points(raw)
    assert "zbyt duża" in str(exc.value)


def test_parse_points_odmawia_nieskonczonosci():
    with pytest.raises(PointsError):
        parse_points(Decimal("Infinity"))
    with pytest.raises(PointsError):
        parse_points(float("nan"))


@pytest.mark.parametrize(
    ("value", "pl", "en"),
    [
        (5, "5", "5"),
        (Decimal("5.00"), "5", "5"),
        (Decimal("4.25"), "4,25", "4.25"),
        (Decimal("3.50"), "3,5", "3.5"),
        (3.5, "3,5", "3.5"),
        (Decimal("-1.50"), "-1,5", "-1.5"),
        (Decimal("0"), "0", "0"),
        (Decimal("120.10"), "120,1", "120.1"),
    ],
)
def test_format_points_bez_zbednych_zer_z_separatorem_jezyka(value, pl, en):
    assert format_points(value, "pl") == pl
    assert format_points(value, "en") == en


def test_format_points_bierze_jezyk_aktywny_i_pusty_dla_braku():
    with translation.override("en"):
        assert format_points(Decimal("4.25")) == "4.25"
    with translation.override("pl"):
        assert format_points(Decimal("4.25")) == "4,25"
    assert format_points(None) == ""
    assert format_points("nie-liczba") == ""


def test_csv_i_pole_liczbowe_zawsze_z_kropka():
    with translation.override("pl"):
        assert points_csv(Decimal("4.25")) == "4.25"
        assert points_csv(Decimal("5.00")) == "5"
        assert input_value(Decimal("12.50")) == "12.5"
    assert points_csv(None) == ""


def test_points_json_int_dla_calkowitej_float_dla_ulamkowej():
    assert points_json(Decimal("5.00")) == 5
    assert isinstance(points_json(Decimal("5.00")), int)
    assert points_json(Decimal("4.25")) == 4.25
    assert isinstance(points_json(Decimal("4.25")), float)
    assert points_json(None) is None
    # JSON zapisuje najkrótszą postać, a odczyt przez ``to_points`` wraca do tej samej liczby.
    assert json.dumps(points_json(Decimal("4.35"))) == "4.35"
    assert to_points(json.loads(json.dumps(points_json(Decimal("4.35"))))) == Decimal("4.35")


def test_jsonable_points_zamienia_decimal_rekurencyjnie_i_nic_poza_nim():
    payload = {"score": Decimal("5.00"), "nested": [{"to": Decimal("4.25")}, "tekst", 3], "flag": True}

    assert jsonable_points(payload) == {"score": 5, "nested": [{"to": 4.25}, "tekst", 3], "flag": True}
    json.dumps(jsonable_points(payload))


def test_to_points_czyta_kazda_postac_zapisana_przez_kolejne_wydania():
    assert to_points(6) == Decimal(6)
    assert to_points(4.25) == Decimal("4.25")
    assert to_points("4,25") == Decimal("4.25")
    assert to_points(Decimal("1.10")) == Decimal("1.10")
    assert to_points(True) is None
    assert to_points(None) is None
    assert to_points("x") is None


@pytest.mark.parametrize(
    ("value", "quantum", "expected"),
    [
        (Decimal("2.5"), Decimal("1"), Decimal("3")),
        (Decimal("3.5"), Decimal("1"), Decimal("4")),
        (Decimal("2.4999"), Decimal("1"), Decimal("2")),
        (Decimal("7.125"), Decimal("0.01"), Decimal("7.13")),
        (Decimal("7.135"), Decimal("0.01"), Decimal("7.14")),
        (Decimal("-0.005"), Decimal("0.01"), Decimal("-0.01")),
    ],
)
def test_round_points_polowka_w_gore_a_nie_bankierskie(value, quantum, expected):
    assert round_points(value, quantum) == expected


def test_is_whole():
    assert is_whole(Decimal("5.00"))
    assert not is_whole(Decimal("4.25"))
    assert not is_whole(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("5.00"), "5"), (Decimal("4.25"), "4,25"), (6, "6"), (3.5, "3,5"), (None, "")],
)
def test_filtr_szablonu_points(value, expected):
    template = Template("{% load web_extras %}{{ value|points }}")
    with translation.override("pl"):
        assert template.render(Context({"value": value})) == expected


def test_filtr_points_input_zawsze_z_kropka():
    template = Template("{% load web_extras %}{{ value|points_input }}")
    with translation.override("pl"):
        assert template.render(Context({"value": Decimal("4.25")})) == "4.25"
