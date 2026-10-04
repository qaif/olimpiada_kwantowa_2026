"""Panel opiekuna drużyny narodowej ``/delegation/`` i przyjęcie zaproszenia (DEL-01).

Panel jest **wąski z założenia**: dane delegacji, współopiekunowie i lista uczniów kraju, a przy
uczniu – dodanie, poprawka przed aktywacją, usunięcie przed startem zawodów i ponowny list. Prac,
ocen i wyników tu nie ma i nie będzie: opiekun drużyny zgłasza uczniów, a nie ocenia ich.

Uprawnienie ma dwa piętra i oba są konieczne:

1. **rola** ``team_leader`` w konkursie (``has_role``) – inaczej 403, tak jak każdy panel roli,
2. **wiersz** ``DelegationLeader`` w bieżącej edycji tego konkursu
   (``delegation_services.leader_for``) – inaczej 404: rola bez delegacji (opiekun odwołany albo
   z innego konkursu przy globalnej grupie) nie ma tu czego oglądać.

Uczeń jest zawsze wybierany z querysetu delegacji opiekuna (``student_of``), więc identyfikator
ucznia innego kraju albo innego konkursu daje 404 – nie istnieje „tutaj”.

Ekrany są tłumaczone (gettext, msgid po polsku): olimpiada międzynarodowa mówi po angielsku
i w dziesięciu innych językach, a opiekun drużyny jest jej uczestnikiem, nie organizatorem.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import TemplateView, View

from apps.accounts import delegation_services as service
from apps.accounts.models import CompetitionRole
from apps.accounts.services import has_role
from apps.core.api import DomainError
from apps.web.delegation_forms import LeaderConfirmForm, LeaderSignupForm, StudentForm
from apps.web.mixins import RoleRequiredMixin
from apps.web.throttle import ThrottledFormMixin

DASHBOARD_TEMPLATE = "web/delegation/dashboard.html"
STUDENT_FORM_TEMPLATE = "web/delegation/student_form.html"
STUDENT_DELETE_TEMPLATE = "web/delegation/student_delete.html"
ACCEPT_TEMPLATE = "web/delegation/accept.html"
ACCEPT_INVALID_TEMPLATE = "web/delegation/accept_invalid.html"
ACCEPT_DONE_TEMPLATE = "web/delegation/accept_done.html"
NO_DELEGATION_TEMPLATE = "web/delegation/no_delegation.html"


#: Komunikat po wypisaniu ucznia – zależnie od tego, czy konto zniknęło, czy tylko zostało odpięte.
MESSAGES_REMOVED = {
    "deleted": gettext_lazy("Uczeń został wypisany z drużyny."),
    "anonymised": gettext_lazy("Uczeń został wypisany z drużyny."),
    "unlinked": gettext_lazy(
        "Uczeń został wypisany z drużyny. Jego konto zostaje – o dalszym udziale zdecyduje organizator."
    ),
}


class TeamLeaderRequiredMixin(RoleRequiredMixin):
    """Opiekun drużyny **z delegacją w bieżącej edycji tego konkursu** (patrz docstring modułu)."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla opiekunów drużyn narodowych."

    def has_role(self, user) -> bool:
        return has_role(user, self.competition, CompetitionRole.TEAM_LEADER)

    def dispatch(self, request, *args, **kwargs):
        # Kolejność jak w pozostałych ekranach za przełącznikiem: anonim → logowanie, zła rola → 403
        # (``RoleRequiredMixin``), a dopiero osoba z rolą dowiaduje się, czy ekran tu istnieje (404).
        if request.user.is_authenticated and self.has_role(request.user):
            service.require_delegations(self.competition)
            self.leader = service.leader_for(request.user, self.competition)
            if self.leader is None:
                # Opiekun bez delegacji w **bieżącej** edycji (prowadził drużynę rok temu albo został
                # odwołany) dostaje wyjaśnienie, a nie 404 – ma rolę, więc ten adres dla niego
                # istnieje; brakuje tylko drużyny (poprawka po przeglądzie).
                return TemplateResponse(request, NO_DELEGATION_TEMPLATE, {}, status=200)
        return super().dispatch(request, *args, **kwargs)

    @property
    def delegation(self):
        return self.leader.delegation


