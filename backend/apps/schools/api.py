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

Rozwiązaniem nie jest podniesienie limitu, tylko **zawężenie do miasta**: po wybraniu miejscowości
lista jest kompletna (doczytywana stronami), a porządek nie jest już alfabetyczny, tylko „najpierw
licea ogólnokształcące, potem technika, potem reszta” (``KIND_ORDER``) – czyli w kolejności, w jakiej
uczestnicy tej olimpiady faktycznie szukają swojej szkoły.
"""

from django.db.models import Case, IntegerField, Value, When
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import Voivodeship
from apps.core.text import fold

from .models import KIND_ORDER, School
from .serializers import (
    CitySearchResultsSerializer,
    CitySuggestionSerializer,
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


def search_cities(query: str, *, voivodeship: str = "", limit: int = MAX_RESULTS):
    """Odrębne miejscowości zaczynające się od ``query``. Pusty wynik dla zapytania za krótkiego.

    Dopasowanie jest **prefiksowe**, a nie „gdziekolwiek w napisie”, i to jest różnica wobec
    wyszukiwarki szkół: nazwa miasta jest krótka i człowiek pisze ją od początku, a fragment
    w środku („law”) dawałby listę, w której nie widać, czego właściwie szukano. Porównanie idzie
    po ``city_search`` (miejscowość bez diakrytyków, małymi literami), więc „lodz” znajduje „Łódź”.

    Zwracamy pary (miejscowość, województwo), a nie same nazwy: „Brzeg” jest w dwóch
    województwach, a uczestnik ma wybrać swoją szkołę, nie cudzą.
    """
    prefix = fold(query or "").strip()
    if len(prefix) < MIN_QUERY_LENGTH:
        return []
    queryset = School.objects.filter(is_active=True, city_search__startswith=prefix)
    if voivodeship in Voivodeship.values:
        queryset = queryset.filter(voivodeship=voivodeship)
    rows = (
        queryset.values("city", "voivodeship", "city_search")
        .distinct()
        .order_by("city_search", "voivodeship")[: max(1, min(limit, MAX_RESULTS))]
    )
    return [{"city": row["city"], "voivodeship": row["voivodeship"]} for row in rows]


def search_schools(
    query: str,
    *,
    voivodeship: str = "",
    city: str = "",
    limit: int = MAX_RESULTS,
    offset: int = 0,
):
    """Szkoły pasujące do zapytania. Zwraca ``(wiersze, czy_jest_więcej)``.

    Dwa tryby, bo są dwa pytania:

    - **bez miejscowości** – jak dotąd: koniunkcja tokenów po ``search_text`` (nazwa + miejscowość
      bez diakrytyków), czyli „mickiewicza krakow” trafia w wiersz, w którym jeden wyraz stoi
      w nazwie, a drugi w mieście, i nie trafia w liceum Mickiewicza w Gdańsku. Zapytanie krótsze
      niż ``MIN_QUERY_LENGTH`` nie zwraca nic – pełne przejście po ośmiu tysiącach wierszy dla
      dwóch liter nikomu nie pomaga,
    - **z miejscowością** – zbiór jest z góry ograniczony do jednego miasta, więc **pusty tekst
      jest dozwolony** i znaczy „pokaż wszystkie szkoły tego miasta”. To jest sedno poprawki po
      uwadze organizatora: uczestnik, który nie wie, jak dokładnie nazywa się jego szkoła w wykazie,
      ma ją przewinąć, a nie zgadywać słowa.

    Porządek jest zawsze ten sam: najpierw typ (licea, technika, reszta), potem nazwa. Alfabet
    bez typu stawiał na początku listy wojewódzkiego miasta same szkoły branżowe.

    ``has_more`` liczymy pobraniem jednego wiersza ponad limit, a nie osobnym ``COUNT(*)``:
    klientowi wystarczy wiedzieć, czy doczytywać, a dokładna liczba kosztowałaby drugie przejście
    po tym samym zbiorze. Wynikiem jest **lista**, a nie ``QuerySet``, i to w obu gałęziach:
    zbiór jest już pobrany (bez tego nie dałoby się policzyć ``has_more``), a funkcja zwracająca
    raz jedno, raz drugie prosi się o ``.count()``, które puściłoby zapytanie po raz drugi.
    """
    tokens = fold(query or "").split()
    city_key = fold(city or "").strip()
    if not city_key and (not tokens or len(fold(query or "").strip()) < MIN_QUERY_LENGTH):
        return [], False
    queryset = School.objects.filter(is_active=True)
    if city_key:
        queryset = queryset.filter(city_search=city_key)
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


def _int_param(request, name: str, default: int) -> int:
    """Parametr liczbowy z zapytania. Śmieć jest ignorowany, a nie zwracany jako 400.

    Podpowiedź jest wygodą, a nie autoryzacją: ``?limit=dużo`` ma dać domyślną listę, a nie błąd
    w środku wypełniania formularza.
    """
    try:
        return int(request.query_params.get(name) or default)
    except ValueError:
        return default


class CitySearchView(GenericAPIView):
    """Podpowiedzi miejscowości – pierwszy krok bloku „szkoła” w formularzu rejestracji."""

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
                "Wielkość liter i polskie znaki nie mają znaczenia.",
            ),
            OpenApiParameter(
                "voivodeship",
                str,
                enum=Voivodeship.values,
                description="Zawężenie do województwa. Wartość spoza listy jest ignorowana.",
            ),
            OpenApiParameter("limit", int, description=f"Ile podpowiedzi, maksymalnie {MAX_RESULTS}."),
        ],
        responses={200: CitySearchResultsSerializer},
    )
    def get(self, request):
        cities = search_cities(
            request.query_params.get("q") or "",
            voivodeship=(request.query_params.get("voivodeship") or "").strip(),
            limit=_int_param(request, "limit", MAX_RESULTS),
        )
        return Response({"results": self.get_serializer(cities, many=True).data})


class SchoolSearchView(GenericAPIView):
    """Podpowiedzi szkół dla formularza rejestracji."""

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
                description="Zawężenie do miejscowości (nazwa z „/api/schools/cities/”). "
                "Porównanie pomija wielkość liter i polskie znaki.",
            ),
            OpenApiParameter(
                "voivodeship",
                str,
                enum=Voivodeship.values,
                description="Zawężenie do województwa. Wartość spoza listy jest ignorowana.",
            ),
            OpenApiParameter("limit", int, description=f"Ile wierszy na stronę, maksymalnie {MAX_RESULTS}."),
            OpenApiParameter("offset", int, description="Ile wierszy pominąć (doczytywanie listy)."),
        ],
        responses={200: SchoolSearchResultsSerializer},
    )
    def get(self, request):
        limit = _int_param(request, "limit", MAX_RESULTS)
        schools, has_more = search_schools(
            request.query_params.get("q") or "",
            voivodeship=(request.query_params.get("voivodeship") or "").strip(),
            city=(request.query_params.get("city") or "").strip(),
            limit=limit,
            offset=_int_param(request, "offset", 0),
        )
        return Response({"results": self.get_serializer(schools, many=True).data, "has_more": has_more})
