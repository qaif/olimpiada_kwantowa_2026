"""Ekrany płacącego: opłaty delegacji (opiekun drużyny), opłata uczestnika i strona zamówienia.

Uprawnienia mają dwa piętra, jak w panelu delegacji: rola (``TeamLeaderRequiredMixin`` /
``ParticipantRequiredMixin`` – inaczej 403) i obiekt z zakresu konkursu (zamówienie, którego ta osoba
jest płacącym – inaczej 404; ``services.order_for_payer``). Flaga ``fees`` jest sprawdzana po roli:
osoba bez roli dostaje 403, zanim odpowiedź zdradzi, czy konkurs pobiera opłaty.

Ekrany nie mają JavaScriptu: „Zapłać kartą” to formularz POST z tokenem CSRF, a serwer odpowiada
przekierowaniem 302 na stronę dostawcy. Kwota nie jest polem formularza – liczy ją serwer.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.core.api import DomainError
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.delegation import TeamLeaderRequiredMixin

from .. import documents, pricing
from .. import services as service
from ..forms import BillingForm
from ..models import BuyerType, Order, OrderStatus, Provider

DELEGATION_TEMPLATE = "payments/delegation.html"
BILLING_TEMPLATE = "payments/billing.html"
ORDER_TEMPLATE = "payments/order.html"
PARTICIPANT_TEMPLATE = "payments/participant_pay.html"


def _profile_initial(profile) -> dict:
    if profile is None:
        return {"buyer_type": BuyerType.INSTITUTION}
    return {name: getattr(profile, name) for name in (*service.BUYER_FIELDS, "observers")}


# --- opiekun drużyny ------------------------------------------------------------------------------


class DelegationPaymentsView(TeamLeaderRequiredMixin, View):
    """``GET /delegation/payments/`` – zestawienie opłat drużyny, zamówienia i dokumenty."""

    def get(self, request):
        service.require_enabled(self.competition)
        delegation = self.delegation
        statement = pricing.statement(delegation)
        orders = list(
            Order.objects.filter(delegation=delegation)
            .prefetch_related("documents", "lines")
            .order_by("-created_at")
        )
        return TemplateResponse(
            request,
            DELEGATION_TEMPLATE,
            {
                "delegation": delegation,
                "statement": statement,
                "profile": service.billing_profile_of(delegation=delegation),
                "orders": orders,
                "kind_labels": dict(BuyerType.choices),
            },
        )


class DelegationBillingView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``/delegation/payments/billing/`` – dane nabywcy i liczba obserwatorów."""

    throttle_scope = "delegation"

    def get(self, request):
        service.require_enabled(self.competition)
        profile = service.billing_profile_of(delegation=self.delegation)
        return self._render(request, BillingForm(initial=_profile_initial(profile), observers=True))

    def post(self, request):
        service.require_enabled(self.competition)
        form = BillingForm(request.POST, observers=True)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            service.save_billing_profile(
                delegation=self.delegation, actor=request.user, request=request, **form.cleaned_data
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=400)
        messages.success(request, _("Dane do faktury zostały zapisane."))
        return redirect(reverse("web:delegation-payments"))

    def _render(self, request, form, *, status: int = 200):
        return TemplateResponse(
            request, BILLING_TEMPLATE, {"form": form, "delegation": self.delegation}, status=status
        )