class DelegationDashboardView(TeamLeaderRequiredMixin, TemplateView):
    """``/delegation/`` – delegacja, współopiekunowie i uczniowie kraju."""

    template_name = DASHBOARD_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        delegation = self.delegation
        students = list(service.students_of(delegation))
        rows = [{"participant": p, "active": service.is_activated(p)} for p in students]
        window_open = service.registration_window_open(delegation)
        started = service.stage_started(delegation.edition)
        context.update(
            {
                "delegation": delegation,
                "leaders": service.leaders_of(delegation),
                "rows": rows,
                "student_count": len(students),
                "seats_left": max(0, delegation.max_students - len(students)),
                "window_open": window_open,
                "stage_started": started,
                "can_add": delegation.is_open and window_open and len(students) < delegation.max_students,
            }
        )
        # Sekcja „Opłaty” (PAY-01) – pusty kontekst, gdy konkurs nie pobiera opłat (flaga ``fees``).
        from apps.payments.services import delegation_card

        context.update(delegation_card(delegation))
        return context


class StudentAddView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``/delegation/students/add/`` – zgłoszenie ucznia. Limit, kraj i okno rozstrzyga serwis."""

    throttle_scope = "delegation"

    def get(self, request):
        return self._render(request, StudentForm(competition=self.competition))

    def post(self, request):
        form = StudentForm(request.POST, competition=self.competition)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            service.add_student(self.leader, **form.cleaned_data, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=400)
        messages.success(request, _("Uczeń został zgłoszony – wysłaliśmy mu link do uruchomienia konta."))
        return redirect(reverse("web:delegation"))

    def _render(self, request, form, *, status: int = 200):
        return TemplateResponse(
            request,
            STUDENT_FORM_TEMPLATE,
            {"form": form, "delegation": self.delegation, "editing": False},
            status=status,
        )


class StudentEditView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``/delegation/students/<pk>/edit/`` – poprawka danych **przed** aktywacją konta ucznia."""

    throttle_scope = "delegation"

    def get(self, request, pk: int):
        participant = service.student_of(self.leader, pk)
        if service.is_activated(participant):
            # Po uruchomieniu konta dane należą do ucznia (poprawia je sam) – formularz byłby obietnicą
            # zapisu, którą serwis i tak odrzuci; zamiast niego strona tylko do odczytu (409).
            return self._render(request, participant, None, status=409)
        initial = {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "email": participant.user.email,
            "birth_date": participant.birth_date,
            "school": participant.school,
            "grade": participant.grade,
            "guardian_email": participant.guardian_email,
        }
        return self._render(request, participant, StudentForm(initial=initial, competition=self.competition))

    def post(self, request, pk: int):
        participant = service.student_of(self.leader, pk)
        form = StudentForm(request.POST, competition=self.competition)
        if not form.is_valid():
            return self._render(request, participant, form, status=400)
        try:
            service.update_student(self.leader, participant, **form.cleaned_data, request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, participant, form, status=400)
        messages.success(request, _("Dane ucznia zostały zapisane."))
        return redirect(reverse("web:delegation"))

    def _render(self, request, participant, form, *, status: int = 200):
        return TemplateResponse(
            request,
            STUDENT_FORM_TEMPLATE,
            {
                "form": form,
                "delegation": self.delegation,
                "participant": participant,
                "editing": True,
                "active": service.is_activated(participant),
            },
            status=status,
        )


class StudentDeleteView(TeamLeaderRequiredMixin, View):
    """``/delegation/students/<pk>/delete/`` – potwierdzenie (GET) i wypisanie ucznia (POST)."""

    def get(self, request, pk: int):
        participant = service.student_of(self.leader, pk)
        return TemplateResponse(
            request,
            STUDENT_DELETE_TEMPLATE,
            {
                "participant": participant,
                "delegation": self.delegation,
                "active": service.is_activated(participant),
            },
        )

    def post(self, request, pk: int):
        participant = service.student_of(self.leader, pk)
        try:
            result = service.remove_student(self.leader, participant, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, MESSAGES_REMOVED[result])
        return redirect(reverse("web:delegation"))


