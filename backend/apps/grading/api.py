"""Widoki API oceniania (prefiks ``/api/grading/``).

Każdy widok deklaruje ``permission_classes`` jawnie, a widoczność wynika z queryseta, nie z
sprawdzenia w ciele metody: recenzent operuje wyłącznie na ``reviews_for_reviewer(jego profil)``,
więc cudza recenzja to 404, a nie 403 – odpowiedź nie może potwierdzać, że dany przydział istnieje.
"""

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status as http
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.accounts.models import CommitteeMember
from apps.accounts.permissions import IsActiveReviewer, IsCoordinator
from apps.competitions.models import Problem, Stage
from apps.competitions.scoping import competition_of, scope_to_competition
from apps.submissions.models import Submission

from .models import ProblemReviewerRule, Review
from .serializers import (
    AssignmentResultSerializer,
    AssignReviewerSerializer,
    AssignReviewersSerializer,
    AssignThirdReviewerSerializer,
    DisputeReviewSerializer,
    FinalGradeSerializer,
    ModerationSubmissionSerializer,
    OverrideFinalGradeSerializer,
    OverrideResultSerializer,
    ProblemReviewerRuleSerializer,
    ProblemRuleCreateSerializer,
    ProblemRuleResultSerializer,
    ResolveModerationSerializer,
    ReviewDraftSerializer,
    ReviewSerializer,
    ReviewSubmitSerializer,
    SetReviewScoreSerializer,
)
from .services import (
    REVIEWER_VERIFIED_ZIP_FILENAME,
    REVIEWER_ZIP_FILENAME,
    active_reviewer_profile,
    add_problem_reviewer_rule,
    assign_reviewer_to_submission,
    assign_reviewers,
    assign_third_reviewer,
    build_reviewer_zip,
    dispute_context,
    moderation_queue,
    override_final_grade,
    remove_problem_reviewer_rule,
    resolve_moderation,
    reviews_for_reviewer,
    revise_review,
    save_draft,
    set_review_score,
    submit_review,
    unassign_reviewer,
)


class ReviewerScopedMixin:
    """Wspólny queryset recenzenta: wyłącznie własne przydziały **w konkursie z żądania**.

    Dwa zawężenia, dwie różne odpowiedzi na dwa różne pytania: konkurs mówi „czyje to dane”,
    przydział – „kto ma je oceniać”. Kolejność jest regułą (§ 3.5) i wykonuje ją
    ``reviews_for_reviewer``.
    """

    permission_classes = [IsActiveReviewer]

    @property
    def competition(self):
        """Konkurs żądania – skrót dla widoku, bo czytają go tu dwie metody."""
        return competition_of(self.request)

    def get_queryset(self):
        return reviews_for_reviewer(
            active_reviewer_profile(self.request.user, self.competition), self.competition
        )

    def get_review(self, pk: int):
        return get_object_or_404(self.get_queryset(), pk=pk)


