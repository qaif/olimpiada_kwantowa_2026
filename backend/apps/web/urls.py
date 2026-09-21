"""Mapa adresów interfejsu WWW (montowana w korzeniu, po prefiksach ``/api/``)."""

from django.urls import path

# Wzorce ekranów wydań E–K stoją w osobnych modułach, bo powstały równolegle (T11, T13, T20, T23,
# T27, T34, T42), a ten plik ma w etapie 2 **jednego** właściciela na wydanie
# (``docs/UNIWERSALNY-ETAP-2.md`` § 4.1). Montaż jest rozwinięciem tych list na **końcu**
# ``urlpatterns``: kolejność wzorców jest umową (etap 1 § 4.3), więc dopisujemy, a nie
# przestawiamy. Bramki flagi tu nie ma — o tym, czy ekran istnieje w tym konkursie, rozstrzyga
# widok (§ 2.1), bo mapa adresów zależna od konkursu znaczyłaby ``reverse()`` dający raz adres,
# a raz ``NoReverseMatch``.
from .urls_competitions import urlpatterns as competition_urlpatterns
from .urls_consents import urlpatterns as consent_urlpatterns
from .urls_documents import urlpatterns as document_urlpatterns
from .urls_fees import urlpatterns as fee_urlpatterns
from .urls_institutions import urlpatterns as institution_urlpatterns
from .urls_pipeline import urlpatterns as pipeline_urlpatterns
from .urls_regions import urlpatterns as region_urlpatterns
from .urls_scoring import urlpatterns as scoring_urlpatterns
from .views import (
    account,
    appeals,
    certificates,
    coordinator,
    coordinator_accounts,
    coordinator_announcements,
    coordinator_certificates,
    coordinator_competition,
    coordinator_events,
    coordinator_forum,
    coordinator_forwarding,
    coordinator_integrations,
    coordinator_issues,
    coordinator_members,
    coordinator_messages,
    coordinator_pages,
    coordinator_participants,
    coordinator_problem_detail,
    coordinator_quality,
    coordinator_reports,
    coordinator_rodo,
    coordinator_sponsor_slider,
    coordinator_stages,
    coordinator_support,
    coordinator_workshops,
    forum,
    guardian,
    invite,
    participant,
    participant_extras,
    participant_tools,
    public,
    quiz,
    reviewer,
    reviewer_extras,
    reviewer_tools,
    supervisor,
    support,
    twofactor,
)

app_name = "web"

