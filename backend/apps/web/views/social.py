"""Widoki logowania przez dostawcę zewnętrznego, które są **nasze**, a nie allauth.

allauth kończy uścisk dłoni OAuth2 i – gdy nie zna jeszcze tego użytkownika – odsyła go tutaj
(``SOCIALACCOUNT_AUTO_SIGNUP = False``, przekierowanie idzie na nazwę ``socialaccount_signup``).
Login społecznościowy czeka wtedy w sesji, a w bazie **nie ma jeszcze żadnego wiersza** – to jest
warunek postawiony wprost: bez zgody RODO nie może powstać konto.

``SocialSignupView`` robi dokładnie to, co ``allauth.socialaccount.internal.flows.signup``:

1. usuwa login społecznościowy z sesji (żeby powrót „wstecz” nie powtórzył rejestracji),
2. zakłada konto – u nas przez ``apps.accounts.services.register_social_participant``,
   czyli z profilem uczestnika, w grupie ``participant`` i bez użytecznego hasła,
3. zapisuje powiązanie ``SocialAccount`` (``sociallogin.save``),
4. oddaje sterowanie allauth (``complete_social_signup``), żeby zalogowanie, sygnały i wybór
   adresu docelowego przebiegły jedną, wspólną drogą – tą samą, którą idzie logowanie konta,
   które już istnieje.

Formularz jest objęty limitem żądań ze scope'em ``register`` – tym samym, co rejestracja hasłem.
Bez tego zakładanie kont miałoby drugą, nielimitowaną drogę.
"""

from __future__ import annotations

from allauth.socialaccount.internal import flows
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView

from apps.accounts.activation import ACTIVATION_REQUIRED_MESSAGE
from apps.accounts.adapters import (
    provider_id,
    provider_label,
    sociallogin_email,
    sociallogin_email_verified,
)
from apps.accounts.consents import ConsentSource
from apps.accounts.services import register_social_participant
from apps.competitions.registration import current_registration_status, registration_message
from apps.core.api import DomainError
from apps.core.models import audit
from apps.web.forms import SocialParticipantSignupForm
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.public import default_panel_url


class SocialSignupView(ThrottledFormMixin, FormView):
    """Dokończenie rejestracji uczestnika po udanym logowaniu u dostawcy.

    Widok jest osiągalny **wyłącznie** z loginem społecznościowym czekającym w sesji. Wejście
    „z ulicy” kończy się przekierowaniem na formularz logowania – nie ma tu ścieżki, którą dałoby
    się założyć konto bez przejścia przez dostawcę.
    """

    template_name = "socialaccount/signup.html"
    form_class = SocialParticipantSignupForm
    throttle_scope = "register"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(default_panel_url(request))
        sociallogin = flows.signup.get_pending_signup(request)
        if not sociallogin:
            return redirect("web:login")
        # Zamknięta rejestracja obowiązuje także tę drogę. Odsyłamy na ``/register/``, bo tam stoi
        # pełne wyjaśnienie z datą – i robimy to **przed** formularzem, żeby nie kazać wypełniać
        # ekranu, którego serwis i tak nie przyjmie. Login społecznościowy zostaje w sesji
        # nietknięty: nie powstaje ani konto, ani powiązanie ``SocialAccount``.
        state = current_registration_status()
        if not state.is_open:
            messages.error(request, registration_message(state))
            return redirect(reverse("web:register"))
        self.sociallogin = sociallogin
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self) -> dict:
        """Imię i nazwisko z profilu u dostawcy – jako propozycja, nie jako fakt."""
        user = self.sociallogin.user
        return {
            "first_name": getattr(user, "first_name", "") or "",
            "last_name": getattr(user, "last_name", "") or "",
        }

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["provider_name"] = provider_label(self.sociallogin)
        # Adres jest pokazywany, ale nie jest polem formularza: wpisywalny pozwalałby założyć
        # konto na cudzy adres i przejąć je resetem hasła.
        context["social_email"] = sociallogin_email(self.sociallogin)
        return context

    def form_valid(self, form):
        request = self.request
        sociallogin = self.sociallogin
        # Czy konto powstanie aktywne, rozstrzyga **odpowiedź dostawcy o tym adresie**, a nie nasza
        # konfiguracja: Google potwierdza adres (``email_verified``), Facebook nie potwierdza go
        # wcale. Szczegóły w ``apps.accounts.adapters.sociallogin_email_verified``.
        email_verified = sociallogin_email_verified(sociallogin)
        try:
            participant = register_social_participant(
                email=sociallogin_email(sociallogin),
                **form.cleaned_data,
                email_verified=email_verified,
                source=ConsentSource.SOCIAL,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.form_invalid(form)
        # Dopiero teraz login przestaje czekać w sesji – wcześniejsze czyszczenie zostawiłoby
        # użytkownika bez możliwości poprawienia formularza po błędzie domenowym.
        flows.signup.clear_pending_signup(request)
        sociallogin.user = participant.user
        # Zapisuje ``SocialAccount`` (i adres e-mail dostawcy w tabeli allauth). Tokenów nie
        # zapisuje – ``SOCIALACCOUNT_STORE_TOKENS = False``.
        sociallogin.save(request)
        audit(
            participant.user,
            "account.social_signup",
            participant.user,
            {"provider": provider_id(sociallogin), "email_verified": email_verified},
            request=request,
        )
        if not email_verified:
            # Konto czeka na link aktywacyjny, więc nie ma czego logować: ``is_active=False``
            # odbiłoby się o kontrolę allauth i skończyło stroną „konto nieaktywne”, czyli
            # komunikatem, który brzmi jak blokada, a nie jak „sprawdź skrzynkę”. Powiązanie
            # ``SocialAccount`` jest już zapisane, więc po aktywacji ten sam przycisk dostawcy
            # wpuszcza od razu, bez powtarzania formularza.
            messages.success(request, ACTIVATION_REQUIRED_MESSAGE)
            return redirect(reverse("web:login"))
        return flows.signup.complete_social_signup(request, sociallogin)


class AccountInactiveView(TemplateView):
    """Strona „konto nieaktywne”.

    Adres istnieje pod nazwą, której szuka allauth (``account_inactive``). W praktyce nie powinien
    być osiągany: konta nieaktywne odrzuca już ``SocialAccountAdapter.pre_social_login``, zanim
    allauth zdąży cokolwiek powiązać. Brak tej nazwy w urlconfie oznaczałby jednak, że awaryjna
    ścieżka allauth wywraca się na ``NoReverseMatch`` zamiast pokazać stronę.
    """

    template_name = "socialaccount/refused.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["reason"] = "inactive"
        return context
