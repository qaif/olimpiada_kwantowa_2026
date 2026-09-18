"""Wyszukiwarka słownika szkół – dwa publiczne endpointy formularza rejestracji.

``GET /api/schools/cities/`` podpowiada **miejscowości**, ``GET /api/schools/`` – szkoły, opcjonalnie
zawężone do wybranej miejscowości. Oba bez uwierzytelnienia i bez sesji, bo woła je formularz
rejestracji **zanim** konto powstanie. Dane są jawne (rejestr ministerialny publikowany na
dane.gov.pl), więc jedyne, co trzeba tu chronić, to koszt zapytania: stąd wspólny scope throttlingu
``schools`` (limit wyższy niż ``register``, bo jedno wypełnienie formularza to kilkanaście żądań
podpowiedzi) i twardy sufit liczby wierszy w odpowiedzi.

**Skąd wziął się krok „Miejscowość”.** Zgłoszenie organizatora: „okno wyboru szkoły po wpisaniu
«wrocław» nie pokazuje liceów ogólnokształcących, a wpisanie «liceum» nie pokazuje odpowiedniej
listy”. Obie obserwacje są prawdziwe i mają jedną przyczynę – dwadzieścia trafień posortowanych
alfabetycznie. Wrocław ma w wykazie ponad sto szkół rozrzuconych po kilku wierszach „miejscowości”
(``Wrocław-Śródmieście``, ``Wrocław-Krzyki``…), a nazwy zaczynające się od „A” (akademickie,
branżowe przy zespołach szkół) wypełniają limit, zanim dojdzie do „LICEUM”. Samo „liceum” w skali
kraju pasuje do kilku tysięcy wierszy i pierwsze dwadzieścia z nich nie ma nic wspólnego z tym,
kto pyta.

**Dlaczego miasto, a nie „miejscowość z wykazu”.** Wykaz zapisuje pięć największych miast
dzielnicami, a Warszawę **wyłącznie** dzielnicami („Śródmieście”, „Wola”, „Mokotów”…), więc
pierwsza wersja kroku „Miejscowość” miała dwie dziury: „warszawa” nie znajdowało niczego, a „wro”
dawało pięć pozycji, z których każda zawężała listę do jednej piątej Wrocławia. Podpowiedzi
chodzą więc po **gminie** (``School.city_parent``, reguła w ``apps.schools.normalise``),
a oryginalna miejscowość zostaje przy wierszu jako adres i jako etykieta „Wrocław (Krzyki)”.

Rozwiązaniem nie jest podniesienie limitu, tylko **zawężenie do miasta**: po wybraniu miejscowości
lista jest kompletna (doczytywana stronami), a porządek nie jest już alfabetyczny, tylko „najpierw
licea ogólnokształcące, potem technika, potem reszta” (``KIND_ORDER``) – czyli w kolejności, w jakiej
uczestnicy tej olimpiady faktycznie szukają swojej szkoły.

**Dwa wykazy** (etap 2, § 1.3.3). Od tego wydania to samo okno odpowiada z dwóch tabel: publicznego
wykazu SIO (``School``, wspólnego dla instalacji – decyzja D2 etapu 1) i słownika wgranego przez
organizatora (``schools.custom.CustomInstitution``, należącego do jednego konkursu). Trzy zdania
rozstrzygają o tym, jak to zrobiliśmy:

- **rodzaje placówek bierze się z konkursu**, a nie ze stałej: ``allowed_institution_types``
  (``apps.accounts.services``) oddaje przy wyłączonej fladze ``institution_types`` dokładnie
  ``("SECONDARY",)``, czyli dzisiejszy zakres podpowiedzi. Wiersz, którego rejestracja by nie
  przyjęła (``_resolve_institution``), nie ma prawa stanąć w podpowiedziach – jedno źródło reguły
  znaczy jedno zachowanie na obu końcach formularza,
- **bez flagi ``custom_school_directory`` zapytanie do drugiej tabeli nie pada ani razu** i nie
  dokłada się ani jeden klucz do odpowiedzi (§ 5.6). Konkurs #1 dostaje stąd to samo, co przed
  etapem 2 – co do klucza, kolejności i liczby zapytań,
- **przy dwóch wykazach wiersz mówi, skąd jest**: ``source`` (``sio``/``custom``) i nazwa kolumny
  dowiązania (``school_id`` albo ``custom_institution_id``). Porządek jest zawsze ten sam –
  najpierw wykaz publiczny (tam trafia większość), potem słownik organizatora – a ``offset``
  przewija **wykaz publiczny**: lista organizatora jest krótka i jedzie w całości na pierwszej
  stronie, więc doczytywanie nie ma czego w niej stronicować.
"""

