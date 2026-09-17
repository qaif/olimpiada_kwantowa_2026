"""``apps.schools.normalise``: gmina wyliczona z miejscowości z wykazu SIO.

Testy czysto tekstowe – moduł nie dotyka bazy ani Django. Pilnują reguły, która ma dwie strony:
dzielnice wielkich miast **mają** trafić pod jedno miasto, a miasta z myślnikiem w nazwie
(„Bielsko-Biała”) i wsie o nazwach dzielnic **nie mają** się przy okazji rozpaść albo przenieść
do Warszawy.
"""

from apps.accounts.models import Voivodeship
from apps.schools.normalise import (
    DISTRICT_SEPARATOR,
    WARSAW_DISTRICTS,
    WARSAW_VOIVODESHIP,
    city_label,
    city_search_for,
    derived_fields,
    district_of,
    parent_city,
    search_text_for,
)

WARSAW_POSTAL = "00-001"


# --- reguła (a): „Miasto-Dzielnica” ------------------------------------------------------------


def test_hyphenated_district_of_a_big_city_folds_to_the_city():
    assert parent_city("Wrocław-Krzyki", Voivodeship.DOLNOSLASKIE, "50-001") == "Wrocław"
    assert parent_city("Kraków-Nowa Huta", Voivodeship.MALOPOLSKIE, "31-001") == "Kraków"
    assert parent_city("Łódź-Bałuty", Voivodeship.LODZKIE, "91-001") == "Łódź"
    assert parent_city("Poznań-Stare Miasto", Voivodeship.WIELKOPOLSKIE, "61-001") == "Poznań"


def test_a_hyphen_alone_does_not_mean_a_district():
    """Sedno reguły (a): odcinamy po myślniku wyłącznie pięć miast z ustawowym podziałem.

    Bez tego warunku wykaz rozpadłby się na miasta, których nie ma: „Bielsko”, „Kędzierzyn”,
    „Busko”, „Jastrzębie” – a uczeń z Bielska-Białej nie znalazłby swojego miasta na liście.
    """
    for city in (
        "Bielsko-Biała",
        "Kędzierzyn-Koźle",
        "Jastrzębie-Zdrój",
        "Busko-Zdrój",
        "Skarżysko-Kamienna",
        "Konstancin-Jeziorna",
    ):
        assert parent_city(city, Voivodeship.SLASKIE, "43-300") == city


def test_the_spelling_of_the_city_is_canonical_not_copied():
    """Dwie pisownie tego samego miasta mają dać jedną pozycję na liście, nie dwie."""
    assert parent_city("KRAKÓW-Podgórze", Voivodeship.MALOPOLSKIE, "30-001") == "Kraków"


# --- reguła (b): dzielnice Warszawy -------------------------------------------------------------


def test_every_warsaw_district_folds_to_the_capital():
    """Wykaz nie zna napisu „Warszawa” – stolica figuruje w nim pod osiemnastoma nazwami."""
    for district in WARSAW_DISTRICTS:
        assert parent_city(district, WARSAW_VOIVODESHIP, WARSAW_POSTAL) == "Warszawa", district


def test_wesola_keeps_its_own_postal_range():
    """Wesoła weszła do Warszawy w 2002 i została przy kodach 05-07x – wyjątek jest w danych."""
    assert parent_city("Wesoła", WARSAW_VOIVODESHIP, "05-075") == "Warszawa"


def test_a_district_name_outside_mazovia_stays_where_it_is():
    """„Wola”, „Bielany” i „Wilanów” to także nazwy wsi – bez województwa reguła byłaby zgadywaniem."""
    assert parent_city("Wola", Voivodeship.MALOPOLSKIE, "32-500") == "Wola"
    assert parent_city("Bielany", Voivodeship.WIELKOPOLSKIE, "62-100") == "Bielany"


def test_a_district_name_with_a_postal_code_from_outside_the_capital_stays_where_it_is():
    """Drugi warunek reguły (b): samo Mazowsze nie wystarcza, bo wsie o tych nazwach są i tam.

    Wykaz nie podaje powiatu ani gminy, więc kod pocztowy jest jedyną daną, która odróżnia
    dzielnicę stolicy od wsi o tej samej nazwie. Gdyby kolumna powiatu kiedyś doszła, to ona
    byłaby warunkiem właściwym.
    """
    assert parent_city("Wola", WARSAW_VOIVODESHIP, "05-660") == "Wola"
    assert parent_city("Wola", WARSAW_VOIVODESHIP, "") == "Wola"


