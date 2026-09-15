"""Mapa adresów interfejsu WWW (montowana w korzeniu, po prefiksach ``/api/``)."""

from django.urls import path

from .views import account, appeals, coordinator, coordinator_stages, participant, public, reviewer

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
    path("register/done/", public.RegisterDoneView.as_view(), name="register-done"),
    # Aktywacja konta. ``resend/`` stoi **przed** wzorcem z tokenem: token jest dowolnym napisem
    # bez ukośnika, więc bez tej kolejności „resend” dałoby się wziąć za token.
    path("activate/resend/", public.ActivationResendView.as_view(), name="activate-resend"),
    path("activate/<str:token>/", public.ActivateAccountView.as_view(), name="activate"),
    path("results/<int:stage_id>/", public.PublicResultsView.as_view(), name="results"),
    # --- własne konto (wszystkie role) -------------------------------------------------------
    # ``/me/profile/`` jest przy panelu uczestnika, bo edytuje **profil uczestnika**;
    # ``/account/…`` obsługuje to, co ma każde konto: nazwisko, adres e-mail, usunięcie konta.
    path("me/profile/", account.ParticipantProfileView.as_view(), name="profile"),
    path("account/profile/", account.AccountProfileView.as_view(), name="account-profile"),
    path("account/email/", account.EmailChangeView.as_view(), name="email-change"),
    path(
        "account/email/confirm/<str:token>/",
        account.EmailChangeConfirmView.as_view(),
        name="email-change-confirm",
    ),
    path("account/delete/", account.AccountDeleteView.as_view(), name="account-delete"),
    path("account/deleted/", account.AccountDeletedView.as_view(), name="account-deleted"),
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
    # Rozmowa kwalifikacyjna: zapis idzie po identyfikatorze terminu (uczestnik wybiera termin),
    # rezygnacja – po identyfikatorze etapu (uczestnik ma w etapie dokładnie jeden zapis, więc
    # nie musi wiedzieć, który to termin).
    path(
        "me/interview-slots/<int:slot_id>/book/",
        participant.InterviewBookView.as_view(),
        name="interview-book",
    ),
    path(
        "me/stages/<int:stage_id>/interview/cancel/",
        participant.InterviewCancelView.as_view(),
        name="interview-cancel",
    ),
    # Zgoda na publikację nazwiska – jedyna zgoda, którą uczestnik zmienia sam w panelu.
    path(
        "me/consents/publish-name/",
        participant.ConsentPublishNameView.as_view(),
        name="consent-publish-name",
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
    # Okno rejestracji uczestników – ustawienie edycji, nie etapu, stąd adres bez identyfikatora.
    path(
        "coordinator/registration/",
        coordinator_stages.RegistrationSettingsView.as_view(),
        name="coordinator-registration",
    ),
    # Kalendarz edycji i zadania. ``stages/new/`` stoi **przed** ``stages/<int:stage_id>/…`` tylko
    # z przyzwyczajenia – ``<int:…>`` i tak nie dopasuje słowa „new”.
    path(
        "coordinator/stages/new/",
        coordinator_stages.StageCreateView.as_view(),
        name="coordinator-stage-new",
    ),
    path(
        "coordinator/stages/<int:stage_id>/edit/",
        coordinator_stages.StageEditView.as_view(),
        name="coordinator-stage-edit",
    ),
    path(
        "coordinator/stages/<int:stage_id>/problems/",
        coordinator_stages.StageProblemsView.as_view(),
        name="coordinator-stage-problems",
    ),
    path(
        "coordinator/stages/<int:stage_id>/interviews/",
        coordinator_stages.StageInterviewsView.as_view(),
        name="coordinator-stage-interviews",
    ),
    path(
        "coordinator/interview-slots/<int:pk>/delete/",
        coordinator_stages.InterviewSlotDeleteView.as_view(),
        name="coordinator-interview-slot-delete",
    ),
    path(
        "coordinator/problems/<int:pk>/edit/",
        coordinator_stages.ProblemEditView.as_view(),
        name="coordinator-problem-edit",
    ),
    path(
        "coordinator/problems/<int:pk>/delete/",
        coordinator_stages.ProblemDeleteView.as_view(),
        name="coordinator-problem-delete",
    ),
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
    # Przydziały ręczne: reguły „zadanie → recenzent” i przydział pojedynczej pracy. Adresy akcji
    # idą po przedmiocie operacji (zadanie, reguła, praca, recenzja), a nie po etapie – etap wynika
    # z nich jednoznacznie i to on decyduje, na który ekran wraca przekierowanie.
    path(
        "coordinator/stages/<int:stage_id>/assignments/",
        coordinator.StageAssignmentsView.as_view(),
        name="coordinator-stage-assignments",
    ),
    path(
        "coordinator/problems/<int:problem_id>/reviewer-rules/",
        coordinator.AddProblemRuleView.as_view(),
        name="coordinator-problem-rule-add",
    ),
    path(
        "coordinator/reviewer-rules/<int:pk>/delete/",
        coordinator.RemoveProblemRuleView.as_view(),
        name="coordinator-problem-rule-remove",
    ),
    path(
        "coordinator/submissions/<int:submission_id>/assign-reviewer/",
        coordinator.AssignSubmissionReviewerView.as_view(),
        name="coordinator-submission-assign",
    ),
    path(
        "coordinator/reviews/<int:pk>/unassign/",
        coordinator.UnassignReviewView.as_view(),
        name="coordinator-review-unassign",
    ),
    # Korekta ocen: punkty pojedynczej recenzji i ocena końcowa pracy. Obie akcje wracają na ekran
    # przydziałów i ocen tego etapu, bo tam koordynator widzi skutek zmiany.
    path(
        "coordinator/reviews/<int:pk>/score/",
        coordinator.SetReviewScoreView.as_view(),
        name="coordinator-review-score",
    ),
    path(
        "coordinator/submissions/<int:submission_id>/final-grade/",
        coordinator.OverrideFinalGradeView.as_view(),
        name="coordinator-final-grade",
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
    # Konta oczekujące na aktywację – obejście na czas problemów z dostarczalnością poczty.
    path(
        "coordinator/accounts/<int:pk>/activate/",
        coordinator.ActivateAccountView.as_view(),
        name="coordinator-account-activate",
    ),
    path(
        "coordinator/accounts/<int:pk>/resend-activation/",
        coordinator.ResendActivationView.as_view(),
        name="coordinator-account-resend",
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
