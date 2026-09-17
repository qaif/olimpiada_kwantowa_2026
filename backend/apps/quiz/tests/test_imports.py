"""Parser pytań z pliku (Markdown i CSV). Czysty tekst na wejściu, struktury na wyjściu – bez bazy.

Przedmiotem tych testów jest **plik pisany ręcznie przez człowieka**, więc większość z nich dotyczy
nie tego, co się uda, tylko tego, co ma powiedzieć parser, kiedy się nie uda. Komunikat bez numeru
wiersza jest w pliku z czterdziestoma pytaniami bezużyteczny i to jest jedyny powód, dla którego
``ParsedQuestion`` w ogóle niesie ``line``.
"""

from decimal import Decimal

import pytest

from apps.quiz import imports
from apps.quiz.grading import MULTIPLE_CHOICE, NUMERIC, SHORT_TEXT, SINGLE_CHOICE

MARKDOWN = """## [pula: kinematyka] [pkt: 2] [ujemne: 0.5]
Ciało spada swobodnie z wysokości 20 m. Ile trwa spadek?
- [ ] 1 s
- [x] 2 s
- [ ] 4 s

## [typ: liczba] [pkt: 3]
Podaj przyspieszenie ziemskie w m/s².
= 9.81 [tol: 0.02] [jednostka: m/s²]

## [typ: tekst]
Jak nazywa się zjawisko opisane wyżej?
= splątanie | splątanie kwantowe | entanglement
"""


def test_markdown_wczytuje_trzy_rodzaje_pytan_z_jednego_pliku():
    pytania = imports.parse(MARKDOWN, fmt="markdown")

    assert [item.kind for item in pytania] == [SINGLE_CHOICE, NUMERIC, SHORT_TEXT]
    wybor, liczba, tekst = pytania

    assert wybor.pool == "kinematyka"
    assert wybor.points == Decimal("2")
    assert wybor.negative_points == Decimal("0.5")
    assert [option["text"] for option in wybor.options] == ["1 s", "2 s", "4 s"]
    assert [option["is_correct"] for option in wybor.options] == [False, True, False]

    assert liczba.settings == {
        "answer": "9.81",
        "tolerance_abs": "0.02",
        "tolerance_rel": "0",
        "unit": "m/s²",
    }
    assert tekst.settings["accepted"] == ["splątanie", "splątanie kwantowe", "entanglement"]


def test_markdown_rozpoznaje_wielokrotny_wybor_po_liczbie_zaznaczen():
    """Rodzaj wynika z kształtu odpowiedzi – w pliku pisanym ręcznie każde zbędne pole to pułapka."""
    pytania = imports.parse("## Które są prawdziwe?\n- [x] A\n- [x] B\n- [ ] C\n")
    assert pytania[0].kind == MULTIPLE_CHOICE
    assert pytania[0].settings == {"partial_credit": "ALL_OR_NOTHING"}


def test_markdown_przyjmuje_tresc_w_wierszu_naglowka():
    pytania = imports.parse("## Ile to 2+2?\n= 4\n")
    assert pytania[0].text == "Ile to 2+2?"
    assert pytania[0].kind == NUMERIC


def test_markdown_domyslne_punkty_to_jeden_bez_kary():
    pytania = imports.parse("## Pytanie\n- [x] tak\n- [ ] nie\n")
    assert pytania[0].points == Decimal("1")
    assert pytania[0].negative_points == Decimal("0")


def test_markdown_kilka_odpowiedzi_tekstowych_nie_jest_liczba_mimo_cyfr():
    """„12 | 12,0” to lista uznawanych zapisów, a nie jedna wartość z marginesem."""
    pytania = imports.parse("## Pytanie\n= 12 | dwanaście\n")
    assert pytania[0].kind == SHORT_TEXT


def test_markdown_odmawia_tresci_przed_pierwszym_naglowkiem():
    with pytest.raises(imports.ImportError_, match="Wiersz 1"):
        imports.parse("Tekst luzem\n## Pytanie\n= 1\n")


def test_markdown_odmawia_pytania_wyboru_bez_poprawnego_wariantu():
    with pytest.raises(imports.ImportError_, match="zaznaczonego poprawnego"):
        imports.parse("## Pytanie\n- [ ] A\n- [ ] B\n")


def test_markdown_odmawia_pytania_bez_odpowiedzi():
    with pytest.raises(imports.ImportError_, match="bez odpowiedzi"):
        imports.parse("## Pytanie bez klucza\n")


def test_markdown_odmawia_pytania_bez_tresci():
    with pytest.raises(imports.ImportError_, match="bez treści"):
        imports.parse("## [pkt: 2]\n- [x] A\n- [ ] B\n")


def test_markdown_odmawia_pliku_bez_ani_jednego_pytania():
    with pytest.raises(imports.ImportError_, match="ani jednego pytania"):
        imports.parse("\n\n")


def test_markdown_wskazuje_wiersz_z_bledna_liczba_punktow():
    with pytest.raises(imports.ImportError_, match="Wiersz 1"):
        imports.parse("## [pkt: dużo]\nPytanie\n- [x] A\n- [ ] B\n")


CSV = """rodzaj;pula;punkty;ujemne;tresc;odpowiedzi
wybor;kinematyka;2;0.5;Ile trwa spadek?;1 s|*2 s|4 s
liczba;;3;0;Przyspieszenie ziemskie?;9.81|tol:0.02|jednostka:m/s²
tekst;;1;0;Nazwa zjawiska?;splątanie|entanglement
"""


def test_csv_wczytuje_trzy_rodzaje_pytan():
    pytania = imports.parse(CSV, fmt="csv")

    assert [item.kind for item in pytania] == [SINGLE_CHOICE, NUMERIC, SHORT_TEXT]
    assert pytania[0].pool == "kinematyka"
    assert [option["is_correct"] for option in pytania[0].options] == [False, True, False]
    assert pytania[1].settings["tolerance_abs"] == "0.02"
    assert pytania[1].settings["unit"] == "m/s²"
    assert pytania[2].settings["accepted"] == ["splątanie", "entanglement"]


def test_csv_przyjmuje_przecinek_jako_separator_kolumn():
    tekst = "tresc,odpowiedzi\nIle to 2+2?,4\n"
    pytania = imports.parse(tekst, fmt="csv")
    assert pytania[0].kind == NUMERIC
    assert pytania[0].settings["answer"] == "4"


def test_csv_pomija_wiersze_bez_tresci():
    """Arkusz zapisany z pustymi wierszami na końcu to najczęstsza postać pliku od organizatora."""
    tekst = "tresc;odpowiedzi\nPytanie;*A|B\n;\n;\n"
    assert len(imports.parse(tekst, fmt="csv")) == 1


def test_csv_odmawia_braku_wymaganej_kolumny():
    with pytest.raises(imports.ImportError_, match="tresc"):
        imports.parse("rodzaj;punkty\nwybor;2\n", fmt="csv")


def test_csv_odmawia_nieznanego_rodzaju_ze_wskazaniem_wiersza():
    with pytest.raises(imports.ImportError_, match="Wiersz 2"):
        imports.parse("rodzaj;tresc;odpowiedzi\nesej;Pytanie;cokolwiek\n", fmt="csv")


def test_csv_odmawia_wiersza_bez_odpowiedzi():
    with pytest.raises(imports.ImportError_, match="brak odpowiedzi"):
        imports.parse("tresc;odpowiedzi\nPytanie;\n", fmt="csv")
