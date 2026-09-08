"""Mapa adresów interfejsu WWW (montowana w korzeniu, po prefiksach ``/api/``)."""

from django.urls import path

from .views import appeals, coordinator, participant, public, reviewer

app_name = "web"

urlpatterns = [
    # --- publiczne ---------------------------------------------------------------------------
    # Korzenia ``/`` tu nie ma: od T-09 obsługuje go ``cms.HomePage`` (Wagtail catch-all na końcu
    # ``config/urls.py``). Wszystkie pozostałe ścieżki ``apps.web`` są dopasowywane wcześniej.
    path("login/", public.LoginView.as_view(), name="login"),
    path("logout/", public.LogoutView.as_view(), name="logout"),
    # Reset hasła. Adres formularza nowego hasła jest krótki (``/reset/…``) celowo: token trafia do
    # listu, a długie adresy bywają łamane przez klienty pocztowe w połowie i przestają być klikalne.
    path("password-reset/", public.PasswordResetView.as_view(), name="password-reset"),
    path("password-reset/sent/", public.PasswordResetSentView.as_view(), name="password-reset-sent"),
    path(
        "reset/<uidb64>/<token>/",
        public.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("reset/done/", public.PasswordResetCompleteView.as_view(), name="password-reset-complete"),
    path("register/", public.RegisterParticipantView.as_view(), name="register"),
    path("register/committee/", public.RegisterCommitteeView.as_view(), name="register-committee"),
    path("results/<int:stage_id>/", public.PublicResultsView.as_view(), name="results"),
    # --- uczestnik ---------------------------------------------------------------------------
    path("me/", participant.MeView.as_view(), name="me"),
    path(
        "me/stages/<int:stage_id>/register/",
        participant.StageRegisterView.as_view(),
        name="stage-register",
    ),
    path(
        "me/stages/<int:stage_id>/problems/<int:number>/upload/",
        participant.ProblemUploadView.as_view(),
        name="problem-upload",
    ),
    path(
        "me/submissions/<int:submission_id>/appeal/",
        participant.AppealCreateView.as_view(),
        name="appeal-create",
    ),
    # --- recenzent ---------------------------------------------------------------------------
    path("review/", reviewer.ReviewListView.as_view(), name="review-list"),
    path("review/<int:pk>/", reviewer.ReviewDetailView.as_view(), name="review-detail"),
    path("review/<int:pk>/draft/", reviewer.ReviewDraftView.as_view(), name="review-draft"),
    path("review/<int:pk>/submit/", reviewer.ReviewSubmitView.as_view(), name="review-submit"),
    # --- koordynator -------------------------------------------------------------------------
    path("coordinator/", coordinator.CoordinatorDashboardView.as_view(), name="coordinator"),
    path(
        "coordinator/stages/<int:stage_id>/close/",
        coordinator.CloseStageView.as_view(),
        name="coordinator-close-stage",
    ),
    path(
        "coordinator/stages/<int:stage_id>/assign/",
        coordinator.AssignReviewersView.as_view(),
        name="coordinator-assign",
    ),
    path(
        "coordinator/moderation/<int:submission_id>/resolve/",
        coordinator.ResolveModerationView.as_view(),
        name="coordinator-resolve",
    ),
    path(
        "coordinator/moderation/<int:submission_id>/assign-third/",
        coordinator.AssignThirdReviewerView.as_view(),
        name="coordinator-assign-third",
    ),
    path(
        "coordinator/committee/<int:pk>/approve/",
        coordinator.ApproveCommitteeMemberView.as_view(),
        name="coordinator-approve",
    ),
    path(
        "coordinator/committee/<int:pk>/verify-district/",
        coordinator.VerifyDistrictView.as_view(),
        name="coordinator-verify-district",
    ),
    path(
        "coordinator/invitations/",
        coordinator.CreateInvitationView.as_view(),
        name="coordinator-invitation",
    ),
    path(
        "coordinator/stages/<int:stage_id>/results/compute/",
        coordinator.ComputeResultsView.as_view(),
        name="coordinator-compute",
    ),
    path(
        "coordinator/stages/<int:stage_id>/results/publish/",
        coordinator.PublishResultsView.as_view(),
        name="coordinator-publish",
    ),
    # --- komisja odwoławcza ------------------------------------------------------------------
    path("appeals/", appeals.AppealsQueueView.as_view(), name="appeals"),
    path("appeals/<int:pk>/decide/", appeals.AppealDecideView.as_view(), name="appeal-decide"),
]
