from django.urls import path

from .api import MySubmissionsView, SubmissionCreateView, SubmissionDownloadView

app_name = "submissions"

urlpatterns = [
    path(
        "stages/<int:stage_id>/problems/<int:number>/submissions/",
        SubmissionCreateView.as_view(),
        name="submission-create",
    ),
    path("me/submissions/", MySubmissionsView.as_view(), name="my-submissions"),
    path("submissions/<int:pk>/download/", SubmissionDownloadView.as_view(), name="submission-download"),
]
