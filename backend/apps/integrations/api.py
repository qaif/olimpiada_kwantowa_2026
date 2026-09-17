"""Publiczne API ``/api/v1/`` dla systemów zewnętrznych (drf-spectacular: sekcja „Integracje”).

Czym się różni od dotychczasowego ``/api/…``: tamto obsługuje **nasze** ekrany i uwierzytelnia
konta, to – cudze serwery i uwierzytelnia klucze. Stąd trzy reguły obowiązujące tu wszędzie:

- **prefiks jest wersją.** Kształt odpowiedzi spod ``/api/v1/`` nie zmieni się w sposób łamiący
  klientów; zmiana kształtu to ``/api/v2/``. Dokładanie pól nie łamie nikogo i wersji nie zmienia,
- **zakres decyduje o widoku, nie o filtrze.** Dane osobowe mają osobny serializer, a nie pole
  dokładane warunkowo – widok wybiera klasę, więc nie ma miejsca, w którym „zapomniałby” sprawdzić
  uprawnienie (``apps.integrations.serializers``),
- **czego klucz nie obejmuje, tego nie ma.** Zasób z innej edycji niż ta przypisana do klucza
  odpowiada 404, a nie 403. Inaczej po kodach odpowiedzi dałoby się ustalić, ile etapów ma edycja,
  do której partner nie ma dostępu.

Wszystko tutaj jest **tylko do odczytu** poza jednym endpointem: ``POST /api/v1/events/`` dopisuje
wydarzenie do linii czasu. To jedyna rzecz, którą system zewnętrzny może w olimpiadzie zmienić,
i celowo najmniej groźna z możliwych – wydarzenie niczego nie otwiera ani nie zamyka.
"""

from __future__ import annotations

import logging

from django.db.models import Count
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status as http
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.competitions.models import Edition, Stage, StageEntry
from apps.core.api import DomainError
from apps.core.models import audit
from apps.results.models import ResultsPublication
from apps.results.statistics import statistics
from apps.submissions.models import Submission

from .auth import ApiKeyAuthentication, ApiKeyRateThrottle, HasApiKey, HasScope
from .models import (
    SCOPE_READ_PARTICIPANTS,
    SCOPE_READ_PARTICIPANTS_PII,
    SCOPE_READ_RESULTS,
    SCOPE_READ_STATS,
    SCOPE_READ_SUBMISSIONS_META,
    SCOPE_WRITE_EVENTS,
    SCOPES,
    WEBHOOK_EVENTS,
)
from .serializers import (
    V1CapabilitiesSerializer,
    V1EditionSerializer,
    V1EventCreateSerializer,
    V1EventSerializer,
    V1ParticipantPiiSerializer,
    V1ParticipantSerializer,
    V1ResultRowSerializer,
    V1ResultsPageSerializer,
    V1StageSerializer,
    V1StatsSerializer,
    V1SubmissionSerializer,
)

logger = logging.getLogger(__name__)

#: Sekcja w ``/api/docs/``. Jedna dla całego ``/api/v1/`` – integrator ma zobaczyć swój kontrakt
#: w jednym kawałku, a nie rozsypany po sekcjach ułożonych według naszych aplikacji.
TAG = "Integracje"


def _not_found(what: str = "Nie znaleziono zasobu.") -> DomainError:
    """404 w postaci błędu domenowego – ten sam kształt odpowiedzi, co wszędzie indziej."""
    return DomainError(what, "NOT_FOUND", http.HTTP_404_NOT_FOUND)


class V1Pagination(PageNumberPagination):
    """Stronicowanie ``/api/v1/``: sto wierszy, ``?page=`` i ``?page_size=`` do pięciuset.

    Ustawione **przy widokach**, a nie w ``REST_FRAMEWORK``: globalna zmiana przestawiłaby kształt
    odpowiedzi wszystkich istniejących endpointów platformy, czyli złamała panel i aplikację
    uczestnika przy okazji dokładania integracji.
    """

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


