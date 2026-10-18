"""Ekran „Komunikacja → Automatyczne przypomnienia” – ``/coordinator/messages/rules/`` (MSG-AUTO-01).

Widoki wyłącznie orkiestrują: kształt danych sprawdza ``RuleForm`` (grupy i ich parametry dziedziczy
z ekranu komunikatów), zapis, audyt i generowanie – ``services``. Reguła spoza konkursu żądania to
404 (``for_competition``), rola – ``CoordinatorRequiredMixin`` i jeszcze raz serwis.

Formularz ma dwa przyciski: **„Podgląd”** liczy trzy najbliższe wysyłki z wypełnionym tekstem
**bez zapisu**, „Zapisz” zapisuje regułę i od razu planuje komunikaty, które mieszczą się w oknie
generowania (tydzień naprzód). Pola grupy chowa ``broadcast-groups.js`` – ten sam plik, co na
ekranie komunikatów, bez kodu inline (CSP).
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.accounts.messaging import grade_choices, school_choices, workshop_choices
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.coordinator_messages import CoordinatorMessagesView

from . import services
from .forms import RuleForm, initial_for
from .models import MessageRule, RuleKind

LIST_TEMPLATE = "message_rules/rules.html"
FORM_TEMPLATE = "message_rules/rule_form.html"

#: Rodzaj reguły w adresie ``new/?kind=…`` – krótko, bo adres stoi w przyciskach.
KIND_SLUGS = {"workshop": RuleKind.WORKSHOP, "stage": RuleKind.STAGE_DEADLINE}

#: Wartość pola ``action`` przycisku „Podgląd” – wszystko inne zapisuje.
ACTION_PREVIEW = "preview"


def _sync_message(request, result: services.SyncResult) -> None:
    """Skutek natychmiastowego przebiegu generowania – koordynator ma wiedzieć, co się zaplanowało."""
    if result.created or result.withdrawn:
        messages.info(
            request,
            _(
                "Zaplanowano komunikatów: %(created)s, wycofano nieaktualnych: %(withdrawn)s. "
                "Zobacz „Komunikaty → Zaplanowane”."
            )
            % {"created": result.created, "withdrawn": result.withdrawn},
        )
    if result.error:
        messages.error(
            request, _("Nie udało się zaplanować przypomnienia: %(error)s") % {"error": result.error}
        )


class RuleLookupMixin(CoordinatorRequiredMixin):
    """Reguła z adresu – wyłącznie z konkursu żądania (cudza = 404)."""

    def rule(self, pk) -> MessageRule:
        rule = MessageRule.objects.for_competition(self.competition).filter(pk=pk).first()
        if rule is None:
            raise Http404(_("Nie ma takiej reguły."))
        return rule

    def form(self, kind: str, data=None, *, initial=None) -> RuleForm:
        """``RuleForm`` z listami wyboru konkursu żądania – te same, co na ekranie komunikatów."""
        competition = self.competition
        return RuleForm(
            data,
            initial=initial,
            kind=kind,
            stages=CoordinatorMessagesView._stages(competition),
            regions=CoordinatorMessagesView._regions(competition),
            schools=school_choices(competition),
            grades=grade_choices(competition),
            workshops=workshop_choices(competition),
        )

    def render_form(self, request, form: RuleForm, *, rule=None, preview=None, status: int = 200):
        if preview is None and rule is not None:
            preview = services.upcoming(rule)
        context = {
            "form": form,
            "rule": rule,
            "kind": form.kind,
            "kind_label": RuleKind(form.kind).label,
            "preview": preview,
            "parameter_map": form.parameter_map(),
            "parameter_fields": form.visible_parameter_fields(),
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class RuleListView(RuleLookupMixin, View):
    """``GET`` – lista reguł konkursu i przyciski „Dodaj”."""

    def get(self, request):
        rules = MessageRule.objects.for_competition(self.competition).select_related("stage", "created_by")
        return TemplateResponse(request, LIST_TEMPLATE, {"rules": list(rules)})


class RuleCreateView(RuleLookupMixin, ThrottledFormMixin, View):
    """``/coordinator/messages/rules/new/?kind=workshop|stage`` – nowa reguła."""

    throttle_scope = "chat"

    def kind(self, request) -> str:
        kind = KIND_SLUGS.get(request.GET.get("kind") or request.POST.get("kind") or "")
        if kind is None:
            raise Http404(_("Nieznany rodzaj reguły."))
        return kind

    def get(self, request):
        return self.render_form(request, self.form(self.kind(request)))

    def post(self, request):
        form = self.form(self.kind(request), request.POST)
        if not form.is_valid():
            return self.render_form(request, form, status=400)
        if request.POST.get("action") == ACTION_PREVIEW:
            return self.render_form(
                request, form, preview=services.upcoming(form.unsaved_rule(self.competition))
            )
        try:
            rule, result = services.create_rule(
                competition=self.competition,
                actor=request.user,
                kind=form.kind,
                request=request,
                **form.rule_fields(),
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
            return self.render_form(request, form, status=400)
        messages.success(request, _("Reguła „%(name)s” zapisana.") % {"name": rule.name})
        _sync_message(request, result)
        return redirect(reverse("web:coordinator-message-rule-edit", args=[rule.pk]))


class RuleEditView(RuleLookupMixin, ThrottledFormMixin, View):
    """``/coordinator/messages/rules/<pk>/`` – edycja i podgląd najbliższych wysyłek."""

    throttle_scope = "chat"

    def get(self, request, pk: int):
        rule = self.rule(pk)
        return self.render_form(request, self.form(rule.kind, initial=initial_for(rule)), rule=rule)

    def post(self, request, pk: int):
        rule = self.rule(pk)
        form = self.form(rule.kind, request.POST)
        if not form.is_valid():
            return self.render_form(request, form, rule=rule, status=400)
        if request.POST.get("action") == ACTION_PREVIEW:
            preview = services.upcoming(form.unsaved_rule(self.competition, instance=rule))
            return self.render_form(request, form, rule=rule, preview=preview)
        try:
            changed, result = services.update_rule(
                rule, actor=request.user, request=request, **form.rule_fields()
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
            return self.render_form(request, form, rule=rule, status=400)
        if changed:
            messages.success(request, _("Zmiany w regule „%(name)s” zapisane.") % {"name": rule.name})
        else:
            messages.info(request, _("Nic się nie zmieniło."))
        _sync_message(request, result)
        return redirect(reverse("web:coordinator-message-rule-edit", args=[rule.pk]))


class RuleToggleView(RuleLookupMixin, ThrottledFormMixin, View):
    """``POST …/rules/<pk>/toggle/`` – włącz albo wyłącz (wyłączenie wycofuje czekające komunikaty)."""

    throttle_scope = "chat"

    def post(self, request, pk: int):
        rule = self.rule(pk)
        result = services.set_enabled(rule, not rule.enabled, actor=request.user, request=request)
        if rule.enabled:
            messages.success(request, _("Reguła „%(name)s” włączona.") % {"name": rule.name})
        else:
            messages.success(
                request,
                _("Reguła „%(name)s” wyłączona – jej czekające komunikaty zostały wycofane.")
                % {"name": rule.name},
            )
        _sync_message(request, result)
        return redirect(reverse("web:coordinator-message-rules"))


class RuleDeleteView(RuleLookupMixin, ThrottledFormMixin, View):
    """``POST /coordinator/messages/rules/<pk>/delete/`` – usunięcie reguły z wycofaniem czekających."""

    throttle_scope = "chat"

    def post(self, request, pk: int):
        rule = self.rule(pk)
        name = rule.name
        withdrawn = services.delete_rule(rule, actor=request.user, request=request)
        messages.success(
            request,
            _("Reguła „%(name)s” usunięta; wycofane komunikaty: %(count)s.")
            % {"name": name, "count": withdrawn},
        )
        return redirect(reverse("web:coordinator-message-rules"))
