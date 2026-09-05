"""Widoki API wyników (prefiks ``/api/``).

Każdy widok deklaruje ``permission_classes`` jawnie, a zakres danych wynika z serializera, nie ze
sprawdzenia w ciele metody:

- ``public/results/{stage_id}/`` jest jedynym widokiem bez uwierzytelnienia i czyta **wyłącznie**
  zamrożony snapshot. Brak publikacji to 404 – etap bez ogłoszonych wyników nie istnieje dla
  publiczności, nawet jeśli istnieje w bazie,
- ``me/results/`` filtruje po profilu uczestnika z żądania; etap przed publikacją nie jest
  zwracany, więc uczestnik nie pozna swojej punktacji przed ogłoszeniem,
- oba widoki koordynatora są jawnie za ``IsCoordinator``: to jedyne miejsce, w którym tabela
  wyników wychodzi z danymi osobowymi.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.accounts.permissions import IsCoordinator, IsParticipant
from apps.competitions.models import Stage

from .models import ResultsPublication
from .serializers import (
    MyStageResultSerializer,
    PublicResultsSerializer,
    PublishResultsSerializer,
    StageResultsPreviewSerializer,
)
from .services import compute_stage_results, publish_results, results_for_participant


class PublicStageResultsView(GenericAPIView):
    """Ogłoszona tabela wyników etapu. Bez logowania, wyłącznie ze snapshotu."""

    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]
    serializer_class = PublicResultsSerializer

    def get_queryset(self):
        return ResultsPublication.objects.select_related("stage")

    @extend_schema(responses={200: PublicResultsSerializer})
    def get(self, request, stage_id: int):
        publication = get_object_or_404(self.get_queryset(), stage_id=stage_id)
        return Response(self.get_serializer(publication).data)


class MyResultsView(GenericAPIView):
    """Własne wyniki uczestnika: punkty per zadanie, komentarze i adnotacje publiczne."""

    permission_classes = [IsParticipant]
    serializer_class = MyStageResultSerializer

    @extend_schema(responses={200: MyStageResultSerializer(many=True)})
    def get(self, request):
        data = results_for_participant(request.user)
        return Response(self.get_serializer(data, many=True).data)


class CoordinatorStageMixin:
    """Wspólna baza widoków koordynatora – etap ze ścieżki wraz z progiem i zadaniami."""

    permission_classes = [IsCoordinator]

    def get_stage(self, pk: int) -> Stage:
        return get_object_or_404(Stage.objects.select_related("edition", "qualification_rule"), pk=pk)


class StageResultsComputeView(CoordinatorStageMixin, GenericAPIView):
    """Podgląd pełnej tabeli wyników bez publikacji (koordynator widzi dane osobowe).

    Przeliczenie zapisuje ``StageEntry.total_points`` – to jedyny efekt uboczny podglądu i jest
    zamierzony: suma punktów jest funkcją ocen, więc jej odświeżenie nie jest decyzją. Statusów
    kwalifikacji ani publikacji ten endpoint nie tyka.
    """

    serializer_class = StageResultsPreviewSerializer

    @extend_schema(request=None, responses={200: StageResultsPreviewSerializer})
    def post(self, request, pk: int):
        stage = self.get_stage(pk)
        rows = compute_stage_results(stage)
        payload = {"stage_id": stage.pk, "count": len(rows), "rows": rows}
        return Response(self.get_serializer(payload).data)


class StageResultsPublishView(CoordinatorStageMixin, GenericAPIView):
    """Publikacja wyników etapu: przeliczenie, kwalifikacja i zamrożenie zanonimizowanej tabeli."""

    serializer_class = PublishResultsSerializer

    @extend_schema(request=PublishResultsSerializer, responses={201: PublicResultsSerializer})
    def post(self, request, pk: int):
        stage = self.get_stage(pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        publication = publish_results(
            stage,
            request.user,
            serializer.validated_data["anonymization"],
            request=request,
        )
        return Response(PublicResultsSerializer(publication).data, status=status.HTTP_201_CREATED)
