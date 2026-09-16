from django.urls import path

from .api import (
    MySubmissionsView,
    StageLockForReviewView,
    SubmissionCreateView,
    SubmissionDownloadView,
    SubmissionLockForReviewView,
)

app_name = "submissions"

urlpatterns = [
    path(
        "stages/<int:stage_id>/problems/<int:number>/submissions/",
        SubmissionCreateView.as_view(),
        name="submission-create",
    ),
    # Blokada do oceny: dla całego etapu i dla pojedynczej pracy. Adresy idą po przedmiocie
    # operacji, tak jak reszta akcji koordynatora.
    path(
        "stages/<int:stage_id>/lock-for-review/",
        StageLockForReviewView.as_view(),
        name="stage-lock-for-review",
    ),
    path(
        "submissions/<int:pk>/lock-for-review/",
        SubmissionLockForReviewView.as_view(),
        name="submission-lock-for-review",
    ),
    path("me/submissions/", MySubmissionsView.as_view(), name="my-submissions"),
    path("submissions/<int:pk>/download/", SubmissionDownloadView.as_view(), name="submission-download"),
]
