"""Kanoniczna miejscowość szkoły: „Wrocław-Krzyki” → „Wrocław”, „Śródmieście” → „Warszawa”.

Wykaz SIO zapisuje największe miasta **dzielnicami**, i to na dwa różne sposoby. Cztery z nich
(Kraków, Łódź, Poznań, Wrocław) mają w kolumnie „Miejscowość” zlepek „Miasto-Dzielnica”, a
Warszawa figuruje pod **samymi nazwami dzielnic** („Śródmieście”, „Wola”, „Mokotów”…), bez słowa
„Warszawa” w żadnym wierszu. Dla uczestnika znaczyło to dwie rzeczy naraz:

- wpisanie „warszawa” w kroku „Miejscowość” nie znajdowało **niczego** – w całym słowniku nie ma
  takiego napisu, mimo że stolica ma w nim ponad trzysta szkół,
- wpisanie „wro” dawało pięć pozycji („Wrocław-Fabryczna”, „Wrocław-Krzyki”, „Wrocław-Psie Pole”,
  „Wrocław-Stare Miasto”, „Wrocław-Śródmieście”), z których żadna nie jest odpowiedzią na pytanie
  „w jakim jesteś mieście” – a wybór którejkolwiek zawężał listę szkół do jednej piątej miasta,
  bez śladu, że reszta gdzieś jest.

Stąd kolumna ``School.city_parent``: nazwa **gminy**, po której grupuje się podpowiedzi i po której
zawęża się wyszukiwarka. Oryginalne ``city`` zostaje nietknięte – to jest adres szkoły, tak jak
stoi w rejestrze, i tak też ma być pokazywany (patrz ``city_label``).

Moduł jest **czysto tekstowy**: bez importu Django i bez dotykania bazy. Dzięki temu tę samą
funkcję woła ``School.save()``, ``seed_schools`` (który omija ``save()`` przez ``bulk_create``)
i backfill w migracji ``schools.0003`` – trzy drogi zapisu, jedna reguła. Gdyby każda liczyła
własną, kolumna rozjechałaby się ze sobą przy pierwszej różnicy, a objawiłoby się to dopiero
uczniowi, który nie znajduje swojej szkoły.

Reguła jest **deterministyczna i zachowawcza**: nie zgaduje. Wiersz, którego nie da się przypisać
do gminy z samych danych wykazu, zostaje przy swojej miejscowości – lista o jedną pozycję dłuższa
jest mniejszą szkodą niż szkoła przypisana do niewłaściwego miasta.
"""

from apps.core.text import fold

#: Miasta, które wykaz zapisuje jako „Miasto-Dzielnica”. Lista jest **zamknięta** i to jest sedno
#: reguły (a): sam myślnik nie znaczy dzielnicy. „Bielsko-Biała”, „Jastrzębie-Zdrój”,
#: „Kędzierzyn-Koźle” czy „Skarżysko-Kamienna” to pełne nazwy miast, a odcięcie ich po myślniku
#: dałoby szkoły w nieistniejącym „Bielsku” i „Kędzierzynie”. Dlatego przed myślnikiem musi stać
#: miasto **z tej krotki** – czyli jedno z pięciu, które mają ustawowy podział na dzielnice.
CITIES_WITH_DISTRICTS: tuple[str, ...] = ("Kraków", "Łódź", "Poznań", "Warszawa", "Wrocław")

#: Postać porównawcza powyższych → pisownia, którą wpisujemy do ``city_parent``. Dzięki mapie
#: „KRAKÓW-Podgórze” i „Kraków-Podgórze” dają ten sam napis, a nie dwie pozycje na liście miast.
_PARENT_BY_FOLDED = {fold(name): name for name in CITIES_WITH_DISTRICTS}

#: Gmina, do której należą nazwy z ``WARSAW_DISTRICTS``.
WARSAW = "Warszawa"

#: Województwo Warszawy (slug ``Voivodeship.MAZOWIECKIE``; powtórzony tu jako napis, bo moduł nie
#: importuje Django – zgodność pilnuje test ``test_normalise.py``).
WARSAW_VOIVODESHIP = "mazowieckie"

#: Osiemnaście dzielnic m.st. Warszawy – jedyne nazwy, które reguła (b) w ogóle rozważa.
WARSAW_DISTRICTS: tuple[str, ...] = (
    "Bemowo",
    "Białołęka",
    "Bielany",
    "Mokotów",
    "Ochota",
    "Praga-Południe",
    "Praga-Północ",
    "Rembertów",
    "Śródmieście",
    "Targówek",
    "Ursus",
    "Ursynów",
    "Wawer",
    "Wesoła",
    "Wilanów",
    "Włochy",
    "Wola",
    "Żoliborz",
)

_WARSAW_DISTRICTS_FOLDED = frozenset(fold(name) for name in WARSAW_DISTRICTS)

