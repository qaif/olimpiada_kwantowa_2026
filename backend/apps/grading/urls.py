from django.urls import path

from .api import (
    ModerationAssignThirdView,
    ModerationListView,
    ModerationResolveView,
    MyReviewsView,
    ReviewDetailView,
    ReviewDisputeView,
    ReviewReviseView,
    ReviewScoreView,
    ReviewSubmitView,
    ReviewUnassignView,
    StageAssignView,
    StageProblemRuleDetailView,
    StageProblemRulesView,
    SubmissionAssignReviewerView,
    SubmissionFinalGradeView,
)

app_name = "grading"

urlpatterns = [
    path("reviews/", MyReviewsView.as_view(), name="review-list"),
    path("reviews/<int:pk>/", ReviewDetailView.as_view(), name="review-detail"),
    path("reviews/<int:pk>/submit/", ReviewSubmitView.as_view(), name="review-submit"),
    # Poprawka własnej oceny stoi obok wystawienia, a nie zamiast niego: to dwie różne czynności
    # z różnymi bramkami, więc i dwa adresy – inaczej odmowa nie mówiłaby, czego dotyczy.
    path("reviews/<int:pk>/revise/", ReviewReviseView.as_view(), name="review-revise"),
    path("reviews/<int:pk>/dispute/", ReviewDisputeView.as_view(), name="review-dispute"),
    # Odebranie pracy (dawniej: cofnięcie przydziału) jest czynnością koordynatora, mimo adresu
    # w gałęzi ``reviews/`` – przedmiotem operacji jest recenzja, a nie praca, i po niej
    # koordynator ją odnajduje.
    path("reviews/<int:pk>/unassign/", ReviewUnassignView.as_view(), name="review-unassign"),
    # Korekta ocen przez koordynatora: punkty pojedynczej recenzji i ocena końcowa pracy.
    path("reviews/<int:pk>/score/", ReviewScoreView.as_view(), name="review-score"),
    path("stages/<int:pk>/assign/", StageAssignView.as_view(), name="stage-assign"),
    path("stages/<int:pk>/problem-rules/", StageProblemRulesView.as_view(), name="stage-problem-rules"),
    path(
        "stages/<int:pk>/problem-rules/<int:rule_id>/",
        StageProblemRuleDetailView.as_view(),
        name="stage-problem-rule-detail",
    ),
    path(
        "submissions/<int:submission_id>/assign/",
        SubmissionAssignReviewerView.as_view(),
        name="submission-assign",
    ),
    path(
        "submissions/<int:submission_id>/final-grade/",
        SubmissionFinalGradeView.as_view(),
        name="submission-final-grade",
    ),
    path("moderation/", ModerationListView.as_view(), name="moderation-list"),
    path(
        "moderation/<int:submission_id>/resolve/",
        ModerationResolveView.as_view(),
        name="moderation-resolve",
    ),
    path(
        "moderation/<int:submission_id>/assign-third/",
        ModerationAssignThirdView.as_view(),
        name="moderation-assign-third",
    ),
]