from django.db.models import Case, IntegerField, Q, Value, When
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import DIRECTORY_INSTITUTION_TYPES, Voivodeship
from apps.accounts.services import (
    allowed_institution_types,
    custom_directory_enabled,
    registration_profile,
)
from apps.core.text import fold

from .custom import CustomInstitution, search_custom_institutions
from .models import KIND_ORDER, InstitutionType, School
from .normalise import DISTRICT_SEPARATOR
from .serializers import (
    SOURCE_CUSTOM,
    SOURCE_DIRECTORY,
    CitySearchResultsSerializer,
    CitySuggestionSerializer,
    CustomInstitutionSuggestionSerializer,
    MergedCitySuggestionSerializer,
    MergedSchoolSuggestionSerializer,
    SchoolSearchResultsSerializer,
    SchoolSuggestionSerializer,
)

#: Ile podpowiedzi maksymalnie wraca w **jednej** odpowiedzi. Lista dłuższa niż ekran nie pomaga
#: wybierać, a każde naciśnięcie klawisza to osobne zapytanie. Pełną listę szkół miasta klient
#: dostaje stronami (``offset`` + ``has_more``), a nie jednym wielkim ładunkiem.
MAX_RESULTS = 20

#: Krótsze zapytanie nie zawęża niczego sensownie („li” pasuje do połowy liceów w Polsce),
#: a kosztuje pełne przejście po tabeli. Ten sam próg ma debounce w ``static/js/school-picker.js``.
#: **Nie obowiązuje**, gdy wskazana jest miejscowość: tam pusty tekst znaczy „pokaż wszystkie”
#: i zbiór jest z góry ograniczony do jednego miasta.
MIN_QUERY_LENGTH = 2


def _kind_rank():
    """Wyrażenie porządkujące: typy z ``KIND_ORDER`` w podanej kolejności, reszta za nimi.

    ``Case``, a nie kolumna w bazie: kolejność typów jest decyzją interfejsu („kto tu startuje”),
    a nie własnością szkoły, więc zmiana priorytetu ma być zmianą jednej krotki w ``models.py``,
    a nie migracją i ponownym wgraniem ośmiu tysięcy wierszy.
    """
    return Case(
        *[When(kind=value, then=Value(index)) for index, value in enumerate(KIND_ORDER)],
        default=Value(len(KIND_ORDER)),
        output_field=IntegerField(),
    )