class ApiKeyViewMixin:
    """Wspólne poświadczenie, limit i sekcja dokumentacji dla wszystkich widoków ``/api/v1/``."""

    authentication_classes = [ApiKeyAuthentication]
    throttle_classes = [ApiKeyRateThrottle]
    pagination_class = V1Pagination

    @property
    def api_key(self):
        return self.request.auth

    def stage_in_scope(self, stage_id: int) -> Stage:
        """Etap widoczny dla tego klucza albo 404. Jedno miejsce na regułę zawężenia do edycji."""
        stage = (
            Stage.objects.select_related("edition")
            .annotate(problem_count=Count("problems", distinct=True))
            .filter(pk=stage_id)
            .first()
        )
        if stage is None or not self.api_key.covers_edition(stage.edition_id):
            raise _not_found("Nie znaleziono etapu.")
        return stage

    def editions_in_scope(self):
        """Edycje, które ten klucz w ogóle widzi."""
        queryset = Edition.objects.all()
        if self.api_key.edition_id is not None:
            queryset = queryset.filter(pk=self.api_key.edition_id)
        return queryset


@extend_schema(tags=[TAG])
class CapabilitiesView(ApiKeyViewMixin, GenericAPIView):
    """``GET /api/v1/`` – co ten klucz może. Punkt wejścia i zarazem test poświadczenia."""

    permission_classes = [HasApiKey]
    serializer_class = V1CapabilitiesSerializer

    @extend_schema(responses={200: V1CapabilitiesSerializer})
    def get(self, request):
        key = self.api_key
        payload = {
            "key_prefix": key.prefix,
            "name": key.name,
            "edition_id": key.edition_id,
            "scopes": key.scopes or [],
            "pii_allowed": key.pii_allowed,
            "rate_limit_per_minute": key.rate_limit_per_minute,
            "available_scopes": dict(SCOPES),
            "webhook_events": dict(WEBHOOK_EVENTS),
        }
        return Response(self.get_serializer(payload).data)


@extend_schema(tags=[TAG])
class EditionListView(ApiKeyViewMixin, ListAPIView):
    """``GET /api/v1/editions/`` – edycje widoczne dla klucza, od najnowszej."""

    permission_classes = [HasApiKey]
    serializer_class = V1EditionSerializer

    def get_queryset(self):
        return self.editions_in_scope().order_by("-created_at", "-id")


@extend_schema(
    tags=[TAG],
    parameters=[
        OpenApiParameter("kind", str, description="Filtr rodzaju etapu (ELIM, DISTRICT, FINAL, …)."),
    ],
)
class EditionStageListView(ApiKeyViewMixin, ListAPIView):
    """``GET /api/v1/editions/<id>/stages/`` – etapy edycji wraz z kalendarzem."""

    permission_classes = [HasApiKey]
    serializer_class = V1StageSerializer

    def get_queryset(self):
        edition_id = self.kwargs["edition_id"]
        if not self.api_key.covers_edition(edition_id):
            raise _not_found("Nie znaleziono edycji.")
        if not Edition.objects.filter(pk=edition_id).exists():
            raise _not_found("Nie znaleziono edycji.")
        queryset = (
            Stage.objects.filter(edition_id=edition_id)
            .annotate(problem_count=Count("problems", distinct=True))
            .order_by("opens_at", "id")
        )
        kind = (self.request.query_params.get("kind") or "").strip()
        if kind:
            queryset = queryset.filter(kind=kind)
        return queryset