#: Kody pocztowe Warszawy zaczynają się od 00 do 04 i **żadna inna** miejscowość ich nie używa.
#: To jest drugi warunek reguły (b), bo samo województwo nie wystarcza: „Wola”, „Bielany” czy
#: „Wilanów” to także nazwy wsi, a niejedna z nich leży na Mazowszu. Wykaz nie podaje powiatu ani
#: gminy (kolumny: województwo, miejscowość, kod pocztowy, ulica), więc kod pocztowy jest jedyną
#: daną, która odróżnia dzielnicę stolicy od wsi o tej samej nazwie.
WARSAW_POSTAL_PREFIXES: tuple[str, ...] = ("00-", "01-", "02-", "03-", "04-")

#: Wyjątek: Wesoła weszła do Warszawy w 2002 roku i **zachowała** swoje kody z puli 05-07x.
#: Bez tej linijki cztery tamtejsze licea zostałyby poza stolicą.
WESOLA_POSTAL_PREFIX = "05-07"

#: Znak oddzielający gminę od dzielnicy w kolumnie ``School.city_search``. Nie spacja i nie
#: myślnik: jedno i drugie występuje w nazwach miejscowości („Nowe Miasto”, „Bielsko-Biała”),
#: więc zapytanie o gminę „Nowe” brałoby też „Nowe Miasto”. Uzasadnienie przy ``city_search_for``.
DISTRICT_SEPARATOR = "|"


def _is_warsaw_address(city_folded: str, postal_code: str) -> bool:
    """Czy adres o tej nazwie dzielnicy i tym kodzie pocztowym leży w Warszawie."""
    code = (postal_code or "").strip()
    if code.startswith(WARSAW_POSTAL_PREFIXES):
        return True
    return city_folded == fold("Wesoła") and code.startswith(WESOLA_POSTAL_PREFIX)


def parent_city(city: str, voivodeship: str = "", postal_code: str = "") -> str:
    """Gmina, w której leży szkoła. Dla zwykłej miejscowości – ona sama.

    Dwie reguły, obie wyprowadzone z danych, a nie ze słownika wyjątków:

    (a) **„Miasto-Dzielnica” → „Miasto”**, ale wyłącznie wtedy, gdy człon przed myślnikiem jest
        jednym z pięciu miast z ``CITIES_WITH_DISTRICTS``. Inaczej „Bielsko-Biała” zamieniłaby się
        w „Bielsko”, a „Busko-Zdrój” w „Busko”,
    (b) **nazwa dzielnicy Warszawy → „Warszawa”**, gdy województwo jest mazowieckie **i** kod
        pocztowy należy do puli stolicy. Oba warunki są konieczne: „Wola” i „Bielany” to również
        nazwy wsi. Gdyby wykaz zyskał kiedyś kolumnę powiatu albo gminy, to ona byłaby tu
        warunkiem właściwym – kod pocztowy jest najlepszym, co daje dzisiejszy plik.

    Wiersz, który nie spełnia żadnej z reguł, zostaje przy swojej nazwie: funkcja nigdy nie
    zwraca pustego napisu, bo ``city_parent`` jest tym, po czym grupuje się cała wyszukiwarka.
    """
    original = (city or "").strip()
    if not original:
        return ""
    head = original.split("-", 1)[0].strip()
    parent = _PARENT_BY_FOLDED.get(fold(head))
    if parent is not None:
        return parent
    folded = fold(original)
    if (
        folded in _WARSAW_DISTRICTS_FOLDED
        and (voivodeship or "") == WARSAW_VOIVODESHIP
        and _is_warsaw_address(folded, postal_code)
    ):
        return WARSAW
    return original


def district_of(city: str, city_parent: str = "") -> str:
    """Człon dzielnicowy adresu: „Krzyki” dla „Wrocław-Krzyki”, „Śródmieście” dla Warszawy.

    Pusty napis, gdy miejscowość **jest** gminą – czyli dla zdecydowanej większości wierszy.
    Wyodrębniamy go w jednym miejscu, bo służy dwóm rzeczom naraz: etykiecie pozycji na liście
    („Wrocław (Krzyki)”) i osobnemu wyrazowi w kolumnie porównawczej – bez niego wpisanie
    „krzyki” przestałoby cokolwiek znajdować, a dotąd znajdowało.
    """
    original = (city or "").strip()
    parent = (city_parent or "").strip() or original
    if fold(original) == fold(parent):
        return ""
    if fold(original).startswith(fold(parent)):
        # „Wrocław-Krzyki” → „Krzyki”. Składanie znaków nie zmienia długości napisu (polskie
        # diakrytyki są jedną literą przed i po), więc odcięcie po długości członu jest bezpieczne.
        return original[len(parent) :].lstrip("- ")
    # Warszawa: w wierszu stoi samo „Śródmieście”, więc dzielnicą jest cała zapisana nazwa.
    return original


