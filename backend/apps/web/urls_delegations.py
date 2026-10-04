"""Adresy delegacji krajowych (DEL-01) – panel opiekuna drużyny, zaproszenie i ekran koordynatora.

Osobny moduł z tego samego powodu, co ``urls_regions``: ``apps/web/urls.py`` rozwija tę listę na
końcu ``urlpatterns``. Bramki trybu konkursu tu nie ma – rozstrzyga ją widok (404 poza trybem
``DELEGATIONS``), żeby ``reverse()`` dawał ten sam adres w każdym konkursie.

Stałe ścieżki (``accept/done/``, ``students/add/``, ``invite/``, ``export.csv``) stoją **przed**
wzorcami z parametrem – ta sama umowa, co przy ``zaproszenie/dziekujemy/``.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_delegations, delegation

urlpatterns = [
    # --- opiekun drużyny ---------------------------------------------------------------------
    path("delegation/", delegation.DelegationDashboardView.as_view(), name="delegation"),
    path("delegation/students/add/", delegation.StudentAddView.as_view(), name="delegation-student-add"),
    path(
        "delegation/students/<int:pk>/edit/",
        delegation.StudentEditView.as_view(),
        name="delegation-student-edit",
    ),
    path(
        "delegation/students/<int:pk>/delete/",
        delegation.StudentDeleteView.as_view(),
        name="delegation-student-delete",
    ),
    path(
        "delegation/students/<int:pk>/resend/",
        delegation.StudentResendView.as_view(),
        name="delegation-student-resend",
    ),
    path(
        "delegation/accept/done/",
        delegation.LeaderInvitationDoneView.as_view(),
        name="delegation-accept-done",
    ),
    path(
        "delegation/accept/<str:token>/",
        delegation.LeaderInvitationView.as_view(),
        name="delegation-accept",
    ),
    path(
        "delegation/accept/<str:token>/logout/",
        delegation.LeaderLogoutForInvitationView.as_view(),
        name="delegation-accept-logout",
    ),
    # --- koordynator ---------------------------------------------------------------------------
    path(
        "coordinator/delegations/",
        coordinator_delegations.DelegationListView.as_view(),
        name="coordinator-delegations",
    ),
    path(
        "coordinator/delegations/invite/",
        coordinator_delegations.LeaderInviteView.as_view(),
        name="coordinator-delegations-invite",
    ),
    path(
        "coordinator/delegations/export.csv",
        coordinator_delegations.DelegationExportView.as_view(),
        name="coordinator-delegations-export",
    ),
    path(
        "coordinator/delegations/invitations/<int:pk>/resend/",
        coordinator_delegations.InvitationResendView.as_view(),
        name="coordinator-delegation-invitation-resend",
    ),
    path(
        "coordinator/delegations/invitations/<int:pk>/revoke/",
        coordinator_delegations.InvitationRevokeView.as_view(),
        name="coordinator-delegation-invitation-revoke",
    ),
    path(
        "coordinator/delegations/leaders/<int:pk>/remove/",
        coordinator_delegations.LeaderRemoveView.as_view(),
        name="coordinator-delegation-leader-remove",
    ),
    path(
        "coordinator/delegations/<int:pk>/",
        coordinator_delegations.DelegationDetailView.as_view(),
        name="coordinator-delegation",
    ),
]