@extend_schema(
    tags=[TAG],
    parameters=[
        OpenApiParameter("voivodeship", str, description="Filtr województwa (wartość z listy zamkniętej)."),
        OpenApiParameter("status", str, description="Filtr stanu wpisu do etapu."),
        OpenApiParameter("grade", int, description="Filtr klasy."),
        OpenApiParameter("school", str, description="Fragment nazwy szkoły (bez rozróżniania wielkości)."),
    ],
)
class StageParticipantListView(ApiKeyViewMixin, ListAPIView):
    """``GET /api/v1/stages/<id>/participants/`` – uczestnicy etapu.

    Domyślnie **bez danych osobowych**. Imiona, nazwiska i adresy pojawiają się wyłącznie wtedy,
    gdy klucz ma zakres ``read:participants_pii`` **i** postawioną przez koordynatora flagę
    „dane osobowe dozwolone” – dwie niezależne zgody, bo to jest lista uczniów.
    """

    permission_classes = [HasScope(SCOPE_READ_PARTICIPANTS)]

    def get_serializer_class(self):
        key = getattr(self.request, "auth", None)
        if key is not None and key.has_scope(SCOPE_READ_PARTICIPANTS_PII):
            return V1ParticipantPiiSerializer
        return V1ParticipantSerializer

    def get_queryset(self):
        stage = self.stage_in_scope(self.kwargs["stage_id"])
        queryset = StageEntry.objects.filter(stage=stage).select_related(
            "participant", "participant__user", "participant__school_ref"
        )
        params = self.request.query_params
        voivodeship = (params.get("voivodeship") or "").strip()
        if voivodeship:
            queryset = queryset.filter(participant__district=voivodeship)
        entry_status = (params.get("status") or "").strip()
        if entry_status:
            queryset = queryset.filter(status=entry_status)
        grade = (params.get("grade") or "").strip()
        if grade.isdigit():
            queryset = queryset.filter(participant__grade=int(grade))
        school = (params.get("school") or "").strip()
        if school:
            queryset = queryset.filter(participant__school__icontains=school)
        return queryset.order_by("participant__public_code")


@extend_schema(tags=[TAG])
class StageResultsView(ApiKeyViewMixin, GenericAPIView):
    """``GET /api/v1/stages/<id>/results/`` – **wyłącznie** ogłoszona, zamrożona tabela.

    Etap bez publikacji odpowiada 404, tak samo jak publiczna strona wyników: dla świata na
    zewnątrz tabela, której nie ogłoszono, nie istnieje. Liczenie jej „na żywo” dla partnera
    byłoby obejściem całej procedury ogłaszania wyników.
    """

    permission_classes = [HasScope(SCOPE_READ_RESULTS)]
    serializer_class = V1ResultRowSerializer

    @extend_schema(responses={200: V1ResultsPageSerializer})
    def get(self, request, stage_id: int):
        stage = self.stage_in_scope(stage_id)
        publication = ResultsPublication.objects.select_related("stage").filter(stage_id=stage.pk).first()
        if publication is None:
            raise _not_found("Wyniki tego etapu nie zostały ogłoszone.")
        rows = [self._row(item) for item in publication.snapshot or []]
        paginator = self.paginator
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response(self.get_serializer(page, many=True).data)
        response.data["stage"] = {
            "stage_id": stage.pk,
            "edition_id": stage.edition_id,
            "published_at": publication.published_at,
            "anonymization": publication.anonymization,
            "count": len(rows),
        }
        return response

    @staticmethod
    def _row(item: dict) -> dict:
        """Wiersz snapshotu w nazwach API: ``district`` → ``voivodeship``.

        Snapshot niesie województwo wyłącznie w tabelach anonimizowanych kodem, więc przy
        pozostałych trybach pola po prostu nie ma – i tak ma zostać, bo dołożenie pustego
        sugerowałoby, że dane są, tylko akurat nieznane.
        """
        row = {key: value for key, value in item.items() if key != "district"}
        if "district" in item:
            row["voivodeship"] = item["district"]
        return row


@extend_schema(
    tags=[TAG],
    parameters=[
        OpenApiParameter("problem", int, description="Filtr numeru zadania."),
        OpenApiParameter("status", str, description="Filtr stanu zgłoszenia."),
    ],
)
class StageSubmissionListView(ApiKeyViewMixin, ListAPIView):
    """``GET /api/v1/stages/<id>/submissions/`` – metadane prac. Plików tędy nie ma i nie będzie.

    Rozwiązanie uczestnika jest jego utworem i materiałem oceny; z systemu wychodzi wyłącznie do
    recenzenta i do koordynatora. Partner dostaje metryczkę: kto (kodem), co, kiedy i w jakim stanie.
    """

    permission_classes = [HasScope(SCOPE_READ_SUBMISSIONS_META)]
    serializer_class = V1SubmissionSerializer

    def get_queryset(self):
        stage = self.stage_in_scope(self.kwargs["stage_id"])
        queryset = Submission.objects.filter(entry__stage=stage).select_related(
            "entry__participant", "problem"
        )
        params = self.request.query_params
        problem = (params.get("problem") or "").strip()
        if problem.isdigit():
            queryset = queryset.filter(problem__number=int(problem))
        submission_status = (params.get("status") or "").strip()
        if submission_status:
            queryset = queryset.filter(status=submission_status)
        return queryset.order_by("entry__participant__public_code", "problem__number", "version")