urlpatterns = [
    # --- publiczne ---------------------------------------------------------------------------
    # Korzenia ``/`` tu nie ma: od T-09 obsługuje go ``cms.HomePage`` (Wagtail catch-all na końcu
    # ``config/urls.py``). Wszystkie pozostałe ścieżki ``apps.web`` są dopasowywane wcześniej.
    path("login/", public.LoginView.as_view(), name="login"),
    # Drugi krok logowania (TOTP). Adres stoi przy logowaniu, a nie przy koncie, bo to jest
    # **ciąg dalszy logowania**: sesja, która tu trafia, nie może jeszcze nic innego (patrz
    # ``apps.accounts.twofactor.TwoFactorMiddleware``).
    path("login/2fa/", twofactor.TwoFactorVerifyView.as_view(), name="twofactor-verify"),
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
    # Przyjęcie zaproszenia wystawionego przez nauczyciela (``apps.accounts.bulk_registration``).
    # Ta sama zasada, co wyżej: adres krótki i polski, bo trafia do listu czytanego przez ucznia,
    # a ekran podziękowania stoi **przed** wzorcem z tokenem – inaczej „dziekujemy” dałoby się
    # wziąć za token.
    path(
        "zaproszenie/dziekujemy/",
        invite.StudentInviteDoneView.as_view(),
        name="student-invite-done",
    ),
    path("zaproszenie/<str:token>/", invite.StudentInviteView.as_view(), name="student-invite"),
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
    # Drugi składnik logowania: konfiguracja, kody zapasowe (pokazywane raz) i wyłączenie.
    # ``codes/`` i ``disable/`` stoją po adresie nadrzędnym wyłącznie dla czytelności – to są
    # stałe segmenty, więc kolejność niczego tu nie rozstrzyga.
    path("account/2fa/", twofactor.TwoFactorSetupView.as_view(), name="twofactor-setup"),
    path("account/2fa/codes/", twofactor.TwoFactorCodesView.as_view(), name="twofactor-codes"),
    path("account/2fa/disable/", twofactor.TwoFactorDisableView.as_view(), name="twofactor-disable"),
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
    # --- forum uczestników (za flagą ``participant_forum``) ----------------------------------
    # Wzorce stoją w mapie **zawsze**, tak samo jak wzorce ekranów za pozostałymi flagami:
    # o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404), bo mapa adresów zależna
    # od konkursu znaczyłaby ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – czyli
    # wywrócony pasek konta na instalacji, w której ktoś właśnie wyłączył przełącznik.
    #
    # Człony ``t/`` i ``p/`` są krótkie celowo: adres wątku bywa przesyłany między uczestnikami,
    # a ``new/`` i ``mine/`` stoją **przed** wzorcem z działem, bo ``<slug>`` dopasowałby oba.
    path("forum/", forum.ForumIndexView.as_view(), name="forum"),
    path("forum/new/", forum.ForumThreadCreateView.as_view(), name="forum-thread-new"),
    path("forum/mine/", forum.ForumMyPostsView.as_view(), name="forum-mine"),
    path("forum/t/<int:pk>/", forum.ForumThreadView.as_view(), name="forum-thread"),
    path("forum/p/<int:pk>/edit/", forum.ForumPostEditView.as_view(), name="forum-post-edit"),
    path("forum/p/<int:pk>/delete/", forum.ForumPostDeleteView.as_view(), name="forum-post-delete"),
    path("forum/p/<int:pk>/report/", forum.ForumPostReportView.as_view(), name="forum-post-report"),
    path("forum/<slug:slug>/", forum.ForumCategoryView.as_view(), name="forum-category"),
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
    # Test online. Wejście jest **po etapie** (uczestnik przychodzi z zakładki „Zadania”, gdzie
    # widzi etap, a nie numer testu), a wszystko dalej – po identyfikatorze **podejścia**: to ono
    # jest przedmiotem tych ekranów i to ono ma właściciela, którego widok sprawdza. Adres
    # z identyfikatorem etapu w dalszych krokach byłby drugą drogą do tych samych danych i drugim
    # miejscem, w którym trzeba pamiętać o filtrze „to moje podejście”.
    path("me/stages/<int:stage_id>/test/", quiz.QuizStartView.as_view(), name="quiz-start"),
    path("me/test/<int:attempt_id>/", quiz.QuizAttemptView.as_view(), name="quiz-attempt"),
    # Autozapis. Osobny adres, a nie ten sam z nagłówkiem „to jest AJAX”: odpowiada JSON-em,
    # przyjmuje wyłącznie POST i nie ma wersji do przeglądania – trzy różnice, które w jednym
    # widoku byłyby trzema rozgałęzieniami w środku obsługi zawodów.
    path("me/test/<int:attempt_id>/zapis/", quiz.QuizAutosaveView.as_view(), name="quiz-autosave"),
    path("me/test/<int:attempt_id>/wynik/", quiz.QuizResultView.as_view(), name="quiz-result"),
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
    # Import listy uczniów i wynikająca z niego lista „kto ma już konto”. Stoją **przed** wzorcem
    # z identyfikatorem ucznia, bo „import” i „students” są zwykłymi segmentami i bez tej
    # kolejności… nic złego by się nie stało (dalej jest ``<int:pk>``), ale czyta się to od
    # ogólnego do szczegółowego, tak jak resztę tego pliku.
    path("supervisor/import/", supervisor.SupervisorImportView.as_view(), name="supervisor-import"),
    path("supervisor/students/", supervisor.SupervisorStudentsView.as_view(), name="supervisor-students"),
    path(
        "supervisor/students/<int:pk>/resend/",
        supervisor.ResendInvitationView.as_view(),
        name="supervisor-resend-invitation",
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
    # Ustawienia **konkursu** (marka, organizator, przełączniki) – w odróżnieniu od ustawień
    # jego rocznika niżej. Adres bez identyfikatora, bo konkurs wskazuje domena żądania: nie ma
    # tu adresu, pod którym dałoby się otworzyć cudzy konkurs.
    path(
        "coordinator/competition/",
        coordinator_competition.CompetitionSettingsView.as_view(),
        name="coordinator-competition",
    ),
    # Okno rejestracji uczestników – ustawienie edycji, nie etapu, stąd adres bez identyfikatora.
    path(
        "coordinator/registration/",
        coordinator_stages.RegistrationSettingsView.as_view(),
        name="coordinator-registration",
    ),
    # Przekazywanie przyjętych rozwiązań na skrzynkę organizatora. Adres bez identyfikatora,
    # bo ustawienie należy do **konkursu**, a konkurs wskazuje domena żądania – nie ma tu adresu,
    # pod którym dałoby się otworzyć cudzą konfigurację.
    path(
        "coordinator/submission-forwarding/",
        coordinator_forwarding.SubmissionForwardingView.as_view(),
        name="coordinator-submission-forwarding",
    ),
    # Slider sponsorów w menu: włącznik, tempo przewijania i poziomy partnerów. Adres bez
    # identyfikatora z tego samego powodu, co przekazywanie rozwiązań wyżej – konkurs wskazuje
    # domena żądania.
    path(
        "coordinator/sponsor-slider/",
        coordinator_sponsor_slider.SponsorSliderView.as_view(),
        name="coordinator-sponsor-slider",
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
    # Test online etapu. Cała gałąź stoi pod ``/quiz/``, bo to jeden przedmiot pracy w czterech
    # widokach (ustawienia, pytania, import, wyniki) – tak samo jak ``/problems/`` wyżej trzyma
    # zadania. ``questions/new/`` przed wzorcem z identyfikatorem: ``<int:…>`` i tak nie dopasuje
    # słowa, ale kolejność mówi, co jest wejściem, a co szczegółem.
    path(
        "coordinator/stages/<int:stage_id>/quiz/",
        quiz.QuizSettingsView.as_view(),
        name="coordinator-stage-quiz",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/questions/",
        quiz.QuizQuestionsView.as_view(),
        name="coordinator-stage-quiz-questions",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/questions/new/",
        quiz.QuizQuestionFormView.as_view(),
        name="coordinator-stage-quiz-question-new",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/questions/<int:question_id>/",
        quiz.QuizQuestionFormView.as_view(),
        name="coordinator-stage-quiz-question-edit",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/questions/<int:question_id>/delete/",
        quiz.QuizQuestionDeleteView.as_view(),
        name="coordinator-stage-quiz-question-delete",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/import/",
        quiz.QuizImportView.as_view(),
        name="coordinator-stage-quiz-import",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/preview/",
        quiz.QuizPreviewView.as_view(),
        name="coordinator-stage-quiz-preview",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/results/",
        quiz.QuizResultsView.as_view(),
        name="coordinator-stage-quiz-results",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/results/export/",
        quiz.QuizResultsExportView.as_view(),
        name="coordinator-stage-quiz-export",
    ),
    path(
        "coordinator/stages/<int:stage_id>/quiz/results/regrade/",
        quiz.QuizRegradeView.as_view(),
        name="coordinator-stage-quiz-regrade",
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
    # Hurtowe zaproszenie uczniów z listy – ten sam import, co u opiekuna, plus kolumna
    # „e-mail opiekuna szkolnego”. Stoi **przed** wzorcem z identyfikatorem konta: „import” jest
    # napisem, a ``<int:pk>`` liczbą, więc kolizji nie ma, ale kolejność od ogólnego do
    # szczegółowego obowiązuje w tym pliku wszędzie.
    path(
        "coordinator/accounts/import/",
        supervisor.CoordinatorStudentImportView.as_view(),
        name="coordinator-accounts-import",
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
    # „Zgubiłem telefon z aplikacją” – jedyna droga powrotu dla członka komisji, który nie ma już
    # ani urządzenia, ani kodów zapasowych. POST, bo zdjęcie komuś zabezpieczenia jest decyzją
    # organizatora i ma zostawić w audycie jego nazwisko (``2fa.reset``).
    path(
        "coordinator/accounts/<int:pk>/2fa-reset/",
        coordinator_accounts.CoordinatorTwoFactorResetView.as_view(),
        name="coordinator-account-2fa-reset",
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
    # Szablony graficzne dokumentów. Gałąź ``certificates/templates/`` stoi obok adresu
    # z identyfikatorem dokumentu i nie może z nim kolidować: ``<int:pk>`` nie dopasuje słowa.
    path(
        "coordinator/certificates/templates/",
        coordinator_certificates.CertificateTemplateListView.as_view(),
        name="coordinator-certificate-templates",
    ),
    path(
        "coordinator/certificates/templates/new/",
        coordinator_certificates.CertificateTemplateFormView.as_view(),
        name="coordinator-certificate-template-new",
    ),
    path(
        "coordinator/certificates/templates/<int:pk>/",
        coordinator_certificates.CertificateTemplateFormView.as_view(),
        name="coordinator-certificate-template-edit",
    ),
    path(
        "coordinator/certificates/templates/<int:pk>/delete/",
        coordinator_certificates.CertificateTemplateDeleteView.as_view(),
        name="coordinator-certificate-template-delete",
    ),
    path(
        "coordinator/certificates/templates/<int:pk>/default/",
        coordinator_certificates.CertificateTemplateDefaultView.as_view(),
        name="coordinator-certificate-template-default",
    ),
    path(
        "coordinator/certificates/templates/<int:pk>/preview/",
        coordinator_certificates.CertificateTemplatePreviewView.as_view(),
        name="coordinator-certificate-template-preview",
    ),
    # Opiekunowie szkolni: zaświadczenia za pracę z uczniami w **edycji**, nie w etapie – stąd
    # własna gałąź adresów, a nie kolejny ekran pod ``stages/<id>/``.
    path(
        "coordinator/supervisors/",
        coordinator_certificates.CoordinatorSupervisorsView.as_view(),
        name="coordinator-supervisors",
    ),
    path(
        "coordinator/supervisors/certificates/",
        coordinator_certificates.IssueSupervisorCertificatesView.as_view(),
        name="coordinator-supervisor-certificates",
    ),
    path(
        "coordinator/supervisors/<int:pk>/certificate/",
        coordinator_certificates.IssueSupervisorCertificateView.as_view(),
        name="coordinator-supervisor-certificate",
    ),
    path(
        "coordinator/workshops/attendance/",
        coordinator_workshops.WorkshopAttendanceView.as_view(),
        name="coordinator-workshop-attendance",
    ),
    path(
        "coordinator/workshops/certificates/",
        coordinator_workshops.IssueWorkshopCertificatesView.as_view(),
        name="coordinator-workshop-certificates",
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
    # Moderacja forum. Kolejka stoi pod adresem bez przyrostka, bo to po nią przychodzi się
    # codziennie; spis wątków, działy i ustawienia są jej sąsiadami, a nie jej podstronami.
    path(
        "coordinator/forum/",
        coordinator_forum.CoordinatorForumView.as_view(),
        name="coordinator-forum",
    ),
    path(
        "coordinator/forum/threads/",
        coordinator_forum.CoordinatorForumThreadsView.as_view(),
        name="coordinator-forum-threads",
    ),
    path(
        "coordinator/forum/categories/",
        coordinator_forum.CoordinatorForumCategoriesView.as_view(),
        name="coordinator-forum-categories",
    ),
    path(
        "coordinator/forum/settings/",
        coordinator_forum.CoordinatorForumSettingsView.as_view(),
        name="coordinator-forum-settings",
    ),
    path(
        "coordinator/forum/t/<int:pk>/",
        coordinator_forum.CoordinatorForumThreadView.as_view(),
        name="coordinator-forum-thread",
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
    # Eksporty na zewnątrz (kuratorium, protokół, zrzut edycji) – widoki w
    # ``coordinator_integrations``, bo mają innego odbiorcę niż arkusze robocze panelu, ale adresy
    # tutaj, czyli tam, gdzie koordynator szuka plików. Kuratorium **musi** stać przed wzorcem
    # ``<kind>/<fmt>/`` niżej: tamten dopasowałby „kuratorium/csv/” jako nieznany rodzaj eksportu
    # i odpowiedział 404. Protokół i zrzut edycji mają po jednym segmencie, więc nie kolidują.
    path(
        "coordinator/export/kuratorium/<str:fmt>/",
        coordinator_integrations.KuratoriumExportView.as_view(),
        name="coordinator-export-kuratorium",
    ),
    path(
        "coordinator/export/protocol/",
        coordinator_integrations.StageProtocolView.as_view(),
        name="coordinator-export-protocol",
    ),
    path(
        "coordinator/export/edition/",
        coordinator_integrations.EditionJsonExportView.as_view(),
        name="coordinator-export-edition-json",
    ),
    path(
        "coordinator/export/<str:kind>/<str:fmt>/",
        coordinator_reports.ExportDownloadView.as_view(),
        name="coordinator-export-download",
    ),
    # --- integracje (klucze API, webhooki) ---------------------------------------------------
    # Jeden ekran i po jednym adresie na czynność. Każda czynność jest POST-em pod własny adres,
    # a nie jednym adresem z polem „akcja”: rozróżnianie po nazwie przycisku zależy od tego, czy
    # przeglądarka go przyśle, a przy wysyłce klawiaturą nie zawsze przysyła.
    path(
        "coordinator/integrations/",
        coordinator_integrations.IntegrationsView.as_view(),
        name="coordinator-integrations",
    ),
    path(
        "coordinator/integrations/keys/",
        coordinator_integrations.ApiKeyCreateView.as_view(),
        name="coordinator-integrations-key-create",
    ),
    path(
        "coordinator/integrations/keys/<int:pk>/revoke/",
        coordinator_integrations.ApiKeyRevokeView.as_view(),
        name="coordinator-integrations-key-revoke",
    ),
    path(
        "coordinator/integrations/webhooks/",
        coordinator_integrations.WebhookCreateView.as_view(),
        name="coordinator-integrations-webhook-create",
    ),
    path(
        "coordinator/integrations/webhooks/<int:pk>/update/",
        coordinator_integrations.WebhookUpdateView.as_view(),
        name="coordinator-integrations-webhook-update",
    ),
    path(
        "coordinator/integrations/webhooks/<int:pk>/delete/",
        coordinator_integrations.WebhookDeleteView.as_view(),
        name="coordinator-integrations-webhook-delete",
    ),
    path(
        "coordinator/integrations/webhooks/<int:pk>/test/",
        coordinator_integrations.WebhookTestView.as_view(),
        name="coordinator-integrations-webhook-test",
    ),
    path(
        "coordinator/integrations/deliveries/<int:pk>/resend/",
        coordinator_integrations.DeliveryResendView.as_view(),
        name="coordinator-integrations-delivery-resend",
    ),
    # --- komisja odwoławcza ------------------------------------------------------------------
    path("appeals/", appeals.AppealsQueueView.as_view(), name="appeals"),
    path("appeals/<int:pk>/decide/", appeals.AppealDecideView.as_view(), name="appeal-decide"),
    # --- wydanie E: konfiguracja zgód i tekstów dokumentów -------------------------------------
    # Dopisane **na końcu** listy, bo kolejność wzorców jest umową: wzorzec wstawiony w środek
    # przesuwa dopasowanie wszystkiego, co stoi po nim, a adresy Konkursu #1 mają zostać co do
    # bajtu takie, jak przed wdrożeniem (§ 0.2 punkt 1).
    *consent_urlpatterns,
    *document_urlpatterns,
    # --- wydanie G: podział terytorialny konkursu ----------------------------------------------
    *region_urlpatterns,
    # --- wydanie H: słownik placówek organizatora i profil rejestracji --------------------------
    *institution_urlpatterns,
    # --- wydanie I: edytor przebiegu i kategorie ------------------------------------------------
    *pipeline_urlpatterns,
    # --- wydanie J: punktacja etapu, drużyny i role recenzenckie --------------------------------
    *scoring_urlpatterns,
    # --- wydanie K: wpisowe, płatności i logistyka etapu stacjonarnego --------------------------
    *fee_urlpatterns,
    # --- konkursy w subdomenach platformy: spis konkursów koordynatora i „Nowy konkurs” ---------
    *competition_urlpatterns,
]
