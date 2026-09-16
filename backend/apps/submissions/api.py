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

from apps.accounts.permissions import (
    IsActiveReviewer,
    IsAppealsCommittee,
    IsCoordinator,
    IsParticipant,
)
from apps.competitions.models import Stage
from apps.core.api import DomainError

from .models import Submission
from .serializers import (
    LockForReviewResultSerializer,
    SubmissionGroupSerializer,
    SubmissionSerializer,
    SubmissionUploadSerializer,
)
from .services import (
    create_submission,
    grouped_submissions_for_user,
    lock_for_review,
    lock_submission_for_review,
)
from .storage import get_submission_storage


def _safe_download_name(name: str) -> str:
    """Nazwa do ``Content-Disposition``: sam plik, bez ścieżek i bez znaków łamiących nagłówek."""
    base = (name or "rozwiazanie").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in base if char.isprintable() and char not in '"\r\n')
    return cleaned.strip() or "rozwiazanie"


def _extension(submission_file) -> str:
    """Rozszerzenie pliku brane z ``object_key`` (klucz jest generowany, więc jest bezpieczny)."""
    tail = submission_file.object_key.rsplit(".", 1)
    candidate = tail[1].lower() if len(tail) == 2 else ""
    if not candidate:
        original = _safe_download_name(submission_file.original_name).rsplit(".", 1)
        candidate = original[1].lower() if len(original) == 2 else ""
    return "".join(char for char in candidate if char.isalnum())[:10] or "dat"


def anonymous_download_name(submission, submission_file) -> str:
    """Nazwa pliku dla każdego, kto nie jest autorem rozwiązania.

    Ocenianie jest ślepe, a ``original_name`` pochodzi od uczestnika i regularnie zawiera nazwisko
    albo szkołę („Jan_Kowalski_LO5.pdf”). Recenzent, komisja odwoławcza i koordynator dostają więc
    nazwę zbudowaną wyłącznie z pseudonimu (``public_code``), numeru zadania i wersji.
    """
    return (
        f"{submission.entry.participant.public_code}"
        f"-z{submission.problem.number}-v{submission.version}.{_extension(submission_file)}"
    )


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
            request=request,
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


class StageLockForReviewView(GenericAPIView):
    """Blokada oddanych prac etapu do oceny **bez** zamykania etapu – tylko koordynator.

    Odpowiednik przycisku „Zablokuj oddane prace do oceny” z panelu. Idempotentny: powtórzenie
    zwraca ``locked: 0``, bo nie ma już czego blokować. Zamknięcie etapu jest osobną czynnością
    (i osobnym przyciskiem) – tutaj okno uploadu zostaje otwarte.
    """

    permission_classes = [IsCoordinator]
    serializer_class = LockForReviewResultSerializer

    @extend_schema(request=None, responses={200: LockForReviewResultSerializer})
    def post(self, request, stage_id: int):
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)
        locked = lock_for_review(stage, actor=request.user, request=request)
        return Response(LockForReviewResultSerializer({"locked": locked}).data)


class SubmissionLockForReviewView(GenericAPIView):
    """Blokada jednej wskazanej pracy do oceny – tylko koordynator.

    Odmowy mają kody maszynowe: ``NOT_LATEST_VERSION`` (jest nowsza wersja tej pracy) oraz
    ``SUBMISSION_NOT_LOCKABLE`` (praca nie jest w stanie „oddane” – np. trwa skan albo jest już
    w ocenie). Rozstrzyga o nich serwis, nie ten widok.
    """

    permission_classes = [IsCoordinator]
    serializer_class = SubmissionSerializer

    @extend_schema(request=None, responses={200: SubmissionSerializer})
    def post(self, request, pk: int):
        submission = get_object_or_404(Submission, pk=pk)
        locked = lock_submission_for_review(submission, actor=request.user, request=request)
        return Response(SubmissionSerializer(locked).data)


class SubmissionDownloadView(GenericAPIView):
    """Pobranie pliku: 302 na presigned URL (S3) albo bezpośrednie wysłanie (backend lokalny).

    Widoczność wynika z ``Submission.objects.for_user`` – cudze zgłoszenie daje 404, także dla
    recenzenta bez przydziału (``grading.Review``) i dla członka komisji odwoławczej, który jest
    w konflikcie interesów albo patrzy na rozwiązanie bez reklamacji. Plik nieprzeskanowany albo
    zainfekowany może pobrać wyłącznie jego właściciel: pozostałe role dostają go dopiero po ``CLEAN``.

    Rola komisji odwoławczej jest tu wymieniona, bo ``GET /api/appeals/`` podaje jej ``download_url``
    rozpatrywanej pracy (T-06) – bez tego rozszerzenie ``for_user`` o reklamacje nie miałoby żadnego
    konsumenta, a komisja nie mogłaby zobaczyć rozwiązania, które ma ocenić.
    """

    permission_classes = [IsParticipant | IsCoordinator | IsActiveReviewer | IsAppealsCommittee]
    serializer_class = SubmissionSerializer

    def get_queryset(self):
        return Submission.objects.for_user(self.request.user).select_related(
            "entry", "entry__participant", "problem"
        )

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
        # Nazwa pliku też jest daną osobową: właściciel dostaje swoją, każdy inny – anonimową.
        if is_owner:
            filename = _safe_download_name(submission_file.original_name)
            disposition = None
        else:
            filename = anonymous_download_name(submission, submission_file)
            disposition = f'attachment; filename="{filename}"'
        storage = get_submission_storage()
        url = storage.presigned_get_url(submission_file.object_key, content_disposition=disposition)
        if url:
            return HttpResponseRedirect(url)
        return FileResponse(
            storage.open(submission_file.object_key),
            as_attachment=True,
            filename=filename,
            content_type=submission_file.mime,
        )
