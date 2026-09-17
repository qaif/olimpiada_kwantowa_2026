from django.urls import path

from .api import CitySearchView, SchoolSearchView

app_name = "schools"

urlpatterns = [
    # Krok „Miejscowość” stoi **przed** wzorcem pustym, bo ten drugi jest w tym module wzorcem
    # najogólniejszym – odwrotna kolejność i tak by zadziałała (ścieżki są rozłączne), ale czyta
    # się ją jak pułapkę na następną zmianę.
    path("cities/", CitySearchView.as_view(), name="cities"),
    path("", SchoolSearchView.as_view(), name="search"),
]
