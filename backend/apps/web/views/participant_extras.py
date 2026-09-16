"""Akcje panelu uczestnika dołożone poza pulpitem: prośba o zgodę opiekuna i ustawienia konta.

Osobny moduł od ``participant.py``, bo to inny przedmiot. Tam jest pulpit etapu (rejestracja,
upload, wyniki, reklamacje); tutaj – dwie czynności, które z etapem nie mają nic wspólnego:
poproszenie opiekuna o zgodę i przestawienie języka oraz kontrastu interfejsu.

Reguły są w serwisach (``apps.accounts.guardian``, ``apps.accounts.preferences``); te widoki
wyłącznie je wołają i zamieniają ``DomainError`` na komunikat (``ActionViewMixin``).
"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import View
from rest_framework import status

from apps.accounts.guardian import request_consent
from apps.accounts.preferences import (
    LANGUAGE_COOKIE_MAX_AGE,
    available_languages,
    safe_next_url,
    save_preferences,
)
from apps.core.api import DomainError
from apps.web.mixins import ActionViewMixin, ParticipantRequiredMixin


def _form_error(message: str) -> DomainError:
    """Błąd formularza w tej samej postaci, co odmowa serwisu – ``ActionViewMixin`` zna tylko tę."""
    return DomainError(message, "INVALID_INPUT", status.HTTP_400_BAD_REQUEST)


class GuardianEmailForm(forms.Form):
    """Adres opiekuna. Formularz mieszka przy widoku, bo nie używa go nikt inny.

    Samego kształtu pilnuje ``EmailField``; reguły („uczestnik nie może być własnym opiekunem”,
    „pełnoletni nie potrzebuje zgody”) są w serwisie, żeby API i panel nie mogły się rozjechać.
    """

    guardian_email = forms.EmailField(
        label=gettext_lazy("Adres e-mail rodzica lub opiekuna prawnego"),
        error_messages={"required": gettext_lazy("Podaj adres e-mail rodzica lub opiekuna prawnego.")},
    )


class GuardianRequestView(ActionViewMixin, ParticipantRequiredMixin, View):
    """``POST /me/guardian/`` – zapisuje adres opiekuna i wysyła na niego prośbę o zgodę.

    Jeden adres na dwa przypadki (pierwsza prośba i „wyślij ponownie”), bo z punktu widzenia
    uczestnika to jedna czynność: „poproś opiekuna”. Powtórna wysyłka jest zamierzona i nie jest
    błędem – list ginie w spamie częściej, niż ktokolwiek chciałby przyznać.
    """

    success_url = reverse_lazy("web:me")

    def perform(self, request) -> str:
        form = GuardianEmailForm(request.POST)
        if not form.is_valid():
            # Formularz ma jedno pole, więc „pierwszy błąd” jest tu błędem jedynym.
            message = " ".join(message for errors in form.errors.values() for message in errors)
            raise _form_error(message)
        email = request_consent(
            self.participant, form.cleaned_data["guardian_email"], actor=request.user, request=request
        )
        return _("Prośba o zgodę została wysłana na adres %(email)s.") % {"email": email}


class PreferencesView(View):
    """``POST /account/preferences/`` – język interfejsu i tryb wysokiego kontrastu.

    Dostępne dla **każdego** konta, także niezalogowanego gościa: kontrast i język są ustawieniem
    przeglądającego, a nie uprawnieniem. Dla zalogowanego zapisujemy je w ``UserPreference``
    (wtedy jadą za nim na inne urządzenie), dla gościa – w ciasteczku języka i w sesji. Reguła
    i zapis siedzą w ``apps.accounts.preferences``; tutaj zostaje odczytanie dwóch pól i powrót
    na stronę, z której przyszło żądanie.

    Powrót idzie przez ``safe_next_url``: adres z pola ``next`` jest danymi od nadawcy żądania,
    więc otwarte przekierowanie byłoby tu gotowym narzędziem do phishingu.
    """

    def post(self, request):
        language = request.POST.get("language") or ""
        if language not in available_languages():
            language = ""
        high_contrast = request.POST.get("high_contrast") == "1"
        state = save_preferences(request, language=language, high_contrast=high_contrast)
        response = HttpResponseRedirect(safe_next_url(request))
        # Ciasteczko języka ustawiamy sami, zamiast montować ``django.views.i18n.set_language``:
        # ten widok zapisuje **dwie** rzeczy naraz, a dwa formularze na jeden przełącznik
        # rozjechałyby się przy pierwszym przeglądaniu bez JavaScriptu.
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            state["language"],
            max_age=LANGUAGE_COOKIE_MAX_AGE,
            samesite="Lax",
            secure=request.is_secure(),
        )
        return response
