"""Mapa adresów interfejsu WWW (montowana w korzeniu, po prefiksach ``/api/``)."""

from django.urls import path

from .views import (
    account,
    appeals,
    coordinator,
    coordinator_accounts,
    coordinator_events,
    coordinator_messages,
    coordinator_reports,
    coordinator_stages,
    participant,
    participant_tools,
    public,
    reviewer,
    reviewer_tools,
)

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
    # Ekrany panelu wyłącznie do odczytu: informacja zwrotna po publikacji, kalendarz osobisty
    # (także jako plik ``.ics``) i archiwum materiałów. ``me/calendar.ics`` jest osobnym adresem,
    # a nie parametrem ``?format=``: kalendarze subskrybują adres, a nie zapytanie z parametrem,
    # i część z nich rozpoznaje plik po rozszerzeniu, zanim spojrzy na nagłówek typu.
    path(
        "me/stages/<int:stage_id>/feedback/",
        participant_tools.ParticipantFeedbackView.as_view(),
        name="participant-feedback",
    ),
    path("me/calendar/", participant_tools.ParticipantCalendarView.as_view(), name="participant-calendar"),
    path(
        "me/calendar.ics",
        participant_tools.ParticipantCalendarIcsView.as_view(),
        name="participant-calendar-ics",
    ),
    path("me/archive/", participant_tools.ParticipantArchiveView.as_view(), name="participant-archive"),
    # --- recenzent ---------------------------------------------------------------------------
    path("review/", reviewer.ReviewListView.as_view(), name="review-list"),
    # Paczka ZIP z własnymi pracami. Stoi przed adresem szczegółowym dla czytelności –
    # ``<int:pk>`` i tak nie dopasuje słowa „download”.
    path("review/download/", reviewer.ReviewQueueDownloadView.as_view(), name="review-download"),
    path("review/<int:pk>/", reviewer.ReviewDetailView.as_view(), name="review-detail"),
    path("review/<int:pk>/draft/", reviewer.ReviewDraftView.as_view(), name="review-draft"),
    path("review/<int:pk>/submit/", reviewer.ReviewSubmitView.as_view(), name="review-submit"),
    path("review/<int:pk>/revise/", reviewer.ReviewReviseView.as_view(), name="review-revise"),
    # Porównanie ocen po odsłonięciu i wątek notatek przy pracy. Adresy idą po recenzji, a nie po
    # pracy: recenzent ma dostęp do **swojego przydziału**, więc cudza praca jest 404 z tego samego
    # queryseta, co reszta panelu.
    path("review/<int:pk>/compare/", reviewer_tools.ReviewCompareView.as_view(), name="review-compare"),
    path("review/<int:pk>/notes/", reviewer_tools.ReviewNoteCreateView.as_view(), name="review-note-add"),
    # Wzorcówka zadania stoi w gałęzi ``review/``, choć dotyczy zadania: czyta ją komitet, a nie
    # publiczność, i to jest jedyna droga do tego pliku (prywatny storage, bez publicznego adresu).
    path(
        "review/problems/<int:pk>/model-solution/",
        reviewer_tools.ProblemModelSolutionView.as_view(),
        name="problem-model-solution",
    ),
    # --- koordynator -------------------------------------------------------------------------
    path("coordinator/", coordinator.CoordinatorDashboardView.as_view(), name="coordinator"),
    # Okno rejestracji uczestników – ustawienie edycji, nie etapu, stąd adres bez identyfikatora.
    path(
        "coordinator/registration/",
        coordinator_stages.RegistrationSettingsView.as_view(),
        name="coordinator-registration",
    ),
    # Wydarzenia linii czasu. Sąsiadują z kalendarzem etapów, bo to ta sama czynność – układanie
    # terminów edycji – tylko dla tej części kalendarza, której system nie egzekwuje.
    path(
        "coordinator/events/",
        coordinator_events.EventListView.as_view(),
        name="coordinator-events",
    ),
    path(
        "coordinator/events/new/",
        coordinator_events.EventCreateView.as_view(),
        name="coordinator-event-new",
    ),
    path(
        "coordinator/events/<int:pk>/edit/",
        coordinator_events.EventEditView.as_view(),
        name="coordinator-event-edit",
    ),
    path(
        "coordinator/events/<int:pk>/delete/",
        coordinator_events.EventDeleteView.as_view(),
        name="coordinator-event-delete",
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
        "coordinator/stages/<int:stage_id>/scale/",
        coordinator_stages.StageScaleView.as_view(),
        name="coordinator-stage-scale",
    ),
    path(
        "coordinator/stages/<int:stage_id>/problems/",
        coordinator_stages.StageProblemsView.as_view(),
        name="coordinator-stage-problems",
    ),
    # Paczka ZIP z pracami etapu: GET – całość albo jedno zadanie (``?problem=``), POST – wiersze
    # zaznaczone w tabeli przydziałów. Jeden adres, bo to jedna czynność w trzech rozmiarach.
    path(
        "coordinator/stages/<int:stage_id>/download/",
        coordinator.StageDownloadView.as_view(),
        name="coordinator-stage-download",
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
    # Blokada do oceny stoi obok zamknięcia etapu, bo to jego łagodniejszy wariant: prace wchodzą
    # do oceniania, a okno uploadu zostaje otwarte.
    path(
        "coordinator/stages/<int:stage_id>/lock-for-review/",
        coordinator.LockStageForReviewView.as_view(),
        name="coordinator-lock-for-review",
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
        "coordinator/submissions/<int:submission_id>/lock-for-review/",
        coordinator.LockSubmissionForReviewView.as_view(),
        name="coordinator-submission-lock",
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
    # Zaproszenia e-mailem: jeden jednorazowy kod na adres, wysyłany listem zamiast dyktowany.
    path(
        "coordinator/invitations/send/",
        coordinator.SendInvitationsView.as_view(),
        name="coordinator-invitations-send",
    ),
    path(
        "coordinator/invitations/<int:pk>/resend/",
        coordinator.ResendInvitationView.as_view(),
        name="coordinator-invitation-resend",
    ),
    path(
        "coordinator/invitations/<int:pk>/revoke/",
        coordinator.RevokeInvitationView.as_view(),
        name="coordinator-invitation-revoke",
    ),
    # Konta wszystkich ról: lista, edycja, blokada, usunięcie. Adres bez identyfikatora stoi przed
    # adresami szczegółowymi wyłącznie dla czytelności – ``<int:pk>`` i tak nie dopasuje pustego
    # segmentu.
    path(
        "coordinator/accounts/",
        coordinator_accounts.CoordinatorAccountsView.as_view(),
        name="coordinator-accounts",
    ),
    path(
        "coordinator/accounts/<int:pk>/",
        coordinator_accounts.CoordinatorAccountEditView.as_view(),
        name="coordinator-account-edit",
    ),
    path(
        "coordinator/accounts/<int:pk>/delete/",
        coordinator_accounts.CoordinatorAccountDeleteView.as_view(),
        name="coordinator-account-delete",
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
    # --- narzędzia koordynatora ---------------------------------------------------------------
    # Cztery ekrany odczytu (postęp, eksport, audyt, symulacja) i jeden z wysyłką komunikatów.
    # Adresy etapowe stoją przy etapie, a nie w osobnej gałęzi ``/coordinator/tools/…``: „postęp
    # oceniania” i „symulacja” są pytaniami o konkretny etap i wraca się do nich z jego karty.
    path(
        "coordinator/stages/<int:stage_id>/progress/",
        coordinator_reports.StageProgressView.as_view(),
        name="coordinator-stage-progress",
    ),
    path(
        "coordinator/stages/<int:stage_id>/progress/remind/",
        coordinator_reports.RemindReviewersView.as_view(),
        name="coordinator-remind-reviewers",
    ),
    path(
        "coordinator/stages/<int:stage_id>/simulation/",
        coordinator_reports.StageSimulationView.as_view(),
        name="coordinator-stage-simulation",
    ),
    path(
        "coordinator/stages/<int:stage_id>/simulation/apply/",
        coordinator_reports.ApplyQualificationRuleView.as_view(),
        name="coordinator-stage-rule-apply",
    ),
    path(
        "coordinator/messages/",
        coordinator_messages.CoordinatorMessagesView.as_view(),
        name="coordinator-messages",
    ),
    path(
        "coordinator/audit/",
        coordinator_reports.AuditBrowserView.as_view(),
        name="coordinator-audit",
    ),
    # Spis eksportów i sam plik. Rodzaj i format są segmentami adresu, a nie parametrami zapytania:
    # adres pliku ma dać się zapisać i powtórzyć, a obie wartości pochodzą z zamkniętych list
    # (nieznana daje 404). Etap dla eksportów etapowych jedzie jako ``?stage=<id>``, bo ten sam
    # widok obsługuje też eksport całej edycji, który etapu nie ma.
    path("coordinator/export/", coordinator_reports.ExportIndexView.as_view(), name="coordinator-export"),
    path(
        "coordinator/export/<str:kind>/<str:fmt>/",
        coordinator_reports.ExportDownloadView.as_view(),
        name="coordinator-export-download",
    ),
    # --- komisja odwoławcza ------------------------------------------------------------------
    path("appeals/", appeals.AppealsQueueView.as_view(), name="appeals"),
    path("appeals/<int:pk>/decide/", appeals.AppealDecideView.as_view(), name="appeal-decide"),
]