def city_label(city: str, city_parent: str = "") -> str:
    """Miejscowość do pokazania człowiekowi: „Wrocław (Krzyki)”, „Warszawa (Śródmieście)”.

    Gmina na początku, bo to ona odpowiada na pytanie „gdzie”, a dzielnica w nawiasie, bo
    odróżnia dwie szkoły o tej samej nazwie w jednym mieście i pozwala poznać swoją po adresie.
    Samo „Wrocław” przy stu trzydziestu wierszach byłoby etykietą, która niczego nie rozstrzyga,
    a samo „Wrocław-Krzyki” – tym, przed czym uciekamy: wyglądem osobnego miasta.
    """
    district = district_of(city, city_parent)
    parent = (city_parent or "").strip() or (city or "").strip()
    return f"{parent} ({district})" if district else parent


def city_search_for(city: str, city_parent: str = "") -> str:
    """Postać porównawcza miejscowości: „wroclaw|krzyki”, „warszawa|srodmiescie”, „gdansk”.

    Kolumna odpowiada na dwa pytania kroku „Miejscowość”:

    - „jakie **gminy** zaczynają się od tych liter” – gmina stoi na początku napisu, więc
      wystarczy dopasowanie prefiksowe (``city_search__startswith``). Dzielnicę znajduje drugi
      warunek, po separatorze (``city_search__contains="|krzyki"``) – dzięki temu „krzyki”
      podpowiada Wrocław, a „law” w środku „Wrocławia” nadal nic, i o to chodziło od początku,
    - „pokaż wszystkie szkoły tej gminy” – ``= klucz`` bierze wiersze bez dzielnicy,
      a ``startswith klucz + "|"`` wszystkie jej dzielnice.

    Separatorem jest ``|``, a nie spacja, i to jest różnica między działającym a prawie działającym
    zawężeniem: w wykazie jest 1216 gmin, a wśród nich 43 pary, w których nazwa jednej jest
    początkiem nazwy drugiej („Opole” i „Opole Lubelskie”, „Brzeg” i „Brzeg Dolny”, „Nowe”
    i „Nowe Miasto”). Przy spacji wybranie „Opola” dokładałoby do listy szkoły z Opola Lubelskiego;
    znak, który w nazwie miejscowości nie występuje, odgradza gminę od dzielnicy jednoznacznie.

    Kolumna jest **wyliczana**, a nie odczytywana rozszerzeniem ``unaccent`` w locie: rozszerzenie
    wymaga uprawnień na bazie, których aplikacja na produkcji nie ma, a złożony raz napis jest dla
    planisty czytelny (``LIKE 'wroc%'`` używa indeksu, ``unaccent(city) LIKE …`` już nie).
    """
    parent = fold((city_parent or "").strip() or (city or "").strip())
    district = fold(district_of(city, city_parent))
    return f"{parent}{DISTRICT_SEPARATOR}{district}" if district else parent


def search_text_for(name: str, city: str, city_parent: str = "") -> str:
    """Postać porównawcza wiersza: „nazwa miejscowość gmina” bez diakrytyków, małymi literami.

    Wyszukiwanie idzie po tej kolumnie, a nie po ``name``/``city`` z ``icontains``, z dwóch
    powodów. Po pierwsze **diakrytyki**: uczeń wpisujący „lodz” ma znaleźć „ŁÓDŹ”, a Postgres bez
    rozszerzenia ``unaccent`` tego nie zrobi. Po drugie **jedno pole na token**: zapytanie
    „mickiewicza krakow” ma trafić w wiersz, w którym jeden wyraz jest w nazwie, a drugi
    w mieście – przy dwóch osobnych kolumnach wymagałoby to iloczynu wariantów.

    Gmina dochodzi do zlepka **obok** oryginalnej miejscowości i to jest cała odpowiedź na
    „warszawa śródmieście” oraz „wrocław krzyki”: pierwszy wyraz trafia w gminę, drugi w dzielnicę,
    a koniunkcja tokenów sama zawęża listę do jednej dzielnicy. Bez gminy w zlepku samo „warszawa”
    nie trafiało w ani jedną stołeczną szkołę.
    """
    parts = [name.strip(), city.strip()]
    parent = (city_parent or "").strip()
    if parent and fold(parent) != fold(city.strip()):
        parts.append(parent)
    return fold(" ".join(part for part in parts if part))


def derived_fields(name: str, city: str, voivodeship: str = "", postal_code: str = "") -> dict:
    """Trzy kolumny wyliczane z danych wykazu, gotowe do zapisu (przycięte do długości pól).

    Jedna funkcja zamiast trzech wywołań u każdego zapisującego, bo kolumny są zależne: obie
    porównawcze liczą się **od** gminy, a gmina od miejscowości, województwa i kodu pocztowego.
    Rozbicie tego na osobne wywołania w ``save()``, w ``seed_schools`` i w migracji skończyłoby
    się kolumnami policzonymi z różnych wersji tej samej reguły.
    """
    parent = parent_city(city, voivodeship, postal_code)
    return {
        "city_parent": parent[:120],
        "city_search": city_search_for(city, parent)[:120],
        "search_text": search_text_for(name, city, parent)[:400],
    }