class MyReviewsView(ReviewerScopedMixin, GenericAPIView):
    """Moje przydziały. Uczestnik jest widoczny wyłącznie jako ``participant_public_code``."""

    serializer_class = ReviewSerializer

    @extend_schema(responses={200: ReviewSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class MyReviewsDownloadView(ReviewerScopedMixin, GenericAPIView):
    """Paczka ZIP ze wszystkimi pracami przydzielonymi zalogowanemu recenzentowi.

    Bliźniak ``GET /review/download/`` z panelu: ta sama reguła zakresu, te same nazwy plików i ten
    sam wpis audytowy – różni się wyłącznie tym, że odpowiedź nie przechodzi przez przeglądarkę
    zalogowaną sesją. Pusta kolejka to ``404 NO_ASSIGNED_SUBMISSIONS``.
    """

    serializer_class = ReviewSerializer

    @extend_schema(responses={(200, "application/zip"): bytes})
    def get(self, request):
        # Ten sam parametr ``students`` (``all``/``verified``), co w panelu – bliźniak nie może
        # umieć mniej niż oryginał. Odmowa ``verified`` przy wyłączonej fladze to ``DomainError``
        # 404, który warstwa API zamienia na odpowiedź z kodem maszynowym.
        from apps.student_status.services import SCOPE_PARAM, wants_verified_only

        verified_only = wants_verified_only(request.query_params.get(SCOPE_PARAM), competition_of(request))
        package = build_reviewer_zip(
            active_reviewer_profile(request.user),
            actor=request.user,
            request=request,
            verified_only=verified_only,
        )
        return FileResponse(
            package.stream,
            as_attachment=True,
            filename=REVIEWER_VERIFIED_ZIP_FILENAME if verified_only else REVIEWER_ZIP_FILENAME,
            content_type="application/zip",
        )


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
            # ``.get`` zamiast indeksu: brak pola znaczy „bez rubryki”, a nie „pusta rubryka”.
            rubric=serializer.validated_data.get("rubric"),
            request=request,
        )
        return Response(ReviewSerializer(review).data)


class ReviewReviseView(ReviewerScopedMixin, GenericAPIView):
    """Poprawienie własnej, już wystawionej oceny – ten sam ładunek, co przy wystawieniu.

    Osobny adres od ``submit/``: tamten zakłada recenzję jeszcze niewystawioną i odmawia
    (``REVIEW_ALREADY_SUBMITTED``), a tu odmowa znaczy coś innego – pracę odebrano, wyniki
    ogłoszono albo ocenę rozstrzygnął ktoś inny. Kto może poprawiać, rozstrzyga queryset
    (cudza recenzja to 404), a co wolno zmienić – serwis.
    """

    serializer_class = ReviewSubmitSerializer

    @extend_schema(request=ReviewSubmitSerializer, responses={200: ReviewSerializer})
    def post(self, request, pk: int):
        review = self.get_review(pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = revise_review(
            review,
            serializer.validated_data["score"],
            serializer.validated_data["comment_internal"],
            serializer.validated_data["comment_for_participant"],
            serializer.validated_data["annotations"],
            rubric=serializer.validated_data.get("rubric"),
            request=request,
        )
        return Response(ReviewSerializer(review).data)


class StageAssignView(GenericAPIView):
    """Przydział recenzentów dla całego etapu – tylko koordynator. Idempotentny."""

    permission_classes = [IsCoordinator]
    serializer_class = AssignReviewersSerializer

    @extend_schema(request=AssignReviewersSerializer, responses={200: AssignmentResultSerializer})
    def post(self, request, pk: int):
        stage = get_object_or_404(
            scope_to_competition(Stage.objects.select_related("edition"), competition_of(request)),
            pk=pk,
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = assign_reviewers(
            stage,
            serializer.validated_data["per_submission"],
            actor=request.user,
            request=request,
        )
        return Response(AssignmentResultSerializer(result).data)


class StageProblemRulesView(GenericAPIView):
    """Reguły „zadanie → recenzent z góry” w obrębie etapu – tylko koordynator.

    Zadanie jest sprawdzane **względem etapu z adresu**: reguła dla zadania z innego etapu byłaby
    cichym przydziałem poza ekranem, na którym koordynator ją tworzył. Niezgodność to 404, a nie
    403 – odpowiedź nie ma potwierdzać istnienia cudzego zadania.
    """

    permission_classes = [IsCoordinator]
    serializer_class = ProblemRuleCreateSerializer

    @extend_schema(request=ProblemRuleCreateSerializer, responses={201: ProblemRuleResultSerializer})
    def post(self, request, pk: int):
        competition = competition_of(request)
        stage = get_object_or_404(scope_to_competition(Stage.objects.all(), competition), pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Zadanie jest sprawdzane względem etapu z adresu, a zakres konkursu idzie osobno: gdyby
        # kiedykolwiek zniknął warunek ``stage=stage``, została by druga bramka, a nie żadna.
        problem = get_object_or_404(
            scope_to_competition(Problem.objects.select_related("stage"), competition),
            pk=serializer.validated_data["problem_id"],
            stage=stage,
        )
        # Recenzent zakresowany tak samo, jak przy przydziale pojedynczej pracy: reguła „to zadanie
        # sprawdza ta osoba” wskazująca członka cudzego komitetu byłaby wpuszczeniem obcego do akt.
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(competition).select_related("user"),
            pk=serializer.validated_data["reviewer_id"],
        )
        result = add_problem_reviewer_rule(problem, reviewer, actor=request.user, request=request)
        return Response(ProblemRuleResultSerializer(result).data, status=http.HTTP_201_CREATED)


class StageProblemRuleDetailView(GenericAPIView):
    """Skasowanie reguły. Recenzje, które z niej powstały, zostają – tak stanowi serwis."""

    permission_classes = [IsCoordinator]
    serializer_class = ProblemReviewerRuleSerializer

    @extend_schema(responses={204: None})
    def delete(self, request, pk: int, rule_id: int):
        rule = get_object_or_404(
            scope_to_competition(
                ProblemReviewerRule.objects.select_related("problem"), competition_of(request)
            ),
            pk=rule_id,
            problem__stage_id=pk,
        )
        remove_problem_reviewer_rule(rule, actor=request.user, request=request)
        return Response(status=http.HTTP_204_NO_CONTENT)


class SubmissionAssignReviewerView(GenericAPIView):
    """Ręczny przydział jednej pracy jednemu recenzentowi (runda ślepa) – tylko koordynator."""

    permission_classes = [IsCoordinator]
    serializer_class = AssignReviewerSerializer

    @extend_schema(request=AssignReviewerSerializer, responses={201: ReviewSerializer})
    def post(self, request, submission_id: int):
        competition = competition_of(request)
        submission = get_object_or_404(
            scope_to_competition(Submission.objects.all(), competition), pk=submission_id
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Recenzent też jest zakresowany: przydział pracy konkursu A osobie z komitetu konkursu B
        # byłby wpuszczeniem obcego do akt, a nie pomyłką w formularzu.
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(competition).select_related("user"),
            pk=serializer.validated_data["reviewer_id"],
        )
        review = assign_reviewer_to_submission(submission, reviewer, actor=request.user, request=request)
        return Response(ReviewSerializer(review).data, status=http.HTTP_201_CREATED)


class ReviewUnassignView(GenericAPIView):
    """Odebranie recenzentowi pracy (→ CANCELLED) – tylko koordynator.

    Działa na przydziale nietkniętym, na szkicu i na recenzji wystawionej. Co blokuje odebranie
    (ogłoszone wyniki, praca zamknięta, ocena rozstrzygnięta przez człowieka) – decyduje serwis.
    """

    permission_classes = [IsCoordinator]
    serializer_class = ReviewSerializer

    @extend_schema(request=None, responses={200: ReviewSerializer})
    def post(self, request, pk: int):
        review = get_object_or_404(
            scope_to_competition(
                Review.objects.select_related("submission", "submission__entry"),
                competition_of(request),
            ),
            pk=pk,
        )
        review = unassign_reviewer(review, actor=request.user, request=request)
        return Response(ReviewSerializer(review).data)


class ReviewScoreView(GenericAPIView):
    """Korekta punktów pojedynczej recenzji przez koordynatora – niezależnie od jej stanu.

    Osobny endpoint od ``reviews/<id>/submit/``: tamten jest dla recenzenta i ma bramkę stanu,
    ten jest narzędziem organizatora i tej bramki świadomie nie ma. Tożsamość aktora rozstrzyga
    klasa uprawnień, a nie treść żądania.
    """

    permission_classes = [IsCoordinator]
    serializer_class = SetReviewScoreSerializer

    @extend_schema(request=SetReviewScoreSerializer, responses={200: ReviewSerializer})
    def post(self, request, pk: int):
        review = get_object_or_404(
            scope_to_competition(
                Review.objects.select_related("submission", "submission__entry"),
                competition_of(request),
            ),
            pk=pk,
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = set_review_score(
            review,
            serializer.validated_data["score"],
            actor=request.user,
            request=request,
            rationale=serializer.validated_data["rationale"],
        )
        return Response(ReviewSerializer(review).data)


class SubmissionFinalGradeView(GenericAPIView):
    """Wpisanie albo korekta oceny końcowej pracy – także pracy bez ani jednej recenzji."""

    permission_classes = [IsCoordinator]
    serializer_class = OverrideFinalGradeSerializer

    @extend_schema(request=OverrideFinalGradeSerializer, responses={200: OverrideResultSerializer})
    def post(self, request, submission_id: int):
        submission = get_object_or_404(
            scope_to_competition(Submission.objects.all(), competition_of(request)), pk=submission_id
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = override_final_grade(
            submission,
            serializer.validated_data["score"],
            rationale=serializer.validated_data["rationale"],
            actor=request.user,
            request=request,
        )
        return Response(OverrideResultSerializer(result).data)


class ModerationListView(GenericAPIView):
    """Kolejka rozjazdów: rozwiązania w MODERATION razem z obiema ocenami rundy 1."""

    permission_classes = [IsCoordinator]
    serializer_class = ModerationSubmissionSerializer

    @extend_schema(responses={200: ModerationSubmissionSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(moderation_queue(competition_of(request)), many=True).data)


class ModerationResolveView(GenericAPIView):
    """Rozstrzygnięcie rozjazdu. Kto i w jakim trybie może rozstrzygać – decyduje serwis."""

    permission_classes = [IsCoordinator | IsActiveReviewer]
    serializer_class = ResolveModerationSerializer

    @extend_schema(request=ResolveModerationSerializer, responses={200: FinalGradeSerializer})
    def post(self, request, submission_id: int):
        submission = get_object_or_404(
            scope_to_competition(Submission.objects.all(), competition_of(request)), pk=submission_id
        )
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
        competition = competition_of(request)
        submission = get_object_or_404(
            scope_to_competition(Submission.objects.all(), competition), pk=submission_id
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer = get_object_or_404(
            CommitteeMember.objects.for_competition(competition).select_related("user"),
            pk=serializer.validated_data["reviewer_id"],
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
