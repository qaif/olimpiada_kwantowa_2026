"""Adresy kreatora pierwszego uruchomienia. Montowane **jedną** linią w ``config/urls.py``.

Trzy wzorce i ani jednego więcej. Przestrzeń nazw ``setup`` jest własna, żeby ``reverse`` kreatora
nie mieszał się z ``web:`` — ten drugi montuje zadanie montażowe wydania i kreator ma od niego
nie zależeć (``/setup/`` musi się otworzyć na instalacji, na której nie ma jeszcze niczego).

Adresy są po polsku, jak reszta adresów publicznych serwisu (``/dokumenty/``, ``/wyniki/``).
"""

from django.urls import path

from apps.tenancy.setup_views import SetupCompetitionView, SetupDoneView, SetupOperatorView

app_name = "setup"

urlpatterns = [
    path("", SetupOperatorView.as_view(), name="operator"),
    path("konkurs/", SetupCompetitionView.as_view(), name="competition"),
    path("gotowe/", SetupDoneView.as_view(), name="done"),
]
