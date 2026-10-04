"""Ekrany okien czasowych: „Okna czasowe” koordynatora i „Okna czasowe drużyny” opiekuna (TZ-01 § 5).

Koordynator – ``/coordinator/stages/<id>/windows/`` (po polsku bez gettext, jak pozostałe ekrany
panelu organizatora, I18N-01 § 0). Każda czynność ma **własny adres POST** i wraca na ekran
z komunikatem – ta sama umowa, co na ekranie delegacji. Bramki w tej kolejności: rola
(``CoordinatorRequiredMixin`` → 403), flaga konkursu (404), etap z **tego** konkursu (404), forma
etapu (rozmowa, trening → 404). Reguły czasu i ramy rozstrzyga ``services`` – widok zamienia
``DomainError`` na komunikat.

Opiekun drużyny – ``/delegation/time-windows/`` (gettext): okna uczniów jego delegacji i strefa
czasowa ucznia. Uczeń jest wybierany z querysetu delegacji opiekuna (``student_of``) – uczeń innego
kraju albo konkursu daje 404.
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, View

from apps.accounts import delegation_services
from apps.accounts.delegations import Delegation
from apps.accounts.models import Participant, Region, RegionLevel
from apps.competitions.models import Stage
from apps.core.api import DomainError
from apps.web.mixins import ActionViewMixin, CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.delegation import TeamLeaderRequiredMixin

from . import services
from .access import enabled
from .forms import (
    CountryTimezoneForm,
    DelegationAssignForm,
    ParticipantExceptionForm,
    PlanCreateForm,
    PlanSettingsForm,
    StudentTimezoneForm,
    WindowStartForm,
)
from .models import CountryTimezone, ParticipantTimezone, TimeWindow
from .overview import leader_rows, overview
from .services import load_plan
from .zones import country_default_timezone, timezone_choices

COORDINATOR_TEMPLATE = "time_windows/coordinator.html"
LEADER_TEMPLATE = "time_windows/leader.html"

#: Ten sam zakres limitu, co czynności ekranu delegacji: kilkadziesiąt zmian na godzinę mieści
#: rozpisanie wszystkich krajów, a nie mieści skryptu klikającego w kółko.
THROTTLE_SCOPE = "delegation"


def _form_error(form) -> DomainError:
    text = " ".join(str(message) for errors in form.errors.values() for message in errors)
    return DomainError(text or "Nieprawidłowe dane formularza.", "INVALID_FORM")


class StageWindowsMixin(CoordinatorRequiredMixin):
    """Rola koordynatora, flaga konkursu i etap tego konkursu, który może mieć okna."""

    def stage_or_404(self, stage_id: int) -> Stage:
        competition = self.competition
        if not enabled(competition):
            raise Http404("Ten konkurs nie korzysta z okien czasowych.")
        stage = get_object_or_404(
            Stage.objects.for_competition(competition).select_related("edition", "edition__competition"),
            pk=stage_id,
        )
        if stage.is_interview or stage.is_training:
            raise Http404("Ten etap nie może mieć okien czasowych.")
        return stage

    def plan_view_or_404(self, stage: Stage):
        view = load_plan(stage)
        if view is None:
            raise Http404("Ten etap nie ma okien czasowych.")
        return view

    def get_success_url(self, stage_id: int, *args, **kwargs) -> str:
        return reverse("web:coordinator-stage-windows", args=[stage_id])


class StageWindowsView(StageWindowsMixin, View):
    """``GET /coordinator/stages/<id>/windows/`` – oś czasu, kraje, uczniowie i wyjątki."""

    def get(self, request, stage_id: int):
        stage = self.stage_or_404(stage_id)
        now = timezone.now()
        view = load_plan(stage)
        context = {
            "stage": stage,
            "now": now,
            "view": view,
            "create_form": PlanCreateForm() if view is None else None,
            "timezone_choices": timezone_choices(),
        }
        if view is not None:
            data = overview(stage, view, self.competition, now)
            context.update(
                {
                    "overview": data,
                    "started": view.started(now),
                    "release_at": view.release_at,
                    "settings_form": PlanSettingsForm(
                        initial={
                            "duration_minutes": view.plan.duration_minutes,
                            "preferred_local_hour": view.plan.preferred_local_hour,
                        }
                    ),
                    "exception_form": ParticipantExceptionForm(windows=view.windows),
                    "windows": view.windows,
                    "stage_opened": now >= stage.opens_at,
                    "quiz_has_own_window": _quiz_has_own_window(stage),
                }
            )
        return TemplateResponse(request, COORDINATOR_TEMPLATE, context)


def _quiz_has_own_window(stage) -> bool:
    """Czy test etapu ma własne terminy – w trybie okien są pomijane i ekran o tym ostrzega."""
    quiz = getattr(stage, "quiz", None) if stage.is_quiz else None
    return quiz is not None and bool(quiz.opens_at or quiz.closes_at)


class PlanCreateView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int) -> str:
        stage = self.stage_or_404(stage_id)
        form = PlanCreateForm(request.POST)
        if not form.is_valid():
            raise _form_error(form)
        services.create_plan(stage, actor=request.user, request=request, **form.cleaned_data)
        return "Tryb okien czasowych włączony."


class PlanSettingsView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int) -> str:
        view = self.plan_view_or_404(self.stage_or_404(stage_id))
        form = PlanSettingsForm(request.POST)
        if not form.is_valid():
            raise _form_error(form)
        services.update_plan(view.plan, actor=request.user, request=request, **form.cleaned_data)
        return "Ustawienia okien zapisane."


class PlanDeleteView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int) -> str:
        view = self.plan_view_or_404(self.stage_or_404(stage_id))
        services.delete_plan(view.plan, actor=request.user, request=request)
        return "Tryb okien czasowych wyłączony – etap ma znów jedno okno."


class WindowAddView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int) -> str:
        view = self.plan_view_or_404(self.stage_or_404(stage_id))
        form = WindowStartForm(request.POST)
        if not form.is_valid():
            raise _form_error(form)
        window = services.add_window(
            view.plan, starts_at=form.cleaned_data["starts_at"], actor=request.user, request=request
        )
        return f"Dodano okno {window.label}."


class _WindowActionMixin(StageWindowsMixin):
    def window_or_404(self, stage: Stage, window_id: int) -> TimeWindow:
        return get_object_or_404(TimeWindow.objects.select_related("plan"), pk=window_id, plan__stage=stage)


class WindowMoveView(_WindowActionMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int, window_id: int) -> str:
        window = self.window_or_404(self.stage_or_404(stage_id), window_id)
        form = WindowStartForm(request.POST)
        if not form.is_valid():
            raise _form_error(form)
        services.move_window(
            window, starts_at=form.cleaned_data["starts_at"], actor=request.user, request=request
        )
        return f"Okno {window.label} przesunięte."


class WindowDeleteView(_WindowActionMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int, window_id: int) -> str:
        window = self.window_or_404(self.stage_or_404(stage_id), window_id)
        label = window.label
        services.delete_window(window, actor=request.user, request=request)
        return f"Okno {label} usunięte."


class DelegationAssignView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int, delegation_id: int) -> str:
        stage = self.stage_or_404(stage_id)
        view = self.plan_view_or_404(stage)
        delegation = get_object_or_404(
            Delegation.objects.for_competition(self.competition).select_related("country"), pk=delegation_id
        )
        form = DelegationAssignForm(request.POST, windows=view.windows)
        if not form.is_valid():
            raise _form_error(form)
        services.assign_delegation(
            view.plan, delegation, form.cleaned_data["window"] or None, actor=request.user, request=request
        )
        return f"Przydział kraju {delegation.country.name} zapisany."


class ParticipantExceptionView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int) -> str:
        stage = self.stage_or_404(stage_id)
        view = self.plan_view_or_404(stage)
        form = ParticipantExceptionForm(request.POST, windows=view.windows)
        if not form.is_valid():
            raise _form_error(form)
        participant = (
            Participant.objects.for_competition(self.competition)
            .select_related("delegation__country", "user")
            .filter(public_code__iexact=form.cleaned_data["code"].strip())
            .first()
        )
        if participant is None:
            raise DomainError(
                "W tym konkursie nie ma uczestnika o takim kodzie.", "WINDOWS_UNKNOWN_PARTICIPANT"
            )
        row = services.set_participant_exception(
            view.plan,
            participant,
            window_id=form.cleaned_data["window"] or None,
            extra_minutes=form.cleaned_data["extra_minutes"],
            reason=form.cleaned_data["reason"],
            actor=request.user,
            request=request,
        )
        if row is None:
            return f"Wyjątek uczestnika {participant.public_code} usunięty."
        return f"Wyjątek uczestnika {participant.public_code} zapisany."


class CountryTimezoneView(StageWindowsMixin, ThrottledFormMixin, ActionViewMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def perform(self, request, stage_id: int, region_id: int) -> str:
        self.stage_or_404(stage_id)
        region = get_object_or_404(
            Region.objects.filter(competition=self.competition, level=RegionLevel.COUNTRY), pk=region_id
        )
        form = CountryTimezoneForm(request.POST)
        if not form.is_valid():
            raise _form_error(form)
        services.set_country_timezone(
            region, form.cleaned_data["timezone"], actor=request.user, request=request
        )
        return f"Strefa kraju {region.name} zapisana."


# --- opiekun drużyny ------------------------------------------------------------------------------


class LeaderWindowsView(TeamLeaderRequiredMixin, TemplateView):
    """``/delegation/time-windows/`` – okna i strefy uczniów delegacji opiekuna."""

    template_name = LEADER_TEMPLATE

    def dispatch(self, request, *args, **kwargs):
        if not enabled(getattr(request, "competition", None)):
            raise Http404("Ten konkurs nie korzysta z okien czasowych.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        delegation = self.delegation
        students = list(delegation_services.students_of(delegation))
        for student in students:
            # Delegacja z krajem jest już w pamięci – rozstrzygnięcie okna nie pyta o nią uczniem po uczniu.
            student.delegation = delegation
        zones = dict(
            ParticipantTimezone.objects.filter(participant__in=students).values_list(
                "participant_id", "timezone"
            )
        )
        country_zone = CountryTimezone.objects.filter(region=delegation.country).values_list(
            "timezone", flat=True
        ).first() or country_default_timezone(delegation.country.code)
        stages = leader_rows(delegation, students, timezone.now())
        for item in stages:
            for row in item["rows"]:
                row["timezone"] = zones.get(row["participant"].pk) or country_zone or ""
        context.update(
            {
                "delegation": delegation,
                "stages": stages,
                "students": [
                    {"participant": student, "timezone": zones.get(student.pk, "")} for student in students
                ],
                "country_timezone": country_zone or "",
                "timezone_choices": timezone_choices(),
            }
        )
        return context


class LeaderStudentTimezoneView(TeamLeaderRequiredMixin, ThrottledFormMixin, View):
    """``POST /delegation/time-windows/<pk>/timezone/`` – strefa wyświetlania jednego ucznia."""

    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        from django.contrib import messages
        from django.shortcuts import redirect

        if not enabled(getattr(request, "competition", None)):
            raise Http404("Ten konkurs nie korzysta z okien czasowych.")
        participant = delegation_services.student_of(self.leader, pk)
        form = StudentTimezoneForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Wybierz strefę czasową z listy."))
        else:
            try:
                services.set_participant_timezone(
                    participant, form.cleaned_data["timezone"], actor=request.user, request=request
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, _("Strefa czasowa ucznia została zapisana."))
        return redirect(reverse("web:delegation-time-windows"))


__all__ = [
    "CountryTimezoneView",
    "DelegationAssignView",
    "LeaderStudentTimezoneView",
    "LeaderWindowsView",
    "ParticipantExceptionView",
    "PlanCreateView",
    "PlanDeleteView",
    "PlanSettingsView",
    "StageWindowsView",
    "WindowAddView",
    "WindowDeleteView",
    "WindowMoveView",
]
