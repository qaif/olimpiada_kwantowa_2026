from django.urls import path

from .api import (
    MyResultsView,
    PublicStageResultsView,
    StageResultsComputeView,
    StageResultsPublishView,
)

app_name = "results"

urlpatterns = [
    path("public/results/<int:stage_id>/", PublicStageResultsView.as_view(), name="public-results"),
    path("me/results/", MyResultsView.as_view(), name="my-results"),
    path(
        "stages/<int:pk>/results/compute/",
        StageResultsComputeView.as_view(),
        name="stage-results-compute",
    ),
    path(
        "stages/<int:pk>/results/publish/",
        StageResultsPublishView.as_view(),
        name="stage-results-publish",
    ),
]
