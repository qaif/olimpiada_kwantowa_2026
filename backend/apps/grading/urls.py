from django.urls import path

from .api import (
    ModerationAssignThirdView,
    ModerationListView,
    ModerationResolveView,
    MyReviewsView,
    ReviewDetailView,
    ReviewSubmitView,
    StageAssignView,
)

app_name = "grading"

urlpatterns = [
    path("reviews/", MyReviewsView.as_view(), name="review-list"),
    path("reviews/<int:pk>/", ReviewDetailView.as_view(), name="review-detail"),
    path("reviews/<int:pk>/submit/", ReviewSubmitView.as_view(), name="review-submit"),
    path("stages/<int:pk>/assign/", StageAssignView.as_view(), name="stage-assign"),
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
