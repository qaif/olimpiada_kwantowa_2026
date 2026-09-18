"""Adresy ekranów wpisowego i logistyki – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 **jednego** właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a trzy zadania montażowe wydania K powstają
równolegle. Montaż jest rozwinięciem tej listy na **końcu** ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_fees import urlpatterns as fee_urlpatterns
    ...
    urlpatterns = [
        ...,
        *fee_urlpatterns,
    ]

Dwa obszary w jednym module, choć flagi są dwie (``fees``, ``onsite_logistics``). Powód jest
praktyczny i ten sam, dla którego dwa obszary są jednym zadaniem: montaż wydania K wpina **jedną**
listę, a podział na dwa pliki znaczyłby dwa importy i dwa miejsca do pominięcia. Bramki flagi tu
nie ma – o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404 przy wyłączonej, § 2.1);
mapa adresów zależna od konkursu znaczyłaby ``reverse()`` dający raz adres, a raz
``NoReverseMatch``, czyli pozycję menu, której nie da się sprawdzić testem widoku.

Kolejność wzorców wpisowego jest istotna w jednym miejscu: ``coordinator/fees/register/`` stoi
**przed** ``coordinator/fees/<int:pk>/``. Konwerter ``int`` i tak nie dopasowałby napisu
„register”, więc jest to zabezpieczenie na wypadek zmiany konwertera, a nie warunek działania.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_fees, coordinator_logistics, participant_fees, participant_logistics

urlpatterns = [
    # --- wpisowe: cennik --------------------------------------------------------------------
    path("coordinator/fees/", coordinator_fees.FeeScheduleListView.as_view(), name="coordinator-fees"),
    path(
        "coordinator/fees/register/",
        coordinator_fees.FeeRegisterView.as_view(),
        name="coordinator-fees-register",
    ),
    path(
        "coordinator/fees/register/charge/",
        coordinator_fees.FeeChargeView.as_view(),
        name="coordinator-fees-charge",
    ),
    path(
        "coordinator/fees/register/<int:pk>/reference/",
        coordinator_fees.FeeReferenceView.as_view(),
        name="coordinator-fee-reference",
    ),
    path(
        "coordinator/fees/register/<int:pk>/payment/",
        coordinator_fees.FeePaymentView.as_view(),
        name="coordinator-fee-payment",
    ),
    path(
        "coordinator/fees/register/<int:pk>/exempt/",
        coordinator_fees.FeeExemptView.as_view(),
        name="coordinator-fee-exempt",
    ),
    path(
        "coordinator/fees/register/<int:pk>/waive/",
        coordinator_fees.FeeWaiveView.as_view(),
        name="coordinator-fee-waive",
    ),
    path(
        "coordinator/fees/register/<int:pk>/refund/",
        coordinator_fees.FeeRefundView.as_view(),
        name="coordinator-fee-refund",
    ),
    path(
        "coordinator/fees/register/<int:pk>/document/",
        coordinator_fees.FeeDocumentView.as_view(),
        name="coordinator-fee-document",
    ),
    # --- wpisowe: webhooki dostawców --------------------------------------------------------
    path(
        "coordinator/fees/payments/",
        coordinator_fees.PaymentEndpointsView.as_view(),
        name="coordinator-fees-payments",
    ),
    path(
        "coordinator/fees/payments/<int:pk>/rotate/",
        coordinator_fees.PaymentSecretRotateView.as_view(),
        name="coordinator-fees-payment-rotate",
    ),
    path(
        "coordinator/fees/<int:pk>/",
        coordinator_fees.FeeScheduleEditView.as_view(),
        name="coordinator-fee-schedule",
    ),
    # --- logistyka: miejsca zawodów ---------------------------------------------------------
    path("coordinator/venues/", coordinator_logistics.VenueListView.as_view(), name="coordinator-venues"),
    path(
        "coordinator/venues/special-needs/",
        coordinator_logistics.SpecialNeedsView.as_view(),
        name="coordinator-venues-special-needs",
    ),
    path(
        "coordinator/venues/<int:pk>/",
        coordinator_logistics.VenueEditView.as_view(),
        name="coordinator-venue",
    ),
    # --- logistyka: etap stacjonarny --------------------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/logistics/",
        coordinator_logistics.StageLogisticsView.as_view(),
        name="coordinator-stage-logistics",
    ),
    path(
        "coordinator/stages/<int:stage_id>/logistics/<str:kind>/",
        coordinator_logistics.LogisticsListView.as_view(),
        name="coordinator-stage-logistics-list",
    ),
    path(
        "coordinator/stages/<int:stage_id>/attendance/",
        coordinator_logistics.StageAttendanceView.as_view(),
        name="coordinator-stage-attendance",
    ),
    # --- panel uczestnika -------------------------------------------------------------------
    # Dwa adresy, bo kafle na ``/me/`` mają po jednej czynności: pobranie rachunku i złożenie
    # deklaracji przyjazdu. Obie stoją w gałęzi ``me/``, a nie w panelu koordynatora – rolę
    # sprawdza ``ParticipantRequiredMixin``, a zakres i tak wychodzi z profilu tego konkursu.
    path("me/fees/document/", participant_fees.FeeDocumentView.as_view(), name="participant-fee-document"),
    path(
        "me/stages/<int:stage_id>/arrival/",
        participant_logistics.ArrivalFormView.as_view(),
        name="participant-arrival",
    ),
]
