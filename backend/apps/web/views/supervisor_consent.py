"""Strona zgody ucznia na opiekuna szkolnego (``/opiekun/zgoda/<token>/``, v0.38.7).

Prowadzi tu link z listu wysłanego przez import listy uczniów (``apps.accounts.supervisor_consent``).
Inaczej niż przy zgodzie opiekuna prawnego (``apps.web.views.guardian``) token **nie** wystarcza:
uczeń ma konto, więc stronę widzi wyłącznie zalogowany właściciel profilu. Link przekazany dalej,
otwarty przez rodzeństwo przy wspólnym komputerze albo – co tu najważniejsze – przez nauczyciela,
który prosi, nie pozwala zgodzić się za ucznia.

Trzy zasady, każda z innego powodu:

- **GET niczego nie zmienia.** Klienty pocztowe i skanery bezpieczeństwa otwierają linki z listów
  same, zanim zobaczy je człowiek; zgoda wyrażona przez skaner nie jest zgodą,
- **dwa przyciski POST, oba jawne** („Zgadzam się” / „Nie zgadzam się”), chronione CSRF jak każdy
  formularz serwisu. Brak domyślnej odpowiedzi jest celowy: zamknięcie strony też jest odmową,
  ale odmowa kliknięta zostawia ślad w audycie, a to bywa potrzebne przy sporze,
- **strona mówi, co nauczyciel zobaczy, i że zastąpi obecnego opiekuna**, jeśli uczeń go ma. Zgoda
  bez tej wiedzy nie byłaby świadoma – a opiekun jest jeden, więc „tak” jednemu znaczy „nie”
  drugiemu.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.supervisor_consent import (
    SUPERVISOR_CONSENT_DAYS,
    VIA_COORDINATOR,
    accept,
    ensure_owner,
    read_token,
    refuse,
    requester,
)
from apps.core.api import DomainError

FORM_TEMPLATE = "web/supervisor/consent.html"
INVALID_TEMPLATE = "web/supervisor/consent_invalid.html"

#: Wartości przycisku ``decision``. Wszystko inne jest błędem formularza, a nie odmową – „brak
#: odpowiedzi” nie może zostać zapisany jako decyzja ucznia.
DECISION_ACCEPT = "accept"
DECISION_REFUSE = "refuse"


class SupervisorConsentView(LoginRequiredMixin, View):
    """Treść prośby (GET) i decyzja ucznia (POST). Niezalogowany – na logowanie z powrotem tutaj."""

    def get(self, request, token: str):
        consent, error = self._consent(request, token)
        if error is not None:
            return error
        return self._render(request, consent, token)

    def post(self, request, token: str):
        consent, error = self._consent(request, token)
        if error is not None:
            return error
        decision = request.POST.get("decision")
        try:
            if decision == DECISION_ACCEPT:
                accept(consent, user=request.user, request=request)
                messages.success(
                    request,
                    "Dziękujemy – nauczyciel został zapisany jako Twój opiekun szkolny. Możesz to "
                    "w każdej chwili zmienić w swoim profilu.",
                )
            elif decision == DECISION_REFUSE:
                refuse(consent, user=request.user, request=request)
                messages.info(request, "Odmowa zapisana – Twój profil pozostał bez zmian.")
            else:
                return self._render(request, consent, token, status=400, missing_decision=True)
        except DomainError as exc:
            return self._invalid(request, exc)
        # POST/redirect/GET: odświeżenie strony wyniku nie może wysłać decyzji drugi raz.
        return redirect(reverse("web:me"))

    def _consent(self, request, token: str):
        """Prośba z tokenu i sprawdzenie właściciela – jedna droga dla GET i POST."""
        try:
            consent = read_token(token)
            ensure_owner(consent, request.user)
        except DomainError as exc:
            return None, self._invalid(request, exc)
        return consent, None

    def _render(self, request, consent, token: str, *, status: int = 200, missing_decision: bool = False):
        context = {
            "token": token,
            "requester": requester(consent.supervisor_email),
            "via_coordinator": consent.via == VIA_COORDINATOR,
            # Obecny adres jest **własną** daną ucznia – wolno go pokazać, a bez niego zdanie
            # „zastąpi obecnego opiekuna” byłoby zagadką.
            "current_email": consent.current_email,
            "competition": consent.participant.competition,
            "valid_days": SUPERVISOR_CONSENT_DAYS,
            "missing_decision": missing_decision,
            "decision_accept": DECISION_ACCEPT,
            "decision_refuse": DECISION_REFUSE,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)

    def _invalid(self, request, exc: DomainError):
        return TemplateResponse(
            request, INVALID_TEMPLATE, {"detail": str(exc.detail)}, status=exc.status_code
        )
