"""Ekran „Zmień hasło” (``/account/password/``) i link do ustawienia hasła dla konta bez hasła.

Ekran jest wspólny dla **wszystkich** ról – wystarczy zalogowanie. Konto jest jedno na platformę,
a hasło nie należy do żadnej roli ani konkursu, więc nie ma tu bramki roli ani konkursu (tak samo
jak przy zmianie adresu e-mail w ``apps.web.views.account``). Reguły są w ``services``.

Kolejność domieszek jest częścią zabezpieczenia: ``LoginRequiredMixin`` stoi **przed** limitem,
więc gość dostaje przekierowanie do logowania, nie zużywając niczyjego kubełka, a ``never_cache``
owija całość – także to przekierowanie. Limit liczy konto, nie adres
(``apps.web.throttle.PerAccountThrottleMixin``).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.generic import FormView, View

from apps.core.api import DomainError
from apps.web.throttle import PerAccountThrottleMixin
from apps.web.views.account import profile_url, relogin_after_lock

from . import services
from .forms import PasswordChangeForm
from .notifications import djcms_note, is_djcms_editor

#: Pola, do których serwis przypina odmowę (po kodzie maszynowym). Reszta idzie nad formularz.
ERROR_FIELDS = {
    services.CODE_WRONG_CURRENT: "old_password",
    services.CODE_INVALID_NEW: "new_password1",
    services.CODE_UNCHANGED: "new_password1",
}


@method_decorator(never_cache, name="dispatch")
class PasswordChangeView(LoginRequiredMixin, PerAccountThrottleMixin, FormView):
    """``/account/password/`` – formularz zmiany hasła albo (konto bez hasła) przycisk linku."""

    template_name = "password_change/change.html"
    form_class = PasswordChangeForm
    throttle_scope = services.THROTTLE_SCOPE
    success_message = gettext_lazy(
        "Hasło zostało zmienione. Pozostałe urządzenia zostały wylogowane, a na adres konta wysłaliśmy "
        "potwierdzenie."
    )

    def post(self, request, *args, **kwargs):
        # Konto bez hasła nie ma czego „zmieniać” – formularz i tak by go nie przepuścił (serwis),
        # ale odpowiedź z błędem „złe aktualne hasło” byłaby myląca. Wraca na ekran z wyjaśnieniem.
        if not request.user.has_usable_password():
            return redirect("web:password-change")
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_password"] = self.request.user.has_usable_password()
        context["profile_url"] = profile_url(self.request)
        return context

    def form_valid(self, form):
        user = self.request.user
        try:
            services.change_password(
                user,
                old_password=form.cleaned_data["old_password"],
                new_password=form.cleaned_data["new_password1"],
                request=self.request,
            )
        except DomainError as exc:
            if exc.machine_code == services.CODE_LOCKED:
                return relogin_after_lock(self.request, str(exc.detail))
            form.add_error(ERROR_FIELDS.get(exc.machine_code), str(exc.detail))
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        if is_djcms_editor(user):
            # Sesji edytora django CMS na innych urządzeniach zmiana hasła nie kończy (osobna baza
            # sesji) – mówimy to wprost, zamiast obiecywać „wszystkie urządzenia” (przegląd L2).
            messages.info(self.request, djcms_note())
        return redirect(profile_url(self.request))


@method_decorator(never_cache, name="dispatch")
class PasswordSetLinkView(LoginRequiredMixin, PerAccountThrottleMixin, View):
    """``POST /account/password/set-link/`` – list resetu hasła na adres **własnego** konta bez hasła.

    Limit ze scope'em ``password_reset`` – ten sam budżet listów, co publiczny „Nie pamiętasz
    hasła?”, bo to ten sam list; liczony per konto, bo adresat jest zawsze jeden: właściciel sesji.
    """

    throttle_scope = "password_reset"
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            services.send_set_password_link(request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(
                request,
                gettext_lazy(
                    "Wysłaliśmy link do ustawienia hasła na adres konta. Link jest ważny 24 godziny."
                ),
            )
        return redirect("web:password-change")
