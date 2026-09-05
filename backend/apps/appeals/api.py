"""Widoki API reklamacji (prefiks ``/api/``).

Każdy widok deklaruje ``permission_classes`` jawnie, a widoczność wynika z queryseta, nie ze
sprawdzenia w ciele metody:

- uczestnik operuje na ``Submission.objects.for_user`` – cudze rozwiązanie to 404, a nie 403,
  bo odpowiedź nie może potwierdzać, że dane zgłoszenie istnieje,
- komisja odwoławcza operuje na ``appeals_queue(profil)`` – reklamacja z konfliktem interesów
  jest dla niej 404 z tego samego powodu.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.accounts.permissions import IsAppealsCommittee, IsParticipant
from apps.submissions.models import Submission

from .models import Appeal
from .serializers import (
    AppealDecideSerializer,
    AppealFileSerializer,
    AppealQueueSerializer,
    MyAppealSerializer,
)
from .services import (
    appeals_committee_profile,
    appeals_for_participant,
    appeals_queue,
    decide_appeal,
    file_appeal,
)


class SubmissionAppealView(GenericAPIView):
    """Złożenie reklamacji na własne rozwiązanie. Okno i stan zgłoszenia egzekwuje serwis."""

    permission_classes = [IsParticipant]
    serializer_class = AppealFileSerializer

    def get_queryset(self):
        return Submission.objects.for_user(self.request.user).select_related(
            "entry", "entry__participant", "entry__stage"
        )

    @extend_schema(request=AppealFileSerializer, responses={201: MyAppealSerializer})
    def post(self, request, pk: int):
        submission = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appeal = file_appeal(request.user, submission, serializer.validated_data["argument"], request=request)
        return Response(MyAppealSerializer(appeal).data, status=status.HTTP_201_CREATED)


class MyAppealsView(GenericAPIView):
    """Własne reklamacje uczestnika wraz z rozstrzygnięciem, jeśli już zapadło."""

    permission_classes = [IsParticipant]
    serializer_class = MyAppealSerializer

    def get_queryset(self):
        return appeals_for_participant(self.request.user)

    @extend_schema(responses={200: MyAppealSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class AppealsCommitteeMixin:
    """Wspólny queryset komisji: reklamacje do rozpatrzenia bez konfliktu interesów."""

    permission_classes = [IsAppealsCommittee]

    def get_member(self):
        return appeals_committee_profile(self.request.user)

    def get_queryset(self):
        return appeals_queue(self.get_member())


class AppealQueueView(AppealsCommitteeMixin, GenericAPIView):
    """Kolejka komisji: argument, obie oceny rundy 1, ocena uzgodniona i link do pliku."""

    serializer_class = AppealQueueSerializer

    @extend_schema(responses={200: AppealQueueSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class AppealDecideView(AppealsCommitteeMixin, GenericAPIView):
    """Rozstrzygnięcie reklamacji.

    Reklamacja jest tu wyszukiwana wśród **wszystkich** oczekujących, nie tylko własnych – konflikt
    interesów ma dać 403 ``CONFLICT_OF_INTEREST`` (kryterium 6), a nie 404, bo autor recenzji zna
    numer sprawy i musi dostać jednoznaczną odmowę.
    """

    serializer_class = AppealDecideSerializer

    @extend_schema(request=AppealDecideSerializer, responses={200: MyAppealSerializer})
    def post(self, request, pk: int):
        appeal = get_object_or_404(Appeal.objects.select_related("submission"), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decide_appeal(
            appeal,
            self.get_member(),
            serializer.validated_data["status"],
            serializer.validated_data["new_score"],
            serializer.validated_data["justification"],
            request=request,
        )
        appeal.refresh_from_db()
        return Response(MyAppealSerializer(appeal).data)
