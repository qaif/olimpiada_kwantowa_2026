"""Mapa adresów interfejsu WWW (montowana w korzeniu, po prefiksach ``/api/``)."""

from django.urls import path

from .views import (
    account,
    appeals,
    certificates,
    coordinator,
    coordinator_accounts,
    coordinator_announcements,
    coordinator_events,
    coordinator_issues,
    coordinator_members,
    coordinator_messages,
    coordinator_pages,
    coordinator_participants,
    coordinator_problem_detail,
    coordinator_quality,
    coordinator_reports,
    coordinator_rodo,
    coordinator_stages,
    coordinator_support,
    guardian,
    participant,
    participant_extras,
    participant_tools,
    public,
    reviewer,
    reviewer_extras,
    reviewer_tools,
    supervisor,
    support,
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
    # Rejestracja opiekuna szkolnego jest **otwarta** (bez kodu zaproszenia): samo konto nie daje
    # wglądu w niczyje dane – panel pokazuje wyłącznie uczniów, którzy sami wpisali ten adres.
    path("register/supervisor/", supervisor.RegisterSupervisorView.as_view(), name="register-supervisor"),
    path("register/done/", public.RegisterDoneView.as_view(), name="register-done"),
    # Aktywacja konta. ``resend/`` stoi **przed** wzorcem z tokenem: token jest dowolnym napisem
    # bez ukośnika, więc bez tej kolejności „resend” dałoby się wziąć za token.
    path("activate/resend/", public.ActivationResendView.as_view(), name="activate-resend"),
    path("activate/<str:token>/", public.ActivateAccountView.as_view(), name="activate"),
    path("results/<int:stage_id>/", public.PublicResultsView.as_view(), name="results"),
    # Zgoda opiekuna składana bez konta – uprawnieniem jest podpisany token w adresie
    # (``apps.accounts.guardian``). Adres jest krótki i polski, bo trafia do listu, który czyta
    # rodzic, a nie do nawigacji serwisu. Ekran podziękowania stoi **przed** wzorcem z tokenem:
    # token jest dowolnym napisem bez ukośnika, więc bez tej kolejności „dziekujemy” dałoby się
    # wziąć za token (ta sama pułapka, co przy ``activate/resend/`` wyżej).
    path("zgoda/dziekujemy/", guardian.GuardianConsentDoneView.as_view(), name="guardian-consent-done"),
    path("zgoda/<str:token>/", guardian.GuardianConsentView.as_view(), name="guardian-consent"),
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
    # Paczka z własnymi danymi (art. 20 RODO). GET, bo niczego nie zmienia – to odczyt własnych
    # danych; limit częstotliwości jest przy eksporcie (``apps.accounts.data_export``).
    path("account/export/", account.AccountExportView.as_view(), name="account-export"),
    path("account/delete/", account.AccountDeleteView.as_view(), name="account-delete"),
    path("account/deleted/", account.AccountDeletedView.as_view(), name="account-deleted"),
    # Język interfejsu i tryb wysokiego kontrastu. Bez logowania, bo to ustawienie
    # **przeglądającego**, a nie uprawnienie konta – gość czytający regulamin ma prawo włączyć
    # kontrast tak samo jak zalogowany uczestnik (``apps.accounts.preferences``).
    path(
        "account/preferences/",
        participant_extras.PreferencesView.as_view(),
        name="account-preferences",
    ),
    # --- zgłoszenia i pomoc (wszystkie role, także bez konta) --------------------------------
    # ``new/`` i ``sent/`` stoją **przed** wzorcem z identyfikatorem – ``<int:pk>`` i tak nie
    # dopasuje słowa, ale kolejność mówi, co jest wejściem, a co szczegółem.
    path("support/new/", support.SupportTicketCreateView.as_view(), name="support-new"),
    path("support/sent/", support.SupportTicketSentView.as_view(), name="support-sent"),
    path("support/", support.SupportTicketListView.as_view(), name="support"),
    path("support/<int:pk>/", support.SupportTicketDetailView.as_view(), name="support-detail"),
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
    # Ten sam zapis wywołany z **listy wyboru** w panelu: jeden formularz, termin w polu
    # ``slot_id``. Adres z identyfikatorem w ścieżce (powyżej) zostaje – jest w linkach
    # wysyłanych z listu, a zmiana kształtu ekranu nie może ich unieważnić.
    path(
        "me/interview/choose/",
        participant.InterviewChooseView.as_view(),
        name="interview-choose",
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
    # Prośba o zgodę opiekuna (pierwsza i każda następna – „wyślij ponownie” to ta sama czynność).
    path(
        "me/guardian/",
        participant_extras.GuardianRequestView.as_view(),
        name="guardian-request",
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
    # Własne dyplomy i zaświadczenia. Lista i pobranie, nic więcej – dokumenty wystawia komitet.
    path(
        "me/certificates/",
        certificates.ParticipantCertificatesView.as_view(),
        name="participant-certificates",
    ),
    path(
        "me/certificates/<int:pk>/",
        certificates.ParticipantCertificateDownloadView.as_view(),
        name="participant-certificate-download",
    ),
    # --- opiekun szkolny ---------------------------------------------------------------------
    # Panel jest wyłącznie do czytania; jedyny zapis to oświadczenie o udziale szkoły w edycji.
    path("supervisor/", supervisor.SupervisorDashboardView.as_view(), name="supervisor"),
    path(
        "supervisor/participation/",
        supervisor.ConfirmParticipationView.as_view(),
        name="supervisor-participation",
    ),
    path(
        "supervisor/certificates/<int:pk>/",
        supervisor.SupervisorCertificateDownloadView.as_view(),
        name="supervisor-certificate-download",
    ),
    # Publiczna weryfikacja dokumentu po kodzie z papieru. Adres jest po polsku i krótki, bo
    # bywa przepisywany z dyplomu ręcznie; nie wydaje danych osobowych bez zgody na publikację.
    path("dyplomy/<str:code>/", certificates.CertificateVerifyView.as_view(), name="certificate-verify"),
    # --- recenzent ---------------------------------------------------------------------------
    path("review/", reviewer.ReviewListView.as_view(), name="review-list"),
    # Paczka ZIP z własnymi pracami. Stoi przed adresem szczegółowym dla czytelności –
    # ``<int:pk>`` i tak nie dopasuje słowa „download”.
    path("review/download/", reviewer.ReviewQueueDownloadView.as_view(), name="review-download"),
    # Szablony komentarzy recenzenta. Adresy są **bez** identyfikatora recenzji, bo szablon nie
    # należy do pracy: pisze się go przy jednej, a używa przy dwudziestu następnych. Stoją przed
    # ``review/<int:pk>/`` dla czytelności – ``<int:pk>`` i tak nie dopasuje słowa „snippets”.
    path("review/snippets/", reviewer_extras.SnippetCreateView.as_view(), name="review-snippet-add"),
    path(
        "review/snippets/<int:pk>/delete/",
        reviewer_extras.SnippetDeleteView.as_view(),
        name="review-snippet-delete",
    ),
    path("review/<int:pk>/", reviewer.ReviewDetailView.as_view(), name="review-detail"),
    path("review/<int:pk>/draft/", reviewer.ReviewDraftView.as_view(), name="review-draft"),
    path("review/<int:pk>/submit/", reviewer.ReviewSubmitView.as_view(), name="review-submit"),
    path("review/<int:pk>/revise/", reviewer.ReviewReviseView.as_view(), name="review-revise"),
    # Porównanie ocen po odsłonięciu i wątek notatek przy pracy. Adresy idą po recenzji, a nie po
    # pracy: recenzent ma dostęp do **swojego przydziału**, więc cudza praca jest 404 z tego samego
    # queryseta, co reszta panelu.
    path("review/<int:pk>/compare/", reviewer_tools.ReviewCompareView.as_view(), name="review-compare"),
    path("review/<int:pk>/notes/", reviewer_tools.ReviewNoteCreateView.as_view(), name="review-note-add"),
    # Pomiar czasu pracy, uwaga do linii kodu i zgłoszenie problemu z pracą – trzy narzędzia, z
    # których żadne nie zmienia oceny, wszystkie po identyfikatorze **recenzji** (czyli po własnym
    # przydziale: cudza recenzja jest 404 z tego samego queryseta, co reszta panelu).
    path(
        "review/<int:pk>/heartbeat/",
        reviewer_extras.ReviewHeartbeatView.as_view(),
        name="review-heartbeat",
    ),
    path(
        "review/<int:pk>/line-note/",
        reviewer_extras.ReviewLineNoteView.as_view(),
        name="review-line-note",
    ),
    path(
        "review/<int:pk>/issues/",
        reviewer_extras.ReviewIssueCreateView.as_view(),
        name="review-issue-add",
    ),
    # Wzorcówka zadania stoi w gałęzi ``review/``, choć dotyczy zadania: czyta ją komitet, a nie
    # publiczność, i to jest jedyna droga do tego pliku (prywatny storage, bez publicznego adresu).
    path(
        "review/problems/<int:pk>/model-solution/",
        reviewer_tools.ProblemModelSolutionView.as_view(),
        name="problem-model-solution",
    ),
    # --- koordynator -------------------------------------------------------------------------
    path("coordinator/", coordinator.CoordinatorDashboardView.as_view(), name="coordinator"),
    # Wyszukiwarka panelu: jedno pole na uczestników, komisję, zadania, etapy i zgłoszenia.
    # Stoi w menu, więc jest dostępna z każdego ekranu panelu.
    path(
        "coordinator/search/",
        coordinator_pages.CoordinatorSearchView.as_view(),
        name="coordinator-search",
    ),
    # Trzy ekrany, które wyprowadziły się z pulpitu (apps/web/views/coordinator_pages.py).
    # Czynności zostały tam, gdzie były – te adresy wyłącznie pokazują.
    path(
        "coordinator/committee/",
        coordinator_pages.CoordinatorCommitteeView.as_view(),
        name="coordinator-committee",
    ),
    path(
        "coordinator/activations/",
        coordinator_pages.CoordinatorActivationsView.as_view(),
        name="coordinator-activations",
    ),
    path(
        "coordinator/moderation/",
        coordinator_pages.CoordinatorModerationView.as_view(),
        name="coordinator-moderation",
    ),
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
    # Karta zadania: wszystko o jednym zadaniu (treść, skala, rubryka, reguły, prace, statystyki)
    # i czynności, które z tego wynikają. Adres bez przyrostka jest kartą, bo to ekran **główny**
    # zadania – ``edit/`` i ``delete/`` są jego czynnościami, a nie odwrotnie.
    path(
        "coordinator/problems/<int:pk>/",
        coordinator_problem_detail.ProblemCardView.as_view(),
        name="coordinator-problem",
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
    # Czynności zbiorcze idą po **etapie**, a nie po pracy: przedmiotem operacji jest zaznaczenie
    # w tabeli etapu, a listy identyfikatorów i tak nie da się zapisać w adresie.
    path(
        "coordinator/stages/<int:stage_id>/assignments/bulk/",
        coordinator.BulkAssignmentActionView.as_view(),
        name="coordinator-assignments-bulk",
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
    # Komisja: spis ludzi, którzy pracują, i karta jednej osoby. Gałąź jest ``members/``, a nie
    # ``committee/``, bo ``committee/`` zajmują od dawna **czynności** na członku (zatwierdzenie,
    # województwo) – lista pod tym samym korzeniem sugerowałaby, że jest ich stroną nadrzędną.
    # Adres bez identyfikatora stoi przed adresem karty wyłącznie dla czytelności: ``<int:pk>``
    # i tak nie dopasuje pustego segmentu.
    path(
        "coordinator/members/",
        coordinator_members.CommitteeMembersView.as_view(),
        name="coordinator-members",
    ),
    path(
        "coordinator/members/<int:pk>/",
        coordinator_members.CommitteeMemberCardView.as_view(),
        name="coordinator-member",
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
    # Karta uczestnika: wszystko o jednej osobie w jednym miejscu (dane, zgody, etapy, prace,
    # oceny, wyniki, reklamacje, audyt) plus czynności celujące w istniejące widoki-akcje.
    # Klucz jest kluczem **profilu uczestnika**, a nie konta: karta opisuje udział w zawodach,
    # a konto bez profilu (recenzent, opiekun) żadnego udziału nie ma.
    path(
        "coordinator/participants/<int:pk>/",
        coordinator_participants.CoordinatorParticipantView.as_view(),
        name="coordinator-participant",
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
    # Paczka z danymi cudzego konta (art. 20 RODO wykonany rękami organizatora – uczestnik prosi
    # listem albo przez telefon). POST, a nie GET jak przy własnym eksporcie: wydanie cudzych
    # danych jest decyzją, a nie odczytem, i ma zostawić ślad zrobiony świadomie.
    path(
        "coordinator/accounts/<int:pk>/export/",
        coordinator_accounts.CoordinatorAccountExportView.as_view(),
        name="coordinator-account-export",
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
    # Wyniki etapu: stan publikacji plus dwa przyciski. Wejście na adres niczego nie przelicza –
    # przeliczenie zapisuje sumy punktów wpisów, więc jest czynnością (POST), a nie otwarciem strony.
    path(
        "coordinator/stages/<int:stage_id>/results/",
        coordinator_pages.CoordinatorStageResultsView.as_view(),
        name="coordinator-stage-results",
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
    # --- jakość oceniania i dokumenty --------------------------------------------------------
    # Trzy ekrany etapu czytane po ocenianiu, przed posiedzeniem komitetu: kalibracja recenzentów,
    # podobieństwa rozwiązań i wystawianie dyplomów. Stoją przy etapie, tak samo jak postęp
    # i symulacja, bo każdy z nich jest pytaniem o **ten** etap.
    path(
        "coordinator/stages/<int:stage_id>/calibration/",
        coordinator_quality.StageCalibrationView.as_view(),
        name="coordinator-stage-calibration",
    ),
    path(
        "coordinator/stages/<int:stage_id>/similarity/",
        coordinator_quality.StageSimilarityView.as_view(),
        name="coordinator-stage-similarity",
    ),
    # ``recompute`` stoi przed wzorcem z identyfikatorem pary wyłącznie dla czytelności –
    # ``<int:pair_id>`` i tak nie dopasuje słowa.
    path(
        "coordinator/stages/<int:stage_id>/similarity/recompute/",
        coordinator_quality.RecomputeSimilarityView.as_view(),
        name="coordinator-similarity-recompute",
    ),
    path(
        "coordinator/stages/<int:stage_id>/similarity/<int:pair_id>/",
        coordinator_quality.SimilarityPairView.as_view(),
        name="coordinator-similarity-pair",
    ),
    path(
        "coordinator/stages/<int:stage_id>/similarity/<int:pair_id>/report/",
        coordinator_quality.ReportSimilarityView.as_view(),
        name="coordinator-similarity-report",
    ),
    # Kwalifikacja ręczna idzie po identyfikatorze **wpisu**, a nie etapu: decyzja dotyczy jednego
    # uczestnika w jednym etapie, a etap wynika z wpisu jednoznacznie.
    path(
        "coordinator/entries/<int:entry_id>/manual-qualification/",
        coordinator_quality.ManualQualificationView.as_view(),
        name="coordinator-manual-qualification",
    ),
    path(
        "coordinator/stages/<int:stage_id>/certificates/",
        coordinator_quality.StageCertificatesView.as_view(),
        name="coordinator-stage-certificates",
    ),
    path(
        "coordinator/stages/<int:stage_id>/certificates/issue/",
        coordinator_quality.IssueCertificateView.as_view(),
        name="coordinator-certificate-issue",
    ),
    path(
        "coordinator/stages/<int:stage_id>/certificates/issue-all/",
        coordinator_quality.IssueAllCertificatesView.as_view(),
        name="coordinator-certificates-all",
    ),
    path(
        "coordinator/certificates/<int:pk>/download/",
        coordinator_quality.CertificateDownloadView.as_view(),
        name="coordinator-certificate-download",
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
    # Kolejka zgłoszeń od uczestników i recenzentów. Adres bez identyfikatora stoi przed adresem
    # szczegółowym wyłącznie dla czytelności – ``<int:pk>`` nie dopasuje pustego segmentu.
    path(
        "coordinator/support/",
        coordinator_support.CoordinatorSupportView.as_view(),
        name="coordinator-support",
    ),
    path(
        "coordinator/support/<int:pk>/",
        coordinator_support.CoordinatorSupportDetailView.as_view(),
        name="coordinator-support-detail",
    ),
    # Komunikaty organizatora (baner na każdej stronie serwisu). Jeden adres na listę, dodanie
    # i edycję: komunikat ma sześć pól, a pisze się go wtedy, gdy liczy się czas.
    path(
        "coordinator/announcements/",
        coordinator_announcements.CoordinatorAnnouncementsView.as_view(),
        name="coordinator-announcements",
    ),
    # --- RODO ---------------------------------------------------------------------------------
    # Retencja danych (plan i ręczne uruchomienie) oraz rejestr czynności przetwarzania.
    # Oba ekrany są bez identyfikatora: dotyczą całego serwisu, a nie pojedynczej edycji.
    path(
        "coordinator/retention/",
        coordinator_rodo.RetentionView.as_view(),
        name="coordinator-retention",
    ),
    path(
        "coordinator/processing-register/",
        coordinator_rodo.ProcessingRegisterView.as_view(),
        name="coordinator-processing-register",
    ),
    # Zgłoszenia problemów z pracami. Adres jest bez etapu, bo ekran obejmuje całą edycję, a etap
    # jest filtrem (``?stage=<id>``) – problem z pracą nie czeka na to, aż koordynator trafi na
    # właściwą kartę. Akcje idą po identyfikatorze zgłoszenia, czyli po przedmiocie operacji.
    path(
        "coordinator/issues/",
        coordinator_issues.CoordinatorIssuesView.as_view(),
        name="coordinator-issues",
    ),
    path(
        "coordinator/issues/<int:pk>/resolve/",
        coordinator_issues.ResolveIssueView.as_view(),
        name="coordinator-issue-resolve",
    ),
    path(
        "coordinator/issues/<int:pk>/unassign/",
        coordinator_issues.IssueUnassignView.as_view(),
        name="coordinator-issue-unassign",
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
