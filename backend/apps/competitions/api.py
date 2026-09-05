"""Widoki API zawodów (prefiks ``/api/competitions/``).

Każdy widok deklaruje ``permission_classes`` jawnie. Widoki tylko orkiestrują – reguły domenowe
są w ``services.py``, kształt odpowiedzi w ``serializers.py``.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.accounts.permissions import IsParticipant
from apps.core.api import DomainError

from .models import Stage
from .serializers import CurrentEditionSerializer, StageEntrySerializer
from .services import current_edition, current_stage, entries_for_user, register_for_stage


class CurrentEditionView(GenericAPIView):
    """Publiczny odczyt bieżącej edycji: etapy i zadania etapu bieżącego. Bez danych osobowych."""

    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]
    serializer_class = CurrentEditionSerializer

    @extend_schema(responses={200: CurrentEditionSerializer})
    def get(self, request):
        edition = current_edition()
        if edition is None:
            raise DomainError(
                "Nie ustawiono bieżącej edycji.", "NO_CURRENT_EDITION", status.HTTP_404_NOT_FOUND
            )
        now = timezone.now()
        stage = current_stage(edition, now)
        context = {**self.get_serializer_context(), "stage": stage, "now": now}
        return Response(CurrentEditionSerializer(edition, context=context).data)


class StageRegisterView(GenericAPIView):
    """Rejestracja zalogowanego uczestnika do etapu eliminacyjnego."""

    permission_classes = [IsParticipant]
    serializer_class = StageEntrySerializer

    @extend_schema(request=None, responses={201: StageEntrySerializer})
    def post(self, request, pk: int):
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=pk)
        # Uczestnik bierze się z request.user, nigdy z body – brak IDOR.
        entry = register_for_stage(request.user.participant, stage)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)


class MyEntriesView(GenericAPIView):
    """Wpisy zalogowanego uczestnika do etapów."""

    permission_classes = [IsParticipant]
    serializer_class = StageEntrySerializer

    def get_queryset(self):
        return entries_for_user(self.request.user)

    @extend_schema(responses={200: StageEntrySerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)
