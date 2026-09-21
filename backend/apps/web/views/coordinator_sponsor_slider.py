"""Ekran „Slider sponsorów” ``/coordinator/sponsor-slider/``.

Trzy decyzje w jednym miejscu: **czy** pasek rotujących logotypów w menu w ogóle się pokazuje,
**jak szybko** się przesuwa i **których** partnerów pokazuje (uwaga organizatora z 21.09.2026,
„jak na Olimpiadzie Biologicznej”). Lista partnerów zostaje jedna – redaguje się ją na stronie
„Partnerzy” w ``/cms/``; ten ekran wyłącznie filtruje i tempuje to, co już tam stoi.

Ustawienie mieszka na ``cms.SiteSettings`` (per witryna konkursu), a nie na ``Competition`` –
z tego samego powodu, co dane organizatora i media społecznościowe: to jest marka i wygląd
serwisu, a nie dane przetwarzane o uczestnikach. Widok czyta i zapisuje wiersz **tej** witryny
(``SiteSettings.for_site(competition.site)``) – konkurs bierze się z domeny żądania, więc nie ma
tu adresu, pod którym dałoby się ruszyć slider sąsiada.

Podgląd pod formularzem pokazuje **każdego** partnera z logotypu, także tego odfiltrowanego – nie
chowa go, tylko oznacza, dlaczego nie trafi do paska. Koordynator, który zastanawia się „czemu nie
widać złotego sponsora”, ma zobaczyć odpowiedź na tym samym ekranie, a nie zgadywać.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.cms.blocks import PARTNER_LEVELS
from apps.cms.models import SPONSOR_SLIDER_MAX_SECONDS, PartnersPage, SiteSettings
from apps.cms.sponsor_slider import normalize_url
from apps.core.models import audit
from apps.web.competition_forms import SponsorSliderForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/sponsor_slider.html"
LEVEL_LABELS = dict(PARTNER_LEVELS)


class SponsorSliderView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/sponsor-slider/`` – włącznik, sekundy i poziomy paska sponsorów."""

    def get(self, request):
        competition = self._competition(request)
        settings_row = self._settings(competition)
        form = SponsorSliderForm(
            initial={
                "enabled": settings_row.sponsor_slider_enabled,
                "seconds": settings_row.sponsor_slider_seconds,
                "levels": settings_row.sponsor_slider_levels,
            },
            level_counts=self._level_counts(competition),
        )
        return self._render(request, competition, settings_row, form)

    def post(self, request):
        competition = self._competition(request)
        settings_row = self._settings(competition)
        form = SponsorSliderForm(request.POST, level_counts=self._level_counts(competition))
        if not form.is_valid():
            return self._render(request, competition, settings_row, form, status=400)
        before = {
            "enabled": settings_row.sponsor_slider_enabled,
            "seconds": settings_row.sponsor_slider_seconds,
            "levels": list(settings_row.sponsor_slider_levels or []),
        }
        settings_row.sponsor_slider_enabled = form.cleaned_data["enabled"]
        settings_row.sponsor_slider_seconds = form.cleaned_data["seconds"]
        settings_row.sponsor_slider_levels = form.cleaned_data["levels"]
        settings_row.save(
            update_fields=["sponsor_slider_enabled", "sponsor_slider_seconds", "sponsor_slider_levels"]
        )
        after = {
            "enabled": settings_row.sponsor_slider_enabled,
            "seconds": settings_row.sponsor_slider_seconds,
            "levels": list(settings_row.sponsor_slider_levels or []),
        }
        if before != after:
            audit(
                request.user,
                "site.sponsor_slider_updated",
                settings_row,
                {
                    "enabled": {"old": before["enabled"], "new": after["enabled"]},
                    "seconds": {"old": before["seconds"], "new": after["seconds"]},
                    "levels": {"old": before["levels"], "new": after["levels"]},
                },
                request=request,
            )
            state = "włączony" if after["enabled"] else "wyłączony"
            messages.success(request, f"Zapisano ustawienia slidera sponsorów – jest teraz {state}.")
        else:
            messages.info(request, "Nic się nie zmieniło.")
        return redirect(reverse("web:coordinator-sponsor-slider"))

    def _competition(self, request):
        """Konkurs żądania. Brak konkursu = nie ma czego konfigurować, czyli 404.

        Ta sama reguła, co na ekranie przekazywania rozwiązań: obiekt bierzemy z
        ``request.competition``, a nie z identyfikatora w adresie – nie ma tu adresu, pod którym
        dałoby się wskazać cudzy konkurs.
        """
        competition = request.competition
        if competition is None:
            raise Http404("Pod tym adresem nie stoi żaden konkurs.")
        return competition

    def _settings(self, competition) -> SiteSettings:
        return SiteSettings.for_site(competition.site)

    def _level_counts(self, competition) -> dict[str, int]:
        """Partnerów **z logotypem** na każdym poziomie – liczba obok pola w formularzu."""
        page = self._partners_page(competition)
        counts: dict[str, int] = {}
        if page is None:
            return counts
        for block in page.partners:
            if block.value.get("logo") is None:
                continue
            level = block.value.get("level")
            counts[level] = counts.get(level, 0) + 1
        return counts

    def _partners_page(self, competition) -> PartnersPage | None:
        return PartnersPage.objects.live().child_of(competition.site.root_page).first()

    def _preview_rows(self, competition, settings_row) -> list[dict]:
        """Co pokaże slider **teraz** – organizator zawsze pierwszy, potem każdy partner.

        Wiersz partnera zostaje na liście nawet wtedy, gdy slider go pomija: koordynator ma
        zobaczyć powód (brak logotypu, poziom poza filtrem, duplikat organizatora), a nie
        zgadywać, czemu znaku nie widać w menu.
        """
        organizer_url = normalize_url(settings_row.contact_url)
        rows = [
            {
                "kind": "organizer",
                "name": settings_row.organizer_name,
                "level_label": "Organizator – zawsze pierwszy",
                "has_logo": settings_row.organizer_logo is not None,
                "has_link": bool((settings_row.contact_url or "").strip()),
                "shown": settings_row.organizer_logo is not None,
                "note": "" if settings_row.organizer_logo is not None else "Brak logotypu",
            }
        ]
        levels = frozenset(settings_row.sponsor_slider_levels or [])
        page = self._partners_page(competition)
        for block in page.partners if page is not None else []:
            value = block.value
            has_logo = value.get("logo") is not None
            url = (value.get("url") or "").strip()
            level = value.get("level")
            duplicate = bool(has_logo and url and organizer_url and normalize_url(url) == organizer_url)
            in_levels = not levels or level in levels
            if not has_logo:
                note = "Brak logotypu"
            elif duplicate:
                note = "Ten sam adres co organizator – pominięty"
            elif not in_levels:
                note = "Poziom nie zaznaczony w sliderze"
            else:
                note = ""
            rows.append(
                {
                    "kind": "partner",
                    "name": value.get("name", ""),
                    "level_label": LEVEL_LABELS.get(level, level or ""),
                    "has_logo": has_logo,
                    "has_link": bool(url),
                    "shown": has_logo and in_levels and not duplicate,
                    "note": note,
                }
            )
        return rows

    def _render(self, request, competition, settings_row, form, *, status: int = 200):
        context = {
            "competition": competition,
            "form": form,
            "max_seconds": SPONSOR_SLIDER_MAX_SECONDS,
            "rows": self._preview_rows(competition, settings_row),
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)