class StudentResendView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``POST /delegation/students/<pk>/resend/`` – ponowny list do ucznia, który nie uruchomił konta."""

    throttle_scope = "delegation"

    def post(self, request, pk: int):
        participant = service.student_of(self.leader, pk)
        try:
            service.resend_student_invitation(self.leader, participant, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Wysłaliśmy link ponownie."))
        return redirect(reverse("web:delegation"))


# --- przyjęcie zaproszenia ---------------------------------------------------------------------


class LeaderInvitationView(ThrottledFormMixin, View):
    """``/delegation/accept/<token>/`` – przyjęcie zaproszenia opiekuna drużyny.

    Cztery stany ekranu, rozstrzygane z adresu zaproszenia i sesji:

    - **gość, adres bez konta** – formularz nowego konta (imię, nazwisko, hasło, zgody),
    - **gość, adres z kontem** – prośba o zalogowanie z powrotem na ten adres; zaproszenie nie
      przejmuje cudzego konta ani nie zmienia jego hasła,
    - **zalogowany adresat** – same zgody i „Przyjmuję”,
    - **zalogowany ktoś inny** – odmowa z przyciskiem wylogowania: link przekazany dalej nie robi
      opiekunem tego, kto go otworzył.

    Limit żądań: scope ``register`` – ekran zakłada konta i przyjmuje token, więc chroni go to samo,
    co rejestrację (zgadywanie tokenów, seryjne zakładanie kont).
    """

    throttle_scope = "register"

    def _invitation(self, request, token: str):
        return service.read_leader_invitation(token, getattr(request, "competition", None))

    def get(self, request, token: str):
        try:
            invitation = self._invitation(request, token)
        except DomainError as exc:
            return self._invalid(request, exc)
        return self._render(request, invitation, token, self._form(request, invitation))

    def post(self, request, token: str):
        try:
            invitation = self._invitation(request, token)
        except DomainError as exc:
            return self._invalid(request, exc)
        state = self._state(request, invitation)
        if state not in ("signup", "confirm"):
            return self._render(request, invitation, token, None, status=400)
        form = self._form(request, invitation, request.POST)
        if not form.is_valid():
            return self._render(request, invitation, token, form, status=400)
        data = dict(form.cleaned_data)
        given = form.given()
        user = request.user if state == "confirm" else None
        try:
            service.accept_leader_invitation(
                invitation,
                user=user,
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
                password=data.get("password", ""),
                given=given,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, invitation, token, form, status=400)
        if state == "confirm":
            messages.success(request, _("Zaproszenie przyjęte – możesz zgłaszać uczniów swojej drużyny."))
            return redirect(reverse("web:delegation"))
        # Nowe konto nie jest logowane automatycznie – tak samo jak po przyjęciu zaproszenia ucznia:
        # pierwsze logowanie świeżo ustawionym hasłem sprawdza, czy zostało zapamiętane.
        return redirect(reverse("web:delegation-accept-done"))

    def _state(self, request, invitation) -> str:
        if request.user.is_authenticated:
            if request.user.email.strip().lower() == invitation.email:
                return "confirm"
            return "mismatch"
        if service.account_exists(invitation.email):
            return "login"
        return "signup"

    def _form(self, request, invitation, data=None):
        state = self._state(request, invitation)
        if state == "signup":
            return LeaderSignupForm(data, email=invitation.email)
        if state == "confirm":
            return LeaderConfirmForm(data)
        return None

    def _render(self, request, invitation, token: str, form, *, status: int = 200):
        state = self._state(request, invitation)
        login_url = f"{reverse('web:login')}?next={reverse('web:delegation-accept', args=[token])}"
        context = {
            "invitation": invitation,
            "delegation": invitation.delegation,
            "state": state,
            "form": form,
            "accept_login_url": login_url,
            "token": token,
            "valid_until": invitation.expires_at,
        }
        return TemplateResponse(request, ACCEPT_TEMPLATE, context, status=status)

    def _invalid(self, request, exc: DomainError):
        return TemplateResponse(
            request, ACCEPT_INVALID_TEMPLATE, {"detail": str(exc.detail)}, status=exc.status_code
        )


class LeaderInvitationDoneView(TemplateView):
    """Po przyjęciu zaproszenia przez nowe konto – zaproszenie do zalogowania."""

    template_name = ACCEPT_DONE_TEMPLATE


class LeaderLogoutForInvitationView(View):
    """``POST`` – wylogowanie z powrotem na zaproszenie (stan „zalogowany ktoś inny”)."""

    def post(self, request, token: str):
        logout(request)
        return redirect(reverse("web:delegation-accept", args=[token]))
