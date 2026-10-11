"""Adresy okien czasowych (TZ-01) – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń ``web:``).

Przestrzeń ``web:``, a nie własna: menu koordynatora (``apps.web.coordinator_nav``) rozpoznaje
podświetlenie po nazwie adresu w tej przestrzeni, tak samo jak przy delegacjach i regionach.
Bramki flagi konkursu tu nie ma – rozstrzyga ją widok (404), żeby ``reverse()`` dawał ten sam
adres w każdym konkursie.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    # --- koordynator ---------------------------------------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/windows/",
        views.StageWindowsView.as_view(),
        name="coordinator-stage-windows",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/create/",
        views.PlanCreateView.as_view(),
        name="coordinator-stage-windows-create",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/settings/",
        views.PlanSettingsView.as_view(),
        name="coordinator-stage-windows-settings",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/delete-plan/",
        views.PlanDeleteView.as_view(),
        name="coordinator-stage-windows-delete-plan",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/add/",
        views.WindowAddView.as_view(),
        name="coordinator-stage-windows-add",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/exceptions/",
        views.ParticipantExceptionView.as_view(),
        name="coordinator-stage-windows-exception",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/<int:window_id>/move/",
        views.WindowMoveView.as_view(),
        name="coordinator-stage-windows-move",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/<int:window_id>/delete/",
        views.WindowDeleteView.as_view(),
        name="coordinator-stage-windows-delete",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/delegations/<int:delegation_id>/",
        views.DelegationAssignView.as_view(),
        name="coordinator-stage-windows-delegation",
    ),
    path(
        "coordinator/stages/<int:stage_id>/windows/countries/<int:region_id>/",
        views.CountryTimezoneView.as_view(),
        name="coordinator-stage-windows-country",
    ),
    # --- opiekun drużyny -----------------------------------------------------------------------
    path("delegation/time-windows/", views.LeaderWindowsView.as_view(), name="delegation-time-windows"),
    path(
        "delegation/time-windows/<int:pk>/timezone/",
        views.LeaderStudentTimezoneView.as_view(),
        name="delegation-time-windows-timezone",
    ),
]
