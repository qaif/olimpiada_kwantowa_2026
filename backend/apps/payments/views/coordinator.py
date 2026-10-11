"""Panel koordynatora „Płatności” – pulpit, cennik, delegacja, zamówienie, zwrot, eksport (PAY-01 § 6).

Ekrany za flagą ``fees`` (404 bez niej, menu bez pozycji), rola sprawdzana wcześniej
(``CoordinatorRequiredMixin`` – uczestnik i opiekun dostają 403 niezależnie od flagi). Każda czynność
ma własny adres POST; serwis sprawdza rolę drugi raz (``services._require_coordinator``), więc
wywołanie spoza widoku nie ominie reguły. Ekrany są po polsku bez gettext (I18N-01 § 0).
"""

from __future__ import annotations

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.models import Edition
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from .. import pricing
from .. import services as service
from ..forms import (
    AdjustmentForm,
    BankTransferForm,
    CoordinatorReasonForm,
    PriceListForm,
    RefundForm,
    SettingsForm,
)
from ..models import (
    DELEGATION_KINDS,
    FeeAdjustment,
    Order,
    OrderStatus,
    Payment,
    PriceKind,
    PricePeriod,
    Provider,
)
from ..providers.stripe import is_test_mode

DASHBOARD_TEMPLATE = "payments/coordinator/dashboard.html"
PRICES_TEMPLATE = "payments/coordinator/prices.html"
DELEGATION_TEMPLATE = "payments/coordinator/delegation.html"
ORDER_TEMPLATE = "payments/coordinator/order.html"


def _errors(form) -> str:
    return "; ".join(text for errors in form.errors.values() for text in errors)


class PaymentsScreenMixin(CoordinatorRequiredMixin):
    def competition_or_404(self, request):
        competition = getattr(request, "competition", None)
        service.require_enabled(competition)
        return competition

    def editions(self, competition):
        return list(Edition.objects.for_competition(competition).order_by("-created_at", "-id"))

    def edition(self, competition, requested: str):
        for edition in self.editions(competition):
            if str(edition.pk) == (requested or ""):
                return edition
        return current_edition(competition)

    def order_or_404(self, competition, pk: int) -> Order:
        return get_object_or_404(
            Order.objects.for_competition(competition).select_related(
                "delegation__country", "participant_fee__participant", "edition", "competition"
            ),
            pk=pk,
        )


class DashboardView(PaymentsScreenMixin, View):
    """``GET /coordinator/payments/`` – kto zapłacił, zaległości, sumy per waluta, do wyjaśnienia."""

    def get(self, request):
        competition = self.competition_or_404(request)
        edition = self.edition(competition, request.GET.get("edition"))
        rows = service.delegation_rows(competition, edition) if edition else []
        orders = list(service.orders_of_edition(competition, edition)) if edition else []
        payments, refunds = service.attention_payments(competition)
        return TemplateResponse(
            request,
            DASHBOARD_TEMPLATE,
            {
                "competition": competition,
                "editions": self.editions(competition),
                "edition": edition,
                "rows": rows,
                "orders": orders,
                "participant_orders": [o for o in orders if o.participant_fee_id],
                "totals": service.totals_by_currency(competition, edition, rows) if edition else {},
                "attention_payments": payments,
                "attention_refunds": refunds,
            },
        )


def _price_initial(price_list) -> dict:
    if price_list is None:
        return {"currency": "EUR", "is_active": True}
    initial = {
        "currency": price_list.currency,
        "early_until": price_list.early_until,
        "late_from": price_list.late_from,
        "is_active": price_list.is_active,
    }
    for (kind, period), amount in pricing.prices_of(price_list).items():
        initial[service.price_key(kind, period)] = amount
    return initial


