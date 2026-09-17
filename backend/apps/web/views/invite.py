"""Przyjęcie zaproszenia do olimpiady – ekran ucznia, którego zgłosił nauczyciel.

Drugi (po zgodzie opiekuna) ekran serwisu obsługiwany **bez zalogowania**: uprawnieniem jest
podpisany token w adresie, a nie sesja. Uczeń nie ma jeszcze czym się zalogować – konto założone
importem nie ma używalnego hasła i jest nieaktywne, i to jest sedno przepływu: hasło ustawia tu
on sam, a nie nauczyciel.

Co ten ekran zbiera i dlaczego akurat to:

- **hasło** – poświadczenie ma należeć do ucznia. Hasło rozdane przez osobę trzecią nie jest
  poświadczeniem, tylko kontem współdzielonym,
- **zgody** – regulamin, RODO i (dla niepełnoletnich) oświadczenie o zgodzie opiekuna.
  Dokładnie ten sam blok, co w rejestracji (``ConsentFieldsMixin``), bo to ta sama czynność
  prawna i musi mieć to samo brzmienie oraz tę samą wersję dokumentu,
- **województwo i telefon** – dwie dane, których nie ma w liście klasowej i których nauczyciel
  nie ma obowiązku znać. Reszta (imię, nazwisko, szkoła, klasa, rocznik) przyszła z listy
  i nie jest tu przepisywana drugi raz.

Czego tu **nie** ma:

- **pola adresu e-mail.** Adres pochodzi z zaproszenia i jest tylko pokazany; edytowalny
  pozwalałby przenieść cudze zaproszenie na własną skrzynkę,
- **CAPTCHY.** Wstępem jest podpisany link wysłany pod konkretny adres – bot musiałby go
  najpierw przechwycić. Limit żądań (scope ``register``) zostaje, bo chroni przed czymś innym:
  przed zgadywaniem tokenów i przed maszynowym wypełnianiem tego formularza.

Cała reguła (ważność linku, wiązanie z adresem, jednorazowość, kolejność zapisu) siedzi
w ``apps.accounts.bulk_registration``; tutaj jest wyłącznie orkiestracja i wybór szablonu.
"""

from __future__ import annotations

from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from apps.accounts.bulk_registration import INVITE_DAYS, accept_invitation, read_invite_token
from apps.accounts.consents import CONSENT_FIELD_NAMES
from apps.core.api import DomainError
from apps.web.forms import (
    PASSWORD_CONFIRM_FIELD,
    REQUIRED_CSS_CLASS,
    ConsentFieldsMixin,
    clean_password_pair,
    password_field,
    phone_field,
    voivodeship_field,
)
from apps.web.throttle import ThrottledFormMixin

FORM_TEMPLATE = "web/invite/accept.html"
INVALID_TEMPLATE = "web/invite/invalid.html"
DONE_TEMPLATE = "web/invite/done.html"


class InviteAcceptForm(ConsentFieldsMixin):
    """Hasło, dwie brakujące dane i komplet zgód. Formularz mieszka tutaj – nikt inny go nie używa.

    ``ConsentFieldsMixin`` dokłada blok zgód z ``apps.accounts.consents``, razem z regułą
    „niepełnoletni musi mieć zgodę opiekuna” postawioną pod właściwym polem. Rocznika w tym
    formularzu **nie ma** (przyszedł z listy klasowej), więc regułę wieku egzekwuje serwis –
    domieszka milczy, gdy nie ma czego porównać, a ``accept_invitation`` rozstrzyga po roczniku
    zapisanym w profilu.
    """

    required_css_class = REQUIRED_CSS_CLASS
    field_order = ["password", PASSWORD_CONFIRM_FIELD, "phone", "district", *CONSENT_FIELD_NAMES]

    password = password_field()
    password2 = password_field("Powtórz hasło")
    phone = phone_field()
    district = voivodeship_field("Województwo")

    def clean(self):
        return clean_password_pair(self, super().clean())


class StudentInviteView(ThrottledFormMixin, View):
    """``/zaproszenie/<token>/`` – treść zaproszenia (GET) i jego przyjęcie (POST)."""

    throttle_scope = "register"

    def get(self, request, token: str):
        try:
            participant = read_invite_token(token)
        except DomainError as exc:
            return self._invalid(request, exc)
        return self._render(request, participant, token, self._initial_form(participant))

    def post(self, request, token: str):
        try:
            participant = read_invite_token(token)
        except DomainError as exc:
            return self._invalid(request, exc)
        form = InviteAcceptForm(request.POST)
        if not form.is_valid():
            return self._render(request, participant, token, form, status=400)
        data = dict(form.cleaned_data)
        given = {name: bool(data.pop(name, False)) for name in CONSENT_FIELD_NAMES}
        try:
            accept_invitation(participant, **data, given=given, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, participant, token, form, status=400)
        # Przekierowanie po POST, a nie strona wprost: odświeżenie ekranu wyniku nie może wysyłać
        # formularza po raz drugi. Konto jest już aktywne, ale **nie logujemy** go tu automatycznie
        # – pierwsze logowanie świeżo ustawionym hasłem jest sprawdzeniem, czy uczeń je zapamiętał,
        # a nie zbędnym krokiem.
        return redirect(reverse("web:student-invite-done"))

    def _initial_form(self, participant) -> InviteAcceptForm:
        """Wartości początkowe: to, co nauczyciel zdążył podać, i nic ponadto."""
        return InviteAcceptForm(initial={"phone": participant.phone, "district": participant.district or ""})

    def _render(self, request, participant, token: str, form, *, status: int = 200):
        context = {
            "token": token,
            "form": form,
            "participant": participant,
            "email": participant.user.email,
            "valid_days": INVITE_DAYS,
            "consent_field_names": CONSENT_FIELD_NAMES,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)

    def _invalid(self, request, exc: DomainError):
        return TemplateResponse(
            request, INVALID_TEMPLATE, {"detail": str(exc.detail)}, status=exc.status_code
        )


class StudentInviteDoneView(TemplateView):
    """Potwierdzenie przyjęcia zaproszenia. Bez tokenu w adresie – niczego już nie identyfikuje."""

    template_name = DONE_TEMPLATE
