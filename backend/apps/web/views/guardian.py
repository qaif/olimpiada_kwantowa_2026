"""Strona zgody opiekuna – jedyny ekran serwisu obsługiwany przez osobę **bez konta**.

Uprawnieniem jest podpisany token w adresie (``apps.accounts.guardian``), a nie sesja: opiekun
przychodzi po jedną czynność i zakładanie mu konta byłoby zebraniem większego zbioru danych niż
ten, po który przyszedł. Cała reguła (ważność linku, wiązanie z adresem, idempotencja
potwierdzenia) siedzi w serwisie; tutaj zostaje wyłącznie rozpakowanie tokenu, formularz i wybór
szablonu.

Dlaczego to nie jest strona w CMS-ie: treść zgody musi pochodzić z ``apps.accounts.consents``,
czyli z tego samego miejsca, z którego bierze ją wpis dowodowy. Strona redakcyjna dałaby się
zmienić bez zmiany wersji w dowodzie i po pierwszej takiej edycji nie dałoby się już odpowiedzieć
na pytanie „na co ta osoba się zgodziła”.
"""

from __future__ import annotations

from django import forms
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.accounts.guardian import (
    GUARDIAN_DAYS,
    confirm_consent,
    consent_text,
    consent_version,
    read_token,
)
from apps.core.api import DomainError

INVALID_TEMPLATE = "web/guardian/invalid.html"
FORM_TEMPLATE = "web/guardian/consent.html"


class GuardianConsentForm(forms.Form):
    """Jedno pole: świadome zaznaczenie. Formularz mieszka tutaj, bo nie używa go nikt inny.

    Pole jest wymagane po stronie serwera, a nie tylko atrybutem ``required`` w HTML: zgoda
    domyślnie zaznaczona albo przyjęta bez zaznaczenia nie jest zgodą (art. 4 pkt 11 RODO –
    „jednoznaczne okazanie woli”).
    """

    agree = forms.BooleanField(
        label=_("Wyrażam zgodę na udział mojego dziecka w Olimpiadzie Kwantowej."),
        error_messages={"required": _("Zaznacz pole zgody, żeby ją potwierdzić.")},
    )


class GuardianConsentView(View):
    """``/zgoda/<token>/`` – treść zgody (GET) i jej potwierdzenie (POST)."""

    def get(self, request, token: str):
        try:
            participant = read_token(token)
        except DomainError as exc:
            return self._invalid(request, exc)
        return self._render(request, participant, token, GuardianConsentForm())

    def post(self, request, token: str):
        try:
            participant = read_token(token)
        except DomainError as exc:
            return self._invalid(request, exc)
        form = GuardianConsentForm(request.POST)
        if not form.is_valid():
            return self._render(request, participant, token, form, status=400)
        confirm_consent(participant, request=request)
        # Przekierowanie po POST, a nie strona podziękowania wprost: odświeżenie ekranu wyniku
        # nie może wysyłać formularza po raz drugi (wzorzec POST/redirect/GET).
        return redirect(reverse("web:guardian-consent-done"))

    def _render(self, request, participant, token: str, form, *, status: int = 200):
        context = {
            "token": token,
            # Imię i szkoła – tyle, żeby opiekun rozpoznał, czego dotyczy prośba. Nazwiska nie
            # pokazujemy: link bywa przekazany dalej albo trafia pod adres z literówką, a imię
            # i nazwa szkoły w zupełności wystarczają do rozpoznania własnego dziecka.
            "first_name": participant.user.first_name,
            "school": participant.school,
            "consent_text": consent_text(),
            "consent_version": consent_version(),
            "valid_days": GUARDIAN_DAYS,
            "form": form,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)

    def _invalid(self, request, exc: DomainError):
        return TemplateResponse(
            request, INVALID_TEMPLATE, {"detail": str(exc.detail)}, status=exc.status_code
        )


class GuardianConsentDoneView(TemplateView):
    """Potwierdzenie zapisania zgody. Bez tokenu w adresie – niczego już nie identyfikuje."""

    template_name = "web/guardian/done.html"
