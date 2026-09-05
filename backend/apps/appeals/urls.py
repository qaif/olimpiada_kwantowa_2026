from django.urls import path

from .api import AppealDecideView, AppealQueueView, MyAppealsView, SubmissionAppealView

app_name = "appeals"

urlpatterns = [
    path("submissions/<int:pk>/appeal/", SubmissionAppealView.as_view(), name="submission-appeal"),
    path("me/appeals/", MyAppealsView.as_view(), name="my-appeals"),
    path("appeals/", AppealQueueView.as_view(), name="appeal-list"),
    path("appeals/<int:pk>/decide/", AppealDecideView.as_view(), name="appeal-decide"),
]
