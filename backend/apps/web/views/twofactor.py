"""Ekrany drugiego składnika logowania: konfiguracja, potwierdzenie kodem i wyłączenie.

Trzy adresy i wyraźny podział ról między nimi:

- ``/account/2fa/`` – **konfiguracja**. Kod QR, sekret do przepisania, potwierdzenie kodem
  z aplikacji, a po włączeniu: lista kodów zapasowych pokazana raz i wyłącznie tu,
- ``/login/2fa/`` – **poczekalnia**. Jedyny adres (obok wylogowania), który
  ``apps.accounts.twofactor.TwoFactorMiddleware`` przepuszcza dla sesji po samym haśle,
- ``/account/2fa/disable/`` – **wyłączenie** własnego drugiego składnika.

Reguł domenowych nie ma tu ani jednej: wszystkie stoją w ``apps.accounts.twofactor`` (protokół,
jednorazowość kodu, audyt), bo tę samą czynność wykonuje też koordynator ze swojego panelu
i przyszły klient API. Widok wyłącznie orkiestruje i dobiera zdanie dla człowieka.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.generic import View

from apps.accounts import twofactor
from apps.core.api import DomainError
from apps.web.mixins import ActionViewMixin
from apps.web.throttle import ThrottledFormMixin

SETUP_TEMPLATE = "web/account/twofactor.html"
CODES_TEMPLATE = "web/account/twofactor_codes.html"
VERIFY_TEMPLATE = "web/account/twofactor_verify.html"


class TwoFactorFeatureMixin:
    """Przy wyłączonym ``TWO_FACTOR_ENABLED`` te adresy po prostu nie istnieją.

    **404, a nie 403 i nie przekierowanie.** Trzysta trzy odsyłałoby donikąd, a 403 mówiłoby
    „ta funkcja tu jest, tylko nie dla ciebie” – czyli nieprawdę. Wyłączona funkcja ma wyglądać
    dokładnie tak, jak funkcja, której w tym serwisie nigdy nie było: adres nie odpowiada,
    a w interfejsie nie ma do niego odnośnika.

    Sprawdzenie stoi w ``dispatch``, czyli **przed** ``LoginRequiredMixin`` w kolejności MRO
    tych widoków – dzięki temu wyłączony adres nie zaczyna od przekierowania na logowanie,
    po którym i tak skończyłby się czterysta czwórką.
    """

    def dispatch(self, request, *args, **kwargs):
        if not twofactor.is_enabled():
            raise Http404("Logowanie dwuskładnikowe jest wyłączone na tej instalacji.")
        return super().dispatch(request, *args, **kwargs)


#: Klucz sesji, pod którym świeżo wygenerowane kody zapasowe czekają na jedno wyświetlenie.
#: W sesji, a nie w kontekście odpowiedzi POST, bo po udanym zapisie robimy przekierowanie
#: (wzorzec POST → redirect → GET): bez niego odświeżenie strony z kodami wysyłałoby formularz
#: potwierdzenia po raz drugi. Klucz jest kasowany przy odczycie – kody widać dokładnie raz.
FRESH_CODES_SESSION_KEY = "2fa_fresh_codes"


class TwoFactorSetupView(TwoFactorFeatureMixin, LoginRequiredMixin, View):
    """``/account/2fa/`` – włączenie drugiego składnika na własnym koncie.

    GET zakłada niepotwierdzony wiersz z nowym sekretem, jeśli konto jeszcze go nie ma. Zapis
    dzieje się więc przy **odczycie** strony i to jest świadome: kod QR musi przeżyć odświeżenie
    strony i przełączenie się na telefon, a sekret bez potwierdzenia nie daje żadnych uprawnień
    (logowanie widzi wyłącznie wiersze potwierdzone). Konto, które ma już drugi składnik włączony,
    dostaje stronę informacyjną – tam nie generujemy niczego.
    """

    def get(self, request):
        device = twofactor.device_for(request.user)
        if device is not None and device.is_confirmed:
            return self._render_enabled(request, device)
        device = twofactor.begin_setup(request.user)
        return self._render_setup(request, device)

    def post(self, request):
        try:
            codes = twofactor.confirm_setup(request.user, request.POST.get("code", ""), request=request)
        except DomainError as exc:
            device = twofactor.device_for(request.user)
            if device is None or device.is_confirmed:
                messages.error(request, str(exc.detail))
                return redirect(reverse("web:twofactor-setup"))
            return self._render_setup(request, device, error=str(exc.detail), status=exc.status_code)
        # Sesja przechodzi bramkę od razu: człowiek właśnie udowodnił, że ma telefon w ręce.
        # Bez tego włączenie 2FA kończyłoby się natychmiastowym żądaniem kodu, który przed chwilą
        # wpisał – a to uczy, że zabezpieczenie jest uciążliwe bez powodu.
        twofactor.mark_verified(request)
        request.session[FRESH_CODES_SESSION_KEY] = codes
        return redirect(reverse("web:twofactor-codes"))

    def _render_setup(self, request, device, *, error: str = "", status: int = 200):
        secret = device.plain_secret() or ""
        uri = twofactor.provisioning_uri(request.user, secret) if secret else ""
        context = {
            "secret": secret,
            "otpauth_uri": uri,
            # ``mark_safe``: SVG powstaje w całości u nas, z macierzy liczb, i nie niesie ani
            # jednego znaku pochodzącego od użytkownika. Wstawiamy go w treść strony, a nie jako
            # ``<img src>``, żeby nie potrzebować wyjątku w polityce CSP dla ``data:``.
            "qr_svg": mark_safe(twofactor.qr_svg(uri)) if uri else "",  # noqa: S308
            "error": error,
            "digits": twofactor.CODE_DIGITS,
            "enabled": False,
        }
        return TemplateResponse(request, SETUP_TEMPLATE, context, status=status)

    def _render_enabled(self, request, device):
        context = {
            "enabled": True,
            "confirmed_at": device.confirmed_at,
            "backup_codes_left": device.backup_codes_left,
            "backup_codes_total": twofactor.BACKUP_CODE_COUNT,
            "last_used_at": device.last_used_at,
        }
        return TemplateResponse(request, SETUP_TEMPLATE, context)


class TwoFactorCodesView(TwoFactorFeatureMixin, LoginRequiredMixin, View):
    """``/account/2fa/codes/`` – kody zapasowe pokazane **raz**, zaraz po włączeniu.

    Wejście na ten adres bez świeżo wygenerowanych kodów wraca na ekran konfiguracji. Nie ma tu
    „pokaż mi je jeszcze raz”: w bazie są wyłącznie skróty, więc odtworzyć się ich nie da, i to
    jest cecha, a nie brak. Kto zgubił kartkę, wyłącza drugi składnik i włącza go od nowa.
    """

    def get(self, request):
        codes = request.session.pop(FRESH_CODES_SESSION_KEY, None)
        if not codes:
            return redirect(reverse("web:twofactor-setup"))
        return TemplateResponse(request, CODES_TEMPLATE, {"codes": codes})


class TwoFactorVerifyView(TwoFactorFeatureMixin, ThrottledFormMixin, LoginRequiredMixin, View):
    """``/login/2fa/`` – drugi krok logowania. Kod z aplikacji albo kod zapasowy.

    Limit (scope ``two_factor``) konsumuje **wyłącznie nieudaną** próbę, tak samo jak przy
    logowaniu hasłem: człowiek, który raz się pomylił przy przepisywaniu i zaraz poprawił,
    nie może zablokować sobie wejścia na własne konto w dniu zawodów.

    Konto, które drugiego składnika nie ma (albo już go potwierdziło w tej sesji), jest stąd
    odsyłane dalej – ten ekran nie ma prawa stać się kolejnym krokiem dla wszystkich.
    """

    throttle_scope = twofactor.THROTTLE_SCOPE
    throttle_on_request = False

    def get(self, request):
        redirection = self._skip_if_not_needed(request)
        return redirection or self._render(request)

    def post(self, request):
        redirection = self._skip_if_not_needed(request)
        if redirection is not None:
            return redirection
        if twofactor.verify(request.user, request.POST.get("code", ""), request=request):
            self.reset_throttle()
            twofactor.mark_verified(request)
            return redirect(self._next_url(request))
        self.consume_throttle()
        return self._render(
            request,
            error="Kod nie pasuje. Przepisz nowy kod z aplikacji albo użyj kodu zapasowego.",
            status=400,
        )

    def _skip_if_not_needed(self, request):
        if twofactor.session_is_verified(request):
            return redirect(self._next_url(request))
        if twofactor.confirmed_device(request.user) is None:
            # Konto bez drugiego składnika nie ma tu czego szukać; warstwa wymuszająca i tak
            # postawi znacznik przy następnym żądaniu.
            return redirect(self._next_url(request))
        return None

    def _next_url(self, request) -> str:
        """Adres po udanej weryfikacji. ``next`` przechodzi przez walidację Django (open redirect).

        Panel właściwy dla roli jako wartość domyślna – ta sama funkcja, co po zalogowaniu hasłem,
        żeby drugi składnik nie zmieniał tego, gdzie człowiek ląduje.
        """
        from django.utils.http import url_has_allowed_host_and_scheme

        from apps.web.views.public import default_panel_url

        candidate = request.POST.get("next") or request.GET.get("next") or ""
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return candidate
        return default_panel_url(request)

    def _render(self, request, *, error: str = "", status: int = 200):
        device = twofactor.confirmed_device(request.user)
        context = {
            "error": error,
            "digits": twofactor.CODE_DIGITS,
            "next": request.POST.get("next") or request.GET.get("next") or "",
            "backup_codes_left": device.backup_codes_left if device else 0,
        }
        return TemplateResponse(request, VERIFY_TEMPLATE, context, status=status)


class TwoFactorDisableView(TwoFactorFeatureMixin, LoginRequiredMixin, ActionViewMixin, View):
    """``/account/2fa/disable/`` – wyłączenie drugiego składnika na własnym koncie (POST).

    Bez ekranu potwierdzenia i bez pytania o hasło, i to wymaga uzasadnienia: żeby tu w ogóle
    dojść, sesja musiała już przejść **cały** drugi składnik (warstwa wymuszająca nie przepuszcza
    nikogo innego), czyli ten, kto klika, ma w ręce telefon albo kartkę z kodami. Dokładanie tu
    hasła chroniłoby przed scenariuszem „porzucona, w pełni zweryfikowana sesja” – a przed nim
    broni wylogowanie i termin ważności sesji, a nie kolejne pole.

    Konto z roli objętej ``TWO_FACTOR_REQUIRED_ROLES`` wyłączy drugi składnik i natychmiast
    zostanie odesłane z powrotem na ekran konfiguracji przez warstwę wymuszającą. To jest
    zachowanie prawidłowe: taka jest właśnie treść tego ustawienia.
    """

    def perform(self, request, *args, **kwargs) -> str:
        if not twofactor.disable(request.user, actor=request.user, request=request):
            raise DomainError("Na tym koncie nie ma włączonego drugiego składnika.")
        return "Drugi składnik logowania został wyłączony."

    def get_success_url(self, *args, **kwargs) -> str:
        return reverse("web:twofactor-setup")
