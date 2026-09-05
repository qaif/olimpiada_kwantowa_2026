"""Widoki API rozwiązań (prefiks ``/api/``).

Każdy widok deklaruje ``permission_classes`` jawnie. Reguły domenowe są w ``services.py``.
Zasada dostępu: cudze rozwiązanie to 404, nie 403 – odpowiedź nie może potwierdzać, że dane
zgłoszenie w ogóle istnieje.
"""

from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.permissions import IsActiveReviewer, IsCoordinator, IsParticipant
from apps.competitions.models import Stage
from apps.core.api import DomainError

from .models import Submission
from .serializers import SubmissionGroupSerializer, SubmissionSerializer, SubmissionUploadSerializer
from .services import create_submission, grouped_submissions_for_user
from .storage import get_submission_storage


def _safe_download_name(name: str) -> str:
    """Nazwa do ``Content-Disposition``: sam plik, bez ścieżek i bez znaków łamiących nagłówek."""
    base = (name or "rozwiazanie").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in base if char.isprintable() and char not in '"\r\n')
    return cleaned.strip() or "rozwiazanie"


class SubmissionCreateView(GenericAPIView):
    """Upload rozwiązania konkretnego zadania. Deadline egzekwowany w serwisie, po stronie serwera."""

    permission_classes = [IsParticipant]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "upload"
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = SubmissionUploadSerializer

    @extend_schema(request=SubmissionUploadSerializer, responses={201: SubmissionSerializer})
    def post(self, request, stage_id: int, number: int):
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Uczestnik (a więc i StageEntry) bierze się wyłącznie z request.user – brak IDOR.
        submission = create_submission(
            user=request.user,
            stage=stage,
            problem_number=number,
            upload=serializer.validated_data["file"],
        )
        return Response(SubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)


class MySubmissionsView(GenericAPIView):
    """Własne rozwiązania uczestnika: per zadanie najnowsza wersja plus historia."""

    permission_classes = [IsParticipant]
    serializer_class = SubmissionGroupSerializer

    @extend_schema(responses={200: SubmissionGroupSerializer(many=True)})
    def get(self, request):
        groups = grouped_submissions_for_user(request.user)
        return Response(self.get_serializer(groups, many=True).data)


class SubmissionDownloadView(GenericAPIView):
    """Pobranie pliku: 302 na presigned URL (S3) albo bezpośrednie wysłanie (backend lokalny).

    Widoczność wynika z ``Submission.objects.for_user`` – cudze zgłoszenie daje 404, także dla
    recenzenta bez przydziału (``grading.Review``). Plik nieprzeskanowany albo zainfekowany może
    pobrać wyłącznie jego właściciel: recenzent i koordynator dostają go dopiero po ``CLEAN``.
    """

    permission_classes = [IsParticipant | IsCoordinator | IsActiveReviewer]
    serializer_class = SubmissionSerializer

    def get_queryset(self):
        return Submission.objects.for_user(self.request.user).select_related("entry", "entry__participant")

    @extend_schema(responses={302: OpenApiResponse(description="Przekierowanie na presigned URL")})
    def get(self, request, pk: int):
        submission = get_object_or_404(self.get_queryset(), pk=pk)
        submission_file = submission.latest_file
        if submission_file is None:
            raise DomainError("Zgłoszenie nie ma pliku.", "FILE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
        participant = getattr(request.user, "participant", None)
        is_owner = participant is not None and submission.entry.participant_id == participant.pk
        if not submission_file.is_clean and not is_owner:
            raise DomainError(
                "Plik nie przeszedł jeszcze skanu antywirusowego.",
                "FILE_NOT_CLEAN",
                status.HTTP_403_FORBIDDEN,
            )
        storage = get_submission_storage()
        url = storage.presigned_get_url(submission_file.object_key)
        if url:
            return HttpResponseRedirect(url)
        return FileResponse(
            storage.open(submission_file.object_key),
            as_attachment=True,
            filename=_safe_download_name(submission_file.original_name),
            content_type=submission_file.mime,
        )
