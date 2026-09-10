"""Wyszukiwarka szkół (``GET /api/schools/``) – jedyny publiczny endpoint słownika.

Bez uwierzytelnienia i bez sesji, bo woła go formularz rejestracji **zanim** konto powstanie.
Dane są jawne (rejestr ministerialny publikowany na dane.gov.pl), więc jedyne, co trzeba tu
chronić, to koszt zapytania: stąd własny scope throttlingu ``schools`` (limit wyższy niż
``register``, bo jedno wypełnienie formularza to kilkanaście żądań podpowiedzi) i twardy sufit
liczby wierszy w odpowiedzi.
"""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import Voivodeship
from apps.core.text import fold

from .models import School
from .serializers import SchoolSearchResultsSerializer, SchoolSuggestionSerializer

#: Ile podpowiedzi maksymalnie wraca. Lista dłuższa niż ekran nie pomaga wybierać, a każde
#: naciśnięcie klawisza to osobne zapytanie.
MAX_RESULTS = 20

#: Krótsze zapytanie nie zawęża niczego sensownie („li” pasuje do połowy liceów w Polsce),
#: a kosztuje pełne przejście po tabeli. Ten sam próg ma debounce w ``static/js/school-picker.js``.
MIN_QUERY_LENGTH = 2


def search_schools(query: str, *, voivodeship: str = "", limit: int = MAX_RESULTS):
    """Szkoły pasujące do zapytania. Pusty wynik dla zapytania krótszego niż ``MIN_QUERY_LENGTH``.

    Dopasowanie jest **koniunkcją tokenów**: „mickiewicza krakow” musi trafić w wiersz, w którym
    jeden wyraz stoi w nazwie, a drugi w mieście – i nie może trafić w liceum Mickiewicza
    w Gdańsku. Porównanie idzie po ``search_text`` (nazwa + miejscowość bez diakrytyków), więc
    „lodz” znajduje „ŁÓDŹ” bez rozszerzenia ``unaccent`` w bazie.
    """
    tokens = fold(query or "").split()
    if not tokens or len(fold(query or "").strip()) < MIN_QUERY_LENGTH:
        return School.objects.none()
    queryset = School.objects.filter(is_active=True)
    if voivodeship in Voivodeship.values:
        queryset = queryset.filter(voivodeship=voivodeship)
    for token in tokens:
        # ``contains``, nie ``icontains``: obie strony porównania są już złożone do małych liter,
        # a wariant bez ``UPPER()`` zostawia bazie szansę na użycie indeksu przy prefiksach.
        queryset = queryset.filter(search_text__contains=token)
    return queryset.order_by("name", "id")[: max(1, min(limit, MAX_RESULTS))]


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
        responses={200: SchoolSearchResultsSerializer},
    )
    def get(self, request):
        try:
            limit = int(request.query_params.get("limit") or MAX_RESULTS)
        except ValueError:
            limit = MAX_RESULTS
        schools = search_schools(
            request.query_params.get("q") or "",
            voivodeship=(request.query_params.get("voivodeship") or "").strip(),
            limit=limit,
        )
        return Response({"results": self.get_serializer(schools, many=True).data})
