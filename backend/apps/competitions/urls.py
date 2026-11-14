from django.urls import path

from .api import (
    CurrentEditionView,
    MyEntriesView,
    ProblemAttachmentView,
    ProblemStatementView,
    StageRegisterView,
)

app_name = "competitions"

urlpatterns = [
    path("editions/current/", CurrentEditionView.as_view(), name="edition-current"),
    path("stages/<int:pk>/register/", StageRegisterView.as_view(), name="stage-register"),
    path("me/entries/", MyEntriesView.as_view(), name="my-entries"),
    path("problems/<int:pk>/statement/", ProblemStatementView.as_view(), name="problem-statement"),
    # Załączniki zadania (PUB-02) – ta sama bramka, co treść; ``pk`` to zadanie (bramka nadzoru).
    path(
        "problems/<int:pk>/attachments/<int:attachment_id>/",
        ProblemAttachmentView.as_view(),
        name="problem-attachment",
    ),
]