class DelegationPrepareView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``POST /delegation/payments/prepare/`` – wystawia zamówienie z pro formą na niepokryty skład."""

    throttle_scope = "checkout"

    def post(self, request):
        service.require_enabled(self.competition)
        try:
            order = service.prepare_delegation_order(self.delegation, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:delegation-payments"))
        messages.success(request, _("Wystawiliśmy fakturę pro forma. Wybierz sposób zapłaty."))
        return redirect(reverse("web:payment-order", args=[order.pk]))


# --- uczestnik ------------------------------------------------------------------------------------


class ParticipantPayView(ParticipantRequiredMixin, ThrottledFormMixin, View):
    """``/me/fees/pay/`` – dane nabywcy i zamówienie na należność z kafla „Wpisowe”."""

    throttle_scope = "checkout"

    def _fee(self):
        from apps.competitions.services import current_edition
        from apps.tenancy.fees import fee_for

        service.require_enabled(self.competition)
        edition = current_edition(self.competition)
        fee = fee_for(self.participant, edition) if edition is not None else None
        if fee is None:
            raise Http404("Brak należności do zapłaty.")
        return fee

    def get(self, request):
        fee = self._fee()
        open_order = Order.objects.filter(participant_fee=fee, status=OrderStatus.OPEN).first()
        if open_order is not None:
            return redirect(reverse("web:payment-order", args=[open_order.pk]))
        profile = service.billing_profile_of(participant=self.participant)
        initial = _profile_initial(profile)
        if profile is None:
            user = request.user
            initial.update(
                {
                    "buyer_type": BuyerType.PERSON,
                    "buyer_name": user.get_full_name(),
                    "buyer_email": user.email,
                }
            )
        return self._render(request, fee, BillingForm(initial=initial))

    def post(self, request):
        fee = self._fee()
        form = BillingForm(request.POST)
        if not form.is_valid():
            return self._render(request, fee, form, status=400)
        try:
            order = service.prepare_participant_order(
                fee, actor=request.user, request=request, **form.cleaned_data
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, fee, form, status=400)
        return redirect(reverse("web:payment-order", args=[order.pk]))

    def _render(self, request, fee, form, *, status: int = 200):
        return TemplateResponse(request, PARTICIPANT_TEMPLATE, {"form": form, "fee": fee}, status=status)


# --- zamówienie (wspólne) ------------------------------------------------------------------------


class OrderView(LoginRequiredMixin, View):
    """``GET /payments/orders/<pk>/`` – płatność zamówienia: metody, dane do przelewu, dokumenty.

    Powrót z Checkout (``?checkout=success``) pokazuje wyłącznie komunikat „potwierdzamy wpłatę” –
    stan zamówienia zmienia webhook dostawcy, nie ten adres.
    """

    def get(self, request, pk: int):
        order = service.order_for_payer(request.user, getattr(request, "competition", None), pk)
        checkout = request.GET.get("checkout")
        if checkout == "success" and order.status == OrderStatus.OPEN:
            messages.info(
                request,
                _(
                    "Dziękujemy. Czekamy na potwierdzenie wpłaty od operatora płatności – "
                    "odśwież stronę za chwilę."
                ),
            )
        elif checkout == "cancel":
            messages.warning(request, _("Płatność została przerwana. Możesz spróbować ponownie."))
        settings_row = service.settings_for(order.competition)
        methods = service.available_methods(order) if order.status == OrderStatus.OPEN else []
        back = reverse("web:delegation-payments") if order.delegation_id else reverse("web:me")
        return TemplateResponse(
            request,
            ORDER_TEMPLATE,
            {
                "order": order,
                "lines": order.lines.all(),
                "documents": order.documents.all(),
                "payments": order.payments.all(),
                "methods": [m for m in methods if m != Provider.BANK_TRANSFER],
                "bank_transfer": Provider.BANK_TRANSFER in methods,
                "settings": settings_row,
                "competition": order.competition,
                "back": back,
                "provider_labels": dict(Provider.choices),
            },
        )


class OrderPayView(LoginRequiredMixin, ThrottledFormMixin, View):
    """``POST /payments/orders/<pk>/pay/<provider>/`` – sesja u dostawcy i przekierowanie 302."""

    throttle_scope = "checkout"

    def post(self, request, pk: int, provider: str):
        order = service.order_for_payer(request.user, getattr(request, "competition", None), pk)
        try:
            url = service.start_checkout(order, provider, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:payment-order", args=[order.pk]))
        return HttpResponseRedirect(url)


class OrderCancelView(LoginRequiredMixin, View):
    """``POST /payments/orders/<pk>/cancel/`` – anulowanie zamówienia przed zapłatą."""

    def post(self, request, pk: int):
        order = service.order_for_payer(request.user, getattr(request, "competition", None), pk)
        try:
            service.cancel_order(order, actor=request.user, reason="payer", request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:payment-order", args=[order.pk]))
        messages.success(request, _("Zamówienie zostało anulowane."))
        if order.delegation_id:
            return redirect(reverse("web:delegation-payments"))
        return redirect(reverse("web:me"))


class DocumentView(LoginRequiredMixin, View):
    """``GET /payments/documents/<pk>/`` – PDF pro formy albo faktury (płacący albo koordynator)."""

    def get(self, request, pk: int):
        from apps.accounts.models import CompetitionRole
        from apps.accounts.services import has_role

        competition = getattr(request, "competition", None)
        coordinator = has_role(request.user, competition, CompetitionRole.COORDINATOR)
        document = service.document_for(competition, pk, user=request.user, coordinator=coordinator)
        response = HttpResponse(documents.render_pdf(document), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{documents.filename(document)}"'
        response["X-Content-Type-Options"] = "nosniff"
        return response
