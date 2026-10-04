"""Adresy logistyki finału (LOG-01) – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń ``web``).

Wszystko pod istniejącymi przedrostkami (``/delegation/`` i ``/coordinator/``), żeby kontrakt tras
CMS-a (``djcms_contract/app_routes.json``) nie musiał się zmieniać. Ekran obsługi rejestracji stoi
pod ``/coordinator/logistics/checkin/`` choć obsługa nie musi być koordynatorem – o dostępie
rozstrzyga przydział (``access.can_check_in``), a nie przedrostek adresu. Bramki trybu konkursu
tu nie ma – rozstrzyga ją widok (404), żeby ``reverse()`` dawał ten sam adres w każdym konkursie.
"""

from __future__ import annotations

from django.urls import path

from . import views, views_letters

urlpatterns = [
    # --- opiekun drużyny -------------------------------------------------------------------------
    path("delegation/logistics/", views.LeaderDashboardView.as_view(), name="delegation-logistics"),
    path(
        "delegation/logistics/guests/add/",
        views.LeaderGuestAddView.as_view(),
        name="delegation-logistics-guest-add",
    ),
    path(
        "delegation/logistics/members/<int:pk>/",
        views.LeaderMemberView.as_view(),
        name="delegation-logistics-member",
    ),
    path(
        "delegation/logistics/members/<int:pk>/photo/",
        views.LeaderPhotoView.as_view(),
        name="delegation-logistics-photo",
    ),
    path(
        "delegation/logistics/members/<int:pk>/guest/",
        views.LeaderGuestEditView.as_view(),
        name="delegation-logistics-guest-edit",
    ),
    path(
        "delegation/logistics/members/<int:pk>/guest/remove/",
        views.LeaderGuestRemoveView.as_view(),
        name="delegation-logistics-guest-remove",
    ),
    path(
        "delegation/logistics/members/<int:pk>/health-consent/withdraw/",
        views.LeaderHealthWithdrawView.as_view(),
        name="delegation-logistics-health-withdraw",
    ),
    path(
        "delegation/logistics/letters/<int:pk>/",
        views.LeaderLetterView.as_view(),
        name="delegation-logistics-letter",
    ),
    # --- obsługa rejestracji ------------------------------------------------------------------------
    path("coordinator/logistics/checkin/", views.CheckinView.as_view(), name="onsite-checkin"),
    path(
        "coordinator/logistics/checkin/<str:token>/",
        views.CheckinMemberView.as_view(),
        name="onsite-checkin-member",
    ),
    path(
        "coordinator/logistics/checkin/<str:token>/photo/",
        views.CheckinPhotoView.as_view(),
        name="onsite-checkin-photo",
    ),
    # --- koordynator i oficer logistyki ---------------------------------------------------------------
    path("coordinator/logistics/", views.OverviewView.as_view(), name="coordinator-onsite"),
    path("coordinator/logistics/settings/", views.SettingsView.as_view(), name="coordinator-onsite-settings"),
    path("coordinator/logistics/access/", views.AccessGrantView.as_view(), name="coordinator-onsite-access"),
    path(
        "coordinator/logistics/access/<int:pk>/revoke/",
        views.AccessRevokeView.as_view(),
        name="coordinator-onsite-access-revoke",
    ),
    path(
        "coordinator/logistics/checkpoints/add/",
        views.CheckpointAddView.as_view(),
        name="coordinator-onsite-checkpoint-add",
    ),
    path(
        "coordinator/logistics/checkpoints/<int:pk>/delete/",
        views.CheckpointDeleteView.as_view(),
        name="coordinator-onsite-checkpoint-delete",
    ),
    path(
        "coordinator/logistics/reminders/", views.RemindersView.as_view(), name="coordinator-onsite-reminders"
    ),
    path("coordinator/logistics/members/", views.MembersView.as_view(), name="coordinator-onsite-members"),
    path(
        "coordinator/logistics/members/<int:pk>/",
        views.OfficerMemberView.as_view(),
        name="coordinator-onsite-member",
    ),
    path(
        "coordinator/logistics/members/<int:pk>/photo/",
        views.OfficerPhotoView.as_view(),
        name="coordinator-onsite-member-photo",
    ),
    path(
        "coordinator/logistics/members/<int:pk>/room/",
        views.OfficerMemberActionView.as_view(action="room"),
        name="coordinator-onsite-member-room",
    ),
    path(
        "coordinator/logistics/members/<int:pk>/badge/",
        views.OfficerMemberActionView.as_view(action="badge"),
        name="coordinator-onsite-member-badge",
    ),
    path(
        "coordinator/logistics/members/<int:pk>/remove-guest/",
        views.OfficerMemberActionView.as_view(action="remove-guest"),
        name="coordinator-onsite-member-remove-guest",
    ),
    path(
        "coordinator/logistics/members/<int:pk>/health-consent/withdraw/",
        views.OfficerMemberActionView.as_view(action="health-withdraw"),
        name="coordinator-onsite-member-health-withdraw",
    ),
    path(
        "coordinator/logistics/delegations/<int:pk>/guests/add/",
        views.OfficerGuestAddView.as_view(),
        name="coordinator-onsite-guest-add",
    ),
    path("coordinator/logistics/travel/", views.TravelView.as_view(), name="coordinator-onsite-travel"),
    path("coordinator/logistics/rooming/", views.RoomingView.as_view(), name="coordinator-onsite-rooming"),
    path(
        "coordinator/logistics/rooming/<int:pk>/delete/",
        views.RoomDeleteView.as_view(),
        name="coordinator-onsite-room-delete",
    ),
    path("coordinator/logistics/dietary/", views.DietaryView.as_view(), name="coordinator-onsite-dietary"),
    path("coordinator/logistics/tshirts/", views.TshirtsView.as_view(), name="coordinator-onsite-tshirts"),
    path(
        "coordinator/logistics/export/<slug:kind>.csv",
        views.ExportView.as_view(),
        name="coordinator-onsite-export",
    ),
    path("coordinator/logistics/letters/", views.LettersView.as_view(), name="coordinator-onsite-letters"),
    path(
        "coordinator/logistics/letters/<int:pk>/",
        views.LetterPdfView.as_view(),
        name="coordinator-onsite-letter",
    ),
    path("coordinator/logistics/badges.pdf", views.BadgesView.as_view(), name="coordinator-onsite-badges"),
    # --- VISA-01: wnioski o listy zapraszające, decyzje, unieważnienie, weryfikacja publiczna ------------
    path(
        "delegation/logistics/letters/",
        views_letters.LeaderLettersView.as_view(),
        name="delegation-logistics-letters",
    ),
    path(
        "delegation/logistics/letter-requests/<int:pk>/withdraw/",
        views_letters.LeaderRequestWithdrawView.as_view(),
        name="delegation-logistics-letter-request-withdraw",
    ),
    path(
        "coordinator/logistics/letter-requests/",
        views_letters.LetterRequestsView.as_view(),
        name="coordinator-onsite-letter-requests",
    ),
    path(
        "coordinator/logistics/letter-requests.csv",
        views_letters.LetterRequestsExportView.as_view(),
        name="coordinator-onsite-letter-requests-export",
    ),
    path(
        "coordinator/logistics/letter-requests/approve/",
        views_letters.LetterRequestsApproveView.as_view(),
        name="coordinator-onsite-letter-requests-approve",
    ),
    path(
        "coordinator/logistics/letter-requests/reject/",
        views_letters.LetterRequestsRejectView.as_view(),
        name="coordinator-onsite-letter-requests-reject",
    ),
    path(
        "coordinator/logistics/letters/<int:pk>/revoke/",
        views_letters.LetterRevokeView.as_view(),
        name="coordinator-onsite-letter-revoke",
    ),
    # Publiczna weryfikacja listu – nowy pierwszy segment ``visa`` (``RESERVED_SLUGS``, kontrakt djcms).
    # Stała ścieżka formularza przed wzorcem z kodem.
    path("visa/verify/", views_letters.VisaVerifyFormView.as_view(), name="visa-verify"),
    path("visa/verify/<str:code>/", views_letters.VisaVerifyView.as_view(), name="visa-verify-code"),
]