def test_the_voivodeship_slug_matches_the_one_used_by_the_rest_of_the_system():
    """Moduł jest bez Django, więc slug jest w nim napisem – ten test pilnuje, by nie odjechał."""
    assert WARSAW_VOIVODESHIP == Voivodeship.MAZOWIECKIE


def test_an_ordinary_city_is_its_own_municipality():
    assert parent_city("Gdańsk", Voivodeship.POMORSKIE, "80-001") == "Gdańsk"
    assert parent_city("Kielce", Voivodeship.SWIETOKRZYSKIE, "25-001") == "Kielce"
    assert parent_city("", "", "") == ""


# --- dzielnica, etykieta, kolumny porównawcze ---------------------------------------------------


def test_the_district_is_what_is_left_of_the_address():
    assert district_of("Wrocław-Krzyki", "Wrocław") == "Krzyki"
    assert district_of("Śródmieście", "Warszawa") == "Śródmieście"
    assert district_of("Gdańsk", "Gdańsk") == ""


def test_the_label_shows_the_city_first_and_the_district_in_brackets():
    """„Wrocław (Krzyki)”: miasto odpowiada na „gdzie”, dzielnica odróżnia szkoły w jednym mieście."""
    assert city_label("Wrocław-Krzyki", "Wrocław") == "Wrocław (Krzyki)"
    assert city_label("Śródmieście", "Warszawa") == "Warszawa (Śródmieście)"
    assert city_label("Gdańsk", "Gdańsk") == "Gdańsk"


def test_the_comparison_column_starts_with_the_municipality():
    assert city_search_for("Wrocław-Krzyki", "Wrocław") == "wroclaw|krzyki"
    assert city_search_for("Śródmieście", "Warszawa") == "warszawa|srodmiescie"
    assert city_search_for("Łódź", "Łódź") == "lodz"


def test_the_separator_never_shows_up_in_a_plain_city_name():
    """Powód wyboru ``|``: w wykazie są 43 pary gmin, w których jedna nazwa zaczyna nazwę drugiej.

    Przy separatorze będącym spacją zapytanie o „Opole” brałoby też „Opole Lubelskie”, a o „Nowe”
    – siedem różnych miejscowości. Znak spoza nazw miejscowości odgradza gminę od dzielnicy tak,
    że dopasowanie prefiksowe zostaje jednoznaczne.
    """
    assert DISTRICT_SEPARATOR not in city_search_for("Opole Lubelskie", "Opole Lubelskie")
    assert DISTRICT_SEPARATOR not in city_search_for("Bielsko-Biała", "Bielsko-Biała")
    assert not city_search_for("Opole Lubelskie", "Opole Lubelskie").startswith(
        f"{city_search_for('Opole', 'Opole')}{DISTRICT_SEPARATOR}"
    )


def test_the_search_column_holds_the_city_and_the_municipality_side_by_side():
    """Bez gminy w zlepku „warszawa” nie trafiało w ani jedną stołeczną szkołę."""
    text = search_text_for("XIV LICEUM OGÓLNOKSZTAŁCĄCE", "Śródmieście", "Warszawa")

    assert text == "xiv liceum ogolnoksztalcace srodmiescie warszawa"
    assert "warszawa" in text.split()
    assert "srodmiescie" in text.split()


def test_the_search_column_does_not_repeat_a_city_that_is_its_own_municipality():
    assert search_text_for("I LICEUM", "Kielce", "Kielce") == "i liceum kielce"


def test_derived_fields_computes_all_three_columns_from_one_rule():
    fields = derived_fields("XIV LICEUM", "Wrocław-Krzyki", Voivodeship.DOLNOSLASKIE, "50-001")

    assert fields == {
        "city_parent": "Wrocław",
        "city_search": "wroclaw|krzyki",
        "search_text": "xiv liceum wroclaw-krzyki wroclaw",
    }


def test_derived_fields_trims_values_to_the_width_of_the_columns():
    """Wiersz z wykazu bywa dłuższy niż kolumna – ucinamy tu, a nie dopiero na błędzie bazy."""
    fields = derived_fields("N" * 500, "M" * 200, Voivodeship.MAZOWIECKIE, "00-001")

    assert len(fields["search_text"]) == 400
    assert len(fields["city_parent"]) == 120
    assert len(fields["city_search"]) == 120