def search_cities(
    query: str,
    *,
    voivodeship: str = "",
    limit: int = MAX_RESULTS,
    institution_types: tuple[str, ...] | None = None,
):
    """Odrębne **gminy** zaczynające się od ``query``. Pusty wynik dla zapytania za krótkiego.

    Dopasowanie jest **prefiksowe**, a nie „gdziekolwiek w napisie”, i to jest różnica wobec
    wyszukiwarki szkół: nazwa miasta jest krótka i człowiek pisze ją od początku, a fragment
    w środku („law”) dawałby listę, w której nie widać, czego właściwie szukano. Prefiks liczy się
    jednak także **od dzielnicy**, i stąd dwa warunki: ``city_search`` ma postać „gmina|dzielnica”
    (patrz ``apps.schools.normalise``), więc „wro” trafia w gminę, a „krzyki” w dzielnicę – ktoś,
    kto zna swoją dzielnicę i nie myśli o niej jako o części miasta, dostaje w odpowiedzi
    „Wrocław” zamiast pustej listy.

    Zwracamy **gminy**, nie miejscowości z wykazu: pięć największych miast jest w nim rozbitych na
    dzielnice, więc „wro” dawało wcześniej pięć pozycji, z których każda zawężała listę szkół do
    jednej piątej Wrocławia, a „warszawa” nie dawało ani jednej – stolica figuruje tam wyłącznie
    pod nazwami dzielnic.

    Zwracamy pary (gmina, województwo), a nie same nazwy: „Brzeg” jest w dwóch województwach,
    a uczestnik ma wybrać swoją szkołę, nie cudzą.

    ``institution_types`` zawęża do rodzajów placówek dopuszczonych w konkursie (§ 1.3.2).
    ``None`` znaczy **bez zawężenia**, czyli dokładnie dzisiejsze zapytanie – i tak wołają tę
    funkcję konkursy z profilem domyślnym, żeby Konkurs #1 pytał bazę tym samym, czym pytał przed
    etapem 2. Pusta krotka znaczy „żaden rodzaj”, a nie „wszystkie”: konkurs, w którym wybrano
    placówkę spoza wykazu, ma dostać listę pustą, a nie cały wykaz.
    """
    prefix = fold(query or "").strip()
    if len(prefix) < MIN_QUERY_LENGTH:
        return []
    queryset = School.objects.filter(is_active=True).filter(
        Q(city_search__startswith=prefix) | Q(city_search__contains=f"{DISTRICT_SEPARATOR}{prefix}")
    )
    if institution_types is not None:
        queryset = queryset.filter(institution_type__in=tuple(institution_types))
    if voivodeship in Voivodeship.values:
        queryset = queryset.filter(voivodeship=voivodeship)
    # Porządek robimy w Pythonie, a nie w bazie, bo ``SELECT DISTINCT`` może sortować wyłącznie
    # po kolumnach, które wypisuje – a sortować chcemy po postaci **złożonej** („Łódź” ma stać
    # przy „Lodzie”, a nie na końcu alfabetu, jak każe większość collation). Zbiór jest z góry
    # mały: prefiks ma co najmniej dwa znaki, a odrębnych gmin w całym słowniku jest ~2 tysiące.
    rows = sorted(
        # ``order_by()`` bez argumentów kasuje domyślny porządek modelu (``name``, ``id``).
        # Bez tego Django dokłada obie kolumny do ``SELECT DISTINCT`` i odrębność liczy się po
        # szkole, a nie po mieście – czyli lista miast znów byłaby listą szkół.
        queryset.values("city_parent", "voivodeship").order_by().distinct(),
        key=lambda row: (fold(row["city_parent"]), row["voivodeship"]),
    )
    return [
        {"city": row["city_parent"], "voivodeship": row["voivodeship"]}
        for row in rows[: max(1, min(limit, MAX_RESULTS))]
    ]


