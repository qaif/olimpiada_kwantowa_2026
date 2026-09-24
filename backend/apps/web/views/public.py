"""Strony publiczne: logowanie, rejestracja, ogłoszone wyniki.

Strona główna (``/``) należy od T-09 do Wagtaila (``apps.cms.models.HomePage``) – tutaj nie ma już
widoku ``home``. Adres ``/`` jest w kodzie zapisany dosłownie, bo wyznacza go korzeń witryny
Wagtaila, a nie wpis w ``urls.py``.

Wszystkie treści od użytkowników (nazwy szkół, etykiety w tabeli wyników) renderują się
z domyślnym autoescapowaniem Django. W żadnym szablonie nie ma ``|safe`` ani ``mark_safe``.

Logowanie i obie rejestracje są objęte limitem żądań identycznym z tym na endpointach API –
patrz ``apps.web.throttle``. Formularz HTML robi to samo, co ``POST /api/auth/…``, więc limit
tylko po stronie DRF byłby obejściem długości jednego adresu URL.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.views import LogoutView as DjangoLogoutView
from django.contrib.auth.views import PasswordResetCompleteView as DjangoPasswordResetCompleteView
from django.contrib.auth.views import PasswordResetConfirmView as DjangoPasswordResetConfirmView
from django.contrib.auth.views import PasswordResetDoneView as DjangoPasswordResetDoneView
from django.contrib.auth.views import PasswordResetView as DjangoPasswordResetView
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from apps.accounts.activation import (
    ACTIVATION_HOURS,
    RESEND_MESSAGE,
    activate_with_token,
    resend_activation,
)
from apps.accounts.consents import ConsentSource
from apps.accounts.services import register_committee, register_participant
from apps.cms.models import SiteSettings
from apps.competitions.scoring import problem_maxima_by_number, stage_maximum_total
from apps.core.api import DomainError
from apps.core.models import audit
from apps.results.models import ResultsPublication
from apps.web.context_processors import roles
from apps.web.forms import (
    ActivationResendForm,
    CommitteeRegisterForm,
    EmailAuthenticationForm,
    ParticipantRegisterForm,
)
from apps.web.throttle import ThrottledFormMixin, reset_for_identity


def default_panel_url(request) -> str:
    """Panel właściwy dla roli zalogowanego użytkownika – domyślny cel po zalogowaniu.

    Bez tego recenzent i koordynator lądowali na panelu uczestnika, czyli od razu na 403. Jedna
    definicja obsługuje obie drogi wejścia: formularz hasłowy (``LoginView``) i logowanie przez
    dostawcę zewnętrznego (``apps.accounts.adapters.AccountAdapter``). Rozjazd między nimi
    oznaczałby, że ta sama osoba trafia gdzie indziej w zależności od tego, jak się zalogowała.
    """
    context = roles(request)
    for flag, name in (
        ("is_participant", "web:me"),
        ("is_reviewer", "web:review-list"),
        ("is_coordinator", "web:coordinator"),
        ("is_appeals_committee", "web:appeals"),
        # Opiekun szkolny na końcu listy: jego panel jest jedynym, jaki ma, ale konto opiekuna
        # nie wyklucza żadnej innej roli, a tamte prowadzą do pracy przy zawodach.
        ("is_supervisor", "web:supervisor"),
    ):
        if context.get(flag):
            return str(reverse_lazy(name))
    return "/"


class LoginView(ThrottledFormMixin, DjangoLoginView):
    """Logowanie sesyjne (Django auth). Loginem jest adres e-mail.

    Limit (scope ``login``) liczy **wyłącznie nieudane** próby: udane logowanie kasuje licznik.
    Inaczej ucierpiałby ten, kto po prostu często się loguje, a nie ten, kto zgaduje hasło.
    """

    template_name = "web/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True
    throttle_scope = "login"
    throttle_on_request = False

    def form_valid(self, form):
        self.reset_throttle()
        return super().form_valid(form)

    def form_invalid(self, form):
        self.consume_throttle()
        return super().form_invalid(form)

    def get_default_redirect_url(self) -> str:
        """Po zalogowaniu bez ``next`` – panel właściwy dla roli, a nie zawsze ``/me/``.

        Parametr ``next`` (obsługiwany przez ``get_redirect_url``) ma pierwszeństwo i jest
        walidowany przez Django, więc otwarte przekierowanie nie wchodzi w grę.
        """
        return default_panel_url(self.request)


class LogoutView(DjangoLogoutView):
    """Wylogowanie. Wyłącznie POST – wylogowanie GET-em byłoby podatne na CSRF przez ``<img>``."""

    next_page = "/"


def service_name(request) -> str:
    """Nazwa serwisu z ``cms.SiteSettings`` – do tematu wiadomości i do treści listu.

    Fallback na ``WAGTAIL_SITE_NAME`` jest po to, żeby wysyłka nie zależała od tego, czy w Wagtailu
    istnieje ``Site`` pasujący do hosta żądania (świeża baza, wywołanie z komendy zarządzającej).
    Temat listu nie jest miejscem, w którym wolno się wywrócić.
    """
    try:
        return SiteSettings.for_request(request).site_name
    except Exception:  # noqa: BLE001 - brak obiektu Site / brak tabeli przy pierwszej migracji
        return settings.WAGTAIL_SITE_NAME


class PasswordResetView(ThrottledFormMixin, DjangoPasswordResetView):
    """„Nie pamiętasz hasła?” – wysyłka linku z jednorazowym tokenem.

    Widok jest wspólny dla wszystkich ról: model konta jest jeden (``accounts.User``), loginem
    zawsze jest e-mail, więc uczestnik, recenzent, komisja i koordynator odzyskują hasło tą samą
    drogą. Konto ``CommitteeStatus.PENDING`` też ją ma – ono jest zwykłym, aktywnym użytkownikiem,
    tylko bez uprawnień recenzenta.

    **Bez enumeracji kont.** Odpowiedź jest identyczna dla adresu istniejącego i nieistniejącego –
    zawsze 302 na ``/password-reset/sent/``. ``PasswordResetForm`` Django szuka konta samo i przy
    braku dopasowania po prostu nic nie wysyła (dotyczy to też kont ``is_active=False`` oraz kont
    z nieużywalnym hashem hasła – to domyślne zachowanie ``get_users`` i go nie zmieniamy).

    Limit (scope ``password_reset``) konsumuje **każdy** POST, także udany: inaczej ten formularz
    byłby wysyłaczem listów na dowolny cudzy adres, ograniczonym wyłącznie cierpliwością nadawcy.
    """

    template_name = "web/password_reset.html"
    # Nazwy szablonów listu są jawne, bo domyślne Django (``registration/password_reset_email.html``)
    # są zajęte przez ``django.contrib.admin`` – tam ten plik jest treścią *tekstową*, mimo nazwy.
    subject_template_name = "registration/password_reset_subject.txt"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email_body.html"
    success_url = reverse_lazy("web:password-reset-sent")
    throttle_scope = "password_reset"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # ``PasswordResetForm`` porównuje adres przez ``iexact``, ale konta trzymamy zawsze małymi
        # literami (``User.save``), więc normalizacja tutaj utrzymuje jeden kształt adresu w całym
        # przepływie: w kubełku throttlingu, w zapytaniu i w nagłówku ``To:``.
        data = kwargs.get("data")
        if data is not None:
            data = data.copy()
            data["email"] = (data.get("email") or "").strip().lower()
            kwargs["data"] = data
        return kwargs

    def form_valid(self, form):
        # ``site_name`` w kontekście listu nadpisuje wartość, którą ``PasswordResetForm`` bierze
        # z ``RequestSite`` (tam jest to nazwa hosta, np. „localhost”). Temat ma nieść nazwę
        # serwisu z ``/cms/``, a nie domenę.
        self.extra_email_context = {
            **(self.extra_email_context or {}),
            "site_name": service_name(self.request),
        }
        return super().form_valid(form)


class PasswordResetSentView(DjangoPasswordResetDoneView):
    """Potwierdzenie wysyłki. Treść jest celowo warunkowa („jeśli konto istnieje”)."""

    template_name = "web/password_reset_sent.html"


class PasswordResetConfirmView(DjangoPasswordResetConfirmView):
    """Formularz nowego hasła spod linku ``/reset/<uidb64>/<token>/``.

    Token jest jednorazowy i ważny ``PASSWORD_RESET_TIMEOUT`` (24 h): ``PasswordResetTokenGenerator``
    miesza do skrótu hash starego hasła i ``last_login``, więc po zmianie hasła ten sam link nie
    przechodzi już walidacji i widok pokazuje stronę „link nieważny”.

    Nowe hasło przechodzi przez ``AUTH_PASSWORD_VALIDATORS`` (m.in. minimum 10 znaków) – tak samo,
    jak przy rejestracji. ``post_reset_login`` zostaje wyłączone: automatyczne zalogowanie
    zamieniałoby dostęp do skrzynki pocztowej w sesję jednym kliknięciem z listu.
    """

    template_name = "web/password_reset_confirm.html"
    success_url = reverse_lazy("web:password-reset-complete")
    post_reset_login = False

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.user
        # Kto zapomniał hasła, zwykle najpierw wyczerpał limit logowania zgadywaniem. Bez tego
        # zerowania reset „działałby”, a zaraz po nim logowanie odbijałoby się o 429.
        reset_for_identity("login", self.request, user.email)
        # Zmiana poświadczeń jest zdarzeniem audytowym. W ``diff`` nie ma ani adresu e-mail, ani
        # tokenu – wystarczy powód zmiany; kto, mówi ``actor``/``target_id``.
        audit(user, "password.reset", user, {"via": "email"}, request=self.request)
        return response


class PasswordResetCompleteView(DjangoPasswordResetCompleteView):
    """Hasło zmienione – jedyne wyjście stąd prowadzi do formularza logowania."""

    template_name = "web/password_reset_complete.html"


class ServiceFormView(FormView):
    """``FormView``, który woła serwis domenowy i zamienia ``DomainError`` na błąd formularza."""

    success_message = ""

    def call_service(self, form):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def form_valid(self, form):
        try:
            self.call_service(form)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.form_invalid(form)
        if self.success_message:
            messages.success(self.request, self.success_message)
        return super().form_valid(form)


class RegisterParticipantView(ThrottledFormMixin, ServiceFormView):
    """Rejestracja otwarta uczestnika – cała logika w ``accounts.services.register_participant``."""

    template_name = "web/register.html"
    form_class = ParticipantRegisterForm
    # Po udanym zgłoszeniu uczestnik ląduje na osobnej stronie „sprawdź skrzynkę”, a nie na
    # formularzu logowania z komunikatem: konto jest nieaktywne, więc logowanie i tak by się nie
    # udało, a strona ma miejsce na całą instrukcję (adres, na który poszedł list, spam, 4 godziny).
    success_url = reverse_lazy("web:register-done")
    success_message = ""
    # Tu liczy się każdy POST, także udany: limit ma powstrzymać seryjne zakładanie kont.
    throttle_scope = "register"
    registration_kind = "participant"

    def call_service(self, form):
        # ``source`` i ``request`` dokładamy tutaj, a nie w formularzu: to fakt o **drodze**
        # żądania, a nie dana wpisana przez uczestnika – i tak trafia do wpisu dowodowego zgody.
        register_participant(**form.cleaned_data, source=ConsentSource.WEB, request=self.request)
        remember_registration(self.request, form.cleaned_data["email"], self.registration_kind)


#: Klucze sesji, którymi strona „sprawdź skrzynkę” dowiaduje się, komu i po co wysłano list.
#: Sesja, a nie parametr w adresie: adres e-mail w URL-u trafiałby do historii i logów proxy.
REGISTRATION_EMAIL_SESSION_KEY = "registration_email"
REGISTRATION_KIND_SESSION_KEY = "registration_kind"


def remember_registration(request, email: str, kind: str) -> None:
    request.session[REGISTRATION_EMAIL_SESSION_KEY] = email
    request.session[REGISTRATION_KIND_SESSION_KEY] = kind


class RegisterDoneView(TemplateView):
    """Strona po udanej rejestracji: dokąd poszedł link aktywacyjny i co zrobić, gdy nie dotarł.

    Zdanie o folderze ze spamem stoi **tu**, a nie w treści listu: kto czyta list, ten go już
    dostał. Adres czytamy z sesji jednorazowo (``pop``) – odświeżenie strony po chwili prowadzi
    z powrotem do formularza, a nie pokazuje cudzego adresu na współdzielonym komputerze.
    """

    template_name = "web/register_done.html"

    def get(self, request, *args, **kwargs):
        email = request.session.pop(REGISTRATION_EMAIL_SESSION_KEY, "")
        kind = request.session.pop(REGISTRATION_KIND_SESSION_KEY, "participant")
        if not email:
            return redirect("web:register")
        context = self.get_context_data(
            email=email,
            is_committee=kind == "committee",
            is_supervisor=kind == "supervisor",
            activation_hours=ACTIVATION_HOURS,
        )
        return self.render_to_response(context)


class RegisterCommitteeView(ThrottledFormMixin, ServiceFormView):
    """Rejestracja członka komitetu na kod zaproszenia.

    Ten sam scope co rejestracja otwarta – limit chroni tu dodatkowo przed zgadywaniem kodu
    zaproszenia, bo każda próba użycia kodu przechodzi przez ten formularz.
    """

    template_name = "web/register_committee.html"
    form_class = CommitteeRegisterForm
    success_url = reverse_lazy("web:register-done")
    throttle_scope = "register"
    success_message = ""
    registration_kind = "committee"

    def call_service(self, form):
        register_committee(**form.cleaned_data, request=self.request)


class ActivateAccountView(TemplateView):
    """Aktywacja konta spod linku ``/activate/<token>/``.

    **Bez automatycznego zalogowania** – tak samo, jak po resecie hasła (``post_reset_login=False``).
    Dostęp do skrzynki pocztowej nie ma zamieniać się jednym kliknięciem z listu w sesję w panelu:
    list bywa przekazywany dalej, a skrzynka bywa otwarta na cudzym komputerze.

    Token zły albo wygasły nie jest błędem 404: kończy się stroną z formularzem „wyślij link
    ponownie”, bo to jedyna sensowna następna czynność, a po czterech godzinach wygaśnięcie linku
    jest sytuacją normalną, nie awarią.
    """

    template_name = "web/activate_done.html"
    invalid_template_name = "web/activate_invalid.html"
    #: Ustawiane w ``get``; niepuste znaczy „pokaż stronę z formularzem ponownej wysyłki”.
    error = ""

    def get(self, request, token: str, *args, **kwargs):
        try:
            activate_with_token(token, request=request)
        except DomainError as exc:
            self.error = str(exc.detail)
        return self.render_to_response(self.get_context_data(**kwargs), status=400 if self.error else 200)

    def get_template_names(self) -> list[str]:
        return [self.invalid_template_name if self.error else self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"error": self.error, "activation_hours": ACTIVATION_HOURS})
        if self.error:
            # Formularz stoi od razu na stronie odmowy: po wygaśnięciu linku jedyną sensowną
            # następną czynnością jest poproszenie o nowy, a nie szukanie go w menu.
            context["form"] = ActivationResendForm()
        return context


class ActivationResendView(ThrottledFormMixin, FormView):
    """„Wyślij link aktywacyjny ponownie” (``/activate/resend/``).

    Dwie rzeczy, które ten widok robi **inaczej** niż zwykły formularz:

    - **odpowiedź nie zależy od stanu konta.** Zawsze ten sam komunikat i to samo przekierowanie,
      niezależnie od tego, czy konto istnieje, czy jest już aktywne. Inaczej formularz byłby
      wyszukiwarką adresów zarejestrowanych w serwisie – i to publiczną, bez logowania,
    - **limit konsumuje każdy POST**, także ten, po którym nic nie zostało wysłane. Scope jest
      wspólny z resetem hasła (``password_reset``, 5/h): oba formularze wysyłają list na adres
      podany przez anonima, więc ograniczenie musi być wspólne – inaczej ten sam adres dałoby się
      zasypać listami przez drugi formularz.
    """

    template_name = "web/activate_resend.html"
    form_class = ActivationResendForm
    success_url = reverse_lazy("web:login")
    throttle_scope = "password_reset"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["activation_hours"] = ACTIVATION_HOURS
        return context

    def form_valid(self, form):
        resend_activation(form.cleaned_data["email"], request=self.request)
        messages.success(self.request, RESEND_MESSAGE)
        return super().form_valid(form)


class PublicResultsView(TemplateView):
    """Ogłoszona tabela wyników etapu – wyłącznie z zamrożonego snapshotu, bez logowania.

    Etap bez publikacji to 404: dla publiczności taka tabela nie istnieje, nawet jeśli etap
    istnieje w bazie (ta sama reguła, co w ``GET /api/public/results/{stage_id}/``).
    """

    template_name = "web/results.html"

    def get_context_data(self, stage_id: int, **kwargs):
        context = super().get_context_data(**kwargs)
        # To samo zapytanie, co ``apps.results.services.published_results``, plus zakres konkursu.
        # Zawężenie jest **tutaj**, a nie w serwisie, bo serwis woła też panel uczestnika, gdzie
        # etap przychodzi już z jego wpisu. Bez niego ten adres byłby najtańszą drogą do cudzych
        # wyników: nie wymaga logowania, a identyfikatory etapów są kolejne.
        publication = (
            ResultsPublication.objects.for_competition(self.request.competition)
            .select_related("stage", "stage__edition")
            .filter(stage_id=stage_id)
            .first()
        )
        if publication is None:
            raise Http404("Wyniki tego etapu nie zostały ogłoszone.")
        rows = publication.rows
        problem_numbers = sorted(
            {key for row in rows for key in (row.get("points") or {})},
            key=lambda value: (len(value), value),
        )
        context.update(
            {
                "publication": publication,
                "stage": publication.stage,
                "rows": rows,
                "problem_numbers": problem_numbers,
                # „Zad. 3 (max 12,5)” – zadania mogą mieć różną liczbę punktów (wydanie 0.35.0).
                "problem_maxima": problem_maxima_by_number(publication.stage),
                "max_total": stage_maximum_total(publication.stage, publication.stage.problems.all()),
            }
        )
        return context


#: Znaczniki własności domeny wydawane przez usługi zewnętrzne. Adres ``/<token>.html`` ma zwracać
#: dokładnie treść ``google-site-verification: <token>.html`` – Google porównuje ją bajt po bajcie.
#: Token nie jest tajemnicą (każdy może pobrać ten plik), więc może stać w kodzie; nowy dopisuje się
#: tutaj, a nie przez wgrywanie pliku na serwer, żeby przetrwał każde wdrożenie.
SITE_VERIFICATION_TOKENS = frozenset({"google13608a204a115889"})


def site_verification(request, token: str) -> HttpResponse:
    """Plik weryfikacyjny Google Search Console pod ``/google….html``; obcy token to zwykłe 404."""
    if token not in SITE_VERIFICATION_TOKENS:
        raise Http404
    return HttpResponse(f"google-site-verification: {token}.html", content_type="text/html; charset=utf-8")