@extend_schema(tags=[TAG])
class StatsListView(ApiKeyViewMixin, ListAPIView):
    """``GET /api/v1/stats/`` – te same liczby, co publiczna strona ``/statystyki/``.

    Źródłem jest zamrożony snapshot publikacji, więc statystyka nie może pokazać niczego, czego
    nie widać w ogłoszonej tabeli. Odczyt idzie przez tę samą pamięć podręczną (10 minut), co
    strona – partner odpytujący co minutę nie liczy nam tysiąca wierszy na żądanie.
    """

    permission_classes = [HasScope(SCOPE_READ_STATS)]
    serializer_class = V1StatsSerializer

    def get_queryset(self):
        rows = statistics()
        edition_id = self.api_key.edition_id
        if edition_id is None:
            return rows
        labels = set(Edition.objects.filter(pk=edition_id).values_list("year_label", flat=True))
        return [row for row in rows if row.get("edition") in labels]


@extend_schema(tags=[TAG])
class EventCreateView(ApiKeyViewMixin, GenericAPIView):
    """``POST /api/v1/events/`` – dopisanie wydarzenia do linii czasu edycji.

    Regułę rozstrzyga ``apps.competitions.events.create_event`` – ten sam serwis, co formularz
    w panelu. Wpis audytowy powstaje **dwa**: jeden od serwisu (``event.created``) i jeden nasz
    (``apikey.event_created``) z przedrostkiem klucza. Pierwszy mówi, co powstało, drugi – czyj
    program to zrobił, bo klucz nie jest kontem i nie ma go w polu wykonawcy.
    """

    permission_classes = [HasScope(SCOPE_WRITE_EVENTS)]
    serializer_class = V1EventCreateSerializer

    @extend_schema(request=V1EventCreateSerializer, responses={201: V1EventSerializer})
    def post(self, request):
        from apps.competitions.events import EVENT_EDITABLE_FIELDS, create_event
        from apps.competitions.services import current_edition

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        edition = self._edition(data.pop("edition_id", None), current_edition)
        fields = {name: data[name] for name in EVENT_EDITABLE_FIELDS if name in data}
        event = create_event(edition=edition, actor=None, request=request, **fields)
        audit(None, "apikey.event_created", event, {"key": self.api_key.prefix}, request=request)
        payload = {
            "id": event.pk,
            "edition_id": event.edition_id,
            "title": event.title,
            "starts_on": event.starts_on,
            "ends_on": event.ends_on,
            "note": event.note,
            "url": event.url,
            "show_on_timeline": event.show_on_timeline,
        }
        return Response(V1EventSerializer(payload).data, status=http.HTTP_201_CREATED)

    def _edition(self, edition_id, current_edition):
        """Edycja wydarzenia: z klucza, z ciała żądania albo bieżąca – w tej kolejności.

        Klucz przypisany do edycji **wygrywa** i odrzuca niezgodne ``edition_id``: inaczej
        partner zawężony do jednego rocznika dopisywałby wydarzenia do cudzego.
        """
        key_edition_id = self.api_key.edition_id
        if key_edition_id is not None:
            if edition_id is not None and int(edition_id) != key_edition_id:
                raise _not_found("Nie znaleziono edycji.")
            edition_id = key_edition_id
        if edition_id is None:
            edition = current_edition()
            if edition is None:
                raise DomainError(
                    "Nie ustawiono bieżącej edycji – podaj edition_id.",
                    "EDITION_REQUIRED",
                    http.HTTP_400_BAD_REQUEST,
                )
            return edition
        edition = Edition.objects.filter(pk=edition_id).first()
        if edition is None:
            raise _not_found("Nie znaleziono edycji.")
        return edition