def search_schools(
    query: str,
    *,
    voivodeship: str = "",
    city: str = "",
    limit: int = MAX_RESULTS,
    offset: int = 0,
    institution_types: tuple[str, ...] | None = None,
):
    """Szkoły pasujące do zapytania. Zwraca ``(wiersze, czy_jest_więcej)``.

    Dwa tryby, bo są dwa pytania:

    - **bez miejscowości** – jak dotąd: koniunkcja tokenów po ``search_text`` (nazwa + miejscowość
      bez diakrytyków), czyli „mickiewicza krakow” trafia w wiersz, w którym jeden wyraz stoi
      w nazwie, a drugi w mieście, i nie trafia w liceum Mickiewicza w Gdańsku. Zapytanie krótsze
      niż ``MIN_QUERY_LENGTH`` nie zwraca nic – pełne przejście po ośmiu tysiącach wierszy dla
      dwóch liter nikomu nie pomaga,
    - **z miejscowością** – zbiór jest z góry ograniczony do jednej **gminy** (razem ze wszystkimi
      jej dzielnicami), więc **pusty tekst jest dozwolony** i znaczy „pokaż wszystkie szkoły tego
      miasta”. To jest sedno poprawki po uwadze organizatora: uczestnik, który nie wie, jak
      dokładnie nazywa się jego szkoła w wykazie, ma ją przewinąć, a nie zgadywać słowa.

    Zawężenie do dzielnicy działa **w drugą stronę**, bez osobnego parametru: „warszawa
    śródmieście” albo „wrocław krzyki” to dwa tokeny, a ``search_text`` trzyma i gminę,
    i oryginalną miejscowość z wykazu – pierwszy wyraz trafia w gminę, drugi w dzielnicę
    i koniunkcja sama zostawia jedną dzielnicę. Działa też po wybraniu miasta z podpowiedzi:
    wpisane obok „krzyki” zawęża pełną listę Wrocławia.

    Porządek jest zawsze ten sam: najpierw typ (licea, technika, reszta), potem nazwa. Alfabet
    bez typu stawiał na początku listy wojewódzkiego miasta same szkoły branżowe.

    ``has_more`` liczymy pobraniem jednego wiersza ponad limit, a nie osobnym ``COUNT(*)``:
    klientowi wystarczy wiedzieć, czy doczytywać, a dokładna liczba kosztowałaby drugie przejście
    po tym samym zbiorze. Wynikiem jest **lista**, a nie ``QuerySet``, i to w obu gałęziach:
    zbiór jest już pobrany (bez tego nie dałoby się policzyć ``has_more``), a funkcja zwracająca
    raz jedno, raz drugie prosi się o ``.count()``, które puściłoby zapytanie po raz drugi.

    ``institution_types`` zawęża do rodzajów placówek dopuszczonych w konkursie – z tą samą
    umową, co w :func:`search_cities`: ``None`` to dzisiejsze zapytanie bez ani jednego warunku
    więcej, pusta krotka to pusta lista.
    """
    tokens = fold(query or "").split()
    city_key = fold(city or "").strip()
    if not city_key and (not tokens or len(fold(query or "").strip()) < MIN_QUERY_LENGTH):
        return [], False
    queryset = School.objects.filter(is_active=True)
    if institution_types is not None:
        queryset = queryset.filter(institution_type__in=tuple(institution_types))
    if city_key:
        # Gmina **razem z dzielnicami**: ``city_search`` ma postać „gmina|dzielnica”, więc wiersz
        # bez dzielnicy pasuje dokładnie, a wiersz dzielnicy zaczyna się od gminy i separatora.
        # Separator jest tu istotny – bez niego „Opole” brałoby też „Opole Lubelskie”, a takich
        # par nazw jest w wykazie czterdzieści kilka.
        queryset = queryset.filter(
            Q(city_search=city_key) | Q(city_search__startswith=f"{city_key}{DISTRICT_SEPARATOR}")
        )
    if voivodeship in Voivodeship.values:
        queryset = queryset.filter(voivodeship=voivodeship)
    for token in tokens:
        # ``contains``, nie ``icontains``: obie strony porównania są już złożone do małych liter,
        # a wariant bez ``UPPER()`` zostawia bazie szansę na użycie indeksu przy prefiksach.
        queryset = queryset.filter(search_text__contains=token)
    page_size = max(1, min(limit, MAX_RESULTS))
    start = max(0, offset)
    rows = list(
        queryset.annotate(kind_rank=_kind_rank()).order_by("kind_rank", "name", "id")[
            start : start + page_size + 1
        ]
    )
    return rows[:page_size], len(rows) > page_size


