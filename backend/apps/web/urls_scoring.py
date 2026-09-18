"""Adresy ekranów wydania J – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł z tego samego powodu, co ``urls_consents`` i ``urls_documents``: ``apps/web/urls.py``
ma w etapie 2 **jednego** właściciela na wydanie (zadanie „montaż”,
``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a ekrany wydania J powstają równolegle z ekranami dwóch
innych zadań. Montaż polega na rozwinięciu tej listy na **końcu** ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_scoring import urlpatterns as scoring_urlpatterns
    ...
    urlpatterns = [
        ...,
        *scoring_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.

Wzorce etapowe stoją pod ``coordinator/stages/<id>/…``, czyli tam, gdzie stoją wszystkie pozostałe
ekrany etapu; czynności na pojedynczym wierszu (kryterium remisu, rola recenzencka) mają własne
gałęzie z identyfikatorem wiersza, bo etap da się z niego odczytać, a adres ma być krótki na tyle,
żeby dało się go przeczytać z logu.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_scoring, coordinator_teams

urlpatterns = [
    # --- wagi zadań i rozstrzyganie remisów (flaga ``weighted_scoring``) ------------------------
    path(
        "coordinator/stages/<int:stage_id>/weights/",
        coordinator_scoring.ProblemWeightsView.as_view(),
        name="coordinator-stage-weights",
    ),
    path(
        "coordinator/stages/<int:stage_id>/tie-breaks/",
        coordinator_scoring.TieBreakListView.as_view(),
        name="coordinator-stage-tie-breaks",
    ),
    path(
        "coordinator/tie-breaks/<int:pk>/delete/",
        coordinator_scoring.TieBreakDeleteView.as_view(),
        name="coordinator-tie-break-delete",
    ),
    # --- punkty z rozmowy (flaga ``process_editor``) -------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/interview-scores/",
        coordinator_scoring.InterviewScoresView.as_view(),
        name="coordinator-stage-interview-scores",
    ),
    # --- role recenzenckie (flaga ``reviewer_roles``) ------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/reviewer-roles/",
        coordinator_scoring.ReviewerRolesView.as_view(),
        name="coordinator-stage-reviewer-roles",
    ),
    path(
        "coordinator/reviewer-roles/<int:pk>/update/",
        coordinator_scoring.ReviewerRoleUpdateView.as_view(),
        name="coordinator-reviewer-role-update",
    ),
    path(
        "coordinator/reviewer-roles/<int:pk>/delete/",
        coordinator_scoring.ReviewerRoleDeleteView.as_view(),
        name="coordinator-reviewer-role-delete",
    ),
    # --- drużyny (flaga ``team_entries``) -------------------------------------------------------
    path("coordinator/teams/", coordinator_teams.TeamListView.as_view(), name="coordinator-teams"),
    path("coordinator/teams/<int:pk>/", coordinator_teams.TeamDetailView.as_view(), name="coordinator-team"),
    path(
        "coordinator/teams/<int:pk>/members/remove/",
        coordinator_teams.TeamMemberRemoveView.as_view(),
        name="coordinator-team-member-remove",
    ),
    path(
        "coordinator/teams/<int:pk>/captain/",
        coordinator_teams.TeamCaptainView.as_view(),
        name="coordinator-team-captain",
    ),
    path(
        "coordinator/teams/<int:pk>/stage/",
        coordinator_teams.TeamStageEntryView.as_view(),
        name="coordinator-team-stage",
    ),
]
