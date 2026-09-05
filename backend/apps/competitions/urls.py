from django.urls import path

from .api import CurrentEditionView, MyEntriesView, StageRegisterView

app_name = "competitions"

urlpatterns = [
    path("editions/current/", CurrentEditionView.as_view(), name="edition-current"),
    path("stages/<int:pk>/register/", StageRegisterView.as_view(), name="stage-register"),
    path("me/entries/", MyEntriesView.as_view(), name="my-entries"),
]
