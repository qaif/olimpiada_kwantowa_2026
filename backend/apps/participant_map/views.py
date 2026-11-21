"""Widoki mapy uczestników (MAP-01): strona publiczna i ekran koordynatora.

Widoki tylko orkiestrują – agregat, próg ochrony, pamięć podręczną, audyt i sprawdzenie roli
trzyma ``services``. Strona publiczna jest za flagą konkursu: wyłączona (albo żądanie bez konkursu)
to 404, a nie 403 – z zewnątrz nie da się nawet sprawdzić, czy taka funkcja istnieje.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.generic import View

from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import PerAccountThrottleMixin

from . import geometry, services
from .forms import MapSettingsForm
from .models import MIN_TOWN_COUNT

PAGE_TEMPLATE = "web/participant_map/page.html"
COORDINATOR_TEMPLATE = "web/participant_map/coordinator.html"
THROTTLE_SCOPE = "participant_map"


def stamp(value) -> str:
    """„21 listopada 2026, 17:40” – chwila wygenerowania agregatu w strefie serwisu."""
    return date_format(timezone.localtime(value), "j E Y, H:i")


class ParticipantMapView(View):
    """``GET /dokumenty/mapa-uczestnikow/`` – kafle, mapa SVG i lista miejscowości (bez JS też)."""

    def get(self, request):
        competition = getattr(request, "competition", None)
        if not services.enabled(competition):
            raise Http404("Ten konkurs nie publikuje mapy uczestników.")
        data = services.aggregate(competition)
        context = {
            "map": data,
            "paths": geometry.voivodeship_paths(),
            "view_box": geometry.view_box(),
            "stamp": stamp(data["generated_at"]),
        }
        return TemplateResponse(request, PAGE_TEMPLATE, context)


class _CoordinatorMapMixin(CoordinatorRequiredMixin, PerAccountThrottleMixin):
    """Rola koordynatora **tego** konkursu przed limitem: obcy dostaje 403, a nie zużywa kubełka."""

    throttle_scope = THROTTLE_SCOPE

    def back(self):
        return redirect(reverse("web:coordinator-participant-map"))


class CoordinatorMapView(_CoordinatorMapMixin, View):
    """``/coordinator/participant-map/`` – włączenie strony, próg ochrony, stan agregatu."""

    def get(self, request):
        competition = self.competition
        form = MapSettingsForm(
            initial={
                "enabled": services.enabled(competition),
                "min_town_count": services.min_town_count(competition),
            }
        )
        return self.render(request, form)

    def post(self, request):
        form = MapSettingsForm(request.POST)
        if not form.is_valid():
            return self.render(request, form, status=400)
        changed = services.save_settings(
            self.competition,
            request.user,
            is_enabled=form.cleaned_data["enabled"],
            threshold=form.cleaned_data["min_town_count"],
            request=request,
        )
        if changed:
            messages.success(request, "Zapisano ustawienia mapy uczestników.")
        else:
            messages.info(request, "Bez zmian – ustawienia mapy są takie same jak wcześniej.")
        return self.back()

    def render(self, request, form, *, status: int = 200):
        competition = self.competition
        data = services.aggregate(competition)
        context = {
            "form": form,
            "map": data,
            "stamp": stamp(data["generated_at"]),
            "is_enabled": services.enabled(competition),
            "public_url": reverse("web:participant-map"),
            "min_allowed": MIN_TOWN_COUNT,
            "unmapped_towns": [town for town in data["towns"] if town["point"] is None],
            "cache_minutes": services.cache_seconds() // 60,
        }
        return TemplateResponse(request, COORDINATOR_TEMPLATE, context, status=status)


class CoordinatorMapRefreshView(_CoordinatorMapMixin, View):
    """``POST /coordinator/participant-map/refresh/`` – przeliczenie teraz, bez czekania na pamięć."""

    def post(self, request):
        result = services.refresh(self.competition, request.user, request=request)
        messages.success(request, f"Mapa odświeżona – stan na {stamp(result['generated_at'])}.")
        return self.back()