class PricesView(PaymentsScreenMixin, View):
    """``GET|POST /coordinator/payments/prices/`` – cennik delegacji edycji i ustawienia sprzedawcy."""

    def get(self, request):
        competition = self.competition_or_404(request)
        edition = self.edition(competition, request.GET.get("edition"))
        return self._render(request, competition, edition)

    def post(self, request):
        competition = self.competition_or_404(request)
        edition = self.edition(competition, request.POST.get("edition"))
        if edition is None:
            raise Http404("Brak edycji.")
        form = PriceListForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Cennika nie zapisano: {_errors(form)}")
            return self._render(request, competition, edition, price_form=form, status=400)
        try:
            service.save_price_list(
                competition,
                edition,
                currency=form.cleaned_data["currency"],
                early_until=form.cleaned_data.get("early_until"),
                late_from=form.cleaned_data.get("late_from"),
                is_active=form.cleaned_data.get("is_active", False),
                prices=form.prices(),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, competition, edition, price_form=form, status=exc.status_code)
        messages.success(request, "Cennik zapisany. Wystawione zamówienia zachowują swoje ceny.")
        return redirect(f"{reverse('web:coordinator-payments-prices')}?edition={edition.pk}")

    def _render(self, request, competition, edition, *, price_form=None, settings_form=None, status=200):
        price_list = pricing.price_list_for(edition) if edition else None
        if price_list is None and edition is not None:
            from ..models import PriceList

            price_list = PriceList.objects.filter(edition=edition).first()
        from ..providers import get_provider

        return TemplateResponse(
            request,
            PRICES_TEMPLATE,
            {
                "competition": competition,
                "editions": self.editions(competition),
                "edition": edition,
                "price_form": price_form or PriceListForm(initial=_price_initial(price_list)),
                "settings_form": settings_form or SettingsForm(instance=service.settings_for(competition)),
                "stripe_configured": get_provider(Provider.STRIPE).is_configured(),
                "stripe_test_mode": is_test_mode(),
                "p24_configured": get_provider(Provider.P24).is_configured(),
                "stripe_webhook_url": request.build_absolute_uri(reverse("web:payments-webhook-stripe")),
                "p24_webhook_url": request.build_absolute_uri(reverse("web:payments-webhook-p24")),
                "periods": PricePeriod.choices,
            },
            status=status,
        )


class SettingsView(PaymentsScreenMixin, View):
    """``POST /coordinator/payments/settings/`` – dane sprzedawcy, rachunek, numeracja, metody."""

    def post(self, request):
        competition = self.competition_or_404(request)
        form = SettingsForm(request.POST, instance=service.settings_for(competition))
        if not form.is_valid():
            messages.error(request, f"Ustawień nie zapisano: {_errors(form)}")
            edition = current_edition(competition)
            return PricesView()._render(request, competition, edition, settings_form=form, status=400)
        try:
            service.save_settings(competition, actor=request.user, request=request, **form.cleaned_data)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Ustawienia płatności zapisane.")
        return redirect(reverse("web:coordinator-payments-prices"))


class DelegationView(PaymentsScreenMixin, View):
    """``GET /coordinator/payments/delegations/<pk>/`` – zestawienie, zamówienia, zniżki i zwolnienia."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        from apps.accounts import delegation_services

        delegation = delegation_services.delegation_for(competition, pk)
        return TemplateResponse(
            request,
            DELEGATION_TEMPLATE,
            {
                "competition": competition,
                "delegation": delegation,
                "statement": pricing.statement(delegation),
                "orders": Order.objects.filter(delegation=delegation).prefetch_related("documents"),
                "adjustments": FeeAdjustment.objects.filter(delegation=delegation).select_related(
                    "created_by"
                ),
                "profile": service.billing_profile_of(delegation=delegation),
                "adjustment_form": AdjustmentForm(),
                "revoke_form": CoordinatorReasonForm(),
                "kinds": DELEGATION_KINDS,
                "kind_labels": dict(PriceKind.choices),
            },
        )


class AdjustmentAddView(PaymentsScreenMixin, ThrottledFormMixin, View):
    throttle_scope = "payments_admin"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        from apps.accounts import delegation_services

        delegation = delegation_services.delegation_for(competition, pk)
        form = AdjustmentForm(request.POST)
        if not form.is_valid():
            messages.error(request, _errors(form))
        else:
            try:
                service.add_adjustment(delegation, actor=request.user, request=request, **form.cleaned_data)
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, "Decyzja zapisana.")
        return redirect(reverse("web:coordinator-payments-delegation", args=[delegation.pk]))


class AdjustmentRevokeView(PaymentsScreenMixin, ThrottledFormMixin, View):
    throttle_scope = "payments_admin"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        adjustment = get_object_or_404(FeeAdjustment.objects.for_competition(competition), pk=pk)
        form = CoordinatorReasonForm(request.POST)
        if not form.is_valid():
            messages.error(request, _errors(form))
        else:
            try:
                service.revoke_adjustment(
                    adjustment, reason=form.cleaned_data["reason"], actor=request.user, request=request
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, "Decyzja cofnięta.")
        return redirect(reverse("web:coordinator-payments-delegation", args=[adjustment.delegation_id]))


class OrderAdminView(PaymentsScreenMixin, View):
    """``GET /coordinator/payments/orders/<pk>/`` – pozycje, wpłaty, zwroty, dokumenty i czynności."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        order = self.order_or_404(competition, pk)
        payments = list(order.payments.prefetch_related("refunds").order_by("-created_at"))
        return TemplateResponse(
            request,
            ORDER_TEMPLATE,
            {
                "competition": competition,
                "order": order,
                "lines": order.lines.all(),
                "documents": order.documents.all(),
                "payments": payments,
                "bank_form": BankTransferForm(),
                "cancel_form": CoordinatorReasonForm(),
                "refund_form": RefundForm(),
                "can_mark_paid": order.status == OrderStatus.OPEN,
                "refundable": [
                    {
                        "payment": p,
                        # Wpłata zamówienia wraca pozycjami (M3); „do wyjaśnienia” – w całości.
                        "lines": service.refundable_lines(order) if p.status == "SUCCEEDED" else [],
                    }
                    for p in payments
                    if p.status in service.REFUNDABLE_STATUSES and p.amount > p.refunded_amount
                ],
                "provider_labels": dict(Provider.choices),
            },
        )


