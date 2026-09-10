from django.urls import path

from .api import SchoolSearchView

app_name = "schools"

urlpatterns = [
    path("", SchoolSearchView.as_view(), name="search"),
]