def search_custom_cities(competition, query: str, *, institution_types=None, limit: int = MAX_RESULTS):
    """Miejscowości ze słownika organizatora – druga połowa kroku „Miejscowość” (§ 1.3.3).

    Reguła dopasowania jest **ta sama**, co w :func:`search_cities`: prefiks po ``city_search``,
    razem z prefiksem dzielnicy. Różnic są dwie i obie wynikają z tego, czym jest ta tabela:
    województwa nie zna (położenie opisuje kodem regionu konkursu), więc oddaje je pustym
    napisem, i nie ma czego sprowadzać do gminy – rozbicie pięciu miast na dzielnice jest
    własnością wykazu SIO, a nie listy organizatora.

    **Bez flagi nie pada ani jedno zapytanie** – warunek sprawdzamy tutaj, tak samo jak robi to
    :func:`apps.schools.custom.search_custom_institutions`, żeby zdanie z § 5.6 było prawdziwe
    niezależnie od tego, kto tę funkcję zawoła.

    Funkcja stoi tu, a nie w ``apps/schools/custom.py``, bo jest **wyszukiwarką**, czyli sprawą
    tego modułu: obok siedzi jej bliźniaczka dla wykazu publicznego i to razem z nią, a nie
    z importem CSV, czyta się regułę „co jest podpowiedzią miejscowości”.
    """
    if not custom_directory_enabled(competition):
        return []
    prefix = fold(query or "").strip()
    if len(prefix) < MIN_QUERY_LENGTH:
        return []
    queryset = (
        CustomInstitution.objects.for_competition(competition)
        .filter(is_active=True)
        .exclude(city="")
        .filter(Q(city_search__startswith=prefix) | Q(city_search__contains=f"{DISTRICT_SEPARATOR}{prefix}"))
    )
    if institution_types is not None:
        queryset = queryset.filter(institution_type__in=tuple(institution_types))
    # Porządek liczymy w Pythonie z tego samego powodu, co przy wykazie publicznym: ``DISTINCT``
    # sortuje wyłącznie po wypisanych kolumnach, a „Łódź” ma stać przy „Lodzi”, a nie na końcu
    # alfabetu. Zbiór jest z góry mały – słownik organizatora liczy najwyżej ``MAX_ROWS`` wierszy.
    names = sorted({row["city"] for row in queryset.values("city").order_by().distinct()}, key=fold)
    return [
        {"city": name, "voivodeship": "", "source": SOURCE_CUSTOM}
        for name in names[: max(1, min(limit, MAX_RESULTS))]
    ]


def _int_param(request, name: str, default: int) -> int:
    """Parametr liczbowy z zapytania. Śmieć jest ignorowany, a nie zwracany jako 400.

    Podpowiedź jest wygodą, a nie autoryzacją: ``?limit=dużo`` ma dać domyślną listę, a nie błąd
    w środku wypełniania formularza.
    """
    try:
        return int(request.query_params.get(name) or default)
    except ValueError:
        return default


def _requested_types(request, allowed: tuple[str, ...]) -> tuple[str, ...]:
    """Rodzaje placówek, o które pyta **to** żądanie – zawsze podzbiór dopuszczonych w konkursie.

    Wartość spoza zbioru dopuszczonych jest **ignorowana**, a nie zwracana jako 400 – tak samo
    jak przy ``voivodeship`` i ``limit``, i z tego samego powodu: podpowiedź jest wygodą, a nie
    autoryzacją, więc przestawiony w adresie parametr ma dać listę domyślną, a nie błąd w środku
    wypełniania formularza. Zawężanie do zbioru dopuszczonych **tutaj** znaczy też, że żaden
    adres nie wyciągnie z wykazu rodzaju placówki, którego ten konkurs nie przyjmie w rejestracji
    (``apps.accounts.services._resolve_institution``).
    """
    chosen = (request.query_params.get("institution_type") or "").strip().upper()
    return (chosen,) if chosen in allowed else allowed


