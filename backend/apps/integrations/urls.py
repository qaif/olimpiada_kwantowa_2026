"""Adresy publicznego API integracji. Montowane pod ``/api/v1/`` w ``config/urls.py``.

Numer wersji jest w **prefiksie**, a nie w nagłówku ani w parametrze: adres da się wpisać do
konfiguracji partnera, wkleić do zgłoszenia i porównać w logu proxy. Wersja w nagłówku byłaby
niewidoczna dokładnie tam, gdzie szuka się przyczyny awarii integracji.

Kolejność wzorców nie ma tu znaczenia – żaden nie jest przedrostkiem innego. Zasoby etapu stoją
pod ``stages/<id>/…``, a nie pod ``editions/<id>/stages/<id>/…``: identyfikator etapu jest
globalny, więc druga ścieżka powtarzałaby informację, którą i tak trzeba by sprawdzić.
"""

from django.urls import path

from .api import (
    CapabilitiesView,
    EditionListView,
    EditionStageListView,
    EventCreateView,
    StageParticipantListView,
    StageResultsView,
    StageSubmissionListView,
    StatsListView,
)
from .inbound import PaymentWebhookView

app_name = "integrations"

urlpatterns = [
    path("", CapabilitiesView.as_view(), name="capabilities"),
    path("editions/", EditionListView.as_view(), name="editions"),
    path("editions/<int:edition_id>/stages/", EditionStageListView.as_view(), name="edition-stages"),
    path(
        "stages/<int:stage_id>/participants/",
        StageParticipantListView.as_view(),
        name="stage-participants",
    ),
    path("stages/<int:stage_id>/results/", StageResultsView.as_view(), name="stage-results"),
    path("stages/<int:stage_id>/submissions/", StageSubmissionListView.as_view(), name="stage-submissions"),
    path("stats/", StatsListView.as_view(), name="stats"),
    path("events/", EventCreateView.as_view(), name="events"),
    # Jedyny adres przyjmujący ruch **do nas** (§ 1.5.1). Stoi pod istniejącym prefiksem, więc
    # ``config/urls.py`` nie jest edytowany – lista adresów zamrożonych w § 0.2 zostaje bez zmian.
    path("payments/<slug:provider>/", PaymentWebhookView.as_view(), name="payment-webhook"),
]