class OrderMarkPaidView(PaymentsScreenMixin, ThrottledFormMixin, View):
    """``POST …/orders/<pk>/mark-paid/`` – wpływ przelewu z kodem zamówienia (opcjonalnie z dowodem)."""

    throttle_scope = "payments_admin"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        order = self.order_or_404(competition, pk)
        form = BankTransferForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _errors(form))
            return redirect(reverse("web:coordinator-payments-order", args=[order.pk]))
        try:
            service.record_bank_transfer(
                order,
                received_on=form.cleaned_data["received_on"],
                note=form.cleaned_data.get("note", ""),
                proof=form.cleaned_data.get("proof"),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Wpłata zapisana – faktura wystawiona, płacący dostał potwierdzenie.")
        return redirect(reverse("web:coordinator-payments-order", args=[order.pk]))


class OrderCancelAdminView(PaymentsScreenMixin, ThrottledFormMixin, View):
    throttle_scope = "payments_admin"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        order = self.order_or_404(competition, pk)
        form = CoordinatorReasonForm(request.POST)
        if not form.is_valid():
            messages.error(request, _errors(form))
            return redirect(reverse("web:coordinator-payments-order", args=[order.pk]))
        try:
            service.cancel_order(
                order, actor=request.user, reason=form.cleaned_data["reason"], request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Zamówienie anulowane. Pro forma zostaje w rejestrze ze swoim numerem.")
        return redirect(reverse("web:coordinator-payments-order", args=[order.pk]))


class RefundView(PaymentsScreenMixin, ThrottledFormMixin, View):
    """``POST /coordinator/payments/payments/<pk>/refund/`` – zwrot u dostawcy (albo zapis przelewu)."""

    throttle_scope = "payments_admin"

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        payment = get_object_or_404(
            Payment.objects.for_competition(competition).select_related("order"), pk=pk
        )
        form = RefundForm(request.POST)
        try:
            lines = {
                int(key.removeprefix("line_")): int(value or 0)
                for key, value in request.POST.items()
                if key.startswith("line_")
            }
        except ValueError:
            lines = None
        if not form.is_valid() or lines is None:
            messages.error(request, _errors(form) or "Ilości zwrotu muszą być liczbami całkowitymi.")
        else:
            try:
                refund = service.refund_payment(
                    payment,
                    lines=lines,
                    reason=form.cleaned_data["reason"],
                    actor=request.user,
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, f"Zwrot zlecony ({refund.get_status_display()}).")
        return redirect(reverse("web:coordinator-payments-order", args=[payment.order_id]))


class ProofView(PaymentsScreenMixin, View):
    """``GET …/payments/<pk>/proof/`` – dowód wpłaty po czystym skanie, zawsze jako załącznik."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        payment = get_object_or_404(Payment.objects.for_competition(competition), pk=pk)
        handle = service.open_proof(payment)
        if handle is None:
            raise Http404("Dowód wpłaty niedostępny (brak pliku albo skan nie jest czysty).")
        audit(request.user, "payments.proof_viewed", payment, {}, request=request)
        ext = payment.proof_key.rsplit(".", 1)[-1]
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=f"dowod-wplaty-{payment.order.reference}.{ext}",
            content_type=payment.proof_mime,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ExportView(PaymentsScreenMixin, View):
    """``GET /coordinator/payments/export.csv`` – zamówienia edycji dla księgowości."""

    def get(self, request):
        competition = self.competition_or_404(request)
        edition = self.edition(competition, request.GET.get("edition"))
        if edition is None:
            raise Http404("Brak edycji.")
        audit(request.user, "payments.exported", edition, {"edition": edition.pk}, request=request)
        return csv_response(service.accounting_dataset(competition, edition))
