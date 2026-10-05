"""Ekrany MAIL-02: potwierdzenie adresu z banera i lista niedoręczalnych adresów koordynatora.

Adresy doklejane do ``apps/web/urls.py`` (przestrzeń ``web:``), jak SEC-01. Ekran koordynatora jest
po polsku bez gettext (panel nie jest tłumaczony – I18N-01 § 0); baner i jego potwierdzenie czyta
uczestnik IQO, więc idą przez gettext.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.core.exports import Dataset, build_response
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from . import services
from .models import DeliveryStatus

#: Scope limitu żądań przycisku „Mój adres jest poprawny” (``DEFAULT_THROTTLE_RATES``).
THROTTLE_SCOPE = "email_confirm"


def _back(request, fallback: str) -> str:
    target = request.POST.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


class ConfirmAddressView(ThrottledFormMixin, LoginRequiredMixin, View):
    """``POST /account/email/deliverable/`` – „Mój adres jest poprawny” (MAIL-02 § 2.4).

    Kasuje twarde odbicie adresu **własnego konta** – innego adresu ten widok nie zna. Wysyłka
    wraca; jeśli adres naprawdę nie istnieje, następny list odbije znowu i baner wróci.
    """

    throttle_scope = THROTTLE_SCOPE
    http_method_names = ["post"]

    def post(self, request):
        user = request.user
        row = services.undeliverable(user.email)
        if row is not None:
            audit(
                user,
                "email.undeliverable_confirmed",
                row,
                {"status": row.status_code, "source": row.source},
                request,
            )
            services.clear(user.email)
            messages.success(
                request,
                _("Dziękujemy. Wznowiliśmy wysyłkę na Twój adres – jeśli list znów nie dotrze, baner wróci."),
            )
        return redirect(_back(request, reverse("web:email-change")))


def _rows(competition) -> list[dict]:
    return services.undeliverable_accounts(competition)


class UndeliverableListView(CoordinatorRequiredMixin, View):
    """``/coordinator/undeliverable-emails/`` – konta tego konkursu z twardo odbitym adresem (§ 2.6)."""

    template_name = "email_delivery/coordinator_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not services.tracking_enabled():
            raise Http404("Śledzenie odbić poczty jest wyłączone na tej instalacji.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        rows = _rows(self.competition)
        if request.GET.get("format") == "csv":
            audit(
                request.user, "email.undeliverable_exported", self.competition, {"rows": len(rows)}, request
            )
            dataset = Dataset(
                header=[
                    "E-mail",
                    "Imię",
                    "Nazwisko",
                    "Konto aktywne",
                    "Niedoręczalny od",
                    "Kod",
                    "Źródło",
                    "Powód",
                    "Twarde odbicia",
                    "Miękkie odbicia",
                ],
                rows=(
                    [
                        row["user"].email,
                        row["user"].first_name,
                        row["user"].last_name,
                        row["user"].is_active,
                        row["status"].undeliverable_at,
                        row["status"].status_code,
                        row["status"].get_source_display(),
                        row["status"].reason,
                        row["status"].hard_bounces,
                        row["status"].soft_bounces,
                    ]
                    for row in rows
                ),
                count=len(rows),
                title="Adresy niedoręczalne",
                filename="adresy-niedoreczalne",
            )
            return build_response(dataset, "csv")
        return TemplateResponse(request, self.template_name, {"rows": rows})


class UndeliverableClearView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/undeliverable-emails/<pk>/clear/`` – „Oznacz jako doręczalny”.

    Wiersz musi należeć do konta widocznego dla koordynatora **tego** konkursu – inaczej 404
    (izolacja konkursów: identyfikator wiersza cudzego konkursu nie istnieje dla tego panelu).
    """

    http_method_names = ["post"]

    def post(self, request, pk: int):
        if not services.tracking_enabled():
            raise Http404("Śledzenie odbić poczty jest wyłączone na tej instalacji.")
        row = DeliveryStatus.objects.filter(pk=pk, undeliverable_at__isnull=False).first()
        if row is None or not any(item["status"].pk == row.pk for item in _rows(self.competition)):
            raise Http404("Nie ma takiego adresu na liście tego konkursu.")
        audit(
            request.user,
            "email.undeliverable_cleared",
            row,
            {"status": row.status_code, "source": row.source},
            request,
        )
        services.clear(row.email)
        messages.success(request, "Adres oznaczony jako doręczalny – wysyłka wznowiona.")
        return redirect(reverse("web:coordinator-undeliverable-emails"))
