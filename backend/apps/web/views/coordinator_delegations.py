"""Ekran „Delegacje” ``/coordinator/delegations/`` – kraje, ich opiekunowie i uczniowie (DEL-01).

Ekran istnieje wyłącznie w konkursie z trybem rejestracji ``DELEGATIONS`` (``Competition.registration_mode``);
w każdym innym adres daje **404**, a menu nie ma tej pozycji – Olimpiada Kwantowa nie widzi tu
niczego. Rolę sprawdza wcześniej ``CoordinatorRequiredMixin``, więc uczestnik dostaje 403
niezależnie od trybu konkursu.

Co tu się dzieje: zaproszenie opiekuna (adres + kraj; delegacja kraju powstaje przy pierwszym
zaproszeniu, kolejny opiekun dołącza do istniejącej), ponowienie i cofnięcie zaproszenia,
odwołanie opiekuna, limit i zamknięcie delegacji oraz eksport CSV. **Każda czynność ma własny
adres POST** – ta sama reguła, co na ekranie regionów: rozróżnianie po nazwie przycisku zależy od
przeglądarki.

Cała logika jest w ``apps.accounts.delegation_services``; widok wybiera obiekt z querysetu
zawężonego do konkursu żądania (``delegation_for``) i zamienia ``DomainError`` na komunikat.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts import delegation_services as service
from apps.accounts.delegations import DelegationInvitation, DelegationLeader
from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.core.models import audit
from apps.web.delegation_forms import DelegationEditForm, LeaderInviteForm
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

LIST_TEMPLATE = "web/coordinator/delegations.html"
DETAIL_TEMPLATE = "web/coordinator/delegation_detail.html"


class DelegationScreenMixin(CoordinatorRequiredMixin):
    """Rola koordynatora, tryb delegacji konkursu i zakres querysetu."""

    def competition_or_404(self, request):
        competition = getattr(request, "competition", None)
        service.require_delegations(competition)
        return competition


class DelegationListView(DelegationScreenMixin, View):
    """``GET /coordinator/delegations/`` – lista krajów i formularz „Zaproś opiekuna”."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return self._render(
            request, competition, LeaderInviteForm(countries=service.country_choices(competition))
        )

    def _render(self, request, competition, form, *, status: int = 200):
        try:
            delegations = list(service.delegations_of(competition))
            no_edition = False
        except DomainError:
            delegations, no_edition = [], True
        context = {
            "competition": competition,
            "delegations": delegations,
            "form": form,
            "no_edition": no_edition,
            "no_countries": not service.country_choices(competition),
            "default_limit": competition.delegation_max_students,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


class LeaderInviteView(DelegationScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/delegations/invite/`` – zaproszenie opiekuna drużyny kraju."""

    throttle_scope = "delegation"

    def post(self, request):
        competition = self.competition_or_404(request)
        form = LeaderInviteForm(request.POST, countries=service.country_choices(competition))
        if not form.is_valid():
            return DelegationListView()._render(request, competition, form, status=400)
        try:
            invitation = service.invite_leader(
                competition,
                email=form.cleaned_data["email"],
                country_code=form.cleaned_data["country"],
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return DelegationListView()._render(request, competition, form, status=400)
        messages.success(
            request,
            f"Zaproszenie wysłane na {invitation.email} (delegacja: {invitation.delegation.country.name}).",
        )
        return redirect(reverse("web:coordinator-delegations"))


class DelegationDetailView(DelegationScreenMixin, View):
    """``/coordinator/delegations/<pk>/`` – opiekunowie, zaproszenia, uczniowie i ustawienia delegacji."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        delegation = service.delegation_for(competition, pk)
        form = DelegationEditForm(
            initial={
                "max_students": delegation.max_students,
                "status": delegation.status,
                "note": delegation.note,
            }
        )
        return self._render(request, delegation, form)

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        delegation = service.delegation_for(competition, pk)
        form = DelegationEditForm(request.POST)
        if not form.is_valid():
            return self._render(request, delegation, form, status=400)
        try:
            service.update_delegation(
                delegation,
                max_students=form.cleaned_data["max_students"],
                status_value=form.cleaned_data["status"],
                note=form.cleaned_data["note"],
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, delegation, form, status=400)
        messages.success(request, "Ustawienia delegacji zostały zapisane.")
        return redirect(reverse("web:coordinator-delegation", args=[delegation.pk]))

    def _render(self, request, delegation, form, *, status: int = 200):
        students = list(service.students_of(delegation))
        context = {
            "delegation": delegation,
            "form": form,
            "leaders": service.leaders_of(delegation),
            "invitations": [
                invitation
                for invitation in service.invitations_of(delegation)
                if invitation.accepted_at is None
            ],
            "rows": [{"participant": p, "active": service.is_activated(p)} for p in students],
            # Uczniowie z uruchomionym kontem wypisani przez opiekuna – czekają na decyzję koordynatora.
            "unlinked": list(service.unlinked_students(delegation)),
            "student_count": len(students),
        }
        return TemplateResponse(request, DETAIL_TEMPLATE, context, status=status)


class _InvitationActionMixin(DelegationScreenMixin):
    """Wspólna droga czynności na zaproszeniu: zakres konkursu, serwis, komunikat, powrót."""

    def invitation_or_404(self, competition, pk: int) -> DelegationInvitation:
        invitation = (
            DelegationInvitation.objects.for_competition(competition)
            .select_related("delegation", "delegation__country", "delegation__competition")
            .filter(pk=pk)
            .first()
        )
        if invitation is None:
            raise Http404("Nie ma takiego zaproszenia w tym konkursie.")
        return invitation


class InvitationResendView(_InvitationActionMixin, ThrottledFormMixin, View):
    """``POST /coordinator/delegations/invitations/<pk>/resend/`` – nowy token i nowy list."""

    throttle_scope = "delegation"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        invitation = self.invitation_or_404(competition, pk)
        try:
            service.resend_leader_invitation(invitation, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Zaproszenie wysłane ponownie na {invitation.email}.")
        return redirect(reverse("web:coordinator-delegation", args=[invitation.delegation_id]))


class InvitationRevokeView(_InvitationActionMixin, View):
    """``POST /coordinator/delegations/invitations/<pk>/revoke/`` – link przestaje działać."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        invitation = self.invitation_or_404(competition, pk)
        try:
            service.revoke_leader_invitation(invitation, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Zaproszenie dla {invitation.email} zostało cofnięte.")
        return redirect(reverse("web:coordinator-delegation", args=[invitation.delegation_id]))


class LeaderRemoveView(DelegationScreenMixin, View):
    """``POST /coordinator/delegations/leaders/<pk>/remove/`` – odwołanie opiekuna z delegacji."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        leader = (
            DelegationLeader.objects.for_competition(competition)
            .active()
            .select_related("user", "delegation", "delegation__competition")
            .filter(pk=pk)
            .first()
        )
        if leader is None:
            raise Http404("Nie ma takiego opiekuna w tym konkursie.")
        delegation_id = leader.delegation_id
        name = leader.user.get_full_name() or leader.user.email
        service.remove_leader(leader, actor=request.user, request=request)
        messages.success(
            request, f"{name} nie jest już opiekunem tej delegacji. Uczniowie zostali w drużynie."
        )
        return redirect(reverse("web:coordinator-delegation", args=[delegation_id]))


class DelegationExportView(DelegationScreenMixin, View):
    """``GET /coordinator/delegations/export.csv`` – opiekunowie i uczniowie wszystkich krajów."""

    def get(self, request):
        competition = self.competition_or_404(request)
        try:
            dataset = service.export_dataset(competition)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator-delegations"))
        # Eksport danych osobowych jest zdarzeniem w audycie – kto i kiedy wyniósł listę ludzi.
        audit(request.user, "delegation.exported", competition, {"rows": dataset.count}, request=request)
        return csv_response(dataset)
