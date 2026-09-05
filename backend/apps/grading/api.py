"""Widoki API oceniania (prefiks ``/api/grading/``).

Każdy widok deklaruje ``permission_classes`` jawnie, a widoczność wynika z queryseta, nie z
sprawdzenia w ciele metody: recenzent operuje wyłącznie na ``reviews_for_reviewer(jego profil)``,
więc cudza recenzja to 404, a nie 403 – odpowiedź nie może potwierdzać, że dany przydział istnieje.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.accounts.models import CommitteeMember
from apps.accounts.permissions import IsActiveReviewer, IsCoordinator
from apps.competitions.models import Stage
from apps.submissions.models import Submission

from .serializers import (
    AssignmentResultSerializer,
    AssignReviewersSerializer,
    AssignThirdReviewerSerializer,
    DisputeReviewSerializer,
    FinalGradeSerializer,
    ModerationSubmissionSerializer,
    ResolveModerationSerializer,
    ReviewDraftSerializer,
    ReviewSerializer,
    ReviewSubmitSerializer,
)
from .services import (
    active_reviewer_profile,
    assign_reviewers,
    assign_third_reviewer,
    dispute_context,
    moderation_queue,
    resolve_moderation,
    reviews_for_reviewer,
    save_draft,
    submit_review,
)


class ReviewerScopedMixin:
    """Wspólny queryset recenzenta: wyłącznie własne przydziały."""

    permission_classes = [IsActiveReviewer]

    def get_queryset(self):
        return reviews_for_reviewer(active_reviewer_profile(self.request.user))

    def get_review(self, pk: int):
        return get_object_or_404(self.get_queryset(), pk=pk)


class MyReviewsView(ReviewerScopedMixin, GenericAPIView):
    """Moje przydziały. Uczestnik jest widoczny wyłącznie jako ``participant_public_code``."""

    serializer_class = ReviewSerializer

    @extend_schema(responses={200: ReviewSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class ReviewDetailView(ReviewerScopedMixin, GenericAPIView):
    """Podgląd i zapis szkicu jednej recenzji. W rundzie 1 nie ma tu cudzych ocen."""

    serializer_class = ReviewSerializer

    @extend_schema(responses={200: ReviewSerializer})
    def get(self, request, pk: int):
        return Response(self.get_serializer(self.get_review(pk)).data)

    @extend_schema(request=ReviewDraftSerializer, responses={200: ReviewSerializer})
    def patch(self, request, pk: int):
        review = self.get_review(pk)
        draft = ReviewDraftSerializer(data=request.data)
        draft.is_valid(raise_exception=True)
        review = save_draft(review, **draft.validated_data)
        return Response(ReviewSerializer(review).data)


class ReviewSubmitView(ReviewerScopedMixin, GenericAPIView):
    """Wystawienie oceny – rozstrzyga konsensus albo kieruje rozwiązanie do moderacji."""

    serializer_class = ReviewSubmitSerializer

    @extend_schema(request=ReviewSubmitSerializer, responses={200: ReviewSerializer})
    def post(self, request, pk: int):
        review = self.get_review(pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = submit_review(
            review,
            serializer.validated_data["score"],
            serializer.validated_data["comment_internal"],
            serializer.validated_data["comment_for_participant"],
            serializer.validated_data["annotations"],
            request=request,
        )
        return Response(ReviewSerializer(review).data)


class StageAssignView(GenericAPIView):
    """Przydział recenzentów dla całego etapu – tylko koordynator. Idempotentny."""

    permission_classes = [IsCoordinator]
    serializer_class = AssignReviewersSerializer

    @extend_schema(request=AssignReviewersSerializer, responses={200: AssignmentResultSerializer})
    def post(self, request, pk: int):
        stage = get_object_or_404(Stage.objects.select_related("edition"), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = assign_reviewers(
            stage,
            serializer.validated_data["per_submission"],
            actor=request.user,
            request=request,
        )
        return Response(AssignmentResultSerializer(result).data)


class ModerationListView(GenericAPIView):
    """Kolejka rozjazdów: rozwiązania w MODERATION razem z obiema ocenami rundy 1."""

    permission_classes = [IsCoordinator]
    serializer_class = ModerationSubmissionSerializer

    @extend_schema(responses={200: ModerationSubmissionSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(moderation_queue(), many=True).data)


class ModerationResolveView(GenericAPIView):
    """Rozstrzygnięcie rozjazdu. Kto i w jakim trybie może rozstrzygać – decyduje serwis."""

    permission_classes = [IsCoordinator | IsActiveReviewer]
    serializer_class = ResolveModerationSerializer

    @extend_schema(request=ResolveModerationSerializer, responses={200: FinalGradeSerializer})
    def post(self, request, submission_id: int):
        submission = get_object_or_404(Submission, pk=submission_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grade = resolve_moderation(
            submission,
            request.user,
            serializer.validated_data["score"],
            None,
            serializer.validated_data["rationale"],
            request=request,
        )
        return Response(FinalGradeSerializer(grade).data)


class ModerationAssignThirdView(GenericAPIView):
    """Wyznaczenie trzeciego recenzenta (runda rozjemcza) – tylko koordynator."""

    permission_classes = [IsCoordinator]
    serializer_class = AssignThirdReviewerSerializer

    @extend_schema(request=AssignThirdReviewerSerializer, responses={200: ReviewSerializer})
    def post(self, request, submission_id: int):
        submission = get_object_or_404(Submission, pk=submission_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer = get_object_or_404(
            CommitteeMember.objects.select_related("user"), pk=serializer.validated_data["reviewer_id"]
        )
        review = assign_third_reviewer(submission, reviewer, actor=request.user, request=request)
        return Response(ReviewSerializer(review).data)


class ReviewDisputeView(ReviewerScopedMixin, GenericAPIView):
    """Materiał rozjemczy rundy 2: punkty i komentarze wewnętrzne obu ocen rundy 1.

    Widoczność jest podwójnie zawężona: queryset daje wyłącznie własne przydziały (cudza recenzja
    to 404, nie 403), a serwis odmawia dla rundy 1. Odpowiedź nie zawiera tożsamości recenzentów.
    """

    serializer_class = DisputeReviewSerializer

    @extend_schema(responses={200: DisputeReviewSerializer(many=True)})
    def get(self, request, pk: int):
        review = self.get_review(pk)
        return Response(self.get_serializer(dispute_context(review), many=True).data)