def _registry_types(profile, wanted: tuple[str, ...]) -> tuple[str, ...] | None:
    """Czym zawęzić zapytanie do **wykazu publicznego** – albo ``None``, czyli niczym.

    ``None`` przy profilu domyślnym jest tu treścią, a nie skrótem: Konkurs #1 ma pytać bazę
    dokładnie tym zapytaniem, co przed etapem 2, a warunek ``institution_type IN ('SECONDARY')``
    – choć wybiera te same 8118 wierszy – jest zmianą w zapytaniu, którego nikt nie zamawiał.
    Ta sama decyzja i to samo uzasadnienie stoi przy ``allowed_types`` w
    ``apps.accounts.services._resolve_institution``.

    Poza profilem domyślnym zostają **rodzaje mające wiersze w wykazie**: „placówka poza Polską”
    i „bez szkoły” nie są pozycjami rejestru (§ 1.3.2), więc wybrane w formularzu dają pustą
    krotkę, czyli pustą listę podpowiedzi – a nie cały wykaz.
    """
    if profile.is_default():
        return None
    return tuple(value for value in wanted if value in DIRECTORY_INSTITUTION_TYPES)


class CitySearchView(GenericAPIView):
    """Podpowiedzi miejscowości – pierwszy krok bloku „szkoła” w formularzu rejestracji.

    Przy włączonym słowniku organizatora lista jest **sumą** miejscowości obu wykazów: najpierw
    gminy z wykazu publicznego, potem miejscowości z listy organizatora, których tam nie było.
    Powtórzoną nazwę zdejmujemy po postaci porównawczej (``fold``), a nie po samym napisie – dwa
    wiersze „Kraków” i „KRAKÓW” byłyby dla człowieka tą samą pozycją dwa razy.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "schools"
    serializer_class = CitySuggestionSerializer
    pagination_class = None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q",
                str,
                description=f"Początek nazwy miejscowości, minimum {MIN_QUERY_LENGTH} znaki. "
                "Wielkość liter i polskie znaki nie mają znaczenia. Wracają **gminy**: początek "
                "nazwy dzielnicy („krzyki”, „śródmieście”) też pasuje, ale w odpowiedzi stoi "
                "miasto („Wrocław”, „Warszawa”).",
            ),
            OpenApiParameter(
                "voivodeship",
                str,
                enum=Voivodeship.values,
                description="Zawężenie do województwa. Wartość spoza listy jest ignorowana.",
            ),
            OpenApiParameter("limit", int, description=f"Ile podpowiedzi, maksymalnie {MAX_RESULTS}."),
            OpenApiParameter(
                "institution_type",
                str,
                enum=InstitutionType.values,
                description="Zawężenie do rodzaju placówki. Wartość niedopuszczona w tym konkursie "
                "jest ignorowana – wracają wtedy wszystkie dopuszczone rodzaje.",
            ),
        ],
        responses={200: CitySearchResultsSerializer},
    )
    def get(self, request):
        competition = getattr(request, "competition", None)
        profile = registration_profile(competition)
        wanted = _requested_types(request, allowed_institution_types(competition))
        query = request.query_params.get("q") or ""
        limit = _int_param(request, "limit", MAX_RESULTS)
        cities = search_cities(
            query,
            voivodeship=(request.query_params.get("voivodeship") or "").strip(),
            limit=limit,
            institution_types=_registry_types(profile, wanted),
        )
        if not custom_directory_enabled(competition):
            return Response({"results": self.get_serializer(cities, many=True).data})
        rows = [{**row, "source": SOURCE_DIRECTORY} for row in cities]
        known = {fold(row["city"]) for row in rows}
        rows.extend(
            row
            for row in search_custom_cities(competition, query, institution_types=wanted, limit=limit)
            if fold(row["city"]) not in known
        )
        return Response({"results": MergedCitySuggestionSerializer(rows, many=True).data})


class SchoolSearchView(GenericAPIView):
    """Podpowiedzi placówek dla formularza rejestracji – z jednego wykazu albo z dwóch.

    Kształt odpowiedzi rozstrzyga **konkurs**, a nie parametr adresu: bez słownika organizatora
    wiersz ma dokładnie te klucze, co przed etapem 2, a z nim każdy wiersz dostaje ``source``
    i nazwę kolumny dowiązania (``school_id`` albo ``custom_institution_id``). Dwa kształty
    zamiast jednego „na zapas”, bo dopisanie pola do odpowiedzi publicznego endpointu Konkursu #1
    jest zmianą widoczną dla jego uczestników (§ 0.1), a nic za nią nie stoi.

    ``has_more`` mówi o **wykazie publicznym** – tam jest lista, którą trzeba przewijać (sto
    kilkadziesiąt szkół dużego miasta). Słownik organizatora jedzie w całości na pierwszej
    stronie, bo jest krótki z definicji (``MAX_ROWS`` to pięć tysięcy wierszy na cały konkurs,
    a jedno miasto to garść), a stronicowanie dwóch list naraz kazałoby klientowi trzymać dwa
    liczniki, żeby nie zobaczyć tego samego wiersza dwa razy.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "schools"
    serializer_class = SchoolSuggestionSerializer
    pagination_class = None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q",
                str,
                description=f"Fragment nazwy lub miejscowości, minimum {MIN_QUERY_LENGTH} znaki. "
                "Wielkość liter i polskie znaki nie mają znaczenia. Razem z „city” wolno zostawić "
                "pusty – wraca wtedy pełna lista szkół tej miejscowości.",
            ),
            OpenApiParameter(
                "city",
                str,
                description="Zawężenie do miejscowości – nazwa gminy z „/api/schools/cities/”. "
                "Obejmuje wszystkie jej dzielnice („Wrocław” bierze też Krzyki i Fabryczną, "
                "„Warszawa” – wszystkie osiemnaście). Porównanie pomija wielkość liter "
                "i polskie znaki.",
            ),
            OpenApiParameter(
                "voivodeship",
                str,
                enum=Voivodeship.values,
                description="Zawężenie do województwa. Wartość spoza listy jest ignorowana.",
            ),
            OpenApiParameter("limit", int, description=f"Ile wierszy na stronę, maksymalnie {MAX_RESULTS}."),
            OpenApiParameter(
                "offset",
                int,
                description="Ile wierszy pominąć (doczytywanie listy). Liczy wiersze **wykazu "
                "publicznego**: słownik organizatora wraca w całości na pierwszej stronie.",
            ),
            OpenApiParameter(
                "institution_type",
                str,
                enum=InstitutionType.values,
                description="Zawężenie do rodzaju placówki. Wartość niedopuszczona w tym konkursie "
                "jest ignorowana – wracają wtedy wszystkie dopuszczone rodzaje.",
            ),
        ],
        responses={200: SchoolSearchResultsSerializer},
    )
    def get(self, request):
        competition = getattr(request, "competition", None)
        profile = registration_profile(competition)
        wanted = _requested_types(request, allowed_institution_types(competition))
        limit = _int_param(request, "limit", MAX_RESULTS)
        offset = _int_param(request, "offset", 0)
        query = request.query_params.get("q") or ""
        city = (request.query_params.get("city") or "").strip()
        schools, has_more = search_schools(
            query,
            voivodeship=(request.query_params.get("voivodeship") or "").strip(),
            city=city,
            limit=limit,
            offset=offset,
            institution_types=_registry_types(profile, wanted),
        )
        if not custom_directory_enabled(competition):
            return Response({"results": self.get_serializer(schools, many=True).data, "has_more": has_more})
        results = list(MergedSchoolSuggestionSerializer(schools, many=True).data)
        # Druga strona jest doczytaniem **wykazu publicznego** – słownik organizatora klient ma
        # już z pierwszej i dołożony tu drugi raz stanąłby na liście podwójnie.
        custom = (
            []
            if offset > 0
            else search_custom_institutions(
                competition, query, institution_types=wanted, city=city, limit=limit
            )
        )
        results.extend(CustomInstitutionSuggestionSerializer(custom, many=True).data)
        return Response({"results": results, "has_more": has_more})
